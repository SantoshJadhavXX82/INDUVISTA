"""Shared pytest fixtures for InduVista backend tests.

Test layers (selectable via pytest markers):

  unit         — pure-function tests, no I/O. Run in <2 seconds.
                 Decoder, byte-order math, address-offset math, scaling.
  integration  — exercise the worker against modbus_simulator OR the live
                 backend over HTTP. Need either modbus_simulator at
                 SIMULATOR_HOST:SIMULATOR_PORT or the backend at
                 BACKEND_BASE_URL.
  e2e          — full stack with backend + worker + simulator + PG.
                 Slowest, runs in CI only. ~2 minutes.

Default test run = unit only:
    pytest tests/ -m unit

Run all layers (when simulator and backend are up):
    pytest tests/

Skip integration cleanly if dependencies are unreachable.
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
        "integration: requires modbus_simulator at SIMULATOR_HOST:SIMULATOR_PORT, "
        "or live backend at BACKEND_BASE_URL",
    )
    config.addinivalue_line(
        "markers",
        "e2e: requires full docker-compose stack (backend + worker + postgres)",
    )
    config.addinivalue_line(
        "markers",
        "opc: requires live OPC UA server (e.g. Kepware) and an OPC source "
        "configured in the backend",
    )
    config.addinivalue_line(
        "markers",
        "slow: tests that take >10 seconds. Skipped by default; run with `-m slow` "
        "or `pytest --runslow` to include.",
    )
    config.addinivalue_line(
        "markers",
        "smoke: live-backend integration smoke tests (auto-skip if unreachable). "
        "Self-seeding and self-cleaning; safe to run against a running stack.",
    )


# ---------------------------------------------------------------------------
# Modbus simulator reachability check — gates integration tests
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
    """Skip the test cleanly if the modbus simulator isn't reachable."""
    if not _simulator_reachable(SIMULATOR_HOST, SIMULATOR_PORT):
        pytest.skip(
            f"modbus_simulator unreachable at {SIMULATOR_HOST}:{SIMULATOR_PORT} "
            "— start it with `docker compose up -d modbus_simulator_1`",
        )
    return (SIMULATOR_HOST, SIMULATOR_PORT)


# ---------------------------------------------------------------------------
# Backend HTTP reachability — gates HTTP-based integration tests
# ---------------------------------------------------------------------------

BACKEND_BASE_URL = os.environ.get("BACKEND_BASE_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def http_client():
    """Session-scoped httpx.Client pointed at the live backend.

    Auto-skips every integration test depending on it if the backend
    isn't reachable. Configure with `BACKEND_BASE_URL` env var; default
    is http://localhost:8000.

    Retries the initial probe a few times — if the container was busy
    (mid-restart, HMR-triggered reload, slow first request after start),
    a single failed probe would otherwise skip the whole session.
    """
    try:
        import httpx
    except ImportError:
        pytest.skip("httpx not installed - `pip install httpx`")

    client = httpx.Client(base_url=BACKEND_BASE_URL, timeout=30.0)

    import time
    last_error = None
    PROBE_ATTEMPTS = 5
    PROBE_BACKOFF_SEC = 2.0
    for attempt in range(1, PROBE_ATTEMPTS + 1):
        try:
            r = client.get("/api/opc-sources", timeout=10.0)
            if r.status_code < 500:
                # Backend responsive — proceed.
                break
            last_error = f"got {r.status_code} from /api/opc-sources"
        except Exception as e:  # httpx.ConnectError, ReadTimeout, etc.
            last_error = f"{type(e).__name__}: {e}"
        # Don't sleep after the final attempt — straight to skip.
        if attempt < PROBE_ATTEMPTS:
            time.sleep(PROBE_BACKOFF_SEC)
    else:
        # Loop exhausted all attempts without break — skip the session.
        client.close()
        pytest.skip(
            f"backend unreachable at {BACKEND_BASE_URL} after "
            f"{PROBE_ATTEMPTS} attempts: {last_error}. "
            "Start it with `docker compose up -d backend`",
        )

    yield client
    client.close()


# ---------------------------------------------------------------------------
# OPC test source — gates OPC integration tests
# ---------------------------------------------------------------------------

# The tests target a real Kepware OPC UA server. They expect a source
# named "KEPWARE_OPC_UA_02" to exist in the backend, pointing at the
# Kepware endpoint, with at least one mapping already registered (so the
# source is in subscription-active state).
#
# In a clean CI environment you'd override these via env vars OR have
# a setup script create the source — but for now we depend on the
# manually-configured KEPWARE_OPC_UA_02 from the OPC-web.2.1 work.

OPC_SOURCE_NAME = os.environ.get("OPC_TEST_SOURCE_NAME", "KEPWARE_OPC_UA_02")
OPC_TEST_PARENT_NODE_ID = os.environ.get(
    "OPC_TEST_PARENT_NODE_ID",
    "ns=2;s=CONDENSATE1.FLC1.MTR1",
)


@pytest.fixture(scope="session")
def opc_kepware_source(http_client):
    """Look up the test OPC source by name. Skip if missing.

    Returns the source dict with id, name, endpoint, etc. Tests use the
    `id` field to construct URLs like /api/opc-sources/{id}/browse.
    """
    r = http_client.get("/api/opc-sources")
    r.raise_for_status()
    sources = r.json()

    matching = [s for s in sources if s.get("name") == OPC_SOURCE_NAME]
    if not matching:
        names = sorted(s.get("name", "<noname>") for s in sources)
        pytest.skip(
            f"OPC source named {OPC_SOURCE_NAME!r} not found. "
            f"Found: {names}. "
            f"Either create it manually pointing at Kepware, "
            f"or set OPC_TEST_SOURCE_NAME to an existing source.",
        )

    return matching[0]
