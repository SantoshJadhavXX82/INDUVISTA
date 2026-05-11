"""Modbus polling worker (supervisor).

Loads enabled devices/blocks/tags from the database, opens a long-lived
Modbus TCP connection per device, and polls every register_block at the
device's scan_interval_ms. Decodes each tag, computes ST per the CV/ST
status model, and submits a Sample batch through a BufferedHistorianWriter
that falls back to a local SQLite buffer on Postgres failure.

Phase 1 contract:
  * One asyncio task per device.
  * Sequential block polling within a device (Modbus TCP allows parallel
    reads, but Phase 1 prioritizes deterministic timing).
  * Single TCP connection per device, reconnect on any read failure.
  * Failures mark all tags in the affected block ST=0 (INVALID, COMM_TIMEOUT).

Phase 2 additions:
  * Stale detection — periodic UPDATE that downgrades aged VALID rows in
    latest_tag_values to SUSPECT/STALE per device.stale_after_sec.
  * Store-and-forward — BufferedHistorianWriter wraps the direct writer and
    spills to /data/sf_buffer.db on any Postgres error. A replay loop in
    this supervisor drains the buffer when Postgres recovers.

Phase 3.5 additions:
  * Config hot-reload — a worker_manager task polls the DB every 10s and
    hashes the polling-relevant config. On change, current DeviceWorker
    tasks are gracefully stopped and replaced with fresh ones built from
    the new config. The historian, buffer, stale-detection, and replay
    loops are unaffected because they hold no per-device state.
  * Cosmetic config edits (description, engineering_unit, min/max) don't
    trigger rebuilds — only structural changes (address, FC, data_type,
    byte_order, scale/offset, device endpoint, scan_interval) do.

Out of scope (Phase 4+): redundancy/duty failover, parallel block reads,
per-block scan intervals, reconnect backoff.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from pymodbus.client import AsyncModbusTcpClient
from sqlalchemy import text

from app.db import engine
from app.historian import BufferedHistorianWriter, HistorianWriter, Sample
from app.local_buffer import LocalBuffer
from app.modbus.decoder import decode_value
from app.modbus.status import ST_COMM_TIMEOUT, ST_DECODE_FAIL, ST_READ_OK

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("worker")


def load_polling_config() -> list[dict]:
    """Read enabled devices/blocks/tags from the database.

    Returns one dict per device:
        {
            "device": {...device row...},
            "blocks": [...register_block rows...],
            "tags_by_block": {block_id: [...tag rows...]},
        }
    """
    with engine.connect() as conn:
        devices = [
            dict(r) for r in conn.execute(text("""
                SELECT id, name, host, port, unit_id, scan_interval_ms
                FROM devices
                WHERE enabled = TRUE
                ORDER BY id
            """)).mappings()
        ]

        result = []
        for d in devices:
            blocks = [
                dict(r) for r in conn.execute(text("""
                    SELECT id, name, function_code, start_address, count,
                           scan_interval_ms
                    FROM register_blocks
                    WHERE device_id = :did AND enabled = TRUE
                    ORDER BY function_code, start_address
                """), {"did": d["id"]}).mappings()
            ]

            tags_by_block: dict[int, list[dict]] = {b["id"]: [] for b in blocks}
            for t in conn.execute(text("""
                SELECT id, device_id, register_block_id, name,
                       data_type, byte_order, function_code,
                       address, register_count, scale, "offset"
                FROM tags
                WHERE device_id = :did
                  AND enabled = TRUE
                  AND register_block_id IS NOT NULL
                ORDER BY register_block_id, address
            """), {"did": d["id"]}).mappings():
                tag = dict(t)
                tags_by_block.setdefault(tag["register_block_id"], []).append(tag)

            result.append({
                "device": d,
                "blocks": blocks,
                "tags_by_block": tags_by_block,
            })

        return result


def _run_stale_check(eng) -> int:
    """One sweep of latest_tag_values: mark VALID/VALID_EXTENDED rows that
    are older than their device's stale_after_sec as SUSPECT/STALE.

    Returns the row count updated. The schema's generated st_class column
    automatically recomputes (128 → 64 flips it from VALID to SUSPECT).
    Rows already in INVALID (ST < 64) are left alone — no point downgrading
    something already known-bad.
    """
    with eng.begin() as conn:
        result = conn.execute(text("""
            UPDATE latest_tag_values lv
            SET st = 64, st_reason = 'STALE'
            FROM tags t, devices d
            WHERE lv.tag_id = t.id
              AND t.device_id = d.id
              AND lv.st >= 128
              AND lv.time < now() - (d.stale_after_sec || ' seconds')::interval
        """))
        return result.rowcount


async def stale_detection_loop(stop_event: asyncio.Event, check_interval: float = 5.0):
    """Background task: periodically downgrade aged VALID rows to SUSPECT/STALE.

    Runs alongside the DeviceWorker tasks. The SUSPECT/STALE tier (64) lives
    in the SUSPECT band of CV/ST — values are probably still right but we
    haven't confirmed them in stale_after_sec seconds. Dashboards/reports
    can choose to keep showing them, grey them out, or hide them entirely.

    The DB UPDATE is sync (psycopg2); we run it in a thread so the supervisor
    event loop stays responsive.
    """
    stale_log = logging.getLogger("worker.stale")
    stale_log.info("Stale detection started (check every %.1fs)", check_interval)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=check_interval)
            break  # stop_event was set; exit cleanly
        except asyncio.TimeoutError:
            pass  # normal — time to run a check
        try:
            n = await asyncio.to_thread(_run_stale_check, engine)
            if n > 0:
                stale_log.info("Marked %d tag(s) STALE (ST 128/192 → 64)", n)
        except Exception:
            stale_log.exception("Stale check failed")
    stale_log.info("Stopped.")


async def replay_loop(
    buffer: LocalBuffer,
    direct: HistorianWriter,
    stop_event: asyncio.Event,
    check_interval: float = 5.0,
    batch_size: int = 500,
):
    """Drain the local SQLite buffer into Postgres when reachable.

    Phase 2 store-and-forward replay. The supervisor's BufferedHistorianWriter
    spills failed direct writes to the local buffer; this task drains them.

    Mechanics:
      * Wake every `check_interval` seconds (or sooner on shutdown).
      * If buffer is empty, idle.
      * Otherwise probe Postgres via direct.is_healthy(); skip if down.
      * Read a batch in oldest-first order, mark source='store_forward',
        write via direct.write_history_only (no latest_tag_values touch),
        then delete the drained rows from the buffer.
      * On batch write failure, leave the rows in place; we'll retry next tick.
    """
    rlog = logging.getLogger("worker.replay")
    rlog.info(
        "Replay started (check every %.1fs, batch=%d)",
        check_interval, batch_size,
    )
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=check_interval)
            break
        except asyncio.TimeoutError:
            pass

        try:
            count = await asyncio.to_thread(buffer.count)
            if count == 0:
                continue

            healthy = await asyncio.to_thread(direct.is_healthy)
            if not healthy:
                rlog.info(
                    "Buffer has %d sample(s); Postgres still unreachable, will retry",
                    count,
                )
                continue

            batch = await asyncio.to_thread(buffer.peek, batch_size)
            if not batch:
                continue

            # Mark every replayed sample as store_forward — preserves the
            # original ST/value but lets downstream consumers distinguish
            # backfilled from live readings.
            for s in batch:
                s.source = "store_forward"

            try:
                await asyncio.to_thread(direct.write_history_only, batch)
            except Exception as e:
                rlog.warning(
                    "Replay batch failed (%s); leaving %d sample(s) in buffer",
                    e, len(batch),
                )
                continue

            n = await asyncio.to_thread(buffer.delete, batch)
            remaining = count - n
            rlog.info(
                "Replayed %d sample(s); %d remaining in buffer",
                n, remaining,
            )
        except Exception:
            rlog.exception("Replay tick failed")
    rlog.info("Stopped.")


# ----------------------------------------------------------------------------
# Phase 5b — buffer status reporting
#
# Periodically writes buffer.count() and buffer.oldest_time() to the singleton
# worker_buffer_status row in Postgres. The API reads this for the
# /api/diagnostics/buffer-health endpoint. Best-effort — if Postgres is down,
# we silently skip the update (which is the exact same time the buffer is
# growing, so the API will simply show stale stats during the outage).
# ----------------------------------------------------------------------------

async def buffer_status_loop(
    buffer: LocalBuffer,
    stop_event: asyncio.Event,
    interval: float = 10.0,
):
    blog = logging.getLogger("worker.buffer_status")
    blog.info("Buffer status reporting started (every %.1fs)", interval)
    while not stop_event.is_set():
        try:
            backlog = await asyncio.to_thread(buffer.count)
            oldest = await asyncio.to_thread(buffer.oldest_time)
            await asyncio.to_thread(_write_buffer_status, backlog, oldest)
        except Exception:
            blog.exception("Buffer status tick failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            continue
    blog.info("Stopped.")


def _write_buffer_status(backlog: int, oldest) -> None:
    """Best-effort UPSERT into the worker_buffer_status singleton."""
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO worker_buffer_status (id, backlog, oldest_sample_at, updated_at)
                VALUES (1, :backlog, :oldest, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    backlog = EXCLUDED.backlog,
                    oldest_sample_at = EXCLUDED.oldest_sample_at,
                    updated_at = NOW()
            """), {"backlog": backlog, "oldest": oldest})
    except Exception:
        # Postgres unreachable — exactly when the buffer is growing.
        # Status reporting is best-effort; never let it crash the loop.
        pass


class DeviceWorker:
    """One polling loop per device."""

    def __init__(self, device: dict, blocks: list[dict],
                 tags_by_block: dict[int, list[dict]],
                 historian: HistorianWriter):
        self.device = device
        self.blocks = blocks
        self.tags_by_block = tags_by_block
        self.historian = historian
        self.client: AsyncModbusTcpClient | None = None
        self._stop = False
        # Phase 5b: track for worker_device_status reporting.
        self._consecutive_failures = 0
        self.log = logging.getLogger(f"worker.{device['name']}")

    def stop(self):
        self._stop = True

    def _report_status_sync(self, total: int, good: int, connection_state: str) -> None:
        """Best-effort UPSERT of worker_device_status. Runs in a thread."""
        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO worker_device_status (
                        device_id, last_cycle_at,
                        last_cycle_samples_total, last_cycle_samples_good,
                        consecutive_failures, connection_state, updated_at
                    )
                    VALUES (
                        :device_id, NOW(),
                        :total, :good,
                        :failures, :state, NOW()
                    )
                    ON CONFLICT (device_id) DO UPDATE SET
                        last_cycle_at = NOW(),
                        last_cycle_samples_total = EXCLUDED.last_cycle_samples_total,
                        last_cycle_samples_good = EXCLUDED.last_cycle_samples_good,
                        consecutive_failures = EXCLUDED.consecutive_failures,
                        connection_state = EXCLUDED.connection_state,
                        updated_at = NOW()
                """), {
                    "device_id": self.device["id"],
                    "total": total,
                    "good": good,
                    "failures": self._consecutive_failures,
                    "state": connection_state,
                })
        except Exception:
            # Postgres might be down — that's exactly when buffering kicks in.
            # Status reporting is best-effort; never let it crash the worker.
            pass

    async def run(self):
        scan_interval = max(self.device["scan_interval_ms"] / 1000.0, 0.1)
        total_tags = sum(len(t) for t in self.tags_by_block.values())
        self.log.info(
            "Polling %s:%d unit=%d interval=%.2fs blocks=%d tags=%d",
            self.device["host"], self.device["port"], self.device["unit_id"],
            scan_interval, len(self.blocks), total_tags,
        )

        loop = asyncio.get_event_loop()
        cycle = 0
        while not self._stop:
            cycle_start = loop.time()
            cycle_ok = False
            samples_total = 0
            samples_good = 0
            try:
                await self._ensure_connected()
                samples: list[Sample] = []
                for block in self.blocks:
                    samples.extend(await self._poll_block(block))

                if samples:
                    n = await asyncio.to_thread(self.historian.write_samples, samples)
                    samples_total = len(samples)
                    samples_good = sum(1 for s in samples if s.st == ST_READ_OK)
                    cycle += 1
                    if cycle % 10 == 0 or cycle == 1:
                        self.log.info(
                            "Cycle %d: wrote %d samples (%d good / %d total)",
                            cycle, n, samples_good, samples_total,
                        )
                cycle_ok = True
            except ConnectionError as e:
                self.log.warning("%s — will retry next cycle", e)
            except Exception:
                self.log.exception("Poll cycle error")

            if cycle_ok:
                self._consecutive_failures = 0
                state = "connected"
            else:
                self._consecutive_failures += 1
                state = "reconnecting" if self.client is None else "disconnected"

            # Phase 5b: report cycle outcome to Postgres (best-effort).
            await asyncio.to_thread(
                self._report_status_sync,
                samples_total, samples_good, state,
            )

            elapsed = loop.time() - cycle_start
            await asyncio.sleep(max(0.0, scan_interval - elapsed))

        if self.client:
            self.client.close()
        self.log.info("Stopped.")

    async def _ensure_connected(self):
        if self.client is not None and self.client.connected:
            return
        self.log.info(
            "Connecting to %s:%d ...", self.device["host"], self.device["port"]
        )
        self.client = AsyncModbusTcpClient(
            host=self.device["host"], port=self.device["port"],
        )
        await self.client.connect()
        if not self.client.connected:
            self.client = None
            raise ConnectionError(
                f"Could not connect to {self.device['host']}:{self.device['port']}"
            )
        self.log.info("Connected.")

    async def _poll_block(self, block: dict) -> list[Sample]:
        fc = block["function_code"]
        start = block["start_address"]
        count = block["count"]
        unit_id = self.device["unit_id"]
        now = datetime.now(timezone.utc)

        try:
            if fc == 1:
                rr = await self.client.read_coils(
                    address=start, count=count, slave=unit_id)
            elif fc == 2:
                rr = await self.client.read_discrete_inputs(
                    address=start, count=count, slave=unit_id)
            elif fc == 3:
                rr = await self.client.read_holding_registers(
                    address=start, count=count, slave=unit_id)
            elif fc == 4:
                rr = await self.client.read_input_registers(
                    address=start, count=count, slave=unit_id)
            else:
                self.log.warning(
                    "Unknown function_code=%d on block %s — skipping",
                    fc, block["name"],
                )
                return []

            if rr.isError():
                self.log.warning("Block %s read error: %s", block["name"], rr)
                return self._failed_samples(block, now, ST_COMM_TIMEOUT, "COMM_TIMEOUT")

            # pymodbus returns bits padded to nearest byte; slice to count
            raw = rr.bits[:count] if fc in (1, 2) else rr.registers
            return self._decode_block(block, raw, now)

        except Exception as e:
            self.log.warning(
                "Block %s read exception: %s — forcing reconnect",
                block["name"], e,
            )
            try:
                if self.client:
                    self.client.close()
            except Exception:
                pass
            self.client = None
            return self._failed_samples(block, now, ST_COMM_TIMEOUT, "COMM_TIMEOUT")

    def _decode_block(self, block: dict, raw, now: datetime) -> list[Sample]:
        samples: list[Sample] = []
        for tag in self.tags_by_block.get(block["id"], []):
            rel = tag["address"] - block["start_address"]
            slice_len = tag["register_count"]
            tag_raw = raw[rel:rel + slice_len]

            try:
                value = decode_value(tag_raw, tag["data_type"], tag["byte_order"])
                # Apply engineering scale + offset (skip for bool)
                if tag["data_type"] != "bool":
                    value = float(value) * tag["scale"] + tag["offset"]

                vd = float(value) if isinstance(value, (int, float, bool)) else None
                samples.append(Sample(
                    tag_id=tag["id"],
                    device_id=tag["device_id"],
                    register_block_id=block["id"],
                    time=now,
                    value_double=vd,
                    value_text=None,
                    st=ST_READ_OK,
                    st_reason="READ_OK",
                ))
            except Exception as e:
                self.log.warning(
                    "Decode failed: %s @ addr=%d type=%s: %s",
                    tag["name"], tag["address"], tag["data_type"], e,
                )
                samples.append(Sample(
                    tag_id=tag["id"],
                    device_id=tag["device_id"],
                    register_block_id=block["id"],
                    time=now,
                    value_double=None,
                    value_text=None,
                    st=ST_DECODE_FAIL,
                    st_reason="DECODE_FAIL",
                ))
        return samples

    def _failed_samples(
        self, block: dict, now: datetime, st: int, reason: str,
    ) -> list[Sample]:
        return [
            Sample(
                tag_id=t["id"],
                device_id=t["device_id"],
                register_block_id=block["id"],
                time=now,
                value_double=None,
                value_text=None,
                st=st,
                st_reason=reason,
            )
            for t in self.tags_by_block.get(block["id"], [])
        ]


# ----------------------------------------------------------------------------
# Phase 3.5 — config hot reload
#
# The supervisor doesn't watch every column on every config table; it watches
# a fingerprint over the *polling-relevant* fields. Cosmetic edits — tag
# description, engineering_unit, min/max alarm bounds — don't trigger rebuilds.
# Structural edits — address, function_code, data_type, byte_order, scale,
# offset, register_count, device endpoint, scan_interval — do.
# ----------------------------------------------------------------------------

def _config_fingerprint(config: list[dict]) -> str:
    """Deterministic hash of polling-relevant fields across all devices."""
    relevant: list = []
    for c in config:
        d = c["device"]
        device_part = (
            d["id"], d["host"], d["port"], d["unit_id"],
            d["scan_interval_ms"],
        )
        blocks_part = tuple(
            (b["id"], b["function_code"], b["start_address"], b["count"])
            for b in sorted(c["blocks"], key=lambda b: b["id"])
        )
        tags_part = tuple(sorted(
            (t["id"], t["register_block_id"], t["address"], t["register_count"],
             t["data_type"], t["byte_order"],
             float(t["scale"]), float(t["offset"]))
            for tag_list in c["tags_by_block"].values()
            for t in tag_list
        ))
        relevant.append((device_part, blocks_part, tags_part))
    relevant.sort()
    return hashlib.md5(repr(relevant).encode()).hexdigest()


async def worker_manager(
    historian: BufferedHistorianWriter,
    stop_event: asyncio.Event,
    reload_interval: float = 10.0,
):
    """Owns DeviceWorker lifecycle and rebuilds on config-fingerprint change.

    Every `reload_interval` seconds:
      1. Load current polling config from DB (in a thread — sync SQLAlchemy).
      2. Hash it.
      3. If the hash differs from last seen: gracefully stop existing workers,
         construct new ones, start them.

    The historian, store-and-forward buffer, stale-detection loop, and replay
    loop are unaffected — they hold no per-worker state. Only the device
    polling tasks are reshuffled.

    On shutdown (stop_event set), stops all current workers and waits for
    their tasks to finish before returning.
    """
    mlog = logging.getLogger("worker.manager")
    current_workers: list[DeviceWorker] = []
    current_tasks: list[asyncio.Task] = []
    current_fp: str | None = None
    no_config_warned = False

    while not stop_event.is_set():
        try:
            config = await asyncio.to_thread(load_polling_config)

            if not config:
                if not no_config_warned:
                    mlog.warning(
                        "No enabled devices yet. "
                        "Run: docker compose run --rm backend python -m app.seed"
                    )
                    no_config_warned = True
                # Re-check on next tick
            else:
                no_config_warned = False
                fp = _config_fingerprint(config)

                if fp != current_fp:
                    n_devices = len(config)
                    n_blocks = sum(len(c["blocks"]) for c in config)
                    n_tags = sum(
                        sum(len(t) for t in c["tags_by_block"].values())
                        for c in config
                    )

                    if current_fp is None:
                        mlog.info(
                            "Initial config: %d device(s), %d block(s), %d tag(s)",
                            n_devices, n_blocks, n_tags,
                        )
                    else:
                        mlog.info(
                            "Config changed (fingerprint %s → %s) — "
                            "rebuilding %d → %d worker(s) [%d block(s), %d tag(s)]",
                            current_fp[:8], fp[:8],
                            len(current_workers), n_devices,
                            n_blocks, n_tags,
                        )
                        # Stop existing workers; wait for their final cycles.
                        for w in current_workers:
                            w.stop()
                        if current_tasks:
                            await asyncio.gather(
                                *current_tasks, return_exceptions=True,
                            )

                    current_workers = [
                        DeviceWorker(
                            c["device"], c["blocks"],
                            c["tags_by_block"], historian,
                        )
                        for c in config
                    ]
                    current_tasks = [
                        asyncio.create_task(w.run())
                        for w in current_workers
                    ]
                    current_fp = fp
        except Exception:
            mlog.exception("Worker manager tick failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=reload_interval)
            break  # shutdown signaled
        except asyncio.TimeoutError:
            continue  # normal — time for next reload check

    # Shutdown
    if current_workers:
        mlog.info("Shutdown: stopping %d worker(s) ...", len(current_workers))
        for w in current_workers:
            w.stop()
        if current_tasks:
            await asyncio.gather(*current_tasks, return_exceptions=True)
    mlog.info("Stopped.")


async def main():
    historian = HistorianWriter(engine)
    buffer_path = Path(os.environ.get("SF_BUFFER_PATH", "/data/sf_buffer.db"))
    log.info("Local store-and-forward buffer: %s", buffer_path)
    sf_buffer = LocalBuffer(buffer_path)
    backlog = sf_buffer.count()
    if backlog > 0:
        log.info("Resuming with %d sample(s) already in local buffer", backlog)

    buffered_historian = BufferedHistorianWriter(historian, sf_buffer)

    # Shared shutdown signal. The worker_manager owns the DeviceWorker
    # lifecycle (creation, stop, restart on config change); the stale and
    # replay loops are long-lived and unaffected by config edits.
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()

    def _shutdown(signame: str):
        log.info("Received %s, stopping ...", signame)
        stop_event.set()

    for signame in ("SIGINT", "SIGTERM"):
        try:
            loop.add_signal_handler(
                getattr(signal, signame),
                lambda s=signame: _shutdown(s),
            )
        except NotImplementedError:
            # Windows event loop lacks signal handlers; KeyboardInterrupt handles SIGINT.
            pass

    tasks = [
        asyncio.create_task(worker_manager(buffered_historian, stop_event)),
        asyncio.create_task(stale_detection_loop(stop_event)),
        asyncio.create_task(replay_loop(sf_buffer, historian, stop_event)),
        asyncio.create_task(buffer_status_loop(sf_buffer, stop_event)),
    ]
    log.info(
        "Started worker manager + stale detection + replay + buffer status",
    )
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted, exiting.")
        sys.exit(0)
