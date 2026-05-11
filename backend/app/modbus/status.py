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
"""

# ---- VALID (128-191) ---------------------------------------------------------
# Phase 1's default for a fresh successful read. Picked at the tier floor so
# VALID_EXTENDED stays available for "good with extras" (Phase 4+ limit
# tagging, manual overrides, etc.).
ST_READ_OK = 128

# ---- SUSPECT (64-127) --------------------------------------------------------
# The most recent good read for this tag is older than device.stale_after_sec.
# The value can still be useful (it was good when last polled), but the
# dashboard / report engine should flag it.
ST_STALE = 64

# ---- INVALID (0-63) ----------------------------------------------------------
# Device unreachable: connection refused, request timeout, or Modbus
# exception response from the slave (function code error, etc.).
ST_COMM_TIMEOUT = 0

# Read succeeded but raw bytes could not be decoded to the configured
# data_type (e.g., struct.error, length mismatch). Distinct from
# COMM_TIMEOUT because the device IS reachable — config is suspect.
ST_DECODE_FAIL = 8

# The worker has never successfully read this tag. Used to pre-populate
# latest_tag_values on first run, or after a tag's register_block_id changes.
ST_NEVER_READ = 0
