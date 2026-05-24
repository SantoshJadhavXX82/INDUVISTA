"""Status tab — connection health + sample counters.

Phase OPC.3.a: gets real per-connection state badges from the pipeline.
Each row in the OPC Connections box updates live:

  ● Disconnected    (gray)    initial state
  ● Connecting...   (yellow)  during handshake
  ● Connected       (green)   subscription active
  ● Reconnecting    (orange)  retrying after a drop
  ● Error           (red)     last attempt failed; will retry
  ● Stopped         (gray)    app is shutting down

Sample counters tick whenever the pipeline gets a batch from any
reader. They reset per app launch (persistent counters land with
the real push pipeline in OPC.4).
"""

from __future__ import annotations

import logging
from collections import defaultdict

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from induvista_datahub.config.manager import ConfigManager
from induvista_datahub.config.schema import OpcDaConnection, OpcUaConnection
from induvista_datahub.ingest.store_forward import StoreForward
from induvista_datahub.workers.pipeline import Pipeline


log = logging.getLogger(__name__)


# State → (display label, foreground color CSS).
_STATE_STYLE: dict[str, tuple[str, str]] = {
    "disconnected": ("Disconnected", "#888"),
    "connecting":   ("Connecting…",  "#c8a200"),
    "connected":    ("Connected",    "#1a7f37"),
    "reconnecting": ("Reconnecting", "#c47000"),
    "error":        ("Error",        "#cf222e"),
    "stopped":      ("Stopped",      "#888"),
}


class StatusTab(QWidget):
    """Connection health + sample counters.

    Built from `config.opc.connections` so the table size is fixed
    at construction time. Pipeline signals drive the live updates.
    """

    # Connection columns: Name, Protocol, Endpoint, State, Last sample,
    # Sample count.
    COLUMNS = ["Connection", "Protocol", "Endpoint", "State", "Last sample", "Samples"]

    def __init__(
        self,
        *,
        store: StoreForward,
        config_mgr: ConfigManager | None = None,
        pipeline: Pipeline | None = None,
    ) -> None:
        super().__init__()
        self.store = store
        self.config_mgr = config_mgr
        self.pipeline = pipeline

        self._row_by_conn: dict[str, int] = {}
        self._sample_count: dict[str, int] = defaultdict(int)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        # ── OPC Connections box ──────────────────────────────────────
        opc_box = QGroupBox("OPC Connections")
        opc_layout = QVBoxLayout(opc_box)

        self.conn_table = QTableWidget(0, len(self.COLUMNS))
        self.conn_table.setHorizontalHeaderLabels(self.COLUMNS)
        self.conn_table.verticalHeader().setVisible(False)
        self.conn_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.conn_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        # First three columns stretch, last three fit their content.
        h = self.conn_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        opc_layout.addWidget(self.conn_table)

        # Aggregate counts at the top of the OPC box.
        summary_row = QHBoxLayout()
        self.summary_label = QLabel()
        summary_row.addWidget(self.summary_label)
        summary_row.addStretch()
        opc_layout.insertLayout(0, summary_row)
        outer.addWidget(opc_box)

        # ── Ingest / push box ────────────────────────────────────────
        push_box = QGroupBox("Push to INDUVISTA")
        from PySide6.QtWidgets import QFormLayout
        push_form = QFormLayout(push_box)
        self.last_push_label = QLabel("Never")
        push_form.addRow("Last successful push:", self.last_push_label)
        self.pushed_lifetime_label = QLabel("0")
        push_form.addRow("Samples pushed (lifetime):", self.pushed_lifetime_label)
        self.pending_label = QLabel(f"{self.store.pending_count():,}")
        push_form.addRow("Pending in buffer:", self.pending_label)
        self.push_errors_label = QLabel("0")
        push_form.addRow("Push errors (last hour):", self.push_errors_label)
        outer.addWidget(push_box)

        outer.addStretch()

        note = QLabel(
            "Phase OPC.3.a — UA read layer live. Per-connection state + "
            "sample counts tick as data flows. Push counters wait for "
            "OPC.4 to wire enqueue → drain."
        )
        note.setWordWrap(True)
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setStyleSheet("color: #888; font-style: italic;")
        outer.addWidget(note)

        # Populate connections + wire to pipeline.
        self._build_connection_rows()
        if self.pipeline is not None:
            self.pipeline.connection_state_changed.connect(self._on_state_changed)
            self.pipeline.sample_observed.connect(self._on_sample_observed)

    # ── Setup ─────────────────────────────────────────────────────────

    def _build_connection_rows(self) -> None:
        if self.config_mgr is None:
            self.summary_label.setText("(no config loaded)")
            return
        cfg = self.config_mgr.current()
        conns = cfg.opc.connections
        self.conn_table.setRowCount(len(conns))

        n_ua = sum(1 for c in conns if isinstance(c, OpcUaConnection))
        n_da = sum(1 for c in conns if isinstance(c, OpcDaConnection))
        self.summary_label.setText(
            f"{len(conns)} configured · {n_ua} UA · {n_da} DA"
        )

        if not conns:
            self.conn_table.setRowCount(1)
            cell = QTableWidgetItem(
                "No OPC connections configured. Add them under "
                "[[opc.connections]] in config.toml."
            )
            cell.setForeground(Qt.GlobalColor.gray)
            cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.conn_table.setItem(0, 0, cell)
            self.conn_table.setSpan(0, 0, 1, len(self.COLUMNS))
            return

        for row, conn in enumerate(conns):
            self._row_by_conn[conn.name] = row
            if isinstance(conn, OpcUaConnection):
                proto = "UA"
                target = conn.endpoint
            else:
                proto = "DA"
                target = f"{conn.prog_id} @ {conn.host}"

            self.conn_table.setItem(row, 0, QTableWidgetItem(conn.name))
            self.conn_table.setItem(row, 1, QTableWidgetItem(proto))
            self.conn_table.setItem(row, 2, QTableWidgetItem(target))
            self.conn_table.setItem(row, 3, self._state_cell("disconnected"))
            self.conn_table.setItem(row, 4, QTableWidgetItem("—"))
            count_cell = QTableWidgetItem("0")
            count_cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.conn_table.setItem(row, 5, count_cell)

    def _state_cell(self, state: str) -> QTableWidgetItem:
        label, color = _STATE_STYLE.get(state, (state, "#888"))
        item = QTableWidgetItem(f"● {label}")
        item.setForeground(Qt.GlobalColor.darkGreen if state == "connected" else Qt.GlobalColor.gray)
        # CSS-style coloring isn't directly supported on QTableWidgetItem,
        # use QBrush via QColor.
        from PySide6.QtGui import QColor
        item.setForeground(QColor(color))
        font = QFont()
        font.setBold(state in ("connected", "error"))
        item.setFont(font)
        return item

    # ── Pipeline signal handlers ──────────────────────────────────────

    def _on_state_changed(self, connection_name: str, state: str, detail: str) -> None:
        row = self._row_by_conn.get(connection_name)
        if row is None:
            return
        self.conn_table.setItem(row, 3, self._state_cell(state))
        if detail:
            # Tooltip on the state cell carries the detail (e.g. error message).
            cell = self.conn_table.item(row, 3)
            if cell is not None:
                cell.setToolTip(detail)

    def _on_sample_observed(self, connection_name: str, last_time_iso: str) -> None:
        row = self._row_by_conn.get(connection_name)
        if row is None:
            return
        self._sample_count[connection_name] += 1
        # Trim the ISO timestamp to HH:MM:SS for readable display.
        short_time = last_time_iso[11:19] if "T" in last_time_iso else last_time_iso
        self.conn_table.setItem(row, 4, QTableWidgetItem(short_time))
        count_cell = QTableWidgetItem(f"{self._sample_count[connection_name]:,}")
        count_cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.conn_table.setItem(row, 5, count_cell)
        # Also tick the buffer counter in case future phases enqueue.
        self.pending_label.setText(f"{self.store.pending_count():,}")
