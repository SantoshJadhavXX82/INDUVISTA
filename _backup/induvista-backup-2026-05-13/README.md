# InduVista — Session Backup (2026-05-13)

Complete snapshot of every code change shipped this session, plus a
rigorous smoke test that verifies all the features work end-to-end against
a live backend.

## What's inside

```
induvista-backup-2026-05-13/
├── README.md                            (this file)
├── CHANGELOG.md                         per-phase log of what changed
├── DEPLOY.md                            apply this backup to a running stack
│
├── backend/app/                         Python sources (unzip into project)
│   ├── api/
│   │   ├── tags.py                      Phase 11 rename + CSV upsert
│   │   ├── devices.py                   Phase 11 rename + Phase 10.2 Enron scan + retry
│   │   ├── register_blocks.py           Phase 9.1.x Enron addressing_mode
│   │   └── diagnostics.py               Phase 9.1.2-hotfix Enron-aware counts
│   ├── workers/
│   │   ├── enron_channel.py             Phase 9.1.1 wire-level NEW file
│   │   └── modbus_supervisor.py         Phase 9.1.1 dispatch + 10.2 cycle samples
│   └── modbus/
│       └── datatypes.py                 Phase 9.1.2 CANONICAL_REGISTER_COUNT NEW
│
├── frontend/src/
│   ├── components/
│   │   ├── layout/Nav.tsx               Phase 11 nav reorganization
│   │   ├── ui/device-picker.tsx         Phase 11 searchable picker NEW
│   │   ├── tags/tag-quality-badge.tsx   Phase 11 quality badge NEW
│   │   └── blocks/block-coverage-map.tsx Phase 11 SVG coverage map NEW
│   ├── pages/
│   │   ├── TagExplorer.tsx              Phase 11 quality column + rename + picker
│   │   ├── RegisterBrowser.tsx          Phase 10.2 Enron checkbox + decoded values
│   │   └── config/
│   │       ├── Devices.tsx              Phase 11 device rename unlocked
│   │       └── RegisterBlocks.tsx       Phase 11 coverage panel in edit drawer
│   ├── types/api.ts                     Phase 10.2 ScanRow decoded fields
│   └── lib/format.ts                    Phase 9.1.2 number formatter fix
│
├── sql/                                 one-shot migrations applied this session
│   ├── tags_core.sql                    108 fiscal-essential 700XA tags
│   └── fix_mole_addressing.sql          gap=2 → gap=1 migration (data integrity)
│
├── 700xa_tag_pack/                      Daniel/Emerson 700XA configuration
│   ├── README.md
│   ├── blocks.sql                       52 GC700XA_* register blocks
│   ├── enable_core_blocks.sql           enables 12 fiscal-essential blocks
│   ├── tags_core.csv                    108 tags (fiscal essentials)
│   ├── tags_full.csv                    1512 tags (all archives + calib)
│   ├── tags_core.sql                    SQL alternative to CSV import
│   └── convert.py                       regenerator if 700XA.xls is updated
│
└── smoke_tests/
    └── smoke_test_all.py                rigorous end-to-end verification
```

## Quick start — verify a deployed stack

After deploying any subset of these files, run the smoke test:

```powershell
cd D:\INDUVISTA
Expand-Archive `
  -Path "$env:USERPROFILE\Downloads\induvista-backup-2026-05-13.zip" `
  -DestinationPath ".\backup" -Force

docker compose cp .\backup\induvista-backup-2026-05-13\smoke_tests\smoke_test_all.py `
  backend:/tmp/smoke.py
docker compose exec backend python /tmp/smoke.py
docker compose exec backend rm /tmp/smoke.py
```

Exit code 0 means every check passed (or was legitimately skipped — e.g.,
"device unreachable" if your Daniel GC is offline at test time).

## What the smoke test exercises

| Phase | Feature | What gets checked |
|-------|---------|-------------------|
| 9.1.x | Enron block creation | POST /register-blocks with addressing_mode=ENRON_HOLDING persists; count=16 stored as logical-value count |
| 9.1.2 | Gap=1 float32 tags | Two consecutive float32 tags at addresses N and N+1 both insert (pre-9.1.2 the second was rejected as overlap) |
| 9.1.2 | register_count auto-derive | POST /tags without register_count auto-fills to 2 for float32 |
| 9.1.2 | Same-address rejection | POST /tags with an address already occupied returns 400/422 |
| 9.1.2-hotfix-diag | Diagnostics summary | overlap_count and block_fit_issue_count honor Enron span=1 (low count, not 15+) |
| 10.2 | Register Browser standard | POST /devices/X/scan-range with FC=3 returns rows |
| 10.2 | Register Browser Enron | POST with addressing_mode=ENRON_HOLDING + value_width_bytes=4 returns rows with decoded_float32_abcd |
| 10.2-hotfix-cycles | Cycle samples | /diagnostics/worker-status reports last_cycle_samples_total > 0 for connected devices |
| 11 | Tag rename | PATCH /tags/X with new name persists; duplicate-name returns 409 |
| 11 | Device rename | PATCH /devices/X with new name persists |
| 11 | CSV upsert | POST /tags/bulk with same names twice → first call all "created", second all "updated" |
| 11 | Address conflict | New name at occupied address → that row gets action="error" while others succeed |
| 11 | Block coverage data | /register-blocks and /tags?register_block_id=X return the fields BlockCoverageMap consumes |
| end-to-end | Gas composition | Live mol-% tags sum to 99–101% (skipped if no live data) |

The smoke test is **idempotent**: each scenario cleans up its own
artifacts on entry (looks for `SMOKE_*` named blocks/tags). Safe to re-run
back-to-back.

## How to restore from this backup

See DEPLOY.md for the apply procedure. Briefly:

1. Stop containers (`docker compose down`)
2. Unzip into project root, overwriting `backend/app/`, `frontend/src/`
3. Apply any pending SQL migrations from `sql/`
4. Rebuild and start (`docker compose up -d --build`)
5. Run `smoke_test_all.py` to verify

## What's NOT in this backup

- Database snapshot (use `pg_dump` separately if you want a full restore)
- Container volumes (`svj_postgres_data`)
- The `.env` file (kept out of source control by design)
- Frontend `node_modules` (rebuild from `package.json`)
- Worker historical state (rebuilds itself from the database on restart)

For a true point-in-time disaster-recovery backup, combine this code
backup with a `pg_dump` of the `induvista` database, taken at the same
moment.
