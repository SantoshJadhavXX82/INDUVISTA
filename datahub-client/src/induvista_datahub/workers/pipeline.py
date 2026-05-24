"""Pipeline orchestrator — owns the OPC readers, exposes their
samples + state changes to the UI via Qt signals.

Phase OPC.3.a — UA only. Readers configured with `kind="da"` get a
warning + are skipped; OPC.3.b adds the DA bridge support.

Phase OPC.4 will wire samples_received → StoreForward.enqueue() so
samples are actually persisted and pushed. For OPC.3.a, samples are
logged then dropped on the floor — the goal is to prove the read
layer flows.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone

from PySide6.QtCore import QObject, Signal

from induvista_datahub.config.schema import AppConfig, OpcUaConnection
from induvista_datahub.ingest.store_forward import StoreForward
from induvista_datahub.opc.base import OpcReaderBase, OpcSample
from induvista_datahub.opc.ua_reader import UaReader


log = logging.getLogger(__name__)


class Pipeline(QObject):
    """Top-level orchestrator. One instance per app, created at startup."""

    # Forwarded reader state changes. (connection_name, state, detail)
    connection_state_changed = Signal(str, str, str)

    # Per-connection sample counter ticks. (connection_name, last_time_iso)
    sample_observed = Signal(str, str)

    def __init__(self, *, config: AppConfig, store: StoreForward) -> None:
        super().__init__()
        self.config = config
        self.store = store
        self._readers: list[OpcReaderBase] = []
        self._tag_id_by_key: dict[tuple[str, str], int] = {}

        # Build the (connection_name, node_id) → induvista_tag_id lookup
        # once. We use it in the sample handler to resolve the OPC node
        # back to an INDUVISTA tag_id. Unmapped samples log a WARN and
        # get dropped.
        for m in config.tag_mappings.mappings:
            key = (m.connection, m.node_id)
            self._tag_id_by_key[key] = m.induvista_tag_id

        # Per-connection lookup of which node_ids this connection
        # owns — needed when we tell the reader what to subscribe to.
        self._nodes_by_conn: dict[str, list[str]] = defaultdict(list)
        for m in config.tag_mappings.mappings:
            self._nodes_by_conn[m.connection].append(m.node_id)

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Build all readers from config, start each one."""
        if self._readers:
            log.warning("Pipeline.start() called twice; ignored")
            return

        n_ua = 0
        n_da = 0
        n_unknown = 0
        for conn in self.config.opc.connections:
            if isinstance(conn, OpcUaConnection):
                n_ua += 1
                reader = UaReader(conn)
                self._wire_reader(reader)
                self._readers.append(reader)
                node_ids = self._nodes_by_conn.get(conn.name, [])
                reader.start_polling(node_ids)
            elif conn.kind == "da":
                n_da += 1
                log.warning(
                    "[%s] OPC DA connection skipped — Phase OPC.3.a is UA "
                    "only; DA wires up in OPC.3.b (32-bit bridge subprocess)",
                    conn.name,
                )
            else:
                n_unknown += 1
                log.warning("Unknown OPC connection kind %r; skipped", getattr(conn, "kind", "?"))

        log.info(
            "Pipeline started: %d UA reader(s), %d DA skipped, %d unknown",
            n_ua, n_da, n_unknown,
        )

    def stop(self) -> None:
        """Stop all readers. Blocking — waits up to ~10s per reader."""
        log.info("Stopping pipeline (%d readers)", len(self._readers))
        for r in self._readers:
            try:
                r.stop()
            except Exception:
                log.exception("Error stopping reader %s", r.name)
        self._readers.clear()
        log.info("Pipeline stopped")

    # ── Wiring ────────────────────────────────────────────────────────

    def _wire_reader(self, reader: OpcReaderBase) -> None:
        """Connect a reader's signals to our handlers. Qt's queued
        connection makes the cross-thread delivery safe."""
        reader.samples_received.connect(self._on_samples)
        reader.state_changed.connect(self._on_state)

    # ── Signal handlers (run on main thread) ──────────────────────────

    def _on_samples(self, samples: list[OpcSample]) -> None:
        """Called when a reader hands us a batch of samples.

        Phase OPC.3.a: log and drop. Phase OPC.4: resolve to tag_id and
        enqueue into StoreForward.
        """
        if not samples:
            return
        latest_time = max(s.time for s in samples).isoformat()
        for s in samples:
            tag_id = self._tag_id_by_key.get((s.connection_name, s.node_id))
            if tag_id is None:
                # Sample we didn't ask for. Could happen if subscribe
                # returned a node_id with a different canonical form
                # than what the user typed — log so they can fix the
                # config.
                log.debug(
                    "[%s] sample for unmapped node %r — dropping "
                    "(value=%r quality=%d)",
                    s.connection_name, s.node_id, s.value, s.quality,
                )
                continue
            log.info(
                "[%s] sample tag_id=%d node=%s value=%r st=%d%s",
                s.connection_name, tag_id, s.node_id, s.value, s.quality,
                f" reason={s.quality_reason!r}" if s.quality_reason else "",
            )
        # Surface to UI — one signal per batch is enough for the status
        # counters; we don't need every sample.
        self.sample_observed.emit(samples[0].connection_name, latest_time)

    def _on_state(self, connection_name: str, state: str, detail: str) -> None:
        log.info(
            "[%s] state → %s%s",
            connection_name, state,
            f" ({detail})" if detail else "",
        )
        self.connection_state_changed.emit(connection_name, state, detail)
