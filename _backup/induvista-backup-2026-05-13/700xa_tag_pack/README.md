# Daniel/Emerson 700XA — InduVista Tag Pack

Generated from `700XA_-_UK_MODBUS_Listing.xls`.

## What's in this bundle

| File | Purpose |
|---|---|
| `blocks.sql` | Creates **all 51** register blocks (disabled). Run once. |
| `tags_core.csv` | ~108 fiscal-essential tags (recommended starting set). |
| `tags_full.csv` | ~1,512 tags — every entry in the spreadsheet (heavy). |
| `enable_core_blocks.sql` | Flips the 12 core blocks to `enabled=TRUE`. |
| `convert.py` | Source script — re-run if you want to change filters. |

## Decisions baked in

- **Device** is hard-coded to `GC_SIM_001` (your existing GC). If you have a
  separate physical 700XA at a different IP, create a new device first and
  sed-replace the name in all four files.
- **Block prefix** is `GC700XA_` so nothing collides with your existing
  `GC_SIM_7001_16` block (the 16 mole-% floats already polling).
- **Addresses 7001–7016** are *excluded* — your existing mole-% block already
  owns them. The first 700XA float block here starts at 7017 (Weight %).
- **Addressing mode**: every block is `ENRON_HOLDING` (the channel you've
  already validated against the real Daniel GC).
- **Function code**: FC=3 (Read Holding Registers) everywhere, including the
  1xxx discrete I/O block. Daniel's UK MODBUS Listing exposes discretes as
  16-bit holding registers in the same table as the rest. If your specific
  device wires 1001–1010 as coils (FC=1), edit just that block's row in
  `blocks.sql` before running.
- **Default scan interval**: 5000 ms (5 s). 700XA analysis cycles are minutes
  long — polling faster wastes bandwidth without gaining new data. Override
  per block via the UI as your operational needs require.
- **register_count**: column is intentionally empty in the CSVs. Phase 9.1.2
  auto-derives it from `data_type`. Float32 → 2, int32 → 2, uint16/bool → 1.
- **Byte order**: `ABCD` (the Daniel default — verified against the real GC's
  94.9% methane reading).
- **Writable flag**: set to `true` only for rows the spreadsheet marks `RD_WR`
  (date/time set registers, command triggers like Clear/Ack alarms, new-data
  flags, site ID). Everything else is read-only.
- **"Unused" rows** in the spreadsheet are skipped — they're not real tags.
  Their addresses may still be inside a block's range; the wire reader pulls
  them and the decoder discards them.

## Block layout

The 12 core blocks cover ~108 fiscal-relevant tags spread across these
address bands:

| Block | Address range | Tags | Contents |
|---|---|---|---|
| `GC700XA_1001_10` | 1001–1010 | 10 | Discrete I/O (DOUT_1..5, DIN_1..5) |
| `GC700XA_3033_32` | 3033–3064 | 32 | Run time, stream number, alarm bitmaps, last-analysis times, new-data flags |
| `GC700XA_3065_32` | 3065–3096 | 32 | CDT refs, last-run validity flags |
| `GC700XA_3097_32` | 3097–3128 | 32 | Tail of last-run validity |
| `GC700XA_5001_2` | 5001–5002 | 2 | Cycle time LONG |
| `GC700XA_7017_32` | 7017–7048 | 31 | Weight %, primary ISO CV/density/Wobbe, calc results |
| `GC700XA_7049_32` | 7049–7080 | 32 | Secondary ISO calcs, averages |
| `GC700XA_7081_32` | 7081–7112 | 31 | Analog inputs, primary CV/Wobbe, FCalib counts |
| `GC700XA_7113_32` | 7113–7144 | 32 | FCalib ISO results, GSMR factors |
| `GC700XA_8963_2` | 8963–8964 | 2 | Clear/Acknowledge alarms (commands) |
| `GC700XA_9006_9` | 9006–9014 | 8 | Real-time clock, Modbus ID, Site ID |
| `GC700XA_9022_32` | 9022–9053 | 26 | Analysis/cycle/run time, current stream, alarms, reset times |

The 39 non-core blocks cover the bulk historical/calibration arrays
(36-element avg/max/min, archive 1/2/3, 80-element response factor and
retention time tables, multi-level calibration coefficients). These are
operationally relevant but rarely real-time-monitored.

## Deploy order

```powershell
# 1. Confirm GC_SIM_001 device exists and isn't using port conflicts
docker compose exec -T postgres psql -U induvista_admin -d induvista -c `
  "SELECT id, name, host, port FROM devices WHERE name = 'GC_SIM_001';"

# 2. Create all the blocks (disabled by default)
docker compose exec -T postgres psql -U induvista_admin -d induvista < blocks.sql

# 3. Verify blocks landed
docker compose exec -T postgres psql -U induvista_admin -d induvista -c `
  "SELECT name, start_address, count, addressing_mode, enabled
   FROM register_blocks WHERE name LIKE 'GC700XA_%' ORDER BY start_address;"
```

### 4. Import tags via the UI

Open InduVista → **Tag Explorer** → **"Import CSV"** → choose `tags_core.csv`
(or `tags_full.csv` if you want everything). Review the success/error grid
before confirming. The API auto-derives `register_count`, validates Enron
uniformity, and refuses bad data — any failures will be specific and fixable.

### 5. Enable the core blocks

```powershell
docker compose exec -T postgres psql -U induvista_admin -d induvista < enable_core_blocks.sql
```

Worker hot-reloads config within 10 s and starts polling.

### 6. Watch the first cycles

```powershell
docker compose logs --tail=80 modbus_worker | Select-String "GC_SIM_001|GC700XA"
```

You should see one connection line per polled block (or a single shared
connection if your device supports it), then `Cycle N: wrote X (X good/X total)`
lines at 5 s intervals.

### 7. Spot-check a value

```powershell
docker compose exec -T postgres psql -U induvista_admin -d induvista -c `
  "SELECT t.name, tv.value_double::text, tv.st_reason, tv.time
   FROM tag_values tv JOIN tags t ON t.id = tv.tag_id
   JOIN register_blocks b ON b.id = tv.register_block_id
   WHERE b.name = 'GC700XA_7017_32'
     AND tv.time > now() - interval '10 seconds'
   ORDER BY t.address LIMIT 10;"
```

If WEIGHT_1..16 show plausible mole-weight percentages (similar magnitude
to your existing mole-% values), the import worked end-to-end.

## If something goes wrong

- **"tag does not fit its register_block"** on import — the block doesn't
  exist yet or has a different start/count than what the CSV row expects.
  Run `blocks.sql` first.
- **"address X already used in this Enron block"** — a tag at that address
  already exists. Likely cause: re-running the import without first clearing
  the prior attempt. Either delete the old tags via UI or use a different
  block_name.
- **"Enron block already contains tags with mixed register_count"** —
  pre-9.1.2 leftovers. Run the consistency-fix migration first.
- **Decode failures on FLOAT tags** — byte order. Daniel's default is `ABCD`
  but some site configurations use `DCBA`. Try editing one tag to `DCBA` and
  see if it reads correctly; if so, bulk-update the rest.

## Re-generating

If you change the spreadsheet or want different filter rules:

```bash
python3 convert.py
# Outputs land in ./out/
```

The `CORE_RANGES` list and `EXCLUDE_ADDRS` set at the top of `convert.py` are
the two knobs to tune.
