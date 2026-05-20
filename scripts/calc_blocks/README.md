# Calc Blocks — seed + smoke test

Two scripts plus shared helpers, living under `scripts/calc_blocks/`:

| File | Role |
|---|---|
| `_block_configs.py`         | Single source of truth: recipe for every block code |
| `_api_client.py`            | Stdlib-only JSON HTTP client (no `requests` dependency) |
| `seed_all_blocks.py`        | Creates one Computed Tag per registered block code |
| `smoke_test_all_blocks.py`  | End-to-end smoke test (8 sections) |
| `run_smoke.ps1`             | PowerShell wrapper matching existing smoke-test style |

## What the seed does

Creates (or rebuilds) a dedicated Computed Device named **`SMOKE_CALC_DEVICE`**
and populates it with **62 computed tags** — exactly one per code in
`BLOCK_REGISTRY`. Each tag's `block_config` references real Modbus tags
discovered via `GET /api/tags`, so the calc evaluator can actually run them.

Naming convention: `smoke_blk_<code>` (lowercased) — e.g. `smoke_blk_sum_of`,
`smoke_blk_voting_m_of_n`, `smoke_blk_if_then_else`.

```bash
# Idempotent: skips tags that already exist
python scripts/calc_blocks/seed_all_blocks.py

# Reset and recreate from scratch
python scripts/calc_blocks/seed_all_blocks.py --reset

# Plan-only, no API changes
python scripts/calc_blocks/seed_all_blocks.py --dry-run

# Seed a subset only
python scripts/calc_blocks/seed_all_blocks.py --only SUM_OF --only AVG_OF

# Against a non-local backend
python scripts/calc_blocks/seed_all_blocks.py --base http://10.0.0.5:8000
```

### Pool requirements

The seed needs the backend to already have, somewhere in `/api/tags`:

- ≥ 4 numeric tags (`float32 | float64 | int16 | uint16 | int32 | uint32 | …`)
- ≥ 1 integer tag (any of the `int*`/`uint*` types)
- ≥ 2 bool tags (`data_type = 'bool'`)

If the pool is short, the seed exits 2 with a clear message. The 700xa tag
pack covers the numeric requirement comfortably; bool tags usually need to
be added by hand (or via the simulator config).

## What the smoke test does

```bash
python scripts/calc_blocks/smoke_test_all_blocks.py
python scripts/calc_blocks/smoke_test_all_blocks.py --quick    # ~10s shorter
python scripts/calc_blocks/smoke_test_all_blocks.py --cleanup  # delete device at end
python scripts/calc_blocks/smoke_test_all_blocks.py --section 7  # one section only

# Or via the PS wrapper:
./scripts/calc_blocks/run_smoke.ps1
./scripts/calc_blocks/run_smoke.ps1 -Cleanup -Quick
```

Sections, in execution order:

| § | What it checks |
|---|---|
| 0 | Backend `/health` responds; docker services `backend`, `postgres`, `calc_evaluator` are up |
| 1 | `GET /api/calc/block-schemas` returns all 62 codes; every schema is well-formed |
| 2 | Pool discovery; reset `SMOKE_CALC_DEVICE`; create all 62 computed tags via API; list endpoint reflects them |
| 3 | Every block produces at least one evaluation within 30 s; non-fallible blocks report `status=ok` |
| 4 | `PATCH execution_rate_ms` round-trip and `GET` reflects the change |
| 5 | `PATCH enabled=false` actually stops scheduling (verified via `last_executed_at`), `enabled=true` resumes it |
| 6 | ADD in `n_ary` mode with mixed tags + constants is accepted and evaluates |
| 7 | Negative cases: unknown block code, invalid `execution_rate_ms`, missing required keys, `SR` with `set==reset`, length-mismatched `weights`, mutually-exclusive `right`+`value` |
| 8 | Cleanup (only if `--cleanup`) |

Exit codes:

- `0` — all assertions passed
- `1` — one or more assertions failed
- `2` — precondition unmet (backend down, pool insufficient)

## Notes on block-specific quirks (caught while building)

- **`MUX_INDEX`** uses key `"values"` in `block_config`, not `"inputs"` — the
  schema (`/api/calc/block-schemas`) advertises `"inputs"` for the UI but
  `validate_config()` rejects without `"values"`. The recipe follows the
  validator, not the schema.
- **`SR`, `RS`, `CTU`, `CTD`** require their two bool tags to be *different*.
- **`WEIGHTED_AVG`** requires `weights` length to match `inputs` length.
- **Comparison and binary arithmetic** require exactly one of `right` (tag)
  or `value` (number) — not both, not neither.
- **`GEOMETRIC_MEAN`** and **`HARMONIC_MEAN`** return BAD quality if any input
  is ≤ 0 or 0 respectively. Same for `SQRT`/`LN`/`LOG10` with negative or
  non-positive inputs. These are marked `quality_may_be_bad=True` so the
  smoke treats "evaluated at least once" as sufficient instead of asserting
  `status=ok`.
- **Stateful blocks** (TON/TOF/TP/R_TRIG/F_TRIG/SR/RS/CTU/CTD) may not have
  `status=ok` on their very first tick; the smoke tolerates that.

## Extending

To add a new block:

1. Add the class in `backend/app/workers/calc_blocks/...` (with `register_block`)
2. Add the schema in `backend/app/workers/calc_blocks/calc_block_schemas.py`
3. Add a `BlockRecipe(...)` row in `_block_configs.py`

The smoke's "every code in the schema has a recipe" cross-check (Section 1)
catches step 3 if you forget it — fail-loud is the design.
