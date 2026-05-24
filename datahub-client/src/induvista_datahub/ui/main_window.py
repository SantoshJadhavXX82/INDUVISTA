"""Main window — QMainWindow with 3 tabs and a status bar.

The window owns the ConfigManager and StoreForward instances passed
in from app.py; the tabs receive whatever they need by constructor
injection so they're independently testable.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QLabel, QMainWindow, QStatusBar, QTabWidget,
)

from induvista_datahub import __version__
from induvista_datahub.config.manager import ConfigManager
from induvista_datahub.ingest.store_forward import StoreForward
from induvista_datahub.ui.settings_tab import SettingsTab
from induvista_datahub.ui.status_tab import StatusTab
from induvista_datahub.ui.tags_tab import TagsTab
from induvista_datahub.workers.pipeline import Pipeline


log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """The single top-level window. Tabbed layout, status bar."""

    def __init__(
        self,
        *,
        config_mgr: ConfigManager,
        store: StoreForward,
        pipeline: Pipeline | None = None,
    ) -> None:
        super().__init__()
        self.config_mgr = config_mgr
        self.store = store
        self.pipeline = pipeline

        self.setWindowTitle(f"InduVista DataHub  ·  v{__version__}")
        self.resize(900, 600)
        self.setMinimumSize(640, 400)

        # ── Tabs ─────────────────────────────────────────────────────
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)

        self.status_tab = StatusTab(
            store=self.store,
            config_mgr=self.config_mgr,
            pipeline=self.pipeline,
        )
        self.tags_tab = TagsTab(config_mgr=self.config_mgr)
        self.settings_tab = SettingsTab(config_mgr=self.config_mgr)

        self.tabs.addTab(self.status_tab, "Status")
        self.tabs.addTab(self.tags_tab, "Tags")
        self.tabs.addTab(self.settings_tab, "Settings")

        self.setCentralWidget(self.tabs)

        # ── Status bar ───────────────────────────────────────────────
        # Two permanent widgets: connection state (left-flowing label)
        # and pending-buffer count (right-side permanent). Tabs can
        # update these via signals if they need to in later phases.
        sb = QStatusBar()
        self.setStatusBar(sb)

        self._connection_label = QLabel("Disconnected")
        self._connection_label.setStyleSheet("color: #888;")
        sb.addWidget(self._connection_label)

        self._buffer_label = QLabel(f"{self.store.pending_count():,} samples buffered")
        sb.addPermanentWidget(self._buffer_label)

        # ── Menu bar ─────────────────────────────────────────────────
        # Minimal File / Help menu — gives the window a "real
        # application" feel without yet wiring anything substantive.
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = mb.addMenu("&Help")
        about_action = QAction("&About InduVista DataHub", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _show_about(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.about(
            self,
            "About InduVista DataHub",
            f"<h3>InduVista DataHub</h3>"
            f"<p>Version {__version__}</p>"
            f"<p>Edge collector for the INDUVISTA platform. "
            f"Reads OPC samples and pushes them to the INDUVISTA "
            f"backend over /api/ingest.</p>"
            f"<p>Phase OPC.2 — skeleton.</p>",
        )

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt naming
        log.info("Main window closing; shutting down")
        if self.pipeline is not None:
            try:
                self.pipeline.stop()
            except Exception:
                log.exception("Error stopping pipeline")
        try:
            self.store.close()
        except Exception:
            log.exception("Error closing store-forward")
        super().closeEvent(event)
