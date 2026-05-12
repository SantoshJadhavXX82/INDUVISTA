"""CV/ST status byte values per InduVista's status model.

The st column is a SMALLINT (0-255). Two generated columns derive from it:
  st_hex   — zero-padded uppercase, e.g. '80' for 128
  st_class — one of the four tier names below

Tier ranges:
  192-255 (0xC0-0xFF)  VALID_EXTENDED  — good with extras (limits, manual override, etc.)
  128-191 (0x80-0xBF)  VALID           — plain good
   64-127 (0x40-0x7F)  SUSPECT         — usable but uncertain (stale, etc.)
    0-63  (0x00-0x3F)  INVALID         — not usable

The free-form st_reason VARCHAR(32) captures the specific cause within a
tier, so workers can use the same numeric tier with different reasons.

NOTE: We REPURPOSE OPC DA's reserved 0x80-0xBF range as the VALID tier.
This is intentional and means InduVista st bytes are NOT OPC-DA-compatible
on the wire. Don't mix them with OPC DA quality bytes; convert at boundaries.

Phase 8.5 (Modbus hardening):
  Refined the INVALID tier into distinct numeric constants so the
  Diagnostics page can distinguish transport-level failures from
  slave-side errors from wire-level corruption. All four still fall under
  st_class='INVALID' for reports; the discriminator lives in st_reason
  and (for those who care) in the exact numeric st.
"""

# ---- VALID (128-191) ---------------------------------------------------------
ST_READ_OK = 128

# ---- SUSPECT (64-127) --------------------------------------------------------
ST_STALE = 64

# ---- INVALID (0-63) ----------------------------------------------------------

# The worker has never successfully read this tag.
ST_NEVER_READ = 0

# Transport-level failures: connection refused, request timeout, TCP RST.
# The device IS NOT REACHABLE.
ST_COMM_TIMEOUT = 4

# Read succeeded but raw bytes could not be decoded to the configured
# data_type. Distinct from COMM_TIMEOUT because the device IS reachable —
# config is suspect.
ST_DECODE_FAIL = 8

# Phase 8.5 — slave returned a Modbus exception response (FC + 0x80).
# Device IS reachable and ALIVE; it's refusing the request.
# st_reason carries the exception code (ILLEGAL_FUNCTION, etc.).
# Almost always indicates a config bug rather than a hardware fault.
ST_MODBUS_EXCEPTION = 12

# Phase 8.5 — wire-level corruption. CRC error, malformed response framing,
# unexpected MBAP transaction ID. Cabling/EMI issues or misconfigured serial.
ST_MODBUS_IO_ERROR = 16

# Phase 8.5 — channel.transport is rtu/serial but the worker is TCP-only.
# Fail loudly rather than silently trying TCP. CONFIG ERROR, not runtime.
ST_TRANSPORT_UNSUPPORTED = 20

# Phase 8.5 — retries were attempted and all failed. Distinguishes
# "single transient blip" from "device is consistently down".
ST_RETRY_EXHAUSTED = 24

# ---- Modbus exception code → reason string ---------------------------------
MODBUS_EXCEPTION_NAMES: dict[int, str] = {
    1:  "ILLEGAL_FUNCTION",
    2:  "ILLEGAL_DATA_ADDRESS",
    3:  "ILLEGAL_DATA_VALUE",
    4:  "SLAVE_DEVICE_FAILURE",
    5:  "ACKNOWLEDGE",
    6:  "SLAVE_DEVICE_BUSY",
    7:  "NEGATIVE_ACKNOWLEDGE",
    8:  "MEMORY_PARITY_ERROR",
    10: "GATEWAY_PATH_UNAVAILABLE",
    11: "GATEWAY_TARGET_NO_RESPONSE",
}
