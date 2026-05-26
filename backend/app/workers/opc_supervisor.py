"""OPC UA polling worker (supervisor) — Phase OPC-web.2 + OPC-web.2.1.

Loads enabled rows from `opc_sources` + `opc_tag_mappings`, opens an
asyncua.Client per source, subscribes to the configured nodes, and
writes samples through a BufferedHistorianWriter that falls back to a
local SQLite buffer on Postgres failure.

PROCESS MODEL
=============

Standalone process, same pattern as `app.workers.modbus_supervisor`.
One container, one asyncio event loop, N concurrent source-worker
coroutines + one flusher coroutine + one config-reloader coroutine.

  ┌─────────────────────────────────────────────────────────────┐
  │  asyncio event loop                                         │
  │                                                             │
  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
  │  │ source 1     │    │ source 2     │    │ source N     │  │
  │  │ worker coro  │    │ worker coro  │ …  │ worker coro  │  │
  │  │              │    │              │    │              │  │
  │  │ asyncua.     │    │ asyncua.     │    │ asyncua.     │  │
  │  │ Client       │    │ Client       │    │ Client       │  │
  │  │ │            │    │ │            │    │ │            │  │
  │  │ DataChange   │    │ DataChange   │    │ DataChange   │  │
  │  │ Notification │    │ Notification │    │ Notification │  │
  │  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘  │
  │         │                   │                   │          │
  │         └───────────────────┴───────────────────┘          │
  │                             │                              │
  │                             ▼                              │
  │                    shared sample buffer                    │
  │                    (list, single-threaded ⇒ no lock)       │
  │                             │                              │
  │                             ▼                              │
  │                       flusher coro                         │
  │                       (every 1s)                           │
  │                             │                              │
  │                             ▼                              │
  │              BufferedHistorianWriter                       │
  │                 ├─ direct  → Postgres tag_values           │
  │                 └─ on fail → SQLite at $SF_OPC_BUFFER_PATH │
  │                                                             │
  │   ┌─────────────────────────────────────────────────────┐  │
  │   │ config_reloader coro (every 30s)                    │  │
  │   │ - SELECT id, updated_at, is_enabled,                │  │
  │   │     last_mapping_change, mapping_count              │  │
  │   │   FROM opc_sources LEFT JOIN opc_tag_mappings       │  │
  │   │ - Compare fingerprints; for each delta:             │  │
  │   │     added   → start a new source worker             │  │
  │   │     removed → fire its restart_event, drop task     │  │
  │   │     changed → fire restart_event, then respawn      │  │
  │   └─────────────────────────────────────────────────────┘  │
  └─────────────────────────────────────────────────────────────┘

Each source coroutine reconnects forever with exponential backoff
(reconnect_min_sec → reconnect_max_sec from the opc_sources row).

CONFIG HOT-RELOAD (Phase OPC-web.2.1)
=====================================

The supervisor polls `opc_sources` + `opc_tag_mappings` every
RELOAD_INTERVAL_SEC (default 30s) and computes a per-source
fingerprint of (updated_at, is_enabled, last_mapping_change,
mapping_count). When the fingerprint for any source changes, only
THAT source's worker is restarted; other sources keep running.

When a source is added or re-enabled, a fresh worker task is spawned
on the fly. When a source is disabled or deleted, its worker's
per-source `restart_event` is set and the worker tears down its
subscription and exits cleanly.

This means operators can edit OPC sources and mappings from the
React page and see changes propagate within ~30s, without a worker
restart and without disturbing other sources. The matching API
endpoints (`create_mapping`, `delete_mapping`) bump
`opc_sources.updated_at` so adding/removing nodes also triggers
reload; the fingerprint's `last_mapping_change` + `mapping_count`
fields are a defence-in-depth backstop in case some future
codepath forgets the bump.

REPLAY
======

Phase OPC-web.2 — NOT IMPLEMENTED. The store-and-forward buffer
collects samples on Postgres outage, but nothing drains them back.
Modbus shares the replay_loop; OPC will use the same mechanism once
we promote the buffer path to shared. For OPC-web.2 the SF buffer is
write-only — samples that land there during an outage will be lost
on container restart. Production deployments should configure
$SF_OPC_BUFFER_PATH to a docker volume so the buffer survives.

QUALITY MAPPING
===============

OPC UA's 32-bit StatusCode encodes severity in the high 2 bits. We
map to INDUVISTA's st byte:

  severity = 00 (Good)      → st = 192     (matches modbus ST_VALID)
  severity = 01 (Uncertain) → st = 96
  severity = 10 (Bad)       → st = 0

The full StatusCode hex is stored in st_reason for diagnostics.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from asyncua import Client, ua
from sqlalchemy import text

from app.db import engine
from app.historian import BufferedHistorianWriter, HistorianWriter, Sample
from app.local_buffer import LocalBuffer


log = logging.getLogger(__name__)


# ── Tunables ────────────────────────────────────────────────────────

# How often the flusher drains the in-memory sample buffer to the
# historian. 1s = ~10 batches per Simpe update cycle; safely below
# the historian's per-batch overhead at typical OPC volumes (~10s
# update rate × dozens of tags). Tune higher if your sources fire
# faster than the historian can absorb.
FLUSH_INTERVAL_SEC = 1.0

# Session + secure-channel timeouts handed to asyncua. Phase OPC-web.2.1
# bumped these from 30s after observing asyncua's secure-channel renewal
# fail every 25 minutes against AGG Software's open62541-based simulator.
# asyncua tries to renew at 75% of channel_timeout; the SecureOpen request
# returns a TimeoutError that asyncua logs and re-raises in its background
# _renew_channel_loop task, killing the renewal task without disconnecting
# the publish loop — so samples keep flowing but the channel silently
# expires. 600s pushes the renewal interval out to 7.5 minutes, well
# clear of the failure window, and the heartbeat watchdog below is a
# defence-in-depth catch for any other silent-stall scenario.
UA_SESSION_TIMEOUT_MS = 600_000
UA_CHANNEL_TIMEOUT_MS = 600_000

# Heartbeat watchdog: if a source goes this many seconds without any
# DataChangeNotification, force a reconnect by firing its restart_event.
# This catches scenarios asyncua can't detect: silent socket stalls,
# secure-channel renewal failures, server hangs that don't drop the TCP
# session.
#
# Computed per-source as max(WATCHDOG_MIN_SEC, multiplier * publishing_interval).
# A 1s publishing interval source needs a different watchdog than one
# published every minute. Default multiplier of 5 is generous enough to
# survive normal jitter; minimum 30s avoids false positives on slow links.
WATCHDOG_PUBLISH_MULTIPLIER = 5
WATCHDOG_MIN_SEC = 30.0

# Phase OPC-web.2.1 — how often the config_reloader polls the DB
# for opc_sources/mappings changes. 30s matches modbus_supervisor's
# cadence; cheap on Postgres (one indexed scan + one count per
# source), gives operators a "config takes effect within ~30s"
# expectation that's clearly distinct from "live" (the worker
# itself reacts to data changes in <1s once subscribed).
RELOAD_INTERVAL_SEC = 30.0


# ── Quality mapping ─────────────────────────────────────────────────


def _ua_status_to_st(status: Any) -> tuple[int, str | None]:
    """OPC UA StatusCode → (INDUVISTA st byte, st_reason text).

    Returns (192, None) for good samples — same as modbus's ST_VALID.
    Returns (96, code_hex) for uncertain.
    Returns (0,  code_hex) for bad.
    """
    if status is None:
        return 192, None
    code = int(status.value) if hasattr(status, "value") else int(status)
    severity = (code >> 30) & 0x3
    if severity == 0:
        return 192, None
    if severity == 1:
        return 96, f"UA_UNCERTAIN_{code:#010x}"
    return 0, f"UA_BAD_{code:#010x}"


def _coerce_value(val: Any) -> tuple[float | None, str | None]:
    """Map a UA-decoded value to (value_double, value_text).

    bool is checked BEFORE int because Python's bool is a subclass
    of int — `isinstance(True, int) == True` would otherwise route
    bools through the numeric branch and lose the type distinction.
    For now we still store bools as 1.0/0.0 in value_double to match
    Modbus tag conventions (the existing dashboard renders them via
    the data_type column, not by inspecting which column has data).
    """
    if val is None:
        return None, None
    if isinstance(val, bool):
        return (1.0 if val else 0.0), None
    if isinstance(val, (int, float)):
        return float(val), None
    if isinstance(val, str):
        return None, val
    # Datetime, GUID, ByteString, etc. — stringify and stash in text.
    return None, str(val)


# ── DataChange handler (asyncua callback target) ────────────────────


@dataclass
class _SourceContext:
    """Per-source bag passed to the asyncua subscription handler. Holds
    everything the handler needs to convert a UA notification into a
    Sample row: the source's synthetic device_id, its NodeId→tag_id
    map, and a reference to the shared buffer.

    `last_sample_ts` is updated by the subscription handler on every
    DataChangeNotification and read by the per-source watchdog task.
    Single-threaded asyncio loop ⇒ no lock needed. Time source is
    `loop.time()` (monotonic) so wall-clock changes don't trip the
    watchdog falsely.
    """
    source_id: int
    source_name: str
    device_id: int
    tag_by_node: dict[str, int]
    buffer: "_SampleBuffer"
    last_sample_ts: float = 0.0


class _SubHandler:
    """asyncua delivers DataChangeNotifications by calling this method
    on the handler object. Convert + enqueue; never raise (asyncua
    swallows the exception but loses the sample).

    `status_change_notification` and `event_notification` are present
    as no-op-ish methods because asyncua probes for them via hasattr()
    and logs an ERROR-level message on every status-change event if
    they're missing (open62541-based servers send these regularly as
    part of secure-channel renewal). The event_notification stub
    exists in case a future config subscribes to UA Events (currently
    we only subscribe to DataChanges, so it never fires); the
    status_change_notification handler logs the StatusCode so operators
    can correlate session/channel-level events with sample disruptions.
    """

    def __init__(self, ctx: _SourceContext) -> None:
        self.ctx = ctx

    def datachange_notification(self, node, val, data) -> None:  # noqa: D401
        try:
            node_id = node.nodeid.to_string()
            tag_id = self.ctx.tag_by_node.get(node_id)
            if tag_id is None:
                # The subscription matches what we requested, so this
                # is rare. It can happen if the server sends a
                # canonicalized form of the NodeId different from what
                # the user typed in opc_tag_mappings.
                log.debug(
                    "[%s] sample for unmapped node %r — dropping",
                    self.ctx.source_name, node_id,
                )
                return

            mv = data.monitored_item.Value
            t = mv.SourceTimestamp or mv.ServerTimestamp or datetime.now(timezone.utc)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            st, reason = _ua_status_to_st(mv.StatusCode)
            value_double, value_text = _coerce_value(val)

            sample = Sample(
                tag_id=tag_id,
                device_id=self.ctx.device_id,
                register_block_id=None,
                time=t,
                value_double=value_double,
                value_text=value_text,
                st=st,
                st_reason=reason,
                source="opc_ua",
            )
            self.ctx.buffer.append(sample)
            # Watchdog timestamp — monotonic, used by the per-source
            # watchdog to detect silent stalls. Update AFTER successful
            # enqueue so a malformed sample doesn't pretend liveness.
            self.ctx.last_sample_ts = asyncio.get_event_loop().time()
        except Exception:
            log.exception(
                "[%s] datachange_notification failed",
                self.ctx.source_name,
            )

    def status_change_notification(self, status) -> None:  # noqa: D401
        """Server-side subscription health change. Logged for forensics.

        Common StatusCodes here include GoodSubscriptionTransferred (the
        server moved our subscription, harmless), BadTimeout (channel
        died), BadNoSubscription (server lost our subscription —
        critical). We don't take action here; if the channel really is
        gone, the publish loop stops too, and the watchdog will fire a
        reconnect within WATCHDOG_PUBLISH_MULTIPLIER × publishing_interval.
        """
        try:
            log.info(
                "[%s] subscription status change: %s",
                self.ctx.source_name, status,
            )
        except Exception:
            pass  # logging must never crash the handler

    def event_notification(self, event) -> None:  # noqa: D401
        """No-op. We don't subscribe to UA Events in this pipeline; the
        method exists only so asyncua's hasattr() probe succeeds and
        we don't get a spurious error log."""
        pass


# ── Sample buffer (single-threaded — no lock needed in asyncio) ─────


class _SampleBuffer:
    """In-memory accumulator. The asyncio event loop is single-threaded
    so append+drain don't need a lock; the flusher coro just yields
    between operations."""

    def __init__(self) -> None:
        self._samples: list[Sample] = []

    def append(self, sample: Sample) -> None:
        self._samples.append(sample)

    def drain(self) -> list[Sample]:
        if not self._samples:
            return []
        batch = self._samples
        self._samples = []
        return batch

    def __len__(self) -> int:
        return len(self._samples)


# ── Config loader ───────────────────────────────────────────────────


def load_sources_from_db() -> list[dict]:
    """One-shot DB load: enabled opc_sources + their tag mappings.
    Returns a list of fully-resolved source descriptors. Sources
    without any mappings are skipped with a warning — connecting and
    subscribing to nothing wastes a TCP session."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, name, endpoint, security_policy, username, password,
                   publishing_interval_ms, reconnect_min_sec, reconnect_max_sec,
                   device_id
            FROM opc_sources
            WHERE is_enabled = TRUE
            ORDER BY id
        """)).mappings().all()

        result: list[dict] = []
        for s in rows:
            mappings = conn.execute(text("""
                SELECT node_id, tag_id
                FROM opc_tag_mappings
                WHERE opc_source_id = :id
            """), {"id": s["id"]}).mappings().all()

            tag_by_node = {m["node_id"]: m["tag_id"] for m in mappings}
            if not tag_by_node:
                log.warning(
                    "[%s] no tag mappings configured; source skipped. "
                    "Add mappings via POST /api/opc-sources/%d/mappings.",
                    s["name"], s["id"],
                )
                continue

            result.append({
                **dict(s),
                "tag_by_node": tag_by_node,
            })
        return result


# ── Config fingerprint (Phase OPC-web.2.1) ──────────────────────────


@dataclass(frozen=True)
class _SourceFingerprint:
    """Per-source signature used to detect config changes between
    reloader ticks. A change in ANY field triggers a worker restart.

    `updated_at` covers the source row itself (PATCH /api/opc-sources/{id}
    bumps it). `last_mapping_change` + `mapping_count` cover the
    mappings: a new mapping bumps the max(created_at); a deletion
    drops the count. The API ALSO bumps source.updated_at on mapping
    add/delete (Phase OPC-web.2.1 patches), so in practice the
    updated_at field alone catches every change — but tracking the
    mappings independently is cheap and bullet-proofs against any
    future API path that forgets the bump.
    """
    is_enabled: bool
    updated_at: datetime
    last_mapping_change: datetime | None
    mapping_count: int


def load_fingerprints_from_db() -> dict[int, _SourceFingerprint]:
    """One query that returns the current fingerprint for EVERY source
    (enabled and disabled). The reloader compares this against the
    previous snapshot to figure out which workers need to be touched.

    We include disabled sources because the transition
    enabled=TRUE → enabled=FALSE is itself a change that should stop
    a running worker. The reloader's comparison treats disabled rows
    as "should not be running" regardless of other fields.
    """
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT s.id,
                   s.is_enabled,
                   s.updated_at,
                   MAX(m.created_at) AS last_mapping_change,
                   COUNT(m.id) AS mapping_count
            FROM opc_sources s
            LEFT JOIN opc_tag_mappings m
                ON m.opc_source_id = s.id
            GROUP BY s.id, s.is_enabled, s.updated_at
        """)).mappings().all()
        return {
            r["id"]: _SourceFingerprint(
                is_enabled=bool(r["is_enabled"]),
                updated_at=r["updated_at"],
                last_mapping_change=r["last_mapping_change"],
                mapping_count=int(r["mapping_count"]),
            )
            for r in rows
        }


def load_one_source_from_db(source_id: int) -> dict | None:
    """Re-fetch a single source's full descriptor for a (re)spawn.
    Returns None if the source has been deleted or is disabled or
    has no mappings — the reloader treats all three the same way:
    "don't start a worker for this id". Mirrors the filtering in
    load_sources_from_db() so an added source goes through the same
    eligibility check that startup does."""
    with engine.connect() as conn:
        s = conn.execute(text("""
            SELECT id, name, endpoint, security_policy, username, password,
                   publishing_interval_ms, reconnect_min_sec, reconnect_max_sec,
                   device_id, is_enabled
            FROM opc_sources
            WHERE id = :id
        """), {"id": source_id}).mappings().first()
        if s is None or not s["is_enabled"]:
            return None

        mappings = conn.execute(text("""
            SELECT node_id, tag_id
            FROM opc_tag_mappings
            WHERE opc_source_id = :id
        """), {"id": source_id}).mappings().all()
        tag_by_node = {m["node_id"]: m["tag_id"] for m in mappings}
        if not tag_by_node:
            return None

        return {**dict(s), "tag_by_node": tag_by_node}


# ── Per-source worker ───────────────────────────────────────────────


async def opc_source_worker(
    source: dict,
    buffer: _SampleBuffer,
    global_stop_event: asyncio.Event,
    restart_event: asyncio.Event,
) -> None:
    """Connect to one OPC UA source, subscribe to its nodes, hold the
    session open until either the global stop_event fires or this
    source's per-worker `restart_event` fires. Reconnect on any
    failure with exponential backoff.

    Phase OPC-web.2.1: `restart_event` lets the config_reloader tear
    this specific worker down without disturbing the others. When
    set, the worker exits cleanly; the reloader then either drops
    the slot (source disabled/deleted) or starts a fresh task with
    a new event (source mutated)."""
    name = source["name"]
    backoff = float(source["reconnect_min_sec"])
    backoff_max = float(source["reconnect_max_sec"])

    ctx = _SourceContext(
        source_id=source["id"],
        source_name=name,
        device_id=source["device_id"],
        tag_by_node=source["tag_by_node"],
        buffer=buffer,
    )

    while not global_stop_event.is_set() and not restart_event.is_set():
        try:
            await _connect_and_subscribe(
                source, ctx, global_stop_event, restart_event,
            )
            # Returned cleanly — connection was closed by one of the events.
            backoff = float(source["reconnect_min_sec"])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(
                "[%s] connection error: %s: %s — reconnecting in %.1fs",
                name, type(e).__name__, e, backoff,
            )

        if global_stop_event.is_set() or restart_event.is_set():
            break

        # Sleep interruptibly so shutdown / hot-reload doesn't wait
        # the full backoff. Two events to watch for, so we race them.
        try:
            await asyncio.wait_for(
                _wait_for_any([global_stop_event, restart_event]),
                timeout=backoff,
            )
            break
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2.0, backoff_max)

    log.info("[%s] worker exiting", name)


async def _wait_for_any(events: list[asyncio.Event]) -> None:
    """Block until any one of the given events is set. Used by the
    source worker to wake up on EITHER the global shutdown signal
    OR its own per-source restart trigger."""
    waiters = [asyncio.create_task(e.wait()) for e in events]
    try:
        done, pending = await asyncio.wait(
            waiters, return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        for w in waiters:
            if not w.done():
                w.cancel()


async def _connect_and_subscribe(
    source: dict,
    ctx: _SourceContext,
    global_stop_event: asyncio.Event,
    restart_event: asyncio.Event,
) -> None:
    """One connect-subscribe-hold cycle. Returns when either event
    fires (or when the connection drops; caller reconnects)."""
    endpoint = source["endpoint"]
    log.info("[%s] connecting to %s", ctx.source_name, endpoint)

    client = Client(endpoint)
    client.session_timeout = UA_SESSION_TIMEOUT_MS
    client.secure_channel_timeout = UA_CHANNEL_TIMEOUT_MS

    if source["username"]:
        client.set_user(source["username"])
        if source["password"]:
            client.set_password(source["password"])

    sec_policy = source["security_policy"]
    if sec_policy and sec_policy != "None":
        try:
            await client.set_security_string(f"{sec_policy},SignAndEncrypt")
        except Exception as e:
            log.warning(
                "[%s] security policy %r failed (%s); falling back to None",
                ctx.source_name, sec_policy, e,
            )

    async with client:
        log.info("[%s] connected", ctx.source_name)

        handler = _SubHandler(ctx)
        sub = await client.create_subscription(
            source["publishing_interval_ms"], handler,
        )

        subscribed = 0
        for node_id in ctx.tag_by_node:
            try:
                node = client.get_node(node_id)
                await sub.subscribe_data_change(node)
                subscribed += 1
            except Exception as e:
                log.warning(
                    "[%s] failed to subscribe to %r: %s",
                    ctx.source_name, node_id, e,
                )

        log.info(
            "[%s] subscription active (%d/%d nodes)",
            ctx.source_name, subscribed, len(ctx.tag_by_node),
        )

        # Seed the watchdog timestamp — if no samples arrive, the
        # watchdog deadline is measured from now, not from 1970.
        ctx.last_sample_ts = asyncio.get_event_loop().time()

        # Heartbeat watchdog: spawned as a child task that flags
        # `stalled_flag.set()` if no samples land within the threshold.
        # We deliberately do NOT have it set restart_event — restart
        # means "drop this source", but a stall means "reconnect this
        # source". The outer loop reconnects on exceptions, so the
        # cleanest path is to raise a stall exception that the outer
        # loop's `except Exception` arm handles with normal backoff.
        stalled_flag: asyncio.Event = asyncio.Event()
        watchdog_task = asyncio.create_task(
            _heartbeat_watchdog(ctx, source, stalled_flag),
            name=f"watchdog-{ctx.source_name}",
        )

        try:
            # Hold until any of three things happen:
            #   - global stop_event (process shutdown)   → propagate up
            #   - restart_event (reloader, source delete) → propagate up
            #   - stalled_flag (watchdog tripped)         → raise StalledError
            # The stalled case routes through the outer loop's exception
            # arm, which reconnects after backoff. The other two route
            # through the clean-exit arm, which respects the request.
            await _wait_for_any(
                [global_stop_event, restart_event, stalled_flag]
            )
            if stalled_flag.is_set() and not (
                global_stop_event.is_set() or restart_event.is_set()
            ):
                raise _WatchdogStall(
                    f"no samples for >{_format_stall_threshold(source)}s"
                )
        finally:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except (asyncio.CancelledError, Exception):
                pass

        reason = (
            "stop event" if global_stop_event.is_set() else "restart event"
        )
        log.info(
            "[%s] %s received; deleting subscription",
            ctx.source_name, reason,
        )
        try:
            await sub.delete()
        except Exception:
            log.exception("[%s] subscription delete failed", ctx.source_name)


class _WatchdogStall(Exception):
    """Raised when the heartbeat watchdog detects a silent stall. The
    outer reconnect loop catches it the same as any other connection
    failure, so the source reconnects with exponential backoff."""


def _format_stall_threshold(source: dict) -> str:
    """Display-only helper for the watchdog log message."""
    pub_ms = float(source["publishing_interval_ms"])
    threshold = max(
        WATCHDOG_MIN_SEC,
        WATCHDOG_PUBLISH_MULTIPLIER * (pub_ms / 1000.0),
    )
    return f"{threshold:.0f}"


async def _heartbeat_watchdog(
    ctx: _SourceContext,
    source: dict,
    stalled_flag: asyncio.Event,
) -> None:
    """Per-source silent-stall detector.

    Computes a stall threshold from the source's publishing_interval_ms
    and the global multiplier/minimum, then wakes every N seconds and
    compares (now - ctx.last_sample_ts) against the threshold. On
    stall, sets `stalled_flag`; the caller (inside _connect_and_subscribe)
    sees the flag fire, raises _WatchdogStall, and the outer worker
    loop reconnects with normal backoff.

    Necessary because asyncua's secure-channel renewal can silently
    fail (the renewal task raises, the publish loop keeps running for
    a while, samples eventually stop, but no exception surfaces in
    user code). Also catches dead sockets the OS hasn't reaped yet
    and server-side hangs that don't drop the TCP connection.
    """
    pub_ms = float(source["publishing_interval_ms"])
    stall_threshold = max(
        WATCHDOG_MIN_SEC,
        WATCHDOG_PUBLISH_MULTIPLIER * (pub_ms / 1000.0),
    )
    # Wake roughly 4× per stall window so we detect stalls within ~25%
    # of the threshold. Floor at 1s to avoid a busy loop on fast
    # publishing intervals; cap at 30s to bound shutdown latency.
    poll_interval = max(1.0, min(stall_threshold / 4.0, 30.0))

    log.debug(
        "[%s] watchdog: stall_threshold=%.1fs poll_interval=%.1fs",
        ctx.source_name, stall_threshold, poll_interval,
    )

    loop = asyncio.get_event_loop()
    while not stalled_flag.is_set():
        await asyncio.sleep(poll_interval)
        if ctx.last_sample_ts == 0.0:
            # Subscription is up but no samples yet — server hasn't
            # published its first value. Don't fire the watchdog here;
            # the seed timestamp set right after `subscription active`
            # means we only reach `last_sample_ts == 0.0` if something
            # very weird happened. Skip this tick.
            continue
        age = loop.time() - ctx.last_sample_ts
        if age > stall_threshold:
            log.warning(
                "[%s] watchdog: no samples for %.1fs (threshold %.1fs) — "
                "triggering reconnect",
                ctx.source_name, age, stall_threshold,
            )
            stalled_flag.set()
            return


# ── Flusher ─────────────────────────────────────────────────────────


async def flusher_loop(
    buffer: _SampleBuffer,
    writer: BufferedHistorianWriter,
    stop_event: asyncio.Event,
) -> None:
    """Drain the sample buffer every FLUSH_INTERVAL_SEC and write
    through the historian. On Postgres failure, the buffered writer
    spills to the local SQLite — this coro never sees the exception."""
    log.info(
        "flusher: started (interval=%.1fs)", FLUSH_INTERVAL_SEC,
    )
    samples_written_total = 0
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=FLUSH_INTERVAL_SEC)
            # Loop ends — fall through to final drain below.
            break
        except asyncio.TimeoutError:
            pass

        batch = buffer.drain()
        if not batch:
            continue
        try:
            n = writer.write_samples(batch)
            samples_written_total += n
            if n != len(batch):
                # write_samples returning fewer than submitted means
                # the buffered writer spilled to SQLite — log at info
                # so the user sees Postgres trouble surface here.
                log.info(
                    "flusher: wrote %d/%d samples (rest buffered locally)",
                    n, len(batch),
                )
        except Exception:
            log.exception("flusher: write_samples raised unexpectedly")

    # Final drain on shutdown so in-flight samples aren't lost.
    final = buffer.drain()
    if final:
        try:
            n = writer.write_samples(final)
            samples_written_total += n
            log.info("flusher: final drain wrote %d samples", n)
        except Exception:
            log.exception("flusher: final drain failed")

    log.info(
        "flusher: exiting (lifetime samples written: %d)",
        samples_written_total,
    )


# ── Per-source task table + reloader (Phase OPC-web.2.1) ────────────


@dataclass
class _ManagedSource:
    """One row of the supervisor's task table: a running source
    worker, its per-worker restart_event, and the fingerprint we
    last saw for it. The reloader uses this to decide what to do
    next tick."""
    task: asyncio.Task
    restart_event: asyncio.Event
    fingerprint: _SourceFingerprint


async def _spawn_source_worker(
    source: dict,
    sample_buffer: _SampleBuffer,
    global_stop_event: asyncio.Event,
    fingerprint: _SourceFingerprint,
    managed: dict[int, _ManagedSource],
) -> None:
    """Helper used by both initial spawn (in main) and the reloader.
    Creates a fresh restart_event for the new worker, fires off the
    task, records it in `managed`. Idempotent in the sense that the
    caller is expected to have already torn down any previous worker
    for this source_id."""
    restart_event = asyncio.Event()
    task = asyncio.create_task(
        opc_source_worker(
            source, sample_buffer, global_stop_event, restart_event,
        ),
        name=f"src-{source['name']}",
    )
    managed[source["id"]] = _ManagedSource(
        task=task,
        restart_event=restart_event,
        fingerprint=fingerprint,
    )


async def _stop_managed(
    source_id: int,
    managed: dict[int, _ManagedSource],
    reason: str,
) -> None:
    """Tell a running source worker to stop, then await its exit.
    Removes its entry from `managed`. Safe to call for an id that's
    not currently managed (no-op in that case)."""
    entry = managed.pop(source_id, None)
    if entry is None:
        return
    log.info("reloader: stopping source id=%d (%s)", source_id, reason)
    entry.restart_event.set()
    try:
        # Bounded wait — the worker should exit within a few seconds
        # once restart_event is set (it has to delete the subscription
        # and close the client). If something hangs we cancel; better
        # than leaking the task forever.
        await asyncio.wait_for(entry.task, timeout=10.0)
    except asyncio.TimeoutError:
        log.warning(
            "reloader: source id=%d did not exit within 10s; cancelling",
            source_id,
        )
        entry.task.cancel()
        try:
            await entry.task
        except (asyncio.CancelledError, Exception):
            pass
    except Exception:
        log.exception(
            "reloader: source id=%d exited with unexpected error", source_id,
        )


async def config_reloader_loop(
    sample_buffer: _SampleBuffer,
    global_stop_event: asyncio.Event,
    managed: dict[int, _ManagedSource],
) -> None:
    """Phase OPC-web.2.1 — every RELOAD_INTERVAL_SEC, query the DB
    for the current fingerprint of every opc_source. For each delta
    versus the previous tick:

      - source appeared       → spawn a new worker
      - source disappeared    → stop its worker (deleted or disabled)
      - source mutated        → stop + respawn worker with fresh config

    A "delete" in the SQL sense doesn't return a row, so deleted
    sources show up as "id in managed but not in fingerprints"; a
    flip to is_enabled=FALSE shows up as "is_enabled changed in the
    fingerprint", which the per-source step handles by stopping
    without respawning.
    """
    log.info(
        "config_reloader: started (interval=%.1fs)", RELOAD_INTERVAL_SEC,
    )

    while not global_stop_event.is_set():
        try:
            await asyncio.wait_for(
                global_stop_event.wait(), timeout=RELOAD_INTERVAL_SEC,
            )
            break  # stop fired during sleep
        except asyncio.TimeoutError:
            pass

        try:
            current = load_fingerprints_from_db()
        except Exception:
            log.exception(
                "config_reloader: DB query failed; retrying next tick"
            )
            continue

        # Mutated or removed: walk our currently-managed set.
        # Use list(...) because we mutate `managed` inside the loop.
        for source_id in list(managed.keys()):
            old_fp = managed[source_id].fingerprint
            new_fp = current.get(source_id)

            if new_fp is None:
                # Row is gone (DELETE) — stop the worker.
                await _stop_managed(source_id, managed, "deleted")
                continue

            if not new_fp.is_enabled:
                # Disabled — stop the worker, don't respawn.
                await _stop_managed(source_id, managed, "disabled")
                continue

            if new_fp == old_fp:
                continue  # no change

            # Mutated — stop, then respawn with fresh config.
            await _stop_managed(source_id, managed, "config changed")
            new_source = load_one_source_from_db(source_id)
            if new_source is None:
                # Race: source got deleted / disabled / lost its
                # mappings between our fingerprint read and the
                # detail read. That's fine — leave it stopped.
                log.info(
                    "reloader: source id=%d no longer eligible after "
                    "fingerprint change (likely lost its mappings); "
                    "leaving stopped",
                    source_id,
                )
                continue
            await _spawn_source_worker(
                new_source, sample_buffer, global_stop_event, new_fp, managed,
            )
            log.info(
                "reloader: restarted source %r (id=%d) with fresh config",
                new_source["name"], source_id,
            )

        # Added: ids in `current` that we're not managing yet.
        for source_id, new_fp in current.items():
            if source_id in managed:
                continue
            if not new_fp.is_enabled:
                continue  # disabled-from-birth → nothing to do
            new_source = load_one_source_from_db(source_id)
            if new_source is None:
                # Eligible by enabled-flag but no mappings yet — skip
                # silently. When the user adds the first mapping, the
                # fingerprint will change (mapping_count 0→1) and we
                # come back here next tick.
                continue
            await _spawn_source_worker(
                new_source, sample_buffer, global_stop_event, new_fp, managed,
            )
            log.info(
                "reloader: started source %r (id=%d)",
                new_source["name"], source_id,
            )

    log.info("config_reloader: exiting")


# ── Entry point ─────────────────────────────────────────────────────


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # asyncua's INFO level dumps every PublishCallback's full payload
    # which is overwhelming — one log line per ~3 KB of struct dump.
    # Defaulting it to WARNING leaves our own __main__ logger informative
    # without drowning out the actual events. Override via env if you're
    # debugging an asyncua-internal issue.
    logging.getLogger("asyncua").setLevel(
        os.environ.get("ASYNCUA_LOG_LEVEL", "WARNING")
    )

    # SF buffer path — separate from modbus's so the two workers don't
    # contend on the same SQLite file. Defaults to /data/sf_opc.db.
    buffer_path = Path(os.environ.get("SF_OPC_BUFFER_PATH", "/data/sf_opc.db"))
    buffer_path.parent.mkdir(parents=True, exist_ok=True)

    log.info("opc_supervisor: starting (sf_buffer=%s)", buffer_path)

    sf_buffer = LocalBuffer(buffer_path)
    backlog = sf_buffer.count()
    if backlog:
        log.warning(
            "opc_supervisor: %d sample(s) in SF buffer from previous run "
            "(replay_loop not yet wired in OPC-web.2 — they will not be "
            "drained automatically)",
            backlog,
        )

    historian = HistorianWriter(engine)
    writer = BufferedHistorianWriter(historian, sf_buffer)

    # Load initial fingerprints AND eligible sources in one pass. The
    # reloader then takes over for subsequent ticks. Doing them
    # together (rather than loading sources, then loading fingerprints
    # separately) guarantees consistency between "what we spawned" and
    # "what the reloader thinks it spawned" — without it, a config
    # change landing between the two loads could cause the reloader
    # to immediately decide the freshly-spawned worker is stale.
    fingerprints = load_fingerprints_from_db()
    sources = load_sources_from_db()
    log.info(
        "opc_supervisor: loaded %d enabled source(s) with mappings",
        len(sources),
    )
    if not sources:
        log.warning(
            "opc_supervisor: nothing to subscribe to yet — worker will "
            "idle until config_reloader picks up new sources. Create "
            "sources + mappings via /api/opc-sources; changes propagate "
            "within ~%.0fs.",
            RELOAD_INTERVAL_SEC,
        )

    sample_buffer = _SampleBuffer()
    global_stop_event = asyncio.Event()
    managed: dict[int, _ManagedSource] = {}

    # ── Signal wiring ───────────────────────────────────────────────
    loop = asyncio.get_running_loop()

    def _shutdown(signame: str) -> None:
        if not global_stop_event.is_set():
            log.info(
                "opc_supervisor: %s received; draining and exiting cleanly",
                signame,
            )
            global_stop_event.set()

    for signame in ("SIGINT", "SIGTERM"):
        try:
            loop.add_signal_handler(
                getattr(signal, signame),
                lambda s=signame: _shutdown(s),
            )
        except NotImplementedError:
            # Windows asyncio — only used in dev, not in the container.
            pass

    # ── Spawn flusher + initial source workers + reloader ───────────
    flusher_task = asyncio.create_task(
        flusher_loop(sample_buffer, writer, global_stop_event),
        name="flusher",
    )

    for src in sources:
        await _spawn_source_worker(
            src, sample_buffer, global_stop_event,
            fingerprints[src["id"]], managed,
        )

    reloader_task = asyncio.create_task(
        config_reloader_loop(sample_buffer, global_stop_event, managed),
        name="config_reloader",
    )

    log.info(
        "opc_supervisor: started flusher + %d source worker(s) + reloader "
        "(poll interval %.0fs)",
        len(managed), RELOAD_INTERVAL_SEC,
    )

    # Wait for shutdown signal.
    await global_stop_event.wait()

    # Let workers observe the stop_event and unwind their subscriptions
    # gracefully. The reloader stops on its own; source workers each
    # have their own task. asyncio.gather collects them in parallel.
    log.info("opc_supervisor: waiting for tasks to drain")
    active_source_tasks = [m.task for m in managed.values()]
    all_tasks = [flusher_task, reloader_task, *active_source_tasks]
    results = await asyncio.gather(*all_tasks, return_exceptions=True)
    for t, r in zip(all_tasks, results):
        if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
            log.warning("opc_supervisor: task %s exited with %r", t.get_name(), r)

    log.info("opc_supervisor: clean exit")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # SIGINT before the signal handler is wired (very narrow window).
        log.info("opc_supervisor: interrupted before signal handler; exiting")
        sys.exit(0)
