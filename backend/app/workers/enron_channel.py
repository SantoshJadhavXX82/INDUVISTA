"""Phase 9.1.1 — Enron Modbus channel.

Real Daniel SIM 2251 and similar fiscal flow computers deviate from standard
Modbus at the wire level: a FC03 response for N values returns

    byte_count = N × width_bytes + trailing_bytes

where trailing_bytes is typically 3 (observed on Daniel) but may be 0, 2, or
anything else depending on firmware. Pymodbus's strict framer rejects these
responses with "Unable to decode request" because standard Modbus expects
byte_count = 2 × count_of_16bit_registers.

This module bypasses pymodbus entirely. It uses raw asyncio sockets to:
  - Open a persistent TCP connection to the device (one socket per device,
    survives across scans, reconnects with exponential backoff on failure)
  - Build the Modbus TCP MBAP + PDU manually (12 bytes for FC03 read)
  - Read the response permissively (any byte_count >= N × width_bytes)
  - Take the first N × width_bytes of data, discard whatever trails
  - Return 2N or N or 4N "fake uint16 registers" that the existing decoder
    (_decode_block in modbus_supervisor.py) consumes unchanged

Zero pymodbus imports. Pure stdlib asyncio + struct.

Supports FC03 (Read Holding Registers) and FC04 (Read Input Registers).
Supports widths 2, 4, and 8 bytes (covers every InduVista data_type).

Design rationale recap (Phase 9.1.1):
  - Risk: byte_count formula varies across firmware  → solved by permissive
    parser (accept any trailing length)
  - Risk: TCP rate-limit if we open per scan        → solved by persistent
    socket (one open, kept alive, reconnect on failure)
  - Risk: pymodbus internals coupling                → solved by zero
    pymodbus import, raw asyncio only
"""
from __future__ import annotations

import asyncio
import logging
import struct
import time
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Modbus exception codes — same names pymodbus uses, kept local so this
# module stays pymodbus-free.
# ---------------------------------------------------------------------------

MODBUS_EXCEPTION_NAMES: dict[int, str] = {
    1: "ILLEGAL_FUNCTION",
    2: "ILLEGAL_DATA_ADDRESS",
    3: "ILLEGAL_DATA_VALUE",
    4: "SLAVE_DEVICE_FAILURE",
    5: "ACKNOWLEDGE",
    6: "SLAVE_DEVICE_BUSY",
    7: "NEGATIVE_ACKNOWLEDGE",
    8: "MEMORY_PARITY_ERROR",
    10: "GATEWAY_PATH_UNAVAILABLE",
    11: "GATEWAY_TARGET_NO_RESPONSE",
}


# ---------------------------------------------------------------------------
# Errors — distinct types so the caller can differentiate
# ---------------------------------------------------------------------------

class EnronError(Exception):
    """Base for Enron channel errors. Caller can catch this category."""


class EnronConnectError(EnronError):
    """TCP connection or initial handshake failed."""


class EnronTimeoutError(EnronError):
    """Read or response timed out."""


class EnronProtocolError(EnronError):
    """Response received but malformed (bad MBAP, FC mismatch,
    transaction-id mismatch, byte_count out of range, etc.)."""


class EnronSlaveException(EnronError):
    """The remote device returned a Modbus exception response (FC byte
    with high bit set). Carries the exception code and friendly name.
    """
    def __init__(self, fc: int, exception_code: int):
        self.function_code = fc
        self.exception_code = exception_code
        name = MODBUS_EXCEPTION_NAMES.get(
            exception_code, f"EXCEPTION_{exception_code}",
        )
        super().__init__(
            f"slave exception {exception_code} ({name}) on FC {fc & 0x7F}",
        )


# ---------------------------------------------------------------------------
# Parser — pure function, the unit-test centerpiece
# ---------------------------------------------------------------------------

def parse_enron_response(
    response: bytes,
    expected_transaction_id: int,
    expected_unit_id: int,
    expected_fc: int,
    expected_count: int,
    value_width_bytes: int,
) -> list[int]:
    """Parse a single MBAP-framed Modbus response permissively.

    Validates structural integrity (MBAP, transaction ID, FC echo) strictly,
    but accepts the Daniel Enron extension where:

        byte_count = N × value_width_bytes + trailing_bytes

    for any trailing_bytes >= 0. The first N × value_width_bytes of the
    data section are returned as a list of fake uint16 registers (so the
    existing decoder consumes them unchanged).

    Args:
        response: raw bytes from the wire. MUST include the full MBAP
            header + PDU.
        expected_transaction_id: the txn id we sent in the request.
        expected_unit_id: the slave id we addressed.
        expected_fc: 3 for FC03, 4 for FC04.
        expected_count: N — the number of logical values requested.
        value_width_bytes: 2 (uint16-class), 4 (uint32/float32-class),
            or 8 (uint64/float64-class).

    Returns:
        A list of (N × value_width_bytes / 2) uint16 values, big-endian
        packed. Same shape pymodbus's response.registers would have if the
        device spoke standard Modbus, so _decode_block needs no change.

    Raises:
        EnronProtocolError: response is structurally malformed.
        EnronSlaveException: device returned a Modbus exception code.
    """
    if value_width_bytes not in (2, 4, 8):
        raise ValueError(
            f"value_width_bytes must be 2, 4, or 8 (got {value_width_bytes})",
        )

    # MBAP header is exactly 7 bytes: transaction(2) protocol(2) length(2) unit(1)
    MBAP_LEN = 7
    if len(response) < MBAP_LEN + 2:
        raise EnronProtocolError(
            f"response too short: {len(response)} bytes "
            f"(need at least {MBAP_LEN + 2})",
        )

    txn_id, protocol_id, length_field, unit_id = struct.unpack(
        ">HHHB", response[:MBAP_LEN],
    )

    if txn_id != expected_transaction_id:
        raise EnronProtocolError(
            f"transaction id mismatch: expected {expected_transaction_id}, "
            f"got {txn_id}",
        )

    if protocol_id != 0:
        # protocol_id != 0 typically means we're talking to a gateway that
        # speaks RTU-over-TCP, not Modbus TCP. Caller should reconfigure
        # the channel's transport field.
        raise EnronProtocolError(
            f"protocol id {protocol_id} != 0 — gateway may be RTU-over-TCP",
        )

    if unit_id != expected_unit_id:
        raise EnronProtocolError(
            f"unit id mismatch: expected {expected_unit_id}, got {unit_id}",
        )

    # The MBAP length counts unit_id + PDU bytes; PDU itself starts at
    # MBAP_LEN and runs to MBAP_LEN + (length_field - 1).
    pdu_len_from_mbap = length_field - 1  # subtract the unit_id byte
    pdu_bytes = response[MBAP_LEN:]
    if len(pdu_bytes) < pdu_len_from_mbap:
        raise EnronProtocolError(
            f"PDU truncated: MBAP says {pdu_len_from_mbap} bytes, "
            f"got {len(pdu_bytes)}",
        )

    # First byte of PDU is the function code (possibly with exception bit)
    if len(pdu_bytes) < 1:
        raise EnronProtocolError("empty PDU")
    fc = pdu_bytes[0]

    # Exception response — high bit of FC set, 1 byte of exception code follows
    if fc & 0x80:
        base_fc = fc & 0x7F
        if base_fc != expected_fc:
            raise EnronProtocolError(
                f"exception response for unexpected FC: "
                f"expected {expected_fc}, got {base_fc}",
            )
        if len(pdu_bytes) < 2:
            raise EnronProtocolError("exception response missing code byte")
        raise EnronSlaveException(fc, pdu_bytes[1])

    if fc != expected_fc:
        raise EnronProtocolError(
            f"FC mismatch: expected {expected_fc}, got {fc}",
        )

    # Normal response: PDU is FC(1) + byte_count(1) + data(byte_count bytes)
    if len(pdu_bytes) < 2:
        raise EnronProtocolError("response missing byte_count")
    byte_count = pdu_bytes[1]

    required_data_bytes = expected_count * value_width_bytes
    if byte_count < required_data_bytes:
        raise EnronProtocolError(
            f"byte_count {byte_count} less than required "
            f"{required_data_bytes} ({expected_count} × {value_width_bytes})",
        )
    # Sanity ceiling — a real Modbus PDU's byte_count field is 1 byte, so it
    # cannot exceed 255 by definition. Anything > 250 in practice is
    # suspect; cap it cleanly rather than slicing past the buffer.
    if byte_count > 250:
        raise EnronProtocolError(
            f"byte_count {byte_count} exceeds Modbus max (250)",
        )

    data = pdu_bytes[2:2 + byte_count]
    if len(data) < byte_count:
        raise EnronProtocolError(
            f"data truncated: expected {byte_count} bytes, "
            f"got {len(data)}",
        )

    # Take only the first N × width bytes; discard whatever trails (Daniel's
    # 3-byte trailer, or any other firmware variant).
    payload = data[:required_data_bytes]

    # Unpack as big-endian uint16 list — same shape pymodbus would have given
    # us, so _decode_block consumes it without modification.
    n_uint16 = required_data_bytes // 2
    return list(struct.unpack(f">{n_uint16}H", payload))


# ---------------------------------------------------------------------------
# Request builder — also a pure function, easy to unit-test
# ---------------------------------------------------------------------------

def build_enron_request(
    transaction_id: int,
    unit_id: int,
    function_code: int,
    start_address: int,
    count: int,
) -> bytes:
    """Build the 12-byte Modbus TCP request frame.

    Format:
        MBAP: transaction(2) protocol(2) length(2) unit(1)   — 7 bytes
        PDU:  fc(1) start(2) count(2)                        — 5 bytes
        Total: 12 bytes

    Same wire format as pymodbus would produce. The device sees a normal
    Modbus request; the deviation is only in how it RESPONDS.
    """
    if function_code not in (3, 4):
        raise ValueError(
            f"function_code must be 3 (FC03) or 4 (FC04), got {function_code}",
        )
    if not (0 <= start_address <= 0xFFFF):
        raise ValueError(f"start_address out of range: {start_address}")
    if not (1 <= count <= 125):
        raise ValueError(f"count out of range: {count} (must be 1..125)")
    if not (0 <= transaction_id <= 0xFFFF):
        raise ValueError(f"transaction_id out of range: {transaction_id}")
    if not (0 <= unit_id <= 0xFF):
        raise ValueError(f"unit_id out of range: {unit_id}")

    # MBAP length field = 1 (unit_id) + 5 (PDU) = 6 bytes
    return struct.pack(
        ">HHHBBHH",
        transaction_id,  # transaction id
        0,               # protocol id (always 0 for Modbus TCP)
        6,               # length: unit + 5-byte PDU
        unit_id,
        function_code,
        start_address,
        count,
    )


# ---------------------------------------------------------------------------
# EnronChannel — the persistent connection
# ---------------------------------------------------------------------------

@dataclass
class EnronChannelStats:
    """Snapshot of channel state for diagnostics."""
    host: str
    port: int
    connected: bool
    reconnect_count: int
    last_reconnect_ts: Optional[float]
    last_error: Optional[str]
    request_count: int
    failed_request_count: int


class EnronChannel:
    """One persistent TCP connection per device for Enron-mode reads.

    Lifecycle:
      - Constructed lazily by DeviceWorker on first Enron block poll
      - .connect() called on demand inside .read_enron(); reconnects with
        exponential backoff on failure
      - .close() called from DeviceWorker.stop()

    Concurrency:
      - One asyncio.Lock per channel serializes requests so a slow poll
        doesn't race with a fast one over the same socket
      - Reconnect runs under the same lock, so callers are guaranteed
        coherent state

    Transaction IDs:
      - Local counter, increments per request, wraps at 0xFFFF
      - Used to validate responses arrive in order
    """

    def __init__(
        self,
        host: str,
        port: int,
        log: Optional[logging.Logger] = None,
        reconnect_initial_ms: int = 1000,
        reconnect_max_ms: int = 30000,
    ):
        self.host = host
        self.port = port
        self.log = (log or logging.getLogger(__name__)).getChild(
            f"enron[{host}:{port}]",
        )

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._lock = asyncio.Lock()

        # Backoff state — same shape as DeviceWorker's pymodbus reconnect.
        self._reconnect_initial_sec = reconnect_initial_ms / 1000.0
        self._reconnect_max_sec = reconnect_max_ms / 1000.0
        self._current_backoff_sec = self._reconnect_initial_sec
        self._next_connect_attempt_mono = 0.0

        # Transaction id counter.
        self._next_txn_id = 1

        # Stats.
        self._reconnect_count = 0
        self._last_reconnect_ts: Optional[float] = None
        self._last_error: Optional[str] = None
        self._request_count = 0
        self._failed_request_count = 0

    # ------------------------------------------------------------------ API

    async def read_enron(
        self,
        unit_id: int,
        function_code: int,
        start_address: int,
        count: int,
        value_width_bytes: int,
        request_timeout_s: float = 3.0,
    ) -> list[int]:
        """Issue a single Enron read. Returns fake uint16 registers.

        Reconnects automatically if the socket is closed. Caller should
        catch EnronError and apply per-block retry / failure handling.
        """
        async with self._lock:
            if not self._is_connected():
                if time.monotonic() < self._next_connect_attempt_mono:
                    raise EnronConnectError(
                        f"in connect backoff until "
                        f"{self._next_connect_attempt_mono - time.monotonic():.1f}s "
                        f"from now",
                    )
                await self._reconnect(request_timeout_s)

            self._request_count += 1
            txn_id = self._next_txn_id
            self._next_txn_id = (self._next_txn_id % 0xFFFF) + 1

            try:
                req = build_enron_request(
                    transaction_id=txn_id,
                    unit_id=unit_id,
                    function_code=function_code,
                    start_address=start_address,
                    count=count,
                )
                self._writer.write(req)
                await asyncio.wait_for(
                    self._writer.drain(),
                    timeout=request_timeout_s,
                )
                # Read the MBAP header first to learn how many bytes follow.
                header = await asyncio.wait_for(
                    self._reader.readexactly(7),
                    timeout=request_timeout_s,
                )
                _, _, length_field, _ = struct.unpack(">HHHB", header)
                # length_field counts unit_id (already read) + PDU bytes.
                pdu_bytes_to_read = length_field - 1
                if pdu_bytes_to_read < 1 or pdu_bytes_to_read > 253:
                    raise EnronProtocolError(
                        f"implausible MBAP length: {length_field}",
                    )
                pdu = await asyncio.wait_for(
                    self._reader.readexactly(pdu_bytes_to_read),
                    timeout=request_timeout_s,
                )
                return parse_enron_response(
                    response=header + pdu,
                    expected_transaction_id=txn_id,
                    expected_unit_id=unit_id,
                    expected_fc=function_code,
                    expected_count=count,
                    value_width_bytes=value_width_bytes,
                )
            except asyncio.TimeoutError as e:
                self._failed_request_count += 1
                self._last_error = "read timeout"
                # On timeout the socket is unreliable — close and let next
                # call reconnect.
                await self._close_locked()
                raise EnronTimeoutError(
                    f"read timeout after {request_timeout_s}s",
                ) from e
            except (asyncio.IncompleteReadError, ConnectionResetError, OSError) as e:
                self._failed_request_count += 1
                self._last_error = f"{type(e).__name__}: {e}"
                await self._close_locked()
                raise EnronConnectError(f"connection lost: {e}") from e
            except EnronError:
                self._failed_request_count += 1
                raise
            except Exception as e:
                self._failed_request_count += 1
                self._last_error = f"unexpected: {type(e).__name__}: {e}"
                raise

    async def close(self) -> None:
        """Close the channel. Idempotent."""
        async with self._lock:
            await self._close_locked()

    def stats(self) -> EnronChannelStats:
        """Non-blocking snapshot for diagnostics."""
        return EnronChannelStats(
            host=self.host,
            port=self.port,
            connected=self._is_connected(),
            reconnect_count=self._reconnect_count,
            last_reconnect_ts=self._last_reconnect_ts,
            last_error=self._last_error,
            request_count=self._request_count,
            failed_request_count=self._failed_request_count,
        )

    # ---------------------------------------------------------- internals

    def _is_connected(self) -> bool:
        if self._writer is None or self._reader is None:
            return False
        # If the underlying transport is closing or closed, treat as
        # disconnected so the next request triggers a reconnect.
        if self._writer.is_closing():
            return False
        return True

    async def _reconnect(self, connect_timeout_s: float) -> None:
        """Open a fresh TCP connection. Must be called under self._lock.

        On failure: apply exponential backoff and raise EnronConnectError.
        On success: reset backoff to initial.
        """
        await self._close_locked()
        try:
            self.log.info("connecting to %s:%d", self.host, self.port)
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=connect_timeout_s,
            )
        except asyncio.TimeoutError:
            self._last_error = f"connect timeout after {connect_timeout_s}s"
            self._schedule_next_attempt()
            raise EnronConnectError(self._last_error)
        except OSError as e:
            self._last_error = f"connect failed: {type(e).__name__}: {e}"
            self._schedule_next_attempt()
            raise EnronConnectError(self._last_error) from e

        # Connected — reset backoff
        self._reconnect_count += 1
        self._last_reconnect_ts = time.monotonic()
        self._current_backoff_sec = self._reconnect_initial_sec
        self._next_connect_attempt_mono = 0.0
        self.log.info(
            "connected (reconnect #%d)", self._reconnect_count,
        )

    def _schedule_next_attempt(self) -> None:
        self._next_connect_attempt_mono = (
            time.monotonic() + self._current_backoff_sec
        )
        self._current_backoff_sec = min(
            self._current_backoff_sec * 2,
            self._reconnect_max_sec,
        )
        self.log.info(
            "next connect attempt in %.1fs",
            self._next_connect_attempt_mono - time.monotonic(),
        )

    async def _close_locked(self) -> None:
        """Close the socket. Caller must hold self._lock."""
        if self._writer is not None:
            try:
                self._writer.close()
                await asyncio.wait_for(
                    self._writer.wait_closed(), timeout=1.0,
                )
            except (asyncio.TimeoutError, OSError, ConnectionResetError):
                pass
            except Exception as e:
                self.log.debug("close error (ignored): %s", e)
        self._reader = None
        self._writer = None
