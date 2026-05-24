"""Abstract base class for OPC readers.

Phase OPC.3 — refactored from a plain ABC to a QObject so concrete
implementations can emit Qt signals across thread boundaries. Both
UaReader and (forthcoming) DaReader run their work on worker threads;
Qt's queued-connection delivery makes the cross-thread signal emit
safe with no manual mutex work.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from PySide6.QtCore import QObject, Signal


# Possible state transitions a reader walks through during its lifetime.
ReaderState = Literal[
    "disconnected",
    "connecting",
    "connected",
    "reconnecting",
    "error",
    "stopped",
]


@dataclass(frozen=True)
class OpcSample:
    """One sample produced by an OPC reader.

    The pipeline translates `node_id` to an INDUVISTA tag_id via the
    configured tag mappings before handing the sample to the
    store-forward buffer.

    `quality` is the raw OPC quality byte from the server (UA) or a
    derived 0-255 mapping (DA). 192 = good, 64 = uncertain,
    0 = bad — matches INDUVISTA's tag_values.st convention.
    """
    connection_name: str
    node_id: str
    time: datetime               # UTC
    value: float | int | str | bool | None
    quality: int
    quality_reason: str | None = None


class OpcReaderBase(QObject):
    """Common contract every concrete reader implements.

    Lifecycle: __init__ → start_polling() → ... → stop()

    Signals fire from the reader's worker thread. Slots connected
    from the main thread receive them via QueuedConnection.
    """

    # Emitted whenever the reader gathers a batch of samples.
    # Batch size depends on protocol — UA hands us all changes in
    # one DataChangeNotification, DA hands us one OnDataChange per
    # group. Both come through as `list[OpcSample]`.
    samples_received = Signal(list)

    # Emitted whenever the connection state changes. Args:
    #   (connection_name: str, new_state: ReaderState, detail: str)
    # `detail` is empty for normal transitions, or carries an error
    # message for transitions to "error" or "reconnecting".
    state_changed = Signal(str, str, str)

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self._state: ReaderState = "disconnected"

    @property
    def state(self) -> ReaderState:
        return self._state

    def _set_state(self, new_state: ReaderState, detail: str = "") -> None:
        """Update internal state + emit the signal. Called by subclasses
        from their worker threads."""
        if new_state != self._state:
            self._state = new_state
            self.state_changed.emit(self.name, new_state, detail)

    @abstractmethod
    def start_polling(self, node_ids: list[str], interval_sec: float = 1.0) -> None:
        """Begin polling/subscribing to the given list of nodes. Idempotent —
        calling twice is a no-op if already started."""

    @abstractmethod
    def stop(self) -> None:
        """Halt the polling loop / cancel the subscription / close the
        connection. Safe to call multiple times."""
