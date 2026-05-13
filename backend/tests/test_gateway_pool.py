"""Unit tests for app.workers.gateway_pool.

The pool's correctness rests on three invariants:

  1. Two devices with the same (transport, host, port) share one client.
  2. Requests through that shared client are serialized (lock held).
  3. Different transports / hosts / ports get separate clients.

These tests verify all three using a fake client factory that captures
calls without touching the network. No pymodbus required.

The test suite also covers:
  - Reconnect on demand (client closed mid-test → next request reconnects)
  - Shutdown closes all clients
  - Stats reflect runtime state without disturbing in-flight requests
  - Concurrent acquirers race-safely under the pool lock
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from app.workers.gateway_pool import (
    GatewayConnection,
    GatewayKey,
    GatewayPool,
    GatewayStats,
)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fake pymodbus client — captures calls and lets tests assert behavior
# without bringing up real TCP sockets.
# ---------------------------------------------------------------------------

class FakeClient:
    """Minimal stand-in for pymodbus's AsyncModbusTcpClient."""

    def __init__(self, key: GatewayKey, connect_succeeds: bool = True):
        self.key = key
        self.connected: bool = False
        self.connect_succeeds = connect_succeeds
        self.connect_call_count = 0
        self.close_call_count = 0

    async def connect(self) -> bool:
        self.connect_call_count += 1
        if not self.connect_succeeds:
            return False
        self.connected = True
        return True

    def close(self) -> None:
        self.close_call_count += 1
        self.connected = False


class FakeClientFactory:
    """Builds FakeClient instances and records every key it was asked for."""

    def __init__(self, connect_succeeds: bool = True):
        self.created: list[FakeClient] = []
        self.connect_succeeds = connect_succeeds

    def __call__(self, key: GatewayKey) -> FakeClient:
        client = FakeClient(key, connect_succeeds=self.connect_succeeds)
        self.created.append(client)
        return client


# ---------------------------------------------------------------------------
# Shared marker for the async test classes below — keeps the sync
# TestGatewayKey class from being incorrectly marked asyncio.
# ---------------------------------------------------------------------------

_async_marks = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# GatewayKey identity
# ---------------------------------------------------------------------------

class TestGatewayKey:
    def test_equal_keys_hash_equal(self):
        a = GatewayKey(transport="tcp", host="10.0.0.1", port=502)
        b = GatewayKey(transport="tcp", host="10.0.0.1", port=502)
        assert a == b
        assert hash(a) == hash(b)

    def test_different_host_different_key(self):
        a = GatewayKey(transport="tcp", host="10.0.0.1", port=502)
        b = GatewayKey(transport="tcp", host="10.0.0.2", port=502)
        assert a != b

    def test_different_port_different_key(self):
        a = GatewayKey(transport="tcp", host="10.0.0.1", port=502)
        b = GatewayKey(transport="tcp", host="10.0.0.1", port=503)
        assert a != b

    def test_different_transport_different_key(self):
        a = GatewayKey(transport="tcp", host="10.0.0.1", port=502)
        b = GatewayKey(transport="rtu_over_tcp", host="10.0.0.1", port=502)
        assert a != b

    def test_key_is_frozen(self):
        """Frozen so it can be a dict key and never mutate."""
        k = GatewayKey(transport="tcp", host="10.0.0.1", port=502)
        with pytest.raises((AttributeError, TypeError)):
            k.host = "10.0.0.2"  # type: ignore

    def test_str_representation(self):
        k = GatewayKey(transport="tcp", host="10.0.0.1", port=502)
        assert "10.0.0.1:502" in str(k)
        assert "tcp" in str(k)


# ---------------------------------------------------------------------------
# Pool keying — the core promise
# ---------------------------------------------------------------------------

@_async_marks
class TestPoolKeying:
    async def test_same_endpoint_returns_same_gateway(self):
        """Two acquires for the same (transport, host, port) → same object."""
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        gw1 = await pool.acquire("tcp", "10.0.0.1", 502)
        gw2 = await pool.acquire("tcp", "10.0.0.1", 502)
        assert gw1 is gw2
        assert len(pool) == 1

    async def test_different_host_separate_gateways(self):
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        gw1 = await pool.acquire("tcp", "10.0.0.1", 502)
        gw2 = await pool.acquire("tcp", "10.0.0.2", 502)
        assert gw1 is not gw2
        assert len(pool) == 2

    async def test_different_port_separate_gateways(self):
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        gw1 = await pool.acquire("tcp", "10.0.0.1", 502)
        gw2 = await pool.acquire("tcp", "10.0.0.1", 5020)
        assert gw1 is not gw2
        assert len(pool) == 2

    async def test_different_transport_separate_gateways(self):
        """tcp and rtu_over_tcp on the same host:port are different
        gateways — different wire format, cannot share a socket."""
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        gw1 = await pool.acquire("tcp", "10.0.0.1", 502)
        gw2 = await pool.acquire("rtu_over_tcp", "10.0.0.1", 502)
        assert gw1 is not gw2
        assert len(pool) == 2

    async def test_acquire_is_lazy_no_connection_yet(self):
        """acquire() must NOT open the TCP connection — that happens on
        first request(). Cheap acquires keep startup fast."""
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        await pool.acquire("tcp", "10.0.0.1", 502)
        # No FakeClient should have been built yet
        assert len(factory.created) == 0


# ---------------------------------------------------------------------------
# Request serialization — the lock semantics
# ---------------------------------------------------------------------------

@_async_marks
class TestRequestSerialization:
    async def test_request_connects_on_first_use(self):
        """First request triggers connect; subsequent requests reuse."""
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        gw = await pool.acquire("tcp", "10.0.0.1", 502)
        async with gw.request():
            pass
        assert len(factory.created) == 1
        assert factory.created[0].connect_call_count == 1

        async with gw.request():
            pass
        # Reuses the same client — no new connect
        assert len(factory.created) == 1
        assert factory.created[0].connect_call_count == 1

    async def test_two_concurrent_requests_serialize(self):
        """When two coroutines try to use the gateway simultaneously,
        only one holds the lock at a time. Verified by observing that
        the second cannot enter while the first is in the with block.
        """
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        gw = await pool.acquire("tcp", "10.0.0.1", 502)
        inside = asyncio.Event()
        release = asyncio.Event()
        order: list[str] = []

        async def slow_task():
            async with gw.request():
                order.append("slow_in")
                inside.set()
                await release.wait()
                order.append("slow_out")

        async def fast_task():
            await inside.wait()  # ensure slow is inside first
            async with gw.request():
                order.append("fast_in")
                order.append("fast_out")

        slow = asyncio.create_task(slow_task())
        fast = asyncio.create_task(fast_task())
        # Give fast a chance to attempt and block
        await asyncio.sleep(0.05)
        # Fast must NOT have entered yet
        assert order == ["slow_in"]
        release.set()
        await asyncio.gather(slow, fast)
        assert order == ["slow_in", "slow_out", "fast_in", "fast_out"]

    async def test_request_count_increments(self):
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        gw = await pool.acquire("tcp", "10.0.0.1", 502)
        for _ in range(5):
            async with gw.request():
                pass
        assert gw.stats().request_count == 5

    async def test_failed_request_counts(self):
        """An exception inside the with block increments the failure
        counter and propagates."""
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        gw = await pool.acquire("tcp", "10.0.0.1", 502)
        with pytest.raises(RuntimeError, match="simulated"):
            async with gw.request():
                raise RuntimeError("simulated read failure")
        s = gw.stats()
        assert s.request_count == 1
        assert s.failed_request_count == 1


# ---------------------------------------------------------------------------
# Reconnect logic
# ---------------------------------------------------------------------------

@_async_marks
class TestReconnect:
    async def test_reconnect_after_disconnect(self):
        """If the underlying client goes from connected=True to False
        between requests, the next request reconnects."""
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        gw = await pool.acquire("tcp", "10.0.0.1", 502)
        async with gw.request():
            pass
        # Simulate gateway dropping the connection
        factory.created[0].connected = False
        async with gw.request():
            pass
        # Either a fresh client or reconnect on the old one — both valid
        # behaviors. Assert that connection happened at least twice in
        # total (the second request had to reconnect somehow).
        total_connects = sum(c.connect_call_count for c in factory.created)
        assert total_connects >= 2

    async def test_connect_failure_propagates(self):
        """If pymodbus's connect() returns False, request() raises."""
        factory = FakeClientFactory(connect_succeeds=False)
        pool = GatewayPool(client_factory=factory)
        gw = await pool.acquire("tcp", "10.0.0.1", 502)
        with pytest.raises(ConnectionError):
            async with gw.request():
                pass

    async def test_connect_timeout_propagates(self):
        """If connect() hangs longer than connect_timeout_s, raises
        ConnectionError (translated from asyncio.TimeoutError)."""
        class HangingClient:
            connected = False
            async def connect(self):
                await asyncio.sleep(10)  # longer than our timeout
                return True
            def close(self):
                pass

        def factory(key: GatewayKey) -> Any:
            return HangingClient()

        pool = GatewayPool(client_factory=factory)
        gw = await pool.acquire("tcp", "10.0.0.1", 502)
        with pytest.raises(ConnectionError):
            async with gw.request(connect_timeout_s=0.1):
                pass

    async def test_reconnect_count_increments(self):
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        gw = await pool.acquire("tcp", "10.0.0.1", 502)
        async with gw.request():
            pass
        assert gw.stats().reconnect_count == 1
        # Force disconnect, request again
        factory.created[0].connected = False
        async with gw.request():
            pass
        assert gw.stats().reconnect_count == 2


# ---------------------------------------------------------------------------
# Multi-device sharing — the headline scenario
# ---------------------------------------------------------------------------

@_async_marks
class TestMultiDeviceSharing:
    async def test_two_devices_one_gateway_one_client(self):
        """Two devices behind the same gateway share ONE TCP client.
        This is the entire point of Phase 10.1."""
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)

        # Device A and Device B both behind 10.0.0.50:502
        gw_a = await pool.acquire("tcp", "10.0.0.50", 502)
        gw_b = await pool.acquire("tcp", "10.0.0.50", 502)
        assert gw_a is gw_b

        # Each device fires a request
        async with gw_a.request():
            pass
        async with gw_b.request():
            pass

        # Only ONE FakeClient should ever have been created
        assert len(factory.created) == 1
        # Both requests went through it
        assert gw_a.stats().request_count == 2

    async def test_two_devices_two_gateways_two_clients(self):
        """Two devices on different gateways → two separate clients."""
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)

        gw_a = await pool.acquire("tcp", "10.0.0.50", 502)
        gw_b = await pool.acquire("tcp", "10.0.0.51", 502)
        async with gw_a.request():
            pass
        async with gw_b.request():
            pass

        assert len(factory.created) == 2

    async def test_single_device_per_gateway_no_regression(self):
        """The backward-compat invariant: a single device on a single
        gateway sees identical behavior to pre-pooling. One client, no
        lock contention, one connect."""
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        gw = await pool.acquire("tcp", "10.0.0.1", 502)
        for _ in range(10):
            async with gw.request():
                pass
        assert len(factory.created) == 1
        assert factory.created[0].connect_call_count == 1


# ---------------------------------------------------------------------------
# Concurrent acquire — pool itself must be race-safe
# ---------------------------------------------------------------------------

@_async_marks
class TestConcurrentAcquire:
    async def test_burst_acquires_same_key_single_entry(self):
        """20 coroutines all acquire the same key simultaneously →
        exactly one GatewayConnection registered."""
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        gws = await asyncio.gather(*[
            pool.acquire("tcp", "10.0.0.1", 502)
            for _ in range(20)
        ])
        # All 20 returned the same object
        assert all(g is gws[0] for g in gws)
        assert len(pool) == 1

    async def test_burst_acquires_different_keys(self):
        """20 coroutines acquiring 20 different keys → 20 entries."""
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        await asyncio.gather(*[
            pool.acquire("tcp", f"10.0.0.{i}", 502)
            for i in range(1, 21)
        ])
        assert len(pool) == 20


# ---------------------------------------------------------------------------
# Stats — diagnostics page support (Phase 10.5)
# ---------------------------------------------------------------------------

@_async_marks
class TestStats:
    async def test_stats_initial_state(self):
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        gw = await pool.acquire("tcp", "10.0.0.1", 502)
        s = gw.stats()
        assert s.transport == "tcp"
        assert s.host == "10.0.0.1"
        assert s.port == 502
        assert s.connected is False
        assert s.reconnect_count == 0
        assert s.request_count == 0
        assert s.failed_request_count == 0
        assert s.last_reconnect_ts is None
        assert s.last_error is None
        assert s.lock_held is False

    async def test_stats_after_use(self):
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        gw = await pool.acquire("tcp", "10.0.0.1", 502)
        async with gw.request():
            pass
        s = gw.stats()
        assert s.connected is True
        assert s.reconnect_count == 1
        assert s.request_count == 1
        assert s.last_reconnect_ts is not None

    async def test_pool_stats_sorted_stable(self):
        """Pool stats returned in deterministic order for UI rendering."""
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        # Acquire in non-alphabetical order
        await pool.acquire("tcp", "10.0.0.5", 502)
        await pool.acquire("tcp", "10.0.0.1", 502)
        await pool.acquire("rtu_over_tcp", "10.0.0.3", 502)
        stats = pool.stats()
        # Sorted by (transport, host, port)
        keys = [(s.transport, s.host, s.port) for s in stats]
        assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

@_async_marks
class TestShutdown:
    async def test_close_all_closes_every_client(self):
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        gw1 = await pool.acquire("tcp", "10.0.0.1", 502)
        gw2 = await pool.acquire("tcp", "10.0.0.2", 502)
        async with gw1.request():
            pass
        async with gw2.request():
            pass
        await pool.close_all()
        # Both clients closed
        for c in factory.created:
            assert c.close_call_count == 1
        # Pool is empty after close_all
        assert len(pool) == 0

    async def test_close_all_idempotent(self):
        """Calling close_all twice doesn't crash."""
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        await pool.acquire("tcp", "10.0.0.1", 502)
        await pool.close_all()
        await pool.close_all()  # should not raise

    async def test_close_on_unused_gateway_safe(self):
        """A gateway that was acquired but never used (no TCP connection)
        can still be cleanly closed."""
        factory = FakeClientFactory()
        pool = GatewayPool(client_factory=factory)
        await pool.acquire("tcp", "10.0.0.1", 502)
        await pool.close_all()  # should not raise
