# ADR 0001 — Active/Passive HA as the Target Topology

**Status:** Accepted. Implementation deferred to Phase 14+.
**Date:** 2026-05-11

## What we decided

InduVista will eventually support **active/passive high availability**:

- **Active node** — owns all writes, runs all workers, serves the API.
- **Passive node** — read-only Postgres replica + warm-standby app. No workers running. Gets promoted to active if the active fails.

We are **not** building this now. We are committing now to **not break the option** while building Phases 4–13.

## Why not build it now

- No deployed customers yet. RTO/RPO requirements are unknown.
- 2x infrastructure cost without a paying customer asking for it.
- Cannot test failover meaningfully without two real boxes and an ops setup.
- The current foundations are HA-friendly without trying — single-source-of-truth in Postgres, stateless workers, UTC throughout, store-and-forward for transient outages. Retrofitting HA on this is mostly Postgres-replication work, not application work.

## Rules that apply NOW to every phase

1. **Canonical state lives in Postgres.** Never on a local filesystem alone. The SQLite store-and-forward buffer is OK because it's transient recovery state, not the canonical record.
2. **Workers stay stateless.** Config is loaded from Postgres (already true after Phase 3.5). No in-memory state that wouldn't survive a node swap.
3. **UTC everywhere.** Already true. Don't regress.
4. **Anything that must run on only one node uses `is_leader()`.** Today this returns `True` in single-node mode. In HA mode it gets wired to the leader-election mechanism. Adding the check now is one line; retrofitting it later is painful.

## Concrete warnings for specific upcoming phases

- **Phase 8 (Reporting):** Save report files into Postgres (as bytea/LO) **or** to a volume that both nodes can read **or** replicate to passive. **Never** save canonical reports to active-only local disk.
- **Phase 9 (Scheduler):** Every scheduled task must be wrapped in `if is_leader():`. Otherwise both nodes will run the same job in HA mode and produce duplicates.
- **Phase 12 (Security/Audit):** Audit-log entries go to Postgres only. Never to local files.

## What's explicitly OUT of scope until Phase 14+

- Postgres streaming replication setup
- Leader election (Patroni / repmgr / custom)
- Automatic failover and health-driven promotion
- DNS / VIP / load balancer in front of the cluster
- Active-active or geo-redundant topologies (probably never; this is a process tool, not a CRDT-friendly app)

## How we'll know if we're on track

- `/health` reports `role: "active"` today. In HA mode it will report `"active"` or `"passive"` and tooling will key off it.
- No new code introduces a singleton without a leader gate.
- No new code writes canonical state outside Postgres.
- When we get a real customer with HA requirements, the work is Postgres-replication setup + leader election + a DNS/VIP — **not** an app refactor.

## Revisit if

- A customer's requirements include day-one active/passive HA in writing.
- We catch ourselves wanting to put canonical state on a local filesystem.
- We add a scheduler or any "this runs once" task without a leader gate.
