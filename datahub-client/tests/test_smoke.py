"""Smoke tests for OPC.2 — verify the skeleton hangs together.

These tests do NOT require PySide6 to be installed; they import the
non-UI modules and exercise:

  - Config schema with defaults
  - Config TOML write + read roundtrip
  - Store-forward schema initialization
  - All non-Qt modules import without errors

The UI modules (anything under induvista_datahub.ui) require PySide6
and are tested in OPC.5 when the UI gets real interactive behavior.
"""

from __future__ import annotations

import tempfile
from pathlib import Path


def test_import_non_ui_modules() -> None:
    """Catch broken imports across the non-UI modules in one shot.

    Phase OPC.3 note: `opc.base` is now a QObject, so all `opc.*` and
    `workers.*` modules require PySide6 to import. If PySide6 isn't
    available (CI without GUI libs), we skip those imports — the
    user's venv always has PySide6 from pyproject.toml.
    """
    # PySide6-free imports first.
    import induvista_datahub                                  # noqa: F401
    import induvista_datahub.core.paths                       # noqa: F401
    import induvista_datahub.core.logging_setup               # noqa: F401
    import induvista_datahub.config.schema                    # noqa: F401
    import induvista_datahub.config.manager                   # noqa: F401
    import induvista_datahub.ingest.pusher                    # noqa: F401
    import induvista_datahub.ingest.store_forward             # noqa: F401

    # PySide6-dependent imports — gated.
    try:
        import PySide6                                        # noqa: F401
    except ImportError:
        return
    import induvista_datahub.opc.base                         # noqa: F401
    import induvista_datahub.opc.ua_reader                    # noqa: F401
    import induvista_datahub.opc.da_reader                    # noqa: F401
    import induvista_datahub.workers.pipeline                 # noqa: F401


def test_config_defaults() -> None:
    from induvista_datahub.config.schema import AppConfig
    cfg = AppConfig.with_defaults()
    assert cfg.server.url == "http://localhost:8000"
    assert cfg.server.api_key == ""
    assert cfg.server.push_interval_sec > 0
    assert cfg.server.batch_size > 0
    assert cfg.opc.connections == []
    assert cfg.tag_mappings.mappings == []
    assert cfg.logging.level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def test_config_save_and_reload_roundtrip() -> None:
    """Write defaults, mutate a field, save, reload — confirm the
    mutation survived a round trip through the TOML serializer."""
    from induvista_datahub.config.manager import ConfigManager
    from induvista_datahub.config.schema import AppConfig

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.toml"
        mgr = ConfigManager(path)

        cfg = mgr.load_or_init()
        assert path.exists(), "first load_or_init should write defaults to disk"

        cfg.server.url = "http://example.com:9000"
        cfg.server.api_key = "inv_abcd1234"
        cfg.server.batch_size = 750
        mgr.save(cfg)

        # New manager, same path — confirms the saved bytes parse cleanly.
        mgr2 = ConfigManager(path)
        reloaded = mgr2.load_or_init()
        assert reloaded.server.url == "http://example.com:9000"
        assert reloaded.server.api_key == "inv_abcd1234"
        assert reloaded.server.batch_size == 750


def test_config_with_opc_connections_roundtrip() -> None:
    """Array-of-tables (the OPC connection list) is the trickiest part
    of the TOML writer. Confirm UA + DA both survive the round trip."""
    from induvista_datahub.config.manager import ConfigManager
    from induvista_datahub.config.schema import (
        AppConfig, OpcDaConnection, OpcUaConnection,
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.toml"
        cfg = AppConfig.with_defaults()
        cfg.opc.connections = [
            OpcUaConnection(name="UA-Plant-A", endpoint="opc.tcp://10.0.1.50:4840"),
            OpcDaConnection(name="DA-Plant-B", prog_id="Matrikon.OPC.Simulation.1"),
        ]
        mgr = ConfigManager(path)
        mgr.save(cfg)

        reloaded = ConfigManager(path).load_or_init()
        assert len(reloaded.opc.connections) == 2
        ua, da = reloaded.opc.connections
        assert ua.kind == "ua" and ua.name == "UA-Plant-A"
        assert da.kind == "da" and da.prog_id == "Matrikon.OPC.Simulation.1"


def test_store_forward_initialize() -> None:
    """Initialize the SQLite buffer; verify the schema is in place
    and the pending count is zero."""
    from induvista_datahub.ingest.store_forward import StoreForward, SCHEMA_VERSION

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "store_forward.db"
        store = StoreForward(db_path)
        store.initialize()
        try:
            assert db_path.exists()
            assert store.pending_count() == 0
            assert store.get_metadata("schema_version") == str(SCHEMA_VERSION)
        finally:
            store.close()


def test_paths_resolution() -> None:
    """get_paths() returns a Paths bundle whose data_dir is under the
    appropriate OS-specific root."""
    from induvista_datahub.core.paths import get_paths

    paths = get_paths()
    # Sanity-check that all four expected attributes exist and are paths.
    assert paths.data_dir is not None
    assert paths.logs_dir.parent == paths.data_dir
    assert paths.config_path.parent == paths.data_dir
    assert paths.store_forward_path.parent == paths.data_dir
    assert paths.config_path.name == "config.toml"
    assert paths.store_forward_path.name == "store_forward.db"
