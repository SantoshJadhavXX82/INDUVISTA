"""OPC DA client — talks to the 32-bit bridge subprocess.

OPC DA is COM-based. The main DataHub app is built 64-bit (to host
asyncua + httpx + PySide6 cleanly), but most legacy DA servers are
32-bit. So DA access goes through a small 32-bit Python subprocess
shipped alongside the main .exe (built separately in OPC.6) that
listens on a loopback socket and proxies COM calls.

Phase OPC.2: full stub. The IPC protocol design + the bridge program
both land in OPC.3.

Sketched IPC protocol (JSON-over-TCP on 127.0.0.1):

  Client → Bridge: {"cmd":"connect","prog_id":"Matrikon.OPC.Simulation.1","host":"localhost"}
  Bridge → Client: {"ok":true}
                     OR {"ok":false,"error":"CoCreateInstanceEx failed (0x80040154)"}

  Client → Bridge: {"cmd":"add_items","items":["Random.Real8","Bucket.Brigade.Real8"]}
  Bridge → Client: {"ok":true,"handles":[1,2]}

  Bridge → Client (unsolicited push on DataChange):
    {"event":"data","items":[
      {"handle":1,"value":3.14,"quality":192,"time":"2026-05-24T12:00:00.000Z"},
      ...
    ]}

The bridge being a separate program also gives us crash-isolation —
if a buggy OPC DA server SEGVs the bridge, the main DataHub UI keeps
running.
"""

from __future__ import annotations

import logging
import sys

from induvista_datahub.config.schema import OpcDaConnection
from induvista_datahub.opc.base import OpcReaderBase


log = logging.getLogger(__name__)


class DaReader(OpcReaderBase):
    """OPC DA reader — client side of the 32-bit bridge protocol.
    Currently a stub — see module docstring."""

    def __init__(self, conn: OpcDaConnection) -> None:
        self.conn = conn
        self._connected = False
        log.debug("DaReader created for %r (prog_id=%r host=%r)",
                  conn.name, conn.prog_id, conn.host)

    def connect(self) -> None:
        # In OPC.3, this will:
        # 1. Locate the bridge .exe (next to our own executable in production,
        #    or .venv site-packages in dev)
        # 2. Spawn it with --port=0 so the OS picks a free loopback port
        # 3. Read the bound port from the bridge's stdout
        # 4. Connect a socket and send {"cmd":"connect", ...}
        if sys.platform != "win32":
            log.warning(
                "OPC DA is Windows-only. DaReader(%r) on %s is a no-op.",
                self.conn.name, sys.platform,
            )
            raise NotImplementedError(
                "OPC DA requires Windows + 32-bit bridge subprocess; "
                "implemented in OPC.3."
            )
        raise NotImplementedError("DA bridge connect — implemented in OPC.3")

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def start_polling(self, node_ids: list[str], interval_sec: float) -> None:
        raise NotImplementedError("DA add_items / data callback — implemented in OPC.3")

    def stop(self) -> None:
        pass
