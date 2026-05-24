"""Tags tab — per-tag mapping table. Shows configured OPC node ↔
INDUVISTA tag_id mappings.

OPC.2: read-only table populated from config.tag_mappings.mappings.
The real interactive editor (with the OPC Browse tree-picker) lands
in OPC.5.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from induvista_datahub.config.manager import ConfigManager


class TagsTab(QWidget):
    COLUMNS = ["OPC Connection", "OPC Node / Item", "INDUVISTA tag_id"]

    def __init__(self, *, config_mgr: ConfigManager) -> None:
        super().__init__()
        self.config_mgr = config_mgr

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(8)

        outer.addWidget(QLabel("<b>Tag mappings</b>"))
        outer.addWidget(QLabel(
            "Each row maps an OPC node on one of your configured servers "
            "to a numeric INDUVISTA tag_id. Edit in config.toml for now; "
            "interactive editor lands in OPC.5."
        ))

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        outer.addWidget(self.table, stretch=1)

        self.refresh()

    def refresh(self) -> None:
        """Reload from the current config snapshot."""
        cfg = self.config_mgr.current()
        rows = cfg.tag_mappings.mappings
        self.table.setRowCount(len(rows))
        for i, m in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(m.connection))
            self.table.setItem(i, 1, QTableWidgetItem(m.node_id))
            item = QTableWidgetItem(str(m.induvista_tag_id))
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(i, 2, item)

        if not rows:
            # Show an empty-state in row 0 — single cell spanning all columns.
            self.table.setRowCount(1)
            cell = QTableWidgetItem(
                "No mappings configured. Add them under [[tag_mappings.mappings]] in config.toml."
            )
            cell.setForeground(Qt.GlobalColor.gray)
            cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(0, 0, cell)
            self.table.setSpan(0, 0, 1, len(self.COLUMNS))
