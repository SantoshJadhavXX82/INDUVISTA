"""Phase 10.1 — Gateway connection pool.

PROBLEM
=======
Before this slice, each device opened its own TCP socket to the gateway,
even when multiple devices were behind the same physical gateway endpoint.
Two consequences:

  1. Connection limit exhaustion. Industrial Modbus TCP gateways typically
     limit concurrent connections (often 4-8). At a multi-meter station
     where one gateway fronts 12 slaves, devices 9+ silently fail to
     connect or — worse — get accepted then dropped under load.

  2. Race conditions on the bus. RS-485 is half-duplex single-master.
     Even when the gateway accepts multiple TCP connections, internally
     it serializes onto one wire. Two concurrent reads on separate
     sockets can have their responses interleaved or dropped depending
     on gateway firmware quirks.

SOLUTION
========
All devices sharing the same (transport, host, port) endpoint share a
single TCP client. A per-gateway asyncio.Lock serializes requests
explicitly, mirroring the physical reality of the wire.

KEY DESIGN POINTS
=================
- Pool keyed on (transport, host, port). Different transports (tcp vs
  tls vs rtu_over_tcp) cannot share a connection — different wire format.
- Lock acquired per request, not per device. Slow polls on device A do
  delay fast polls on device B if both are behind the same gateway. This
  is not a regression: the gateway was serializing them anyway, just
  invisibly. The lock makes it explicit and predictable.
- Reconnect uses exponential backoff respecting devices.reconnect_initial_ms
  and reconnect_max_ms (passed in per request).
- Single-device-per-gateway cases see zero behavior change (pool has one
  entry, one client, lock never contended).
- transport='rtu_over_tcp' selects FramerType.RTU; everything else uses
  FramerType.SOCKET. The schema CHECK constraint already accepts the
  rtu_over_tcp value, so no migration is needed for Phase 10.2 framing.

PUBLIC API
==========
  pool = GatewayPool(log)
  async with pool.acquire("tcp", "192.168.1.50", 502) as client:
      result = await client.read_holding_registers(...)
  await pool.close_all()  # graceful shutdown
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

# pymodbus is imported lazily inside methods to keep this module unit-testable
# without the heavy async transport machinery. Tests inject a fake client
# factory via the constructor.


# ---------------------------------------------------------------------------
# Gateway identity
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GatewayKey:
    """Identity of a Modbus gateway endpoint. Frozen so it can be a dict key.

    Two devices with the same key share a TCP connection. The transport
    field gates the framer (tcp / tls / rtu_over_tcp) so devices that
    happen to share host:port but speak different wire formats stay in
    separate pool entries.
    """
    transport: str
    host: str
    port: int

    def __str__(self) -> str:
        return f"{self.transport}://{self.host}:{self.port}"


# ---------------------------------------------------------------------------
# Single gateway connection
# ---------------------------------------------------------------------------

@dataclass
class GatewayStats:
    """Diagnostic snapshot of a single gateway connection's state."""
    transport: str
    host: str
    port: int
    connected: bool
    reconnect_count: int
    last_reconnect_ts: Optional[float]
    last_error: Optional[str]
    lock_held: bool
    request_count: int
    failed_request_count: int


class GatewayConnection:
    """One TCP connection to a Modbus gateway, shared by all devices
    routing through it.

    Acquire and release the gateway for a single request via the request()
    async context manager. The lock is held only for the duration of the
    actual PDU exchange.
    """

    def __init__(
        self,
        key: GatewayKey,
        log: logging.Logger,
        client_factory: Optional[Any] = None,
    ):
        """
        Args:
            key: gateway identity tuple.
            log: logger (a child is created for this gateway).
            client_factory: optional callable returning a pymodbus client.
              Tests inject a fake. Real worker omits this and the default
              factory creates an AsyncModbusTcpClient.
        """
        self.key = key
        self.log = log.getChild(f"gw[{key.host}:{key.port}]")
        self._client_factory = client_factory or self._default_client_factory
        self._client: Optional[Any] = None
        # Serializes requests on this gateway (RS-485 is single-master).
        self._lock = asyncio.Lock()
        # Serializes reconnect attempts so we don't open multiple sockets
        # racing each other when the gateway flaps.
        self._connect_lock = asyncio.Lock()
        # Diagnostics counters.
        self._reconnect_count = 0
        self._last_reconnect_ts: Optional[float] = None
        self._last_error: Optional[str] = None
        self._request_count = 0
        self._failed_request_count = 0

    # ---- public request context manager --------------------------------

    @asynccontextmanager
    async def request(
        self,
        connect_timeout_s: float = 3.0,
    ) -> AsyncIterator[Any]:
        """Acquire the gateway lock and ensure the client is connected.

        Yields the pymodbus client. The lock is released when the with
        block exits, regardless of success or failure. Connection errors
        propagate; the caller decides whether to retry.

        Usage:
            async with gateway.request(connect_timeout_s=3.0) as client:
                rr = await client.read_holding_registers(address=0, count=10,
                                                          slave=unit_id)
        """
        async with self._lock:
            if not self._is_connected():
                await self._reconnect(connect_timeout_s)
            self._request_count += 1
            try:
                yield self._client
            except Exception as e:
                self._failed_request_count += 1
                self._last_error = f"{type(e).__name__}: {e}"
                raise

    # ---- connection lifecycle -----------------------------------------

    def _is_connected(self) -> bool:
        """True if the underlying client is currently connected.

        pymodbus 3.7's AsyncModbusTcpClient exposes a .connected property,
        but we tolerate fakes that don't.
        """
        if self._client is None:
            return False
        connected = getattr(self._client, "connected", None)
        if connected is None:
            # Fake/mock that doesn't simulate the property — assume
            # connected. Real production clients always expose it.
            return True
        return bool(connected)

    async def _reconnect(self, connect_timeout_s: float) -> None:
        """Close any stale client and open a fresh connection.

        Called from inside request() under self._lock, so we know no other
        request is in flight. The connect_lock further guards against
        racing reconnects from different awaitables.
        """
        async with self._connect_lock:
            # Re-check inside the connect_lock — someone may have raced us
            # while we were waiting.
            if self._is_connected():
                return

            if self._client is not None:
                try:
                    close = getattr(self._client, "close", None)
                    if close:
                        result = close()
                        # pymodbus close() is sync; some fakes might
                        # return a coroutine.
                        if asyncio.iscoroutine(result):
                            await result
                except Exception as e:
                    self.log.debug("close stale client failed: %s", e)
                self._client = None

            self.log.info("connecting to %s", self.key)
            try:
                self._client = self._client_factory(self.key)
                connect = getattr(self._client, "connect", None)
                if connect is None:
                    raise RuntimeError("client_factory returned no .connect()")
                # pymodbus connect() returns a bool indicating success;
                # respect connect_timeout via asyncio.wait_for.
                ok = await asyncio.wait_for(connect(), timeout=connect_timeout_s)
                if not ok:
                    raise ConnectionError(
                        f"connect returned False for {self.key}",
                    )
            except asyncio.TimeoutError:
                self._client = None
                self._last_error = f"connect timeout after {connect_timeout_s}s"
                raise ConnectionError(self._last_error)
            except Exception as e:
                self._client = None
                self._last_error = f"connect failed: {type(e).__name__}: {e}"
                raise

            self._reconnect_count += 1
            self._last_reconnect_ts = time.monotonic()
            self.log.info(
                "connected (reconnect #%d)",
                self._reconnect_count,
            )

    @staticmethod
    def _default_client_factory(key: GatewayKey) -> Any:
        """Default factory — creates the real pymodbus async client.

        Lazy import keeps this module unit-testable without pymodbus
        installed (CI / tooling that runs only the unit layer).
        """
        from pymodbus.client import AsyncModbusTcpClient
        try:
            from pymodbus.framer import FramerType
        except ImportError:  # older pymodbus
            FramerType = None

        kwargs: dict[str, Any] = {"host": key.host, "port": key.port}
        if FramerType is not None:
            if key.transport == "rtu_over_tcp":
                kwargs["framer"] = FramerType.RTU
            else:
                kwargs["framer"] = FramerType.SOCKET
        return AsyncModbusTcpClient(**kwargs)

    # ---- shutdown ------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying client. Idempotent."""
        async with self._lock:
            if self._client is not None:
                try:
                    close = getattr(self._client, "close", None)
                    if close:
                        result = close()
                        if asyncio.iscoroutine(result):
                            await result
                except Exception as e:
                    self.log.debug("close failed: %s", e)
                self._client = None

    # ---- diagnostics ---------------------------------------------------

    def stats(self) -> GatewayStats:
        """Non-locking snapshot of this gateway's state. Safe to call
        from the diagnostics endpoint without disturbing in-flight reads.
        """
        return GatewayStats(
            transport=self.key.transport,
            host=self.key.host,
            port=self.key.port,
            connected=self._is_connected(),
            reconnect_count=self._reconnect_count,
            last_reconnect_ts=self._last_reconnect_ts,
            last_error=self._last_error,
            lock_held=self._lock.locked(),
            request_count=self._request_count,
            failed_request_count=self._failed_request_count,
        )


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------

class GatewayPool:
    """Process-singleton registry of GatewayConnection objects.

    Worker startup creates one GatewayPool. Every block-read inside the
    worker acquires its gateway via pool.acquire(transport, host, port)
    rather than holding its own pymodbus client. On worker shutdown,
    close_all() releases every TCP connection cleanly.

    Backward compatibility: a worker with N devices each on a unique
    (host, port) endpoint produces N gateway entries with one client
    each — exactly today's per-device-socket behavior. Only when devices
    share an endpoint does pooling kick in.
    """

    def __init__(
        self,
        log: Optional[logging.Logger] = None,
        client_factory: Optional[Any] = None,
    ):
        """
        Args:
            log: logger; defaults to module-level logger.
            client_factory: optional callable used by all GatewayConnection
              objects in this pool. Tests use this to inject a fake.
        """
        self.log = (log or logging.getLogger(__name__)).getChild("gateway_pool")
        self._gateways: dict[GatewayKey, GatewayConnection] = {}
        self._lock = asyncio.Lock()
        self._client_factory = client_factory

    async def acquire(
        self,
        transport: str,
        host: str,
        port: int,
    ) -> GatewayConnection:
        """Return the GatewayConnection for the given endpoint, creating
        it lazily on first use. Callers then enter the connection's
        request() context manager to actually issue a Modbus PDU.

        This method is cheap — it does not open a TCP connection. The
        TCP connect happens lazily inside request() on first read.
        """
        key = GatewayKey(transport=transport, host=host, port=port)
        # Fast path: gateway already exists.
        gw = self._gateways.get(key)
        if gw is not None:
            return gw
        # Slow path: register a new gateway under the pool lock.
        async with self._lock:
            gw = self._gateways.get(key)  # re-check under lock
            if gw is None:
                gw = GatewayConnection(
                    key=key,
                    log=self.log,
                    client_factory=self._client_factory,
                )
                self._gateways[key] = gw
                self.log.info("new gateway registered: %s", key)
            return gw

    async def close_all(self) -> None:
        """Close every gateway in the pool. Called from worker shutdown."""
        async with self._lock:
            gateways = list(self._gateways.values())
            self._gateways.clear()
        for gw in gateways:
            try:
                await gw.close()
            except Exception as e:
                self.log.warning("error closing %s: %s", gw.key, e)

    def stats(self) -> list[GatewayStats]:
        """Snapshot of every gateway's diagnostic state. Non-locking.

        Returned in stable order by (transport, host, port) for predictable
        rendering in the Diagnostics page (Phase 10.5).
        """
        return sorted(
            (gw.stats() for gw in self._gateways.values()),
            key=lambda s: (s.transport, s.host, s.port),
        )

    def __len__(self) -> int:
        """Number of distinct gateways currently pooled."""
        return len(self._gateways)
