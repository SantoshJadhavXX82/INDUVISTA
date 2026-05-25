"""OPC UA polling worker (supervisor) — Phase OPC-web.2.

Loads enabled rows from `opc_sources` + `opc_tag_mappings`, opens an
asyncua.Client per source, subscribes to the configured nodes, and
writes samples through a BufferedHistorianWriter that falls back to a
local SQLite buffer on Postgres failure.

PROCESS MODEL
=============

Standalone process, same pattern as `app.workers.modbus_supervisor`.
One container, one asyncio event loop, N concurrent source-worker
coroutines + one flusher coroutine.

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
  └─────────────────────────────────────────────────────────────┘

Each source coroutine reconnects forever with exponential backoff
(reconnect_min_sec → reconnect_max_sec from the opc_sources row).

CONFIG HOT-RELOAD
=================

Phase OPC-web.2 — NOT IMPLEMENTED. The worker loads opc_sources +
opc_tag_mappings once at startup. To pick up config changes:

    docker compose restart opc_worker

This keeps the first delivery focused. Phase OPC-web.2.1 adds a
fingerprint-based hot-reload similar to modbus_supervisor.

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

# Session + secure-channel timeouts handed to asyncua. Generous
# enough to ride out a brief network hiccup, short enough that a
# zombie session disappears in time for the reconnect loop to step in.
UA_SESSION_TIMEOUT_MS = 30_000
UA_CHANNEL_TIMEOUT_MS = 30_000


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
    map, and a reference to the shared buffer."""
    source_id: int
    source_name: str
    device_id: int
    tag_by_node: dict[str, int]
    buffer: "_SampleBuffer"


class _SubHandler:
    """asyncua delivers DataChangeNotifications by calling this method
    on the handler object. Convert + enqueue; never raise (asyncua
    swallows the exception but loses the sample)."""

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
        except Exception:
            log.exception(
                "[%s] datachange_notification failed",
                self.ctx.source_name,
            )


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


# ── Per-source worker ───────────────────────────────────────────────


async def opc_source_worker(
    source: dict,
    buffer: _SampleBuffer,
    stop_event: asyncio.Event,
) -> None:
    """Connect to one OPC UA source, subscribe to its nodes, hold the
    session open until stop_event. Reconnect on any failure with
    exponential backoff."""
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

    while not stop_event.is_set():
        try:
            await _connect_and_subscribe(source, ctx, stop_event)
            # Returned cleanly — connection was closed by stop_event.
            backoff = float(source["reconnect_min_sec"])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(
                "[%s] connection error: %s: %s — reconnecting in %.1fs",
                name, type(e).__name__, e, backoff,
            )

        if stop_event.is_set():
            break

        # Sleep interruptibly so shutdown doesn't wait the full backoff.
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            break
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2.0, backoff_max)

    log.info("[%s] worker exiting", name)


async def _connect_and_subscribe(
    source: dict,
    ctx: _SourceContext,
    stop_event: asyncio.Event,
) -> None:
    """One connect-subscribe-hold cycle. Returns when stop_event fires
    (or when the connection drops; caller reconnects)."""
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

        # Hold until stop_event.
        await stop_event.wait()
        log.info("[%s] stop event received; deleting subscription", ctx.source_name)
        try:
            await sub.delete()
        except Exception:
            log.exception("[%s] subscription delete failed", ctx.source_name)


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

    sources = load_sources_from_db()
    log.info(
        "opc_supervisor: loaded %d enabled source(s) with mappings",
        len(sources),
    )
    if not sources:
        log.warning(
            "opc_supervisor: nothing to subscribe to yet — worker will "
            "idle. Create sources + mappings via /api/opc-sources, then "
            "`docker compose restart opc_worker` to pick them up."
        )

    sample_buffer = _SampleBuffer()
    stop_event = asyncio.Event()

    # ── Signal wiring ───────────────────────────────────────────────
    loop = asyncio.get_running_loop()

    def _shutdown(signame: str) -> None:
        if not stop_event.is_set():
            log.info(
                "opc_supervisor: %s received; draining and exiting cleanly",
                signame,
            )
            stop_event.set()

    for signame in ("SIGINT", "SIGTERM"):
        try:
            loop.add_signal_handler(
                getattr(signal, signame),
                lambda s=signame: _shutdown(s),
            )
        except NotImplementedError:
            # Windows asyncio — only used in dev, not in the container.
            pass

    # ── Spawn tasks ─────────────────────────────────────────────────
    tasks: list[asyncio.Task] = [
        asyncio.create_task(
            flusher_loop(sample_buffer, writer, stop_event),
            name="flusher",
        ),
    ]
    for src in sources:
        tasks.append(asyncio.create_task(
            opc_source_worker(src, sample_buffer, stop_event),
            name=f"src-{src['name']}",
        ))

    log.info(
        "opc_supervisor: started flusher + %d source worker(s)",
        len(sources),
    )

    # Wait for shutdown signal.
    await stop_event.wait()

    # Let workers observe the stop_event and unwind their subscriptions
    # gracefully. asyncio.gather collects them in parallel.
    log.info("opc_supervisor: waiting for tasks to drain")
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for t, r in zip(tasks, results):
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
