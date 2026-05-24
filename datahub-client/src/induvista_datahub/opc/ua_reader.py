"""OPC UA client — wraps asyncua.

Architecture:

  Main thread (Qt event loop)
       ▲
       │ Qt signals (samples_received, state_changed)
       │ delivered via QueuedConnection
       │
  Worker thread "ua-<name>"
       │ runs asyncio.new_event_loop()
       │ runs _main_coro() to completion (until stop)
       │
  Inside the asyncio loop:
       asyncua.Client (TCP socket to server)
       Subscription (server publishes data changes)
       DataChangeHandler (callback per change, emits Qt signal)

We use a plain threading.Thread, not QThread, because asyncio's
event loop doesn't compose with QThread's loop. Thread.join on
stop is cooperative — the worker watches an asyncio.Event and
unwinds cleanly when set.

Reconnect strategy:

  On any connection failure or disconnect, the worker enters the
  reconnect loop: sleep min_sec, retry; on failure double the
  delay up to max_sec. The wait is interruptible via the stop
  event so app shutdown is responsive.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from asyncua import Client, ua
from asyncua.common.subscription import DataChangeNotif

from induvista_datahub.config.schema import OpcUaConnection
from induvista_datahub.opc.base import OpcReaderBase, OpcSample


log = logging.getLogger(__name__)


# OPC UA StatusCode → INDUVISTA quality byte mapping.
# UA's StatusCode is a 32-bit value; the high 2 bits are the severity:
#   00 = Good      → 192
#   01 = Uncertain → 96
#   10 = Bad       → 0
def _ua_status_to_quality(status: ua.StatusCode | None) -> tuple[int, str | None]:
    """Return (quality_byte, reason). reason is None for good samples."""
    if status is None:
        return 192, None
    code = int(status.value) if hasattr(status, "value") else int(status)
    severity = (code >> 30) & 0x3
    if severity == 0:
        return 192, None
    if severity == 1:
        return 96, str(status)
    return 0, str(status)


class _SubscriptionHandler:
    """asyncua dispatches DataChangeNotifications to handler objects
    via this method name. Pure pass-through into the reader's
    accumulator + Qt signal emit."""

    def __init__(self, reader: "UaReader", node_id_by_handle: dict[int, str]) -> None:
        self._reader = reader
        self._node_id_by_handle = node_id_by_handle

    def datachange_notification(self, node, val, data) -> None:  # noqa: D401
        """Called from inside the asyncio loop (reader's thread).
        `node` is the asyncua Node; `val` is the new value; `data`
        carries timestamp + StatusCode."""
        try:
            node_id = str(node.nodeid.to_string())
            # data.monitored_item.Value.ServerTimestamp is the timestamp.
            mv = data.monitored_item.Value
            t = mv.SourceTimestamp or mv.ServerTimestamp or datetime.now(timezone.utc)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            status = mv.StatusCode if mv.StatusCode is not None else None
            quality, reason = _ua_status_to_quality(status)

            sample = OpcSample(
                connection_name=self._reader.name,
                node_id=node_id,
                time=t,
                value=val,
                quality=quality,
                quality_reason=reason,
            )
            # Emit on the Qt signal — Qt marshals cross-thread.
            self._reader.samples_received.emit([sample])
        except Exception:
            log.exception("[%s] datachange_notification failed", self._reader.name)


class UaReader(OpcReaderBase):
    """OPC UA reader. Owns one worker thread + one asyncio loop."""

    def __init__(self, conn: OpcUaConnection) -> None:
        super().__init__(name=conn.name)
        self.conn = conn
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._node_ids: list[str] = []
        self._started = False
        self._lock = threading.Lock()

    # ── Public API ───────────────────────────────────────────────────

    def start_polling(self, node_ids: list[str], interval_sec: float = 1.0) -> None:
        with self._lock:
            if self._started:
                log.warning("[%s] start_polling called twice; ignored", self.name)
                return
            self._started = True
            self._node_ids = list(node_ids)
            self._thread = threading.Thread(
                target=self._thread_main,
                name=f"ua-{self.name}",
                daemon=True,
            )
            self._thread.start()
            log.info(
                "[%s] UaReader started (endpoint=%s, %d nodes, interval=%dms)",
                self.name, self.conn.endpoint, len(self._node_ids),
                self.conn.publishing_interval_ms,
            )

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
        if self._loop is not None and self._stop_event is not None:
            # Set the stop event from outside the loop.
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            if self._thread.is_alive():
                log.warning("[%s] worker thread did not stop within 10s", self.name)
        self._set_state("stopped")

    # ── Worker thread entry ──────────────────────────────────────────

    def _thread_main(self) -> None:
        """Run the asyncio loop for this reader's lifetime."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop_event = asyncio.Event()
        try:
            self._loop.run_until_complete(self._main_coro())
        except Exception:
            log.exception("[%s] worker thread crashed", self.name)
        finally:
            try:
                self._loop.close()
            except Exception:
                pass
            log.debug("[%s] worker thread exiting", self.name)

    async def _main_coro(self) -> None:
        """Reconnect loop. Runs until stop_event is set."""
        backoff = self.conn.reconnect_min_sec
        while not self._stop_event.is_set():
            try:
                self._set_state("connecting")
                await self._connect_and_subscribe()
                # If we got here, the connection died gracefully —
                # reset backoff for the next retry.
                backoff = self.conn.reconnect_min_sec
            except asyncio.CancelledError:
                raise
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                # Phase OPC.3.a — log.exception adds the full traceback
                # to the log so cross-library failures (asyncua type
                # machinery on Python 3.14, etc.) can be diagnosed
                # without re-running with a debugger attached.
                log.exception("[%s] connection error: %s", self.name, err)
                self._set_state("error", err)

            if self._stop_event.is_set():
                break

            # Wait before reconnecting, but stay responsive to stop.
            self._set_state("reconnecting", f"retrying in {backoff:.1f}s")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                # If wait_for returned without TimeoutError, stop was set.
                break
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2.0, self.conn.reconnect_max_sec)

        self._set_state("stopped")

    async def _connect_and_subscribe(self) -> None:
        """One connect-and-run cycle. Returns when the connection is
        gracefully closed or the stop_event fires; raises on any
        connection error (caller handles reconnect)."""
        client = Client(url=self.conn.endpoint)
        client.session_timeout = 30000      # 30s — server can drop us if we vanish
        client.secure_channel_timeout = 30000

        # Auth — only set username/password if both provided.
        if self.conn.username:
            client.set_user(self.conn.username)
            if self.conn.password:
                client.set_password(self.conn.password)

        # Security policy. "None" is the default and means anonymous,
        # no encryption. Higher policies need certificates which we
        # don't manage in OPC.3 — to be added in OPC.5 onboarding.
        if self.conn.security_policy and self.conn.security_policy != "None":
            try:
                await client.set_security_string(
                    f"{self.conn.security_policy},SignAndEncrypt"
                )
            except Exception as e:
                log.warning(
                    "[%s] security policy %r failed to apply: %s — "
                    "falling back to None",
                    self.name, self.conn.security_policy, e,
                )

        async with client:
            self._set_state("connected")
            log.info("[%s] connected to %s", self.name, self.conn.endpoint)

            if not self._node_ids:
                # No subscriptions configured. Stay connected so the
                # status badge shows green, but warn loudly.
                log.warning(
                    "[%s] no tag mappings for this connection; "
                    "browse the server with UAExpert to find node_ids "
                    "and add them under [[tag_mappings.mappings]] in config.toml",
                    self.name,
                )
                # Hold the connection open until stop or server disconnect.
                await self._stop_event.wait()
                return

            # Create the subscription.
            handle_to_node: dict[int, str] = {}
            handler = _SubscriptionHandler(self, handle_to_node)
            sub = await client.create_subscription(
                self.conn.publishing_interval_ms, handler,
            )

            # Build asyncua Node objects, subscribe each. We do it one
            # at a time so a single bad NodeId doesn't poison the whole
            # batch — UA's batch subscribe is all-or-nothing.
            subscribed = 0
            for node_id in self._node_ids:
                try:
                    node = client.get_node(node_id)
                    handle = await sub.subscribe_data_change(node)
                    handle_to_node[handle] = node_id
                    subscribed += 1
                except Exception as e:
                    log.warning(
                        "[%s] failed to subscribe to %r: %s",
                        self.name, node_id, e,
                    )

            log.info(
                "[%s] subscription active (%d/%d nodes subscribed)",
                self.name, subscribed, len(self._node_ids),
            )

            # Phase OPC.3.a — when ANY subscription failed (usually the
            # operator typed a NodeId format that doesn't match this
            # server's address space), walk the Objects tree and log
            # every Variable we find. The user reads the log, copies
            # the real NodeId string into config.toml, restarts.
            if subscribed < len(self._node_ids):
                log.info(
                    "[%s] ── address space browse (run once for discovery) ──",
                    self.name,
                )
                await _browse_and_log_variables(client, self.name)
                log.info("[%s] ── end of browse ──", self.name)

            # Hold the connection open until stop.
            await self._stop_event.wait()
            log.info("[%s] stop event received; closing subscription", self.name)
            try:
                await sub.delete()
            except Exception:
                log.exception("[%s] error deleting subscription", self.name)


# ---------------------------------------------------------------------------
# Address space browser — discovery helper for first-time NodeId hunting
# ---------------------------------------------------------------------------

# Hard caps so a real plant server with thousands of nodes doesn't dump
# a 50 MB log file. For discovery against a simulator with ~14 tags
# these limits never trigger.
_MAX_VARIABLES_LOGGED = 200
_MAX_DEPTH = 4


async def _browse_and_log_variables(client, conn_name: str) -> None:
    """Walk the server's Objects folder, log every Variable's NodeId
    and BrowseName. Folders are descended into; method nodes / object
    types are skipped. Output is bounded by _MAX_VARIABLES_LOGGED so
    huge servers don't drown the log."""
    counter = {"variables": 0, "folders": 0}
    try:
        # All OPC UA servers expose i=85 (the Objects folder) as the
        # entry point for the user-visible address space.
        objects_node = client.get_node(ua.ObjectIds.ObjectsFolder)
        await _browse_recursive(objects_node, conn_name, 0, counter)
    except Exception as e:
        log.warning("[%s] browse failed: %s", conn_name, e)
        return
    log.info(
        "[%s] browse summary: %d folders walked, %d variables logged "
        "(cap %d). Copy a NodeId string above into config.toml under "
        "[[tag_mappings.mappings]] node_id = ...",
        conn_name, counter["folders"], counter["variables"], _MAX_VARIABLES_LOGGED,
    )


async def _browse_recursive(node, conn_name: str, depth: int, counter: dict) -> None:
    """Depth-first walk. Logs each Variable; recurses into Objects /
    Folders. Bails when caps are hit."""
    if depth >= _MAX_DEPTH:
        return
    if counter["variables"] >= _MAX_VARIABLES_LOGGED:
        return
    try:
        children = await node.get_children()
    except Exception:
        return
    indent = "  " * depth
    for child in children:
        if counter["variables"] >= _MAX_VARIABLES_LOGGED:
            log.info(
                "[%s] %s(... browse truncated at %d variables ...)",
                conn_name, indent, _MAX_VARIABLES_LOGGED,
            )
            return
        try:
            browse_name = await child.read_browse_name()
            node_class = await child.read_node_class()
            node_id_str = child.nodeid.to_string()
        except Exception:
            continue

        # Skip namespace 0 (the OPC UA standard nodes) — operators
        # never want to subscribe to those.
        if child.nodeid.NamespaceIndex == 0:
            continue

        if node_class == ua.NodeClass.Variable:
            log.info(
                "[%s] %s├─ Variable: %s   node_id = %r",
                conn_name, indent, browse_name.Name, node_id_str,
            )
            counter["variables"] += 1
        elif node_class in (ua.NodeClass.Object, ua.NodeClass.View):
            log.info(
                "[%s] %s├─ Folder:   %s",
                conn_name, indent, browse_name.Name,
            )
            counter["folders"] += 1
            await _browse_recursive(child, conn_name, depth + 1, counter)
