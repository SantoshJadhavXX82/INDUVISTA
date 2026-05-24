"""QApplication bootstrap — sets up logging, loads config, opens the
main window, starts the OPC pipeline, runs the Qt event loop.

Kept separate from __main__.py so test code can call `run()` if
needed without going through the script entry, and so PyInstaller
(OPC.6) has a clean import target.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from induvista_datahub import __version__
from induvista_datahub.config.manager import ConfigManager
from induvista_datahub.core.logging_setup import setup_logging
from induvista_datahub.core.paths import ensure_data_dirs
from induvista_datahub.ingest.store_forward import StoreForward
from induvista_datahub.ui.main_window import MainWindow
from induvista_datahub.workers.pipeline import Pipeline


log = logging.getLogger(__name__)


ORG_NAME = "InduVista"
APP_NAME = "DataHub"


def run() -> int:
    """Build the app, show the window, start the pipeline, run the
    event loop. Returns the loop's exit code."""
    paths = ensure_data_dirs()
    setup_logging(paths.logs_dir)
    log.info("Starting InduVista DataHub v%s", __version__)
    log.info("Data dir: %s", paths.data_dir)

    config_mgr = ConfigManager(paths.config_path)
    config = config_mgr.load_or_init()
    log.info(
        "Config loaded (server.url=%r, %d OPC connections, %d tag mappings)",
        config.server.url, len(config.opc.connections),
        len(config.tag_mappings.mappings),
    )

    store = StoreForward(paths.store_forward_path)
    store.initialize()
    log.info("Store-and-forward initialized; %d samples pending", store.pending_count())

    # Phase OPC.3.a — build the orchestrator (UA readers only).
    # The pipeline doesn't start its readers until pipeline.start()
    # is called below, so it's safe to construct before QApplication.
    pipeline = Pipeline(config=config, store=store)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationVersion(__version__)

    screen = app.primaryScreen()
    if screen is not None:
        log.info(
            "Primary screen: %s, geometry=%s, DPR=%.2f",
            screen.name(), screen.geometry().getRect(),
            screen.devicePixelRatio(),
        )
    else:
        log.warning("QApplication.primaryScreen() is None — running headless!")

    window = MainWindow(config_mgr=config_mgr, store=store, pipeline=pipeline)

    # Center the window on the primary screen — guards against
    # multi-monitor edge cases where Qt picks a negative-coord default.
    if screen is not None:
        sg = screen.geometry()
        x = sg.x() + (sg.width()  - 900) // 2
        y = sg.y() + (sg.height() - 600) // 2
        window.move(max(sg.x(), x), max(sg.y(), y))

    window.show()
    window.raise_()
    window.activateWindow()
    log.info(
        "MainWindow geometry after show: %s, isVisible=%s",
        window.geometry().getRect(), window.isVisible(),
    )

    # Second raise after 250ms — handles compositors that briefly
    # cover newly-created windows.
    def _force_front() -> None:
        window.raise_()
        window.activateWindow()
    QTimer.singleShot(250, _force_front)

    # Start OPC readers AFTER the window is visible — any connection
    # failures surface in the StatusTab while the user is watching.
    pipeline.start()

    log.info("Entering event loop")
    rc = app.exec()
    log.info("Event loop exited with code %d", rc)
    return rc
