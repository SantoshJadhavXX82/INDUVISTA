"""Shared pytest fixtures for InduVista backend tests.

Test layers (selectable via pytest markers):

  unit         — pure-function tests, no I/O. Run in <2 seconds.
                 Decoder, byte-order math, address-offset math, scaling.
  integration  — exercise the worker against modbus_simulator. Need
                 the simulator service reachable on host:5020. ~30s.
  e2e          — full stack with backend + worker + simulator + PG.
                 Slowest, runs in CI only. ~2 minutes.

Default test run = unit only:
    pytest tests/ -m unit

Run all layers (when simulator is up):
    pytest tests/

Skip integration cleanly if simulator unreachable.
"""
from __future__ import annotations

import os
import socket
import pytest


# ---------------------------------------------------------------------------
# Marker registration — keeps pytest from warning about unknown marks
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "unit: pure-function tests, no I/O dependency",
    )
    config.addinivalue_line(
        "markers",
        "integration: requires modbus_simulator at SIMULATOR_HOST:SIMULATOR_PORT",
    )
    config.addinivalue_line(
        "markers",
        "e2e: requires full docker-compose stack (backend + worker + postgres)",
    )


# ---------------------------------------------------------------------------
# Simulator reachability check — gates integration tests
# ---------------------------------------------------------------------------

SIMULATOR_HOST = os.environ.get("SIMULATOR_HOST", "modbus_simulator_1")
SIMULATOR_PORT = int(os.environ.get("SIMULATOR_PORT", "5020"))


def _simulator_reachable(host: str, port: int, timeout_sec: float = 1.0) -> bool:
    """Quick TCP probe — non-blocking decision for whether to skip integration."""
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True
    except (OSError, socket.timeout):
        return False


@pytest.fixture(scope="session")
def simulator_endpoint():
    """Skip the test cleanly if the simulator isn't reachable.

    Returns (host, port) so integration tests can connect via pymodbus.
    """
    if not _simulator_reachable(SIMULATOR_HOST, SIMULATOR_PORT):
        pytest.skip(
            f"modbus_simulator unreachable at {SIMULATOR_HOST}:{SIMULATOR_PORT} "
            "— start it with `docker compose up -d modbus_simulator_1`",
        )
    return (SIMULATOR_HOST, SIMULATOR_PORT)
