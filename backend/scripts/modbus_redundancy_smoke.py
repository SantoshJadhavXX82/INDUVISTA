"""
Modbus connection-redundancy smoke (Phase 9) — deterministic, no DB, no network.

Runs INSIDE the modbus worker container (it imports the real worker module and
exercises the real DeviceWorker code), with the pymodbus client monkeypatched by
a controllable fake. No pytest required:

    docker cp backend/scripts/modbus_redundancy_smoke.py svj_modbus_worker:/tmp/mr.py
    docker exec -e PYTHONPATH=/app svj_modbus_worker python /tmp/mr.py

What it proves:
  * endpoint list build: simplex=1, redundant+secondary=2, redundant w/o
    secondary=1 (defensive), computed/opc NULL host/port = empty (never dialed)
  * _active_unit_id: primary uses unit_id; backup uses secondary_unit_id when set,
    else falls back to unit_id
  * connect loop: primary-first; failover to backup when primary down; FAILBACK to
    primary on next reconnect once it recovers; backoff only when ALL endpoints fail
  * simplex unchanged (single endpoint, no failover)
"""
import os
import sys
import time
import asyncio
import logging

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg2://localhost:5432/induvista_test"
)
# In the container the app lives at /app; allow running from a repo checkout too.
for p in ("/app", os.getcwd(), os.path.join(os.getcwd(), "backend")):
    if p not in sys.path:
        sys.path.insert(0, p)

import app.workers.modbus_supervisor as mod  # noqa: E402

DeviceWorker = mod.DeviceWorker

logging.basicConfig(level=logging.INFO, format="    %(message)s")

_PASS = 0
_FAIL = 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


# --- controllable fake pymodbus client -------------------------------------
class FakeClient:
    live = set()  # set of (host, port) currently "reachable"

    def __init__(self, host=None, port=None, timeout=None):
        self.host = host
        self.port = port
        self.connected = False

    async def connect(self):
        self.connected = (self.host, self.port) in FakeClient.live
        return self.connected

    def close(self):
        self.connected = False


mod.AsyncModbusTcpClient = FakeClient


def make_device(**kw):
    d = dict(
        id=1, name="T", host="primary", port=502, unit_id=1,
        scan_interval_ms=1000, request_timeout_ms=1000, retry_count=1,
        reconnect_initial_ms=1000, reconnect_max_ms=30000,
        channel_transport="tcp", connection_mode="simplex",
        secondary_host=None, secondary_port=None, secondary_unit_id=None,
    )
    d.update(kw)
    return d


def worker(dev):
    w = DeviceWorker(dev, [], {}, None)
    w.log = logging.getLogger("smoke." + dev["name"])
    return w


async def _connect(w):
    return await w._ensure_connected_with_backoff()


def force_reconnect(w):
    """Simulate the live connection having dropped, clearing the backoff gate."""
    w.client = None
    w._next_connect_attempt_mono = 0.0


# ---------------------------------------------------------------------------
print("endpoint construction:")
check("simplex -> 1 endpoint",
      worker(make_device(name="SIMP"))._endpoints == [("primary", 502)])
check("redundant + secondary -> 2 endpoints (primary, backup)",
      worker(make_device(name="RED", connection_mode="redundant",
                         secondary_host="backup", secondary_port=502))._endpoints
      == [("primary", 502), ("backup", 502)])
check("redundant w/o secondary -> 1 endpoint (defensive)",
      worker(make_device(name="REDX", connection_mode="redundant"))._endpoints
      == [("primary", 502)])
check("computed/opc NULL host/port -> empty endpoints (never dialed)",
      worker(make_device(name="CALC", host=None, port=None))._endpoints == [])

print("active unit id:")
wu = worker(make_device(name="UID", connection_mode="redundant",
                        secondary_host="b", secondary_port=502,
                        unit_id=1, secondary_unit_id=7))
wu._active_ep_idx = 0
check("primary endpoint uses unit_id", wu._active_unit_id() == 1)
wu._active_ep_idx = 1
check("backup endpoint uses secondary_unit_id", wu._active_unit_id() == 7)
wu.device["secondary_unit_id"] = None
check("backup w/o secondary_unit_id -> falls back to unit_id",
      wu._active_unit_id() == 1)

print("failover / failback (redundant):")
wr = worker(make_device(name="FAIL", connection_mode="redundant",
                        secondary_host="backup", secondary_port=502))
FakeClient.live = {("primary", 502), ("backup", 502)}
r = asyncio.run(_connect(wr))
check("both up -> connect on PRIMARY",
      r is True and wr._active_ep_idx == 0 and wr.client and wr.client.connected)

FakeClient.live = {("backup", 502)}
force_reconnect(wr)
r = asyncio.run(_connect(wr))
check("primary down -> FAILOVER to backup",
      r is True and wr._active_ep_idx == 1 and wr.client.connected)

FakeClient.live = {("primary", 502), ("backup", 502)}
force_reconnect(wr)
r = asyncio.run(_connect(wr))
check("primary restored -> FAILBACK to primary on reconnect",
      r is True and wr._active_ep_idx == 0)

FakeClient.live = set()
force_reconnect(wr)
now = time.monotonic()
r = asyncio.run(_connect(wr))
check("all endpoints down -> no connect + backoff scheduled",
      r is False and wr.client is None and wr._next_connect_attempt_mono > now)

print("simplex (unchanged):")
ws = worker(make_device(name="SIMP2"))
FakeClient.live = {("primary", 502)}
ws._next_connect_attempt_mono = 0.0
ws.client = None
r = asyncio.run(_connect(ws))
check("simplex primary up -> connect", r is True and ws._active_ep_idx == 0)
FakeClient.live = set()
force_reconnect(ws)
r = asyncio.run(_connect(ws))
check("simplex primary down -> no connect (no backup)",
      r is False and ws.client is None)

print()
if _FAIL == 0:
    print(f"ALL PASS ({_PASS} checks)")
    sys.exit(0)
else:
    print(f"{_FAIL} FAILED, {_PASS} passed")
    sys.exit(1)
