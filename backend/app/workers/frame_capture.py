"""Phase 7 Batch 2 — Frame Inspector (B1) capture-side helper.

Pushes one TX + one RX frame per block read into a Valkey list capped at
200 entries per device. Capture is gated by a per-device flag (default OFF)
so this code path is no-op in normal operation.

The split between worker and API: the worker pushes frames; the API reads
them. Valkey is the shared message bus. Both sides use this module so the
encoding/decoding stays consistent.

All Valkey interactions are failure-safe: if Valkey is unreachable, capture
silently no-ops rather than breaking polling. This is by design — frame
capture is a diagnostic aid, not a critical path.
"""
from __future__ import annotations

import json
import os
import struct
from datetime import datetime, timezone
from typing import Any

import redis

# Module-level Redis client (Valkey is wire-compatible). Initialized lazily
# because the worker may import this module before Valkey is up.
_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        host = os.environ.get("VALKEY_HOST", "valkey")
        port = int(os.environ.get("VALKEY_PORT", "6379"))
        _client = redis.Redis(
            host=host, port=port, decode_responses=True,
            socket_connect_timeout=2, socket_timeout=2,
        )
    return _client


# Module-level transaction-id sequence — wraps at 65536 to match the MBAP
# header's 16-bit transaction-ID field. Paired TX/RX get the same value.
_txn_seq = 0


def _next_txn_id() -> int:
    global _txn_seq
    _txn_seq = (_txn_seq + 1) % 65536
    return _txn_seq


# ---------------------------------------------------------------------------
# Capture state (per-device flag)
# ---------------------------------------------------------------------------

def is_capture_enabled(device_id: int) -> bool:
    """Cheap check used on every poll. Failure-safe."""
    try:
        return _get_client().get(f"device_capture:{device_id}") == "1"
    except Exception:
        return False


def set_capture(device_id: int, enabled: bool) -> None:
    """Toggle capture for a device. Clearing also flushes any existing
    frames so the UI doesn't show stale captures from previous sessions."""
    c = _get_client()
    if enabled:
        c.set(f"device_capture:{device_id}", "1")
    else:
        c.delete(f"device_capture:{device_id}")
        c.delete(f"device_frames:{device_id}")


def get_capture_state(device_id: int) -> bool:
    return is_capture_enabled(device_id)


# ---------------------------------------------------------------------------
# Frame storage (capped ring buffer in Valkey)
# ---------------------------------------------------------------------------

def push_frame(device_id: int, frame: dict) -> None:
    """LPUSH + LTRIM to maintain a 200-element ring buffer. Failure-safe."""
    try:
        c = _get_client()
        pipe = c.pipeline()
        pipe.lpush(f"device_frames:{device_id}", json.dumps(frame))
        pipe.ltrim(f"device_frames:{device_id}", 0, 199)
        pipe.execute()
    except Exception:
        pass  # never let frame capture break polling


def get_frames(device_id: int, limit: int = 200) -> list[dict]:
    """Return frames newest-first."""
    try:
        items = _get_client().lrange(f"device_frames:{device_id}", 0, limit - 1)
        return [json.loads(x) for x in items]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Frame byte-encoding helpers — reconstruct what would go on the wire
# ---------------------------------------------------------------------------
#
# pymodbus doesn't expose the actual TX/RX byte stream cleanly, so we
# reconstruct it from the FC + parameters + response data. The bytes
# match what a Modbus TCP master/slave would emit — engineers reading
# the Frame Inspector see the same hex they'd see in Wireshark.

def encode_request_frame(unit_id: int, fc: int, address: int, count: int, txn_id: int) -> bytes:
    """MBAP + PDU for a read request (FC 1/2/3/4). 12 bytes."""
    mbap = struct.pack(">HHHB", txn_id, 0, 6, unit_id)
    pdu = struct.pack(">BHH", fc, address, count)
    return mbap + pdu


def encode_response_frame_registers(unit_id: int, fc: int, registers: list[int], txn_id: int) -> bytes:
    """MBAP + PDU for FC3/FC4 responses."""
    byte_count = len(registers) * 2
    data = b"".join(int(reg).to_bytes(2, "big", signed=False) for reg in registers)
    pdu = struct.pack(">BB", fc, byte_count) + data
    mbap = struct.pack(">HHHB", txn_id, 0, 2 + len(pdu), unit_id)
    return mbap + pdu


def encode_response_frame_bits(unit_id: int, fc: int, bits: list[bool], txn_id: int) -> bytes:
    """MBAP + PDU for FC1/FC2 responses."""
    byte_count = (len(bits) + 7) // 8
    packed = bytearray(byte_count)
    for i, b in enumerate(bits):
        if b:
            packed[i // 8] |= 1 << (i % 8)
    pdu = struct.pack(">BB", fc, byte_count) + bytes(packed)
    mbap = struct.pack(">HHHB", txn_id, 0, 2 + len(pdu), unit_id)
    return mbap + pdu


def encode_exception_frame(unit_id: int, fc: int, exception_code: int, txn_id: int) -> bytes:
    """Modbus exception response: FC | 0x80, then 1-byte exception code."""
    pdu = struct.pack(">BB", fc | 0x80, exception_code)
    mbap = struct.pack(">HHHB", txn_id, 0, 3, unit_id)
    return mbap + pdu


def bytes_to_hex(b: bytes) -> str:
    return " ".join(f"{x:02x}" for x in b)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Single entry-point called from the worker after each block read
# ---------------------------------------------------------------------------

def capture_block_read(
    *,
    device_id: int,
    block: dict,
    unit_id: int,
    fc: int,
    start: int,
    count: int,
    response_data: Any,   # list[int] for FC3/4, list[bool] for FC1/2, None for error
    error: str | None,
    latency_ms: float,
) -> None:
    """Push the TX and RX frames for a single block read.

    Called from `_poll_block`. Cheap when capture is off (one Valkey GET).
    """
    if not is_capture_enabled(device_id):
        return

    txn_id = _next_txn_id()
    block_name = block.get("name", "block")

    # TX frame (what we asked for)
    tx_bytes = encode_request_frame(unit_id, fc, start, count, txn_id)
    tx_frame = {
        "seq": txn_id,
        "timestamp": _now_iso(),
        "direction": "tx",
        "function_code": fc,
        "address": start,
        "register_count": count,
        "unit_id": unit_id,
        "block_name": block_name,
        "transaction_id": txn_id,
        "hex_bytes": bytes_to_hex(tx_bytes),
        "byte_count": len(tx_bytes),
        "latency_ms": None,
        "error": None,
        "summary": f"FC{fc} @ {start}/{count}",
    }

    # RX frame (what we got back)
    if error:
        # We don't always know the modbus exception code from a generic
        # pymodbus error — use 0x02 (illegal data address) as a placeholder
        # since it's the most common cause of failures in commissioning.
        rx_bytes = encode_exception_frame(unit_id, fc, 0x02, txn_id)
        summary = f"⚠ {error[:50]}"
    elif fc in (1, 2):
        rx_bytes = encode_response_frame_bits(unit_id, fc, list(response_data or []), txn_id)
        summary = f"{count} bit{'s' if count != 1 else ''}"
    else:
        rx_bytes = encode_response_frame_registers(unit_id, fc, list(response_data or []), txn_id)
        summary = f"{count} register{'s' if count != 1 else ''}"

    rx_frame = {
        "seq": txn_id,
        "timestamp": _now_iso(),
        "direction": "rx",
        "function_code": fc,
        "address": start,
        "register_count": count,
        "unit_id": unit_id,
        "block_name": block_name,
        "transaction_id": txn_id,
        "hex_bytes": bytes_to_hex(rx_bytes),
        "byte_count": len(rx_bytes),
        "latency_ms": round(latency_ms, 2),
        "error": error,
        "summary": summary,
    }

    # Push RX first then TX so newest-first ordering keeps the pair together
    push_frame(device_id, tx_frame)
    push_frame(device_id, rx_frame)
