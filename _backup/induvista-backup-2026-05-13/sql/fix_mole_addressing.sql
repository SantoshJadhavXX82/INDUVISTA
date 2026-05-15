-- Migrate Last_Analy_Mole_01..16 from gap=2 to gap=1 Enron addressing.
--
-- Before:  16 tags at 7001, 7003, 7005, ..., 7031 in a block of count=32.
--          Wire reads 32 floats (Moles 1..32 in the GC), only odd-indexed
--          ones are decoded into tags. Mole_02 is actually Mole 3, etc.
--
-- After:   16 tags at 7001, 7002, 7003, ..., 7016 in a block of count=16.
--          Each tag's name matches the GC component it actually reads.
--
-- The Phase 9.1.2 overlap detector treats Enron-block tags as 1-address
-- spans, so consecutive float32 tags at gap=1 are valid configuration.

BEGIN;

-- Disable the block so the worker stops polling mid-migration.
UPDATE register_blocks
SET    enabled = FALSE
WHERE  name = 'GC700XA_7001_16';

-- Move every tag from old_addr to new_addr using the linear remap:
--     new_addr = (old_addr + 7001) / 2
-- which gives 7001→7001, 7003→7002, 7005→7003, ..., 7031→7016.
-- Postgres UPDATE applies all row changes atomically, so the intermediate
-- state can never violate uniqueness even though some new addresses equal
-- some old ones (7001, 7003, 7005, ...).
UPDATE tags
SET    address = (address + 7001) / 2
WHERE  register_block_id = (
           SELECT id FROM register_blocks WHERE name = 'GC700XA_7001_16'
       );

-- Shrink the block to its true size (16 logical values, not 32) and turn
-- it back on. The worker hot-reloads config every ~10 s and will pick up
-- the new layout without a restart.
UPDATE register_blocks
SET    count   = 16,
       enabled = TRUE
WHERE  name = 'GC700XA_7001_16';

-- Verify in the same transaction. If anything looks wrong, roll back.
SELECT t.name, t.address, t.register_count, b.count AS block_count, b.enabled
FROM   tags t
JOIN   register_blocks b ON b.id = t.register_block_id
WHERE  b.name = 'GC700XA_7001_16'
ORDER  BY t.address;

COMMIT;
