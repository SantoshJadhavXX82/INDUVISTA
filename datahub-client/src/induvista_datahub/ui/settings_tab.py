"""Settings tab — INDUVISTA server URL + API key, saved to config.toml.

This is the most "functional" tab in OPC.2 because it actually reads
and writes the config file. The OPC connection editor + onboarding
wizard are deferred to OPC.5.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from induvista_datahub.config.manager import ConfigManager


log = logging.getLogger(__name__)


class SettingsTab(QWidget):
    def __init__(self, *, config_mgr: ConfigManager) -> None:
        super().__init__()
        self.config_mgr = config_mgr

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        # ── INDUVISTA backend ────────────────────────────────────────
        backend_box = QGroupBox("INDUVISTA Backend")
        backend_form = QFormLayout(backend_box)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("http://10.0.1.10:8000")
        backend_form.addRow("Server URL:", self.url_edit)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("inv_...   (paste from POST /api/admin/api-keys)")
        # Hide the API key visually like a password.
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        backend_form.addRow("API Key:", self.api_key_edit)

        # Reveal/hide toggle for the API key — operators need to confirm
        # they pasted it correctly. Toggling between echo modes is the
        # simplest way to give them a peek.
        self._show_key_btn = QPushButton("Show")
        self._show_key_btn.setCheckable(True)
        self._show_key_btn.setFixedWidth(70)
        self._show_key_btn.toggled.connect(self._toggle_key_visibility)
        backend_form.addRow("", self._show_key_btn)

        outer.addWidget(backend_box)

        # ── Push tuning ──────────────────────────────────────────────
        tuning_box = QGroupBox("Push tuning")
        tuning_form = QFormLayout(tuning_box)

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 300)
        self.interval_spin.setSuffix(" sec")
        tuning_form.addRow("Drain interval:", self.interval_spin)

        self.batch_spin = QSpinBox()
        self.batch_spin.setRange(1, 5000)
        self.batch_spin.setSuffix(" samples")
        tuning_form.addRow("Batch size:", self.batch_spin)

        outer.addWidget(tuning_box)

        # ── Save row ─────────────────────────────────────────────────
        save_row = QHBoxLayout()
        save_row.addStretch()
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888;")
        save_row.addWidget(self.status_label)

        self.save_btn = QPushButton("Save")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._save)
        save_row.addWidget(self.save_btn)
        outer.addLayout(save_row)

        outer.addStretch()

        # Populate from current config.
        self._load_into_widgets()

    # ── Helpers ──────────────────────────────────────────────────────

    def _load_into_widgets(self) -> None:
        cfg = self.config_mgr.current()
        self.url_edit.setText(cfg.server.url)
        self.api_key_edit.setText(cfg.server.api_key)
        self.interval_spin.setValue(int(cfg.server.push_interval_sec))
        self.batch_spin.setValue(cfg.server.batch_size)

    def _toggle_key_visibility(self, checked: bool) -> None:
        if checked:
            self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self._show_key_btn.setText("Hide")
        else:
            self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._show_key_btn.setText("Show")

    def _save(self) -> None:
        cfg = self.config_mgr.current()
        cfg.server.url = self.url_edit.text().strip().rstrip("/")
        cfg.server.api_key = self.api_key_edit.text().strip()
        cfg.server.push_interval_sec = float(self.interval_spin.value())
        cfg.server.batch_size = self.batch_spin.value()

        try:
            self.config_mgr.save(cfg)
        except OSError as e:
            log.exception("Failed to save config")
            QMessageBox.critical(
                self, "Save failed",
                f"Could not write config to disk:\n\n{e}",
            )
            return

        self.status_label.setText("Saved")
        # Clear the "Saved" hint after a moment so it doesn't linger.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2000, lambda: self.status_label.setText(""))
