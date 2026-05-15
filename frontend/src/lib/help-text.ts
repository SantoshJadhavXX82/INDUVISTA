/**
 * Parameter help-text dictionary — Phase 8.4 (comprehensive coverage).
 *
 * Every visible configurable parameter in InduVista has an entry here.
 * Centralized to keep wording consistent and translation-ready (i18n later
 * just wraps these strings).
 *
 * Structure per entry:
 *   description — what it is + what it controls (1-2 sentences)
 *   example     — a concrete sample value (optional)
 *   impact      — where it shows up / what changes when you set it (optional)
 *
 * Usage in a component:
 *
 *   import { HelpTip } from "@/components/ui/help-tip";
 *   import { help } from "@/lib/help-text";
 *
 *   <Label>Engineering unit <HelpTip entry={help.tag.engineering_unit} /></Label>
 *
 * Style guide for entries:
 *   - Speak directly to a process / instrument engineer who knows Modbus
 *     but may not know InduVista's specifics. No marketing voice.
 *   - Prefer concrete examples over abstractions.
 *   - "Impact" should answer: "what changes downstream if I get this wrong?"
 */

export type HelpEntry = {
  description: string;
  example?: string;
  impact?: string;
};

type Section = Record<string, HelpEntry>;

export const help = {
  // =========================================================================
  // TAG (the most-used config — every tag in the system)
  // =========================================================================
  tag: {
    name: {
      description:
        "Unique identifier for this tag. Used everywhere the tag appears — dashboards, reports, exports, audit trails, historian.",
      example: "FC001_FlowRate, P101_DischargePress, T200_OutletTemp",
      impact:
        "Renaming a tag updates display everywhere; historical data stays intact (linked by tag_id, not name). Convention: prefix with device name for searchability.",
    } satisfies HelpEntry,

    description: {
      description:
        "Free-text human description. Shown in tooltips, the tag details panel, and reports.",
      example: "Discharge pressure transmitter (primary), 4-20mA from PT-101",
    } satisfies HelpEntry,

    data_type: {
      description:
        "How the raw register bytes are decoded into a number. Must match what the device actually publishes — wrong choice produces garbage values.",
      example:
        "float32 for analog readings, uint16 for status/counter, int32 for signed counters, bool for coils",
      impact:
        "Affects register_count (1 for 16-bit, 2 for 32-bit, 4 for 64-bit) and which byte-order options make sense. Change carefully — historian values stored under the old type may render oddly.",
    } satisfies HelpEntry,

    byte_order: {
      description:
        "Byte ordering for multi-register values. Modbus addresses each 16-bit register big-endian internally; this setting controls how they combine into 32/64-bit values. The most common source of 'numbers look weird' bugs.",
      example:
        "ABCD = big-endian (standard), CDAB = word-swapped (Daniel/Emerson typical), BADC = byte-swapped, DCBA = little-endian",
      impact:
        "Wrong byte order produces garbage floats. Use the Test-Read panel to verify against a known value. Once a tag has been polling, changing this re-interprets historical raw bytes incorrectly — fix and re-deploy carefully.",
    } satisfies HelpEntry,

    function_code: {
      description:
        "Modbus function code for the read. 1 = read coil (single bit), 2 = read discrete input (single bit, read-only), 3 = read holding register (16-bit), 4 = read input register (16-bit, read-only).",
      example:
        "3 for most analog values and counters, 1 for digital outputs, 2 for hardwired digital inputs",
      impact:
        "Combined with address, defines the actual register being read. Wrong FC → 'illegal function' or 'illegal address' exception from the device, or worse, silently reading the wrong space.",
    } satisfies HelpEntry,

    address: {
      description:
        "Register address within the device's address space. PDU-style (0-based) — not Modicon notation (40001, 30001).",
      example:
        "PDU 100 = Modicon 40101 for FC3; PDU 0 = Modicon 40001",
      impact:
        "Address + register_count must fall within a configured register block for the worker to read it. Off-by-one between PDU and Modicon is the second-most-common Modbus bug after byte order.",
    } satisfies HelpEntry,

    register_count: {
      description:
        "How many 16-bit registers this tag occupies. Set automatically when you pick a data type; only override for unusual packings.",
      example:
        "1 for int16/uint16/bool, 2 for int32/uint32/float32, 4 for int64/uint64/float64",
    } satisfies HelpEntry,

    engineering_unit: {
      description:
        "Unit of measure shown next to values everywhere. Pick from the global master (consistent across tags) or override with a custom value for one-off cases.",
      example: "kg/h, °C, bar, m³/h, mol%, μS/cm",
      impact:
        "Display-only — doesn't transform the value. Use scale/offset to convert between units (e.g. raw 235 → 23.5 °C). Master units are managed under Configuration → Engineering Units.",
    } satisfies HelpEntry,

    scale: {
      description:
        "Multiplier applied to the raw register value. Used to convert raw integer counts into engineering units.",
      example:
        "0.1 if the device sends temperature ×10 (raw 235 → display 23.5); 0.01 for some pressure transmitters",
      impact:
        "Display value = raw × scale + offset. Historian stores the converted (scaled) value. Wrong scale silently produces wrong numbers — no error, just bad data.",
    } satisfies HelpEntry,

    offset: {
      description:
        "Additive offset applied after scaling. Used for zero-point shifts and unit-system conversions.",
      example: "-273.15 to convert Kelvin to Celsius; sensor-specific calibration offset",
      impact:
        "Display value = raw × scale + offset. Order matters: scale first, then offset.",
    } satisfies HelpEntry,

    min_value: {
      description:
        "Lower clamp for validation. Values below this are stored but flagged SUSPECT in the status byte (ST).",
      example: "0 for a temperature that should never go negative; 4 for a 4-20mA loop reading in mA",
      impact:
        "Out-of-range samples show amber in the dashboard, count as suspect in reports, and surface in Data Gaps. Doesn't reject the value — just flags it.",
    } satisfies HelpEntry,

    max_value: {
      description:
        "Upper clamp for validation. Values above this are stored but flagged SUSPECT.",
      example:
        "100 for a percentage, 150 for a typical bar pressure, 20 for a 4-20mA loop in mA",
      impact:
        "Same as min_value — out-of-range samples surface as suspect quality in dashboards, reports, and Data Gaps.",
    } satisfies HelpEntry,

    enabled: {
      description:
        "When off, the worker stops polling this tag and no new samples are written. Historical data is preserved.",
      impact:
        "Toggle off for tags that are still defined but not actively used (e.g. equipment under maintenance, decommissioned sensors).",
    } satisfies HelpEntry,

    is_heartbeat: {
      description:
        "Watches the value for freeze. If the numeric value doesn't change for longer than the threshold, samples are marked HEARTBEAT_FROZEN — useful for detecting device task halts or stuck I/O.",
      example:
        "Enable on a device-side rolling counter, toggle bit, or 1Hz scan-rate tag that should always be changing.",
      impact:
        "Frozen heartbeat tags show suspect status in the Live Dashboard. Pair with min_value/max_value for the strongest validation of liveness + sanity.",
    } satisfies HelpEntry,

    heartbeat_max_stale_sec: {
      description:
        "How long a heartbeat tag may stay unchanged before being marked stale.",
      example: "3 seconds for a 1Hz toggle; 120 seconds for an end-of-minute counter",
      impact:
        "Rule of thumb: 2-3× the expected update interval. Too tight = false alarms on normal jitter; too loose = late detection of frozen tasks.",
    } satisfies HelpEntry,

    named_set: {
      description:
        "Translates raw integer values into human-readable text in dashboards and reports.",
      example: "MOTOR_STATE: 0 → Stopped, 1 → Starting, 2 → Running, …",
      impact:
        "Display only — CV remains the raw integer. Best for integer/boolean tags with discrete state semantics (motor states, alarm severities, valve positions).",
    } satisfies HelpEntry,

    groups: {
      description:
        "Logical classifications attached to this tag. Orthogonal to the polling structure — used for filtering, dashboards, and report scoping.",
      example: "Compressor-A, Hourly-Fiscal-Report, North-Plant",
      impact:
        "A tag can belong to any number of groups. Groups are managed under Configuration → Groups; you can also create new ones inline from this drawer.",
    } satisfies HelpEntry,
  } satisfies Section,

  // =========================================================================
  // CHANNEL (the transport layer — TCP socket or serial port)
  // =========================================================================
  channel: {
    name: {
      description:
        "Unique identifier for this transport. Shown in device assignments and diagnostics.",
      example: "PlantNet-TCP, RS485-Compressor-Loop, Daniel-Serial-A",
    } satisfies HelpEntry,

    transport: {
      description:
        "Whether this network uses TCP/IP (Ethernet) or a serial link (RS-232 / RS-485). Determines which other fields apply.",
      example: "TCP for modern PLCs, smart meters, gateways; RTU for legacy serial gas chromatographs",
      impact:
        "TCP shows host/port. RTU shows serial device / baud / parity / stop bits. ASCII is rarely used and mostly for legacy interop.",
    } satisfies HelpEntry,

    enabled: {
      description:
        "When off, no devices on this network will be polled. Useful for taking a comms link out of service for maintenance.",
      impact:
        "Disabling a network pauses every device that uses it. Workers stop scanning, no new samples, but historical data is preserved.",
    } satisfies HelpEntry,

    host: {
      description:
        "IP address or hostname of the TCP endpoint. Reachable from the InduVista host network.",
      example: "192.168.10.50, gateway.plant.local",
      impact:
        "Test reachability with ping or Frame Inspector before saving. Wrong host shows as connection-refused or timeout in diagnostics.",
    } satisfies HelpEntry,

    port: {
      description:
        "TCP port of the Modbus endpoint. Standard is 502 but many gateways and PLCs override.",
      example: "502 (standard), 1502 (often used by smart meters), 5020/5021 (this app's simulators)",
      impact:
        "Mismatch with the device's listening port produces immediate connection-refused errors visible in the Networks diagnostics page.",
    } satisfies HelpEntry,

    serial_device: {
      description:
        "OS-level serial device path. On Linux this is /dev/ttyUSB0 or similar; on Windows it's COM3, COM4, etc.",
      example: "/dev/ttyUSB0, /dev/ttyS0, COM3",
      impact:
        "Must be accessible by the InduVista container's user. Permission errors here look like 'device busy' in logs.",
    } satisfies HelpEntry,

    baud_rate: {
      description:
        "Serial line speed in bits per second. Must match the device's configuration exactly.",
      example: "9600 (most common for industrial), 19200, 38400, 57600, 115200",
      impact:
        "Mismatch causes garbled bytes that look like CRC errors. Check the device manual or DIP-switch settings before guessing.",
    } satisfies HelpEntry,

    parity: {
      description:
        "Serial parity check. None is most common for industrial; Even is used by some gas chromatographs.",
      example: "None (default), Even, Odd",
      impact:
        "Wrong parity produces consistent CRC errors. If you see 100% failed reads, suspect parity or baud before suspecting the cable.",
    } satisfies HelpEntry,

    stop_bits: {
      description:
        "Number of stop bits per byte. Always 1 unless the device specifically requires 2.",
      example: "1 (default), 2",
    } satisfies HelpEntry,

    data_bits: {
      description:
        "Bits per byte. Always 8 for binary Modbus RTU. ASCII mode sometimes uses 7.",
      example: "8 for RTU (default), 7 for ASCII",
    } satisfies HelpEntry,

    response_timeout_ms: {
      description:
        "How long to wait for a response before giving up and retrying. Tunes the tradeoff between fast failure and tolerance for slow networks.",
      example: "1000 ms for LAN, 3000 ms for VPN, 5000+ for sat link",
      impact:
        "Too tight = false timeouts on temporary network blips. Too loose = slow scan cycles when a device is dead. Watch the response-time histogram in diagnostics.",
    } satisfies HelpEntry,

    retries: {
      description:
        "How many times to retry a failed read before marking the tag SUSPECT and moving on.",
      example: "2 for LAN, 1 for chatty buses, 3 for unreliable links",
      impact:
        "Each retry takes one response_timeout_ms. 2 retries × 1000 ms timeout = 3 second worst-case per read.",
    } satisfies HelpEntry,
  } satisfies Section,

  // =========================================================================
  // DEVICE (one Modbus slave on a channel)
  // =========================================================================
  device: {
    name: {
      description:
        "Unique name for this device. Used to tag samples in the historian and to label tab groups in the Tag Explorer.",
      example: "FLOWCOMP_001, FC_001, GC700XA_03",
      impact:
        "Convention: short uppercase tag-style. Changing the name leaves history intact (linked by id, not name).",
    } satisfies HelpEntry,

    channel: {
      description:
        "Which network carries communication to this device. A device belongs to exactly one network.",
      impact:
        "Moving a device to a different network changes the connection path. Worker reloads after save; brief gap in samples is expected.",
    } satisfies HelpEntry,

    unit_id: {
      description:
        "Modbus slave ID (sometimes called 'station address' or 'unit identifier'). On serial buses this picks the slave; on TCP it's often 1 or 255 but some gateways use it to route to backend serial devices.",
      example: "1 for most TCP devices, 1-247 for serial RTU bus addressing",
      impact:
        "Wrong unit ID on a shared serial bus = silently reading the wrong device. On TCP it usually just returns an 'invalid slave' exception.",
    } satisfies HelpEntry,

    scan_interval_ms: {
      description:
        "Target poll cadence in milliseconds. The worker will read all enabled tags on this device at roughly this rate, scheduling permitting.",
      example: "1000 for typical analog, 500 for fast control loops, 5000 for slow chromatographs",
      impact:
        "Too fast burns CPU and saturates the bus. Too slow loses transient events. Watch the Diagnostics page for 'scan slip' — when the worker can't keep up.",
    } satisfies HelpEntry,

    enabled: {
      description:
        "When off, the worker skips this device entirely. Useful for staging a device's tag config before going live, or for taking a device out of service.",
      impact:
        "Disabling stops polling but keeps the configuration. Historical data is preserved.",
    } satisfies HelpEntry,

    description: {
      description: "Free-text notes about this device. Shown in the device tab on Tag Explorer.",
      example: "Flow computer #1 — fiscal metering on inlet header",
    } satisfies HelpEntry,
  } satisfies Section,

  // =========================================================================
  // REGISTER BLOCK (a contiguous range of registers polled as one read)
  // =========================================================================
  block: {
    name: {
      description:
        "Unique name for this block. Shown in tag-create dropdowns and Frame Inspector.",
      example: "FC001_HR_0_29 (Holding 0-29), FC001_IR_100_119 (Input 100-119)",
      impact:
        "Convention: include the device, function code, and address range so the purpose is obvious at a glance.",
    } satisfies HelpEntry,

    device: {
      description:
        "Which device this block belongs to. Blocks scope to a single device, even if multiple devices share a network.",
    } satisfies HelpEntry,

    function_code: {
      description:
        "Modbus function code for the block read. All tags assigned to this block must use the same FC.",
      example:
        "3 (read holding registers), 4 (read input registers), 1 (read coils), 2 (read discrete inputs)",
      impact:
        "Tags using a different FC simply can't be assigned to this block — they'd have to go in their own block (which incurs another round trip per scan).",
    } satisfies HelpEntry,

    start_address: {
      description:
        "First register address in the block (PDU-style, 0-based).",
      example:
        "0 to start at Modicon 40001 with FC3; 100 to start at 40101 with FC3",
      impact:
        "Together with register_count, defines the block's address range. Tags whose addresses fall outside this range can't be assigned to it.",
    } satisfies HelpEntry,

    register_count: {
      description:
        "How many 16-bit registers to read in one request. Modbus standard allows up to 125 for FC3/FC4 and up to 2000 for FC1/FC2, but most devices cap lower.",
      example: "30 for a typical group of analogs, 125 max for FC3 per Modbus spec",
      impact:
        "Larger blocks = fewer round trips = faster scan but riskier — one corrupt byte fails the whole block. Watch for 'illegal data quantity' errors when picking large counts.",
    } satisfies HelpEntry,

    enabled: {
      description:
        "When off, the worker skips this block. All tags assigned to it stop receiving updates.",
      impact:
        "Useful for testing — disable a block to silence noisy or broken registers without removing tag configuration.",
    } satisfies HelpEntry,
  } satisfies Section,

  // =========================================================================
  // GROUP (logical classification of tags)
  // =========================================================================
  group: {
    name: {
      description:
        "Unique name for this group. Shown as a chip on tags and in filter dropdowns.",
      example: "North-Plant, Compressor-A, Hourly-Fiscal-Report, Boiler-Trip-Conditions",
    } satisfies HelpEntry,

    group_type: {
      description:
        "Logical classification of the group itself. Helps organize the master list and surfaces a small badge on chips.",
      example:
        "AREA for plant/site, EQUIPMENT for specific units, UNIT for process sections, PACKAGE for vendor skids, REPORT for delivery-grouped tags, CUSTOM for anything else",
      impact:
        "Doesn't change tag behavior — just helps navigation and filtering. CSV-imported groups default to CUSTOM.",
    } satisfies HelpEntry,

    parent_group_id: {
      description:
        "Optional parent for nesting. Useful for hierarchies like Area → Unit → Equipment.",
      example: "Group 'Compressor-A' (EQUIPMENT) with parent 'North-Plant' (AREA)",
      impact:
        "Display-only nesting; doesn't propagate tag membership upward. A tag in Compressor-A is NOT automatically in North-Plant.",
    } satisfies HelpEntry,

    description: {
      description: "Free-text purpose / notes. Shown in the groups master table.",
    } satisfies HelpEntry,

    enabled: {
      description:
        "When off, the group is hidden from tag dropdowns but membership and historical data are preserved.",
    } satisfies HelpEntry,

    display_order: {
      description: "Sort order for group lists. Lower comes first; ties break by name.",
    } satisfies HelpEntry,
  } satisfies Section,

  // =========================================================================
  // NAMED SET (value→label translation for integer/bool tags)
  // =========================================================================
  named_set: {
    name: {
      description:
        "Unique name for this enumeration set. UPPERCASE_SNAKE_CASE by convention.",
      example: "MOTOR_STATE, ALARM_SEVERITY, VALVE_STATE",
    } satisfies HelpEntry,

    description: {
      description: "Free-text purpose for the set. Shown in the master table preview.",
    } satisfies HelpEntry,

    raw_value: {
      description:
        "The integer value the device produces. Each value must be unique within a set.",
      example: "0, 1, 2, …",
    } satisfies HelpEntry,

    display_text: {
      description:
        "Human-readable label shown wherever the raw value would otherwise appear in UI.",
      example: "Running, Stopped, Tripped",
    } satisfies HelpEntry,

    display_order: {
      description:
        "Sort order for displaying values in dropdowns and legends. Lower comes first.",
      example: "Use 0, 1, 2, … to match the natural sequence of a state machine",
    } satisfies HelpEntry,

    color: {
      description:
        "Optional color hint applied to the label in dashboards (chips, status indicators). CSS color names or hex.",
      example: "red for Tripped, green for Running, amber for Standby, #ef4444 for hex",
    } satisfies HelpEntry,

    enabled: {
      description:
        "When off, this set is hidden from the tag-assignment dropdown. Tags already using it keep working.",
    } satisfies HelpEntry,
  } satisfies Section,

  // =========================================================================
  // ENGINEERING UNIT (the unit master)
  // =========================================================================
  engineering_unit: {
    code: {
      description:
        "Symbol shown next to values (canonical form). Unicode is fine — use °C, μS, m³, etc.",
      example: "kg/h, °C, m³/h, μS/cm, mol%, bar",
      impact:
        "Stored once, referenced by many tags. Editing the code propagates everywhere the unit is shown.",
    } satisfies HelpEntry,

    label: {
      description:
        "Human-readable name shown in dropdowns alongside the code.",
      example: "Kilograms per hour, Degrees Celsius, Cubic metres per hour",
    } satisfies HelpEntry,

    quantity_kind: {
      description:
        "Lowercase snake_case category used to group units in dropdowns. Reuse existing kinds when possible.",
      example: "flow_mass, flow_volume, pressure, temperature, concentration, length",
      impact:
        "Tags pick from units within the same quantity_kind grouping. Mixing 'pressure' and 'temperature' units in the same dropdown would be unhelpful, so this groups them visibly.",
    } satisfies HelpEntry,

    description: {
      description:
        "Free-text notes about this unit (e.g. provenance, conversion factors, when to use).",
    } satisfies HelpEntry,

    enabled: {
      description:
        "When off, this unit is hidden from the tag-assignment dropdown. Tags already using it keep working.",
    } satisfies HelpEntry,
  } satisfies Section,

  // =========================================================================
  // DIAGNOSTICS (read-only metrics surfaced in the Diagnostics page)
  // =========================================================================
  diagnostics: {
    st_status: {
      description:
        "Single-byte (0-255) quality indicator stored with every sample. Bands tell you how much to trust the reading.",
      example:
        "192-255 = VALID_EXTENDED, 128-191 = VALID, 64-127 = SUSPECT, 0-63 = INVALID",
      impact:
        "Reports include or exclude samples based on these bands. Suspect samples appear in dashboards with an amber tint; invalid samples are excluded from fiscal totals.",
    } satisfies HelpEntry,

    valid_extended: {
      description:
        "Band 192-255 — fully trusted readings, no flags, in-range, fresh. Use for fiscal reporting and high-grade dashboards.",
    } satisfies HelpEntry,

    valid: {
      description:
        "Band 128-191 — trusted readings with minor flags (e.g. one retry needed, slight skew). Acceptable for most reporting.",
    } satisfies HelpEntry,

    suspect: {
      description:
        "Band 64-127 — readings that succeeded but failed validation (out of min/max range, heartbeat frozen, multiple retries). Visible but flagged in UI.",
      impact:
        "Counts toward Data Gap reporting. Investigate the source before trusting these in calculations.",
    } satisfies HelpEntry,

    invalid: {
      description:
        "Band 0-63 — failed reads, comms errors, exceptions, or values rejected outright (NaN, out of physical range).",
      impact:
        "Excluded from fiscal totals and most reports. Persists in the historian so you can investigate later.",
    } satisfies HelpEntry,

    buffer_health: {
      description:
        "Health of the in-memory ring buffer that stages samples before they're written to TimescaleDB.",
      example: "HEALTHY (< 50% full), WARNING (50-90%), CRITICAL (≥ 90% — risk of drops)",
      impact:
        "WARNING means writes can't keep up — usually DB pressure. CRITICAL means samples may be dropping. Check DB load and slow-query logs.",
    } satisfies HelpEntry,

    worker_status: {
      description:
        "Current state of the Modbus worker process for this network/device.",
      example:
        "RUNNING (polling normally), IDLE (no enabled tags), ERROR (last cycle failed), STOPPED (manually disabled)",
    } satisfies HelpEntry,

    frame_rate: {
      description:
        "Modbus frames per second observed on this network — outgoing requests and incoming responses combined.",
      impact:
        "A sustained drop usually means a comm failure. A spike usually means retries piling up — check error rate alongside.",
    } satisfies HelpEntry,

    error_rate: {
      description:
        "Failed-reads-per-minute on this channel, rolling. Includes timeouts, CRC errors, exception responses, and parse failures.",
      impact:
        "Healthy industrial links sit at < 0.1/min. > 1/min sustained usually indicates a problem (cable, comm settings, slave overload).",
    } satisfies HelpEntry,

    last_seen: {
      description:
        "Timestamp of the most recent successful sample. Color-coded by age.",
      example: "Green: < 1 scan-interval ago. Amber: 1-3 intervals. Red: > 3 intervals.",
      impact:
        "Stale 'last seen' on a heartbeat-watched tag triggers the FROZEN flag in ST status.",
    } satisfies HelpEntry,
  } satisfies Section,

  // =========================================================================
  // IMPORT / EXPORT (CSV format reference)
  // =========================================================================
  import: {
    csv_format: {
      description:
        "Tags can be imported and exported as CSV. Every configurable field maps to a column; missing columns get sensible defaults.",
      example:
        "Required: name, device_name, data_type, function_code, address. Optional: everything else.",
      impact:
        "Use the Download Template button to grab a CSV with the correct headers and two example rows.",
    } satisfies HelpEntry,

    groups_column: {
      description:
        "Group memberships as semicolon-separated names. Semicolon (not comma) avoids clashing with the CSV delimiter.",
      example: "Compressor-A;Hourly-Fiscal-Report",
      impact:
        "Unknown group names are auto-created as CUSTOM during import — friendly for cross-instance migration. Existing groups match case-insensitively.",
    } satisfies HelpEntry,

    named_set_column: {
      description:
        "Enumeration assignment by name (case-insensitive match against the enumerations master).",
      example: "MOTOR_STATE, ON_OFF, ALARM_SEVERITY",
      impact:
        "Unknown enumeration names are silently ignored — the tag imports without an assignment and the row gets a soft warning. Set up the enumeration first if you need it.",
    } satisfies HelpEntry,

    is_heartbeat_column: {
      description:
        "Heartbeat watch flag. Truthy values: true, 1, yes (case-insensitive). Anything else = false.",
      example: "true, false, 1, 0, yes",
    } satisfies HelpEntry,

    engineering_unit_column: {
      description:
        "Engineering unit by code or label (case-insensitive match against the master).",
      example: "kg/h, bar, °C  (or full names like 'Kilograms per hour')",
      impact:
        "Matched values become FK references; unmatched values are stored as a per-tag override text. Add to the master under Configuration → Engineering Units if you import a unit repeatedly.",
    } satisfies HelpEntry,
  } satisfies Section,
};
