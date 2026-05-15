-- Enable the fiscal-essential 700XA blocks (run AFTER importing tags_core.csv
-- and confirming the polls look healthy on a single block first).
--
-- Each block is rate-limited to 5s polls by default — gas analysis cycles
-- run on minutes, so polling faster wastes bandwidth without gaining info.
-- Adjust scan_interval_ms per block as your operational needs require.

UPDATE register_blocks SET enabled = TRUE
WHERE name IN (
    'GC700XA_1001_10',     -- Discrete I/O (10 booleans)
    'GC700XA_3033_32',     -- Run time, stream, alarms, last-analysis times, alarm bitmaps
    'GC700XA_3065_32',     -- Stream new-data flags, CDT refs, last-run validity
    'GC700XA_3097_32',     -- Tail of last-run validity
    'GC700XA_5001_2',      -- Cycle time LONG
    'GC700XA_7017_32',     -- Weight %, ISO calcs (CV, density, Wobbe), calc results
    'GC700XA_7049_32',     -- Secondary ISO calcs (CV/density/Wobbe sec), averages
    'GC700XA_7081_32',     -- Primary CV/Wobbe/density, FCalib counts, auto-cal start
    'GC700XA_7113_32',     -- FCalib results, GSMR factors
    'GC700XA_8963_2',      -- Clear/Acknowledge alarms (writable command registers)
    'GC700XA_9006_9',      -- Current date/time, Modbus ID, Site ID
    'GC700XA_9022_32'      -- Analysis time, current stream, alarm flags, reset times
);

SELECT name, function_code, start_address, count, addressing_mode,
       scan_interval_ms, enabled
FROM register_blocks
WHERE name LIKE 'GC700XA_%'
ORDER BY start_address;
