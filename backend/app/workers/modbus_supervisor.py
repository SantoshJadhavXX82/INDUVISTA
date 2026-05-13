"""Modbus polling worker (supervisor) — Phase 8.5 hardened.

Loads enabled devices/blocks/tags from the database, opens a long-lived
Modbus TCP connection per device, and polls each register_block at the
block's scan_interval_ms (falling back to the device's scan_interval_ms
when null). Decodes each tag, computes ST per the CV/ST status model,
and submits a Sample batch through a BufferedHistorianWriter that falls
back to a local SQLite buffer on Postgres failure.

Phase 1 contract:
  * One asyncio task per device, one TCP connection per device, reconnect
    on any read failure.

Phase 2:
  * Stale detection — periodic UPDATE that downgrades aged VALID rows.
  * Store-and-forward — BufferedHistorianWriter spills to SQLite on PG fail.

Phase 3.5:
  * Config hot-reload — fingerprint of polling-relevant fields.

Phase 8.5 hardening (this revision):
  * Per-block scheduler — each register_block has its own ticker.
    blocks within the same device poll concurrently over the same TCP
    socket (pymodbus multiplexes via MBAP transaction IDs).
  * Retry-once-on-failure — configurable per device. Modbus norms expect
    a retry on transient timeouts.
  * Exception classification — slave-side ModbusExceptionResponse,
    wire-level ModbusIOException, and transport ConnectionException
    map to distinct ST codes (ST_MODBUS_EXCEPTION, ST_MODBUS_IO_ERROR,
    ST_COMM_TIMEOUT). st_reason carries the exception code name
    (ILLEGAL_DATA_ADDRESS, GATEWAY_TARGET_NO_RESPONSE, ...).
  * Reconnect backoff — exponential doubling, capped at reconnect_max_ms.
    Prevents the previous behavior of reconnect-every-scan-interval
    against a dead device.
  * Transport gating — channels.transport in (rtu, serial) currently fall
    out to ST_TRANSPORT_UNSUPPORTED rather than silently attempting TCP.
  * Per-request timeout — devices.request_timeout_ms controls the pymodbus
    timeout. Tunable for slow links.
  * Response-time tracking — per-cycle avg/max latency written back to
    worker_device_status. Cumulative average too.

Out of scope (Phase 9+): redundancy/duty failover, RTU/serial transport
implementation, write-via-REST endpoint (delivered separately in Phase 8.5).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import (
    ConnectionException,
    ModbusException,
    ModbusIOException,
)
from sqlalchemy import text

from app.db import engine
from app.historian import BufferedHistorianWriter, HistorianWriter, Sample
from app.local_buffer import LocalBuffer
from app.modbus.decoder import decode_value
from app.workers.enron_channel import (
    EnronChannel,
    EnronConnectError,
    EnronProtocolError,
    EnronSlaveException,
    EnronTimeoutError,
)
from app.modbus.status import (
    MODBUS_EXCEPTION_NAMES,
    ST_COMM_TIMEOUT,
    ST_DECODE_FAIL,
    ST_MODBUS_EXCEPTION,
    ST_MODBUS_IO_ERROR,
    ST_READ_OK,
    ST_RETRY_EXHAUSTED,
    ST_TRANSPORT_UNSUPPORTED,
)

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
            "device": {...device row including timeout/retry/backoff...},
            "channel": {...channel row (id, transport)...},
            "blocks": [...register_block rows with scan_interval_ms...],
            "tags_by_block": {block_id: [...tag rows...]},
        }

    Phase 8.5: now also pulls channel.transport so the worker can fail-fast
    on rtu/serial, and the new device.* timeout/retry/reconnect columns.
    """
    with engine.connect() as conn:
        devices = [
            dict(r) for r in conn.execute(text("""
                SELECT d.id, d.name, d.host, d.port, d.unit_id,
                       d.scan_interval_ms,
                       d.request_timeout_ms, d.retry_count,
                       d.reconnect_initial_ms, d.reconnect_max_ms,
                       d.channel_id,
                       c.transport AS channel_transport,
                       c.enabled   AS channel_enabled
                FROM devices d
                JOIN channels c ON c.id = d.channel_id
                WHERE d.enabled = TRUE
                ORDER BY d.id
            """)).mappings()
        ]

        result = []
        for d in devices:
            if not d["channel_enabled"]:
                # Channel disabled — skip the device entirely. The hot-reload
                # loop will pick it up if the channel is re-enabled.
                continue
            blocks = [
                dict(r) for r in conn.execute(text("""
                    SELECT id, name, function_code, start_address, count,
                           scan_interval_ms, addressing_mode
                    FROM register_blocks
                    WHERE device_id = :did AND enabled = TRUE
                    ORDER BY function_code, start_address
                """), {"did": d["id"]}).mappings()
            ]

            tags_by_block: dict[int, list[dict]] = {b["id"]: [] for b in blocks}
            for t in conn.execute(text("""
                SELECT id, device_id, register_block_id, name,
                       data_type, byte_order, function_code,
                       address, register_count, scale, "offset",
                       is_heartbeat, heartbeat_max_stale_sec
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
    """SQL UPDATE that downgrades VALID rows past their stale threshold."""
    with eng.begin() as conn:
        result = conn.execute(text("""
            UPDATE latest_tag_values lv
            SET st = 64, st_reason = 'STALE'
            FROM tags t
            JOIN devices d ON d.id = t.device_id
            WHERE lv.tag_id = t.id
              AND lv.st >= 128
              AND lv.time < NOW() - (d.stale_after_sec * INTERVAL '1 second')
        """))
        return result.rowcount


async def stale_detection_loop(stop_event: asyncio.Event, interval: float = 15.0):
    slog = logging.getLogger("worker.stale")
    slog.info("Stale-detection started (every %.1fs)", interval)
    while not stop_event.is_set():
        try:
            n = await asyncio.to_thread(_run_stale_check, engine)
            if n:
                slog.info("Marked %d tag(s) STALE", n)
        except Exception:
            slog.exception("Stale-detection tick failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            continue
    slog.info("Stopped.")


async def replay_loop(
    buffer: LocalBuffer,
    direct: HistorianWriter,
    stop_event: asyncio.Event,
    check_interval: float = 5.0,
    batch_size: int = 500,
):
    """Drain the local SQLite buffer into Postgres when reachable."""
    rlog = logging.getLogger("worker.replay")
    rlog.info("Replay started (check every %.1fs, batch=%d)",
              check_interval, batch_size)
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
                rlog.info("Buffer has %d sample(s); PG down, retry later", count)
                continue

            batch = await asyncio.to_thread(buffer.peek, batch_size)
            if not batch:
                continue

            for s in batch:
                s.source = "store_forward"

            try:
                await asyncio.to_thread(direct.write_history_only, batch)
            except Exception as e:
                rlog.warning("Replay batch failed (%s); %d remain in buffer",
                             e, len(batch))
                continue

            n = await asyncio.to_thread(buffer.delete, batch)
            rlog.info("Replayed %d sample(s); %d remaining", n, count - n)
        except Exception:
            rlog.exception("Replay tick failed")
    rlog.info("Stopped.")


async def buffer_status_loop(
    buffer: LocalBuffer, stop_event: asyncio.Event, interval: float = 10.0,
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
        pass


# ============================================================================
# DeviceWorker — Phase 8.5 refactor
# ============================================================================

class DeviceWorker:
    """One device, one TCP socket, parallel per-block schedulers.

    Phase 8.5 architecture:
      - `run()` opens the socket (with backoff retry on failure) and spawns
        one block-loop coroutine per register_block.
      - Each block-loop runs at its own scan_interval_ms (or the device's
        if the block's is null). pymodbus's AsyncModbusTcpClient multiplexes
        concurrent requests over the same TCP socket via MBAP transaction
        IDs, so parallel block reads are safe.
      - On socket failure, all block tasks back off until reconnect succeeds.
    """

    def __init__(self, device: dict, blocks: list[dict],
                 tags_by_block: dict[int, list[dict]],
                 historian):
        self.device = device
        self.blocks = blocks
        self.tags_by_block = tags_by_block
        self.historian = historian

        self.client: AsyncModbusTcpClient | None = None
        # asyncio.Lock for the connect path so two block tasks don't both
        # try to create a fresh client during a reconnect race.
        self._connect_lock = asyncio.Lock()

        # Phase 9.1.1 — separate persistent socket for Enron-mode blocks.
        # Lazy: only created when this device has at least one Enron block.
        # Coexists with self.client (pymodbus for STANDARD blocks); both
        # may be active at once on a device with mixed-mode blocks.
        self.enron: EnronChannel | None = None

        self._stop = False

        # Per-heartbeat-tag state (Phase 7 E1a) — monotonic time of last change.
        self._heartbeat_state: dict[int, tuple[float | None, float]] = {}

        # Worker-restart-local cumulative counters (Phase 7 E1c).
        self._cumulative_total = 0
        self._cumulative_good = 0

        # Phase 8.5 — reconnect backoff state. Monotonic gate; if we tried
        # and failed recently, hold off until this time.
        self._next_connect_attempt_mono: float = 0.0
        self._current_backoff_sec: float = (
            device.get("reconnect_initial_ms", 1000) / 1000.0
        )

        # Phase 5b state — consecutive failures for status reporting.
        self._consecutive_failures = 0

        # Phase 8.5 — response time tracking.
        # Each block-cycle pushes its latency into _cycle_latencies; once a
        # status flush happens, we compute avg/max from this window and clear.
        self._cycle_latencies: list[float] = []
        self._cumulative_latency_sum_ms: float = 0.0
        self._cumulative_latency_count: int = 0

        # Phase 10.2-hotfix-cycle-samples — per-status-flush-window sample
        # counters. Block loops increment these on every successful write;
        # the 5s status flush drains them and reports them as
        # last_cycle_samples_total/good, then resets to 0. This is what
        # the Diagnostics "Samples" column shows.
        self._window_samples_total: int = 0
        self._window_samples_good: int = 0

        self.log = logging.getLogger(f"worker.{device['name']}")

    def stop(self):
        self._stop = True

    # ------------------------------------------------------------------ run
    async def run(self):
        total_tags = sum(len(t) for t in self.tags_by_block.values())
        transport = self.device.get("channel_transport", "tcp")

        # Phase 8.5 — transport gate. The worker is TCP-only.
        # If someone configured RTU or serial, fail loudly rather than
        # silently attempting a TCP connect and showing garbage errors.
        if transport != "tcp":
            self.log.error(
                "Transport '%s' is not implemented in this worker (TCP only). "
                "All tags on this device will be marked TRANSPORT_UNSUPPORTED. "
                "Either switch the channel to TCP or wait for RTU/serial support.",
                transport,
            )
            await self._mark_unsupported_loop()
            return

        self.log.info(
            "Polling %s:%d unit=%d device_interval=%dms blocks=%d tags=%d "
            "(timeout=%dms, retries=%d)",
            self.device["host"], self.device["port"], self.device["unit_id"],
            self.device["scan_interval_ms"], len(self.blocks), total_tags,
            self.device["request_timeout_ms"], self.device["retry_count"],
        )

        # Launch one task per block. They run concurrently and share the
        # TCP socket via pymodbus's MBAP transaction-ID multiplexing.
        status_task = asyncio.create_task(self._status_flush_loop())
        block_tasks = [
            asyncio.create_task(self._block_loop(b)) for b in self.blocks
        ]

        try:
            await asyncio.gather(*block_tasks, return_exceptions=True)
        finally:
            status_task.cancel()
            try:
                await status_task
            except asyncio.CancelledError:
                pass
            if self.client:
                try:
                    self.client.close()
                except Exception:
                    pass
            # Phase 9.1.1 — close persistent Enron socket on shutdown
            if self.enron:
                try:
                    await self.enron.close()
                except Exception:
                    pass

        self.log.info("Stopped.")

    # ---------- transport-unsupported loop ----------------------------------
    async def _mark_unsupported_loop(self):
        """Periodically emit ST_TRANSPORT_UNSUPPORTED samples for every tag.

        We don't poll anything, but we DO want the Live page to show clearly
        that this device is misconfigured rather than just stale. Emit one
        sample per tag per device.scan_interval_ms until shutdown.
        """
        interval = max(self.device["scan_interval_ms"] / 1000.0, 1.0)
        while not self._stop:
            now = datetime.now(timezone.utc)
            samples: list[Sample] = []
            for block in self.blocks:
                for t in self.tags_by_block.get(block["id"], []):
                    samples.append(Sample(
                        tag_id=t["id"],
                        device_id=t["device_id"],
                        register_block_id=block["id"],
                        time=now,
                        value_double=None,
                        value_text=None,
                        st=ST_TRANSPORT_UNSUPPORTED,
                        st_reason=f"TRANSPORT_UNSUPPORTED_{self.device.get('channel_transport', '?').upper()}",
                    ))
            if samples:
                try:
                    await asyncio.to_thread(self.historian.write_samples, samples)
                except Exception:
                    pass
            await asyncio.to_thread(
                self._report_status_sync,
                len(samples), 0, "transport_unsupported",
            )
            try:
                await asyncio.wait_for(
                    asyncio.create_task(self._stop_or_sleep(interval)),
                    timeout=interval + 1,
                )
            except asyncio.TimeoutError:
                pass

    async def _stop_or_sleep(self, secs: float):
        end = time.monotonic() + secs
        while not self._stop and time.monotonic() < end:
            await asyncio.sleep(min(0.5, end - time.monotonic()))

    # ---------- per-block schedule loop -------------------------------------
    async def _block_loop(self, block: dict):
        """One async loop per block, ticking at the block's scan interval.

        Phase 8.5: block.scan_interval_ms is honored when set; falls back to
        device.scan_interval_ms otherwise. Allows fast totalizers to coexist
        with slow chromatograph blocks on the same device.
        """
        block_interval_ms = block.get("scan_interval_ms") or self.device["scan_interval_ms"]
        scan_interval = max(block_interval_ms / 1000.0, 0.05)
        blog = logging.getLogger(f"worker.{self.device['name']}.{block['name']}")
        blog.info("Block loop: interval=%.2fs", scan_interval)

        cycle = 0
        while not self._stop:
            cycle_start = time.monotonic()
            try:
                samples = await self._poll_block_with_retry(block)
                if samples:
                    n = await asyncio.to_thread(
                        self.historian.write_samples, samples,
                    )
                    self._cumulative_total += len(samples)
                    good = sum(1 for s in samples if s.st == ST_READ_OK)
                    self._cumulative_good += good
                    # Phase 10.2-hotfix — feed the status-flush window so the
                    # Diagnostics "Samples" column shows real values, not the
                    # stale 0/0 from the initial INSERT.
                    self._window_samples_total += len(samples)
                    self._window_samples_good += good
                    cycle += 1
                    if cycle % 20 == 0 or cycle == 1:
                        blog.info(
                            "Cycle %d: wrote %d (%d good/%d total)",
                            cycle, n, good, len(samples),
                        )
            except Exception:
                blog.exception("Block cycle error")

            elapsed = time.monotonic() - cycle_start
            await asyncio.sleep(max(0.0, scan_interval - elapsed))

    # ---------- retry wrapper -----------------------------------------------
    async def _poll_block_with_retry(self, block: dict) -> list[Sample]:
        """Try a block read up to (retry_count + 1) times.

        Returns the samples from the LAST attempt — successful or not.
        Logic:
          - If the first attempt yields all-good samples, no retry needed.
          - If any sample failed, retry up to retry_count more times.
          - Between retries, sleep 50ms (tunable in code, kept short on
            purpose — Modbus retries should be quick).
          - If the final attempt still has failures, those samples are
            already tagged with the right ST/reason from _poll_block_once;
            we may upgrade them to ST_RETRY_EXHAUSTED for clarity if the
            failure was transient (COMM_TIMEOUT/IO_ERROR).

        Note: For ST_MODBUS_EXCEPTION (illegal addr, illegal func), retries
        won't help — the slave is rejecting deliberately. We retry anyway
        because the retry_count is a property of the device and devices
        with retry_count=0 won't retry at all.
        """
        retry_count = self.device.get("retry_count", 1)
        attempts = retry_count + 1  # initial attempt + retries
        samples: list[Sample] = []

        for attempt in range(attempts):
            samples = await self._poll_block_once(block)

            # Did every sample succeed?
            if samples and all(s.st >= ST_READ_OK for s in samples):
                if attempt > 0:
                    self.log.info(
                        "Block %s recovered on retry %d/%d",
                        block["name"], attempt, retry_count,
                    )
                return samples

            # Not the last attempt — wait briefly and retry
            if attempt < attempts - 1:
                await asyncio.sleep(0.05)
                continue

            # Exhausted retries — re-classify any transient failures as
            # RETRY_EXHAUSTED so diagnostics shows we tried and gave up,
            # not just "one read failed".
            if retry_count > 0:
                for s in samples:
                    if s.st in (ST_COMM_TIMEOUT, ST_MODBUS_IO_ERROR):
                        s.st = ST_RETRY_EXHAUSTED
                        s.st_reason = (
                            f"RETRY_EXHAUSTED ({retry_count + 1} attempts): "
                            f"{s.st_reason}"
                        )[:64]
        return samples

    # ---------- Phase 9.1.1: Enron block read --------------------------------
    async def _poll_enron_block_once(
        self, block: dict, now: datetime,
    ) -> list[Sample]:
        """Poll an Enron-mode block via the dedicated persistent socket.

        Width inferred from the first tag's register_count:
            register_count = 1 → 2 bytes (uint16/int16/bool)
            register_count = 2 → 4 bytes (uint32/int32/float32)
            register_count = 4 → 8 bytes (uint64/int64/float64)

        The Enron channel returns fake uint16 registers in the same shape
        pymodbus would produce for a standard read of the same byte count,
        so _decode_block consumes the result unchanged.
        """
        from app.workers import frame_capture

        fc = block["function_code"]
        start = block["start_address"]
        count = block["count"]
        unit_id = self.device["unit_id"]

        # Need at least one tag to infer width — an Enron block with no tags
        # is misconfigured.
        tags = self.tags_by_block.get(block["id"], [])
        if not tags:
            self.log.warning(
                "Enron block %s has no tags — cannot infer width, skipping",
                block["name"],
            )
            return []

        # Width inference. API validation (Phase 9.1.1) enforces that all
        # tags in an Enron block share register_count, so the first tag's
        # value is authoritative.
        first_rc = tags[0]["register_count"]
        if first_rc not in (1, 2, 4):
            self.log.warning(
                "Enron block %s: first tag has register_count=%d, must be "
                "1/2/4 — skipping",
                block["name"], first_rc,
            )
            return self._failed_samples(
                block, now, ST_MODBUS_IO_ERROR, "ENRON_WIDTH_INVALID",
            )
        value_width_bytes = first_rc * 2

        # Lazy channel construction. One per device, reused across scans.
        if self.enron is None:
            self.enron = EnronChannel(
                host=self.device["host"],
                port=self.device["port"],
                log=self.log,
                reconnect_initial_ms=self.device.get(
                    "reconnect_initial_ms", 1000,
                ),
                reconnect_max_ms=self.device.get(
                    "reconnect_max_ms", 30000,
                ),
            )

        request_timeout_s = self.device["request_timeout_ms"] / 1000.0
        t0 = time.monotonic()
        try:
            raw = await self.enron.read_enron(
                unit_id=unit_id,
                function_code=fc,
                start_address=start,
                count=count,
                value_width_bytes=value_width_bytes,
                request_timeout_s=request_timeout_s,
            )
            latency_ms = (time.monotonic() - t0) * 1000
            self._record_latency(latency_ms)
            frame_capture.capture_block_read(
                device_id=self.device["id"], block=block, unit_id=unit_id,
                fc=fc, start=start, count=count,
                response_data=raw, error=None, latency_ms=latency_ms,
            )
            return self._decode_block(block, raw, now)

        except EnronSlaveException as e:
            latency_ms = (time.monotonic() - t0) * 1000
            self._record_latency(latency_ms)
            reason = MODBUS_EXCEPTION_NAMES.get(
                e.exception_code, f"EXCEPTION_{e.exception_code}",
            )
            self.log.warning(
                "Enron block %s slave exception %d (%s)",
                block["name"], e.exception_code, reason,
            )
            frame_capture.capture_block_read(
                device_id=self.device["id"], block=block,
                unit_id=unit_id, fc=fc, start=start, count=count,
                response_data=None,
                error=f"EXCEPTION_{e.exception_code}_{reason}",
                latency_ms=latency_ms,
            )
            return self._failed_samples(
                block, now, ST_MODBUS_EXCEPTION, reason,
            )

        except EnronProtocolError as e:
            latency_ms = (time.monotonic() - t0) * 1000
            self._record_latency(latency_ms)
            self.log.warning("Enron block %s protocol error: %s",
                             block["name"], e)
            frame_capture.capture_block_read(
                device_id=self.device["id"], block=block, unit_id=unit_id,
                fc=fc, start=start, count=count,
                response_data=None, error=f"ENRON_PROTO: {e}",
                latency_ms=latency_ms,
            )
            return self._failed_samples(
                block, now, ST_MODBUS_IO_ERROR,
                f"ENRON_PROTO: {str(e)[:48]}",
            )

        except EnronTimeoutError:
            latency_ms = (time.monotonic() - t0) * 1000
            self.log.warning(
                "Enron block %s timeout (%dms)",
                block["name"], self.device["request_timeout_ms"],
            )
            frame_capture.capture_block_read(
                device_id=self.device["id"], block=block, unit_id=unit_id,
                fc=fc, start=start, count=count,
                response_data=None, error="ENRON_TIMEOUT",
                latency_ms=latency_ms,
            )
            return self._failed_samples(
                block, now, ST_COMM_TIMEOUT, "TIMEOUT",
            )

        except EnronConnectError as e:
            latency_ms = (time.monotonic() - t0) * 1000
            self.log.warning("Enron block %s connect/conn lost: %s",
                             block["name"], e)
            frame_capture.capture_block_read(
                device_id=self.device["id"], block=block, unit_id=unit_id,
                fc=fc, start=start, count=count,
                response_data=None, error=f"ENRON_CONN: {e}",
                latency_ms=latency_ms,
            )
            return self._failed_samples(
                block, now, ST_COMM_TIMEOUT, "CONN_LOST",
            )

        except Exception as e:
            latency_ms = (time.monotonic() - t0) * 1000
            self.log.warning("Enron block %s unexpected error: %s",
                             block["name"], e)
            frame_capture.capture_block_read(
                device_id=self.device["id"], block=block, unit_id=unit_id,
                fc=fc, start=start, count=count,
                response_data=None, error=f"ENRON_UNKNOWN: {e}",
                latency_ms=latency_ms,
            )
            return self._failed_samples(
                block, now, ST_COMM_TIMEOUT,
                f"UNKNOWN: {str(e)[:32]}",
            )

    # ---------- single block read -------------------------------------------
    async def _poll_block_once(self, block: dict) -> list[Sample]:
        fc = block["function_code"]
        start = block["start_address"]
        count = block["count"]
        unit_id = self.device["unit_id"]
        now = datetime.now(timezone.utc)

        # Phase 9.1.1 — Enron blocks go through a separate persistent socket
        # with permissive byte_count handling (real Daniel firmware sends
        # byte_count = 4N + trailing, which pymodbus rejects). Fully isolated
        # from the pymodbus path; shares only _decode_block and _failed_samples.
        if block.get("addressing_mode") in ("ENRON_HOLDING", "ENRON_INPUT"):
            return await self._poll_enron_block_once(block, now)

        if not await self._ensure_connected_with_backoff():
            return self._failed_samples(
                block, now, ST_COMM_TIMEOUT, "CONN_BACKOFF",
            )

        from app.workers import frame_capture
        t0 = time.monotonic()

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
                self.log.warning("Unknown function_code=%d on block %s — skip",
                                 fc, block["name"])
                return []

            latency_ms = (time.monotonic() - t0) * 1000
            self._record_latency(latency_ms)

            # Phase 8.5 — distinguish slave-side exception from IO error.
            # pymodbus's response.isError() is true for both, but the type
            # is different: ExceptionResponse has exception_code set.
            if rr.isError():
                exc_code = getattr(rr, "exception_code", None)
                if exc_code is not None:
                    reason = MODBUS_EXCEPTION_NAMES.get(
                        exc_code, f"EXCEPTION_{exc_code}",
                    )
                    self.log.warning(
                        "Block %s slave exception %d (%s)",
                        block["name"], exc_code, reason,
                    )
                    frame_capture.capture_block_read(
                        device_id=self.device["id"], block=block,
                        unit_id=unit_id, fc=fc, start=start, count=count,
                        response_data=None,
                        error=f"EXCEPTION_{exc_code}_{reason}",
                        latency_ms=latency_ms,
                    )
                    return self._failed_samples(
                        block, now, ST_MODBUS_EXCEPTION, reason,
                    )
                # No exception_code — generic isError(); treat as IO error.
                self.log.warning("Block %s read error (no exc code): %s",
                                 block["name"], rr)
                frame_capture.capture_block_read(
                    device_id=self.device["id"], block=block,
                    unit_id=unit_id, fc=fc, start=start, count=count,
                    response_data=None, error=str(rr), latency_ms=latency_ms,
                )
                return self._failed_samples(
                    block, now, ST_MODBUS_IO_ERROR, "IO_ERROR",
                )

            raw = rr.bits[:count] if fc in (1, 2) else rr.registers
            frame_capture.capture_block_read(
                device_id=self.device["id"], block=block, unit_id=unit_id,
                fc=fc, start=start, count=count,
                response_data=raw, error=None, latency_ms=latency_ms,
            )
            return self._decode_block(block, raw, now)

        except ModbusIOException as e:
            # Wire-level corruption — CRC, framing. Don't drop the connection;
            # one bad frame doesn't mean the socket is broken.
            latency_ms = (time.monotonic() - t0) * 1000
            self._record_latency(latency_ms)
            self.log.warning("Block %s IO error: %s", block["name"], e)
            frame_capture.capture_block_read(
                device_id=self.device["id"], block=block, unit_id=unit_id,
                fc=fc, start=start, count=count,
                response_data=None, error=f"IO_ERROR: {e}",
                latency_ms=latency_ms,
            )
            return self._failed_samples(
                block, now, ST_MODBUS_IO_ERROR, f"IO_ERROR: {str(e)[:48]}",
            )

        except ConnectionException as e:
            # Connection lost — force reconnect on next attempt.
            latency_ms = (time.monotonic() - t0) * 1000
            self.log.warning("Block %s lost connection: %s", block["name"], e)
            frame_capture.capture_block_read(
                device_id=self.device["id"], block=block, unit_id=unit_id,
                fc=fc, start=start, count=count,
                response_data=None, error=f"CONN_LOST: {e}",
                latency_ms=latency_ms,
            )
            await self._drop_client()
            return self._failed_samples(
                block, now, ST_COMM_TIMEOUT, "CONN_LOST",
            )

        except asyncio.TimeoutError:
            latency_ms = (time.monotonic() - t0) * 1000
            self.log.warning("Block %s timeout (%dms)",
                             block["name"], self.device["request_timeout_ms"])
            frame_capture.capture_block_read(
                device_id=self.device["id"], block=block, unit_id=unit_id,
                fc=fc, start=start, count=count,
                response_data=None, error="TIMEOUT", latency_ms=latency_ms,
            )
            return self._failed_samples(
                block, now, ST_COMM_TIMEOUT, "TIMEOUT",
            )

        except Exception as e:
            latency_ms = (time.monotonic() - t0) * 1000
            self.log.warning("Block %s unknown error: %s", block["name"], e)
            frame_capture.capture_block_read(
                device_id=self.device["id"], block=block, unit_id=unit_id,
                fc=fc, start=start, count=count,
                response_data=None, error=f"UNKNOWN: {e}",
                latency_ms=latency_ms,
            )
            await self._drop_client()
            return self._failed_samples(
                block, now, ST_COMM_TIMEOUT, f"UNKNOWN: {str(e)[:32]}",
            )

    # ---------- connect + backoff -------------------------------------------
    async def _ensure_connected_with_backoff(self) -> bool:
        """Open the TCP socket if not already; honor reconnect backoff.

        Returns True if connected, False if currently in backoff cooldown.
        Multiple block tasks may hit this concurrently — _connect_lock
        serializes the connect attempt.
        """
        if self.client is not None and self.client.connected:
            return True

        # Backoff gate — if we tried recently and failed, hold off.
        if time.monotonic() < self._next_connect_attempt_mono:
            return False

        async with self._connect_lock:
            # Re-check inside the lock — another task may have connected.
            if self.client is not None and self.client.connected:
                return True
            if time.monotonic() < self._next_connect_attempt_mono:
                return False

            timeout_sec = self.device["request_timeout_ms"] / 1000.0
            try:
                self.log.info(
                    "Connecting to %s:%d ...",
                    self.device["host"], self.device["port"],
                )
                self.client = AsyncModbusTcpClient(
                    host=self.device["host"],
                    port=self.device["port"],
                    timeout=timeout_sec,
                )
                await self.client.connect()
                if self.client.connected:
                    self.log.info("Connected.")
                    # Reset backoff
                    self._current_backoff_sec = (
                        self.device["reconnect_initial_ms"] / 1000.0
                    )
                    self._next_connect_attempt_mono = 0.0
                    self._consecutive_failures = 0
                    return True
                self.client = None
            except Exception as e:
                self.log.warning("Connect failed: %s", e)
                self.client = None

            # Connect failed — schedule next attempt with exponential backoff.
            self._consecutive_failures += 1
            self._next_connect_attempt_mono = (
                time.monotonic() + self._current_backoff_sec
            )
            self.log.info(
                "Next connect attempt in %.1fs",
                self._current_backoff_sec,
            )
            max_backoff = self.device["reconnect_max_ms"] / 1000.0
            self._current_backoff_sec = min(
                self._current_backoff_sec * 2, max_backoff,
            )
            return False

    async def _drop_client(self):
        """Close the current client and force a reconnect on next read."""
        async with self._connect_lock:
            if self.client:
                try:
                    self.client.close()
                except Exception:
                    pass
            self.client = None

    # ---------- response-time tracking + status flush -----------------------
    def _record_latency(self, ms: float) -> None:
        self._cycle_latencies.append(ms)
        self._cumulative_latency_sum_ms += ms
        self._cumulative_latency_count += 1

    async def _status_flush_loop(self):
        """Every 5 seconds, write rolled-up status to worker_device_status."""
        while not self._stop:
            try:
                await asyncio.sleep(5.0)
                # Flush current window
                lats = self._cycle_latencies
                self._cycle_latencies = []  # reset window

                # Phase 10.2-hotfix — snapshot and reset per-window sample
                # counters. Whatever was written in the last ~5 seconds is
                # what the Diagnostics "Samples" column will show.
                window_total = self._window_samples_total
                window_good = self._window_samples_good
                self._window_samples_total = 0
                self._window_samples_good = 0

                avg = sum(lats) / len(lats) if lats else None
                mx = max(lats) if lats else None
                cum_avg = (
                    self._cumulative_latency_sum_ms /
                    self._cumulative_latency_count
                ) if self._cumulative_latency_count else None

                # A device is "connected" if EITHER transport is up:
                #   - pymodbus client for STANDARD blocks
                #   - persistent Enron socket for ENRON blocks
                # Phase 9.1.1 introduced the second path. Reporting the
                # union avoids a confusing "disconnected" badge on devices
                # whose blocks are all Enron — those never use the pymodbus
                # client at all but are happily polling via self.enron.
                pymodbus_up = (
                    self.client is not None and self.client.connected
                )
                enron_up = (
                    self.enron is not None and self.enron.stats().connected
                )
                state = "connected" if (pymodbus_up or enron_up) else (
                    "reconnecting" if self._consecutive_failures > 0
                    else "disconnected"
                )

                await asyncio.to_thread(
                    self._report_status_sync_with_latency,
                    state, avg, mx, cum_avg,
                    window_total, window_good,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                self.log.exception("Status flush error")

    def _report_status_sync(self, total: int, good: int, connection_state: str) -> None:
        """Legacy status writer — kept for transport-unsupported loop."""
        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO worker_device_status (
                        device_id, last_cycle_at,
                        last_cycle_samples_total, last_cycle_samples_good,
                        cumulative_samples_total, cumulative_samples_good,
                        consecutive_failures, connection_state, updated_at
                    )
                    VALUES (
                        :device_id, NOW(),
                        :total, :good,
                        :cum_total, :cum_good,
                        :failures, :state, NOW()
                    )
                    ON CONFLICT (device_id) DO UPDATE SET
                        last_cycle_at = NOW(),
                        last_cycle_samples_total = EXCLUDED.last_cycle_samples_total,
                        last_cycle_samples_good = EXCLUDED.last_cycle_samples_good,
                        cumulative_samples_total = EXCLUDED.cumulative_samples_total,
                        cumulative_samples_good = EXCLUDED.cumulative_samples_good,
                        consecutive_failures = EXCLUDED.consecutive_failures,
                        connection_state = EXCLUDED.connection_state,
                        updated_at = NOW()
                """), {
                    "device_id": self.device["id"],
                    "total": total, "good": good,
                    "cum_total": self._cumulative_total,
                    "cum_good": self._cumulative_good,
                    "failures": self._consecutive_failures,
                    "state": connection_state,
                })
        except Exception:
            pass

    def _report_status_sync_with_latency(
        self, state: str,
        avg_ms: float | None, max_ms: float | None, cum_avg_ms: float | None,
        last_cycle_total: int = 0, last_cycle_good: int = 0,
    ) -> None:
        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO worker_device_status (
                        device_id, last_cycle_at,
                        last_cycle_samples_total, last_cycle_samples_good,
                        cumulative_samples_total, cumulative_samples_good,
                        consecutive_failures, connection_state,
                        last_cycle_response_ms_avg, last_cycle_response_ms_max,
                        cumulative_response_ms_avg, updated_at
                    )
                    VALUES (
                        :device_id, NOW(),
                        :last_total, :last_good,
                        :cum_total, :cum_good,
                        :failures, :state,
                        :avg_ms, :max_ms, :cum_avg, NOW()
                    )
                    ON CONFLICT (device_id) DO UPDATE SET
                        last_cycle_at = NOW(),
                        last_cycle_samples_total = EXCLUDED.last_cycle_samples_total,
                        last_cycle_samples_good = EXCLUDED.last_cycle_samples_good,
                        cumulative_samples_total = EXCLUDED.cumulative_samples_total,
                        cumulative_samples_good = EXCLUDED.cumulative_samples_good,
                        consecutive_failures = EXCLUDED.consecutive_failures,
                        connection_state = EXCLUDED.connection_state,
                        last_cycle_response_ms_avg = EXCLUDED.last_cycle_response_ms_avg,
                        last_cycle_response_ms_max = EXCLUDED.last_cycle_response_ms_max,
                        cumulative_response_ms_avg = EXCLUDED.cumulative_response_ms_avg,
                        updated_at = NOW()
                """), {
                    "device_id": self.device["id"],
                    "last_total": last_cycle_total,
                    "last_good": last_cycle_good,
                    "cum_total": self._cumulative_total,
                    "cum_good": self._cumulative_good,
                    "failures": self._consecutive_failures,
                    "state": state,
                    "avg_ms": avg_ms, "max_ms": max_ms, "cum_avg": cum_avg_ms,
                })
        except Exception:
            pass

    # ---------- decode + heartbeat ------------------------------------------
    def _decode_block(self, block: dict, raw, now: datetime) -> list[Sample]:
        samples: list[Sample] = []
        now_mono = time.monotonic()
        # Phase 9.1 — Enron Modbus addressing.
        #
        #  STANDARD (default, all of Modbus):
        #    1 address slot = 1 physical 16-bit register.
        #    rel = tag.address - block.start_address
        #    A float32 at logical address 7001 in a block starting at 7001
        #    is at byte offset 0; the next float32 at 7003 is at offset 4.
        #
        #  ENRON_HOLDING / ENRON_INPUT (Daniel SIM 2251, Emerson FB107 GC,
        #  Rosemount, ABB Totalflow, OMNI, most fiscal flow computers):
        #    1 address slot = 1 logical value of the tag's width.
        #    rel = (tag.address - block.start_address) * tag.register_count
        #    16 float32 mole-% values live at addresses 7001..7016 (16 slots,
        #    not 32). Byte offsets within the block-read response: 0, 4, 8,
        #    ..., 60. Wire-level Modbus PDU is unchanged — block.count stays
        #    in physical 16-bit registers, gateway/GC speak standard Modbus.
        is_enron = block.get("addressing_mode") in (
            "ENRON_HOLDING", "ENRON_INPUT",
        )
        for tag in self.tags_by_block.get(block["id"], []):
            rel_logical = tag["address"] - block["start_address"]
            if is_enron:
                rel = rel_logical * tag["register_count"]
            else:
                rel = rel_logical
            slice_len = tag["register_count"]
            tag_raw = raw[rel:rel + slice_len]

            try:
                value = decode_value(tag_raw, tag["data_type"], tag["byte_order"])
                if tag["data_type"] != "bool":
                    value = float(value) * tag["scale"] + tag["offset"]
                vd = float(value) if isinstance(value, (int, float, bool)) else None

                st = ST_READ_OK
                st_reason = "READ_OK"
                if tag.get("is_heartbeat") and tag.get("heartbeat_max_stale_sec"):
                    prev = self._heartbeat_state.get(tag["id"])
                    if prev is None:
                        self._heartbeat_state[tag["id"]] = (vd, now_mono)
                    else:
                        prev_value, prev_change_mono = prev
                        if vd != prev_value:
                            self._heartbeat_state[tag["id"]] = (vd, now_mono)
                        else:
                            stale_sec = now_mono - prev_change_mono
                            if stale_sec > tag["heartbeat_max_stale_sec"]:
                                st = ST_COMM_TIMEOUT
                                st_reason = f"HEARTBEAT_FROZEN ({int(stale_sec)}s)"

                samples.append(Sample(
                    tag_id=tag["id"], device_id=tag["device_id"],
                    register_block_id=block["id"], time=now,
                    value_double=vd, value_text=None,
                    st=st, st_reason=st_reason,
                ))
            except Exception as e:
                self.log.warning("Decode failed: %s @ %d: %s",
                                 tag["name"], tag["address"], e)
                samples.append(Sample(
                    tag_id=tag["id"], device_id=tag["device_id"],
                    register_block_id=block["id"], time=now,
                    value_double=None, value_text=None,
                    st=ST_DECODE_FAIL, st_reason="DECODE_FAIL",
                ))
        return samples

    def _failed_samples(
        self, block: dict, now: datetime, st: int, reason: str,
    ) -> list[Sample]:
        return [
            Sample(
                tag_id=t["id"], device_id=t["device_id"],
                register_block_id=block["id"], time=now,
                value_double=None, value_text=None,
                st=st, st_reason=reason,
            )
            for t in self.tags_by_block.get(block["id"], [])
        ]


# ============================================================================
# Hot-reload manager (Phase 3.5)
# ============================================================================

def _config_fingerprint(config: list[dict]) -> str:
    """Hash of polling-relevant fields across all devices. Phase 8.5 includes
    timeout/retry/reconnect + per-block scan intervals + channel transport.
    """
    relevant: list = []
    for c in config:
        d = c["device"]
        device_part = (
            d["id"], d["host"], d["port"], d["unit_id"],
            d["scan_interval_ms"],
            d["request_timeout_ms"], d["retry_count"],
            d["reconnect_initial_ms"], d["reconnect_max_ms"],
            d.get("channel_transport"),
        )
        blocks_part = tuple(
            (b["id"], b["function_code"], b["start_address"], b["count"],
             b.get("scan_interval_ms"))
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
    historian, stop_event: asyncio.Event, reload_interval: float = 10.0,
):
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
                            "Config changed (%s → %s) — rebuilding %d→%d "
                            "worker(s) [%d block(s), %d tag(s)]",
                            current_fp[:8], fp[:8],
                            len(current_workers), n_devices, n_blocks, n_tags,
                        )
                        for w in current_workers:
                            w.stop()
                        if current_tasks:
                            await asyncio.gather(*current_tasks,
                                                 return_exceptions=True)

                    current_workers = [
                        DeviceWorker(c["device"], c["blocks"],
                                     c["tags_by_block"], historian)
                        for c in config
                    ]
                    current_tasks = [
                        asyncio.create_task(w.run()) for w in current_workers
                    ]
                    current_fp = fp
        except Exception:
            mlog.exception("Worker manager tick failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=reload_interval)
            break
        except asyncio.TimeoutError:
            continue

    if current_workers:
        mlog.info("Shutdown: stopping %d worker(s) ...", len(current_workers))
        for w in current_workers:
            w.stop()
        if current_tasks:
            await asyncio.gather(*current_tasks, return_exceptions=True)
    mlog.info("Stopped.")


# ============================================================================
# Entry point
# ============================================================================

async def main():
    historian = HistorianWriter(engine)
    buffer_path = Path(os.environ.get("SF_BUFFER_PATH", "/data/sf_buffer.db"))
    log.info("Local store-and-forward buffer: %s", buffer_path)
    sf_buffer = LocalBuffer(buffer_path)
    backlog = sf_buffer.count()
    if backlog > 0:
        log.info("Resuming with %d sample(s) already in local buffer", backlog)

    buffered_historian = BufferedHistorianWriter(historian, sf_buffer)

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
            pass

    tasks = [
        asyncio.create_task(worker_manager(buffered_historian, stop_event)),
        asyncio.create_task(stale_detection_loop(stop_event)),
        asyncio.create_task(replay_loop(sf_buffer, historian, stop_event)),
        asyncio.create_task(buffer_status_loop(sf_buffer, stop_event)),
    ]
    log.info("Started worker manager + stale detection + replay + buffer status")
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted, exiting.")
        sys.exit(0)
