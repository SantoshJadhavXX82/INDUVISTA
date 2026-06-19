"""Calc dependency flow (chain) lookup.

For one computed tag, returns the FULL upstream flow as a nested tree:
the calc's inputs, and - when an input is itself produced by another
calc (either the calc's own anchor tag, or the external output target
of another calc) - that upstream calc's flow, recursively.

Every tag node carries its latest value, quality (st byte) and
timestamp so the UI can draw a live flow diagram with values on all
inputs and outputs.

Reuses BLOCK_REGISTRY[block_type].inputs(block_config) - the same
extractor the evaluator uses - so edges match what is actually read.

Cycle-safe: a calc already on the current path is returned as a stub
with "cycle": true. Depth-capped (default 6).

Mounted in main.py:  app.include_router(calc_flow.router)
"""

import json

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.db import SessionLocal
from app.workers.calc_blocks import BLOCK_REGISTRY


router = APIRouter(tags=["calc"])


def _cfg(raw) -> dict:
    if isinstance(raw, str):
        try:
            return json.loads(raw) or {}
        except Exception:
            return {}
    return raw or {}


_OPERAND_SCALAR_KEYS = (
    "left", "right", "input", "condition", "then_value", "else_value",
    "set", "reset", "primary", "standby", "count_up", "count_down",
    "load", "index",
)


def _decode_operand(spec):
    """Mirror calc_blocks.base.resolve_operand_spec, never raising.
    Returns ("tag", id) | ("const", float) | None."""
    import math
    if isinstance(spec, bool) or spec is None:
        return None
    if isinstance(spec, int):
        return ("tag", spec) if spec > 0 else ("const", float(spec))
    if isinstance(spec, float):
        if math.isfinite(spec) and spec > 0 and spec.is_integer():
            return ("tag", int(spec))
        return ("const", float(spec)) if math.isfinite(spec) else None
    if isinstance(spec, dict):
        t = spec.get("tag")
        if isinstance(t, int) and not isinstance(t, bool) and t > 0:
            return ("tag", t)
        v = spec.get("value")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return ("const", float(v))
    return None


def _operands(block_type: str, cfg: dict) -> list[tuple[str, object, str]]:
    """Ordered operand list: [("tag", id, label) | ("const", value, label)].
    Walks the known operand keys (scalars then inputs[]), then appends any
    tag ids the block's own inputs() reports that the walker missed."""
    out: list[tuple[str, object, str]] = []
    seen_tags: set[int] = set()
    for key in _OPERAND_SCALAR_KEYS:
        if key in cfg:
            d = _decode_operand(cfg.get(key))
            if d is None:
                continue
            kind, val = d
            if kind == "tag":
                if val in seen_tags:
                    continue
                seen_tags.add(val)
            out.append((kind, val, key))
    inputs = cfg.get("inputs")
    if isinstance(inputs, list):
        for i, spec in enumerate(inputs):
            d = _decode_operand(spec)
            if d is None:
                continue
            kind, val = d
            if kind == "tag":
                if val in seen_tags:
                    continue
                seen_tags.add(val)
            out.append((kind, val, f"inputs[{i}]"))
    # Safety net: any tag the evaluator reads that the walker missed.
    cls = BLOCK_REGISTRY.get(block_type)
    if cls is not None:
        try:
            for t in cls.inputs(cfg):
                if isinstance(t, int) and t > 0 and t not in seen_tags:
                    seen_tags.add(t)
                    out.append(("tag", t, "input"))
        except Exception:
            pass
    return out


def _input_ids(block_type: str, cfg: dict) -> list[int]:
    return [v for k, v, _ in _operands(block_type, cfg) if k == "tag"]


@router.get("/api/calc/definitions/{def_id}/flow")
def get_flow(def_id: int, depth: int = Query(default=6, ge=1, le=10)) -> dict:
    """Nested flow tree for one computed tag. Shape:

        {
          "def_id": 12, "name": "FlowTotal", "block_type": "ADD",
          "enabled": true, "cycle": false,
          "output": {tag node},
          "inputs": [
            {tag node, "kind": "raw" | "computed" | "const", "calc": <nested|null>}
          ]
        }

    tag node = {tag_id, name, device_name, value, quality, ts,
                resolved}. "kind": "computed" means the tag is produced
    by another calc (anchor or external target); its "calc" key holds
    that calc's nested flow (or a {cycle: true} stub).
    """
    with SessionLocal() as db:
        calcs = db.execute(text(
            "SELECT ct.id, ct.block_type, ct.block_config, ct.enabled,"
            "       ct.output_tag_id, t.name"
            "  FROM computed_tags ct JOIN tags t ON t.id = ct.id"
        )).mappings().all()

        by_id = {c["id"]: c for c in calcs}
        # tag_id -> calc that PRODUCES it (anchor id, or external target)
        producer: dict[int, dict] = {}
        for c in calcs:
            if c["output_tag_id"] is not None:
                producer[c["output_tag_id"]] = c
            else:
                producer[c["id"]] = c

        if def_id not in by_id:
            raise HTTPException(status_code=404,
                                detail=f"Computed tag {def_id} not found")

        # ---- pass 1: walk the tree, collect involved tag ids ----------
        involved: set[int] = set()

        def collect(cid: int, path: frozenset[int], d: int) -> None:
            c = by_id[cid]
            out_tag = c["output_tag_id"] if c["output_tag_id"] is not None else c["id"]
            involved.add(out_tag)
            involved.add(c["id"])
            if d <= 0:
                return
            for tid in _input_ids(c["block_type"], _cfg(c["block_config"])):
                involved.add(tid)
                up = producer.get(tid)
                if up is not None and up["id"] not in path:
                    collect(up["id"], path | {up["id"]}, d - 1)

        collect(def_id, frozenset({def_id}), depth)

        ids = sorted(involved)
        meta = {
            r["id"]: r
            for r in db.execute(text(
                "SELECT t.id, t.name, d.name AS device_name"
                "  FROM tags t LEFT JOIN devices d ON d.id = t.device_id"
                " WHERE t.id = ANY(:ids)"), {"ids": ids}).mappings().all()
        } if ids else {}
        latest = {
            r["tag_id"]: r
            for r in db.execute(text(
                "SELECT DISTINCT ON (tag_id) tag_id, value_double, st, time"
                "  FROM tag_values WHERE tag_id = ANY(:ids)"
                " ORDER BY tag_id, time DESC"), {"ids": ids}).mappings().all()
        } if ids else {}

        def tag_node(tid: int) -> dict:
            m = meta.get(tid)
            lv = latest.get(tid)
            ts = lv["time"] if lv else None
            return {
                "tag_id": tid,
                "name": m["name"] if m else None,
                "device_name": m["device_name"] if m else None,
                "resolved": m is not None,
                "value": (float(lv["value_double"])
                          if (lv and lv["value_double"] is not None) else None),
                "quality": (int(lv["st"]) if (lv and lv["st"] is not None) else None),
                "ts": ts.isoformat() if ts else None,
            }

        # ---- pass 2: build the nested tree -----------------------------
        def build(cid: int, path: frozenset[int], d: int) -> dict:
            c = by_id[cid]
            out_tag = c["output_tag_id"] if c["output_tag_id"] is not None else c["id"]
            node = {
                "def_id": c["id"],
                "name": c["name"],
                "block_type": c["block_type"],
                "enabled": c["enabled"],
                "cycle": False,
                "output": tag_node(out_tag),
                "inputs": [],
            }
            if d <= 0:
                node["truncated"] = True
                return node
            for okind, oval, olabel in _operands(c["block_type"], _cfg(c["block_config"])):
                if okind == "const":
                    node["inputs"].append({
                        "kind": "const", "label": olabel, "value": oval,
                    })
                    continue
                tid = oval
                inp = tag_node(tid)
                inp["label"] = olabel
                up = producer.get(tid)
                if up is None:
                    inp["kind"] = "raw"
                    inp["calc"] = None
                elif up["id"] in path:
                    inp["kind"] = "computed"
                    inp["calc"] = {
                        "def_id": up["id"], "name": up["name"],
                        "block_type": up["block_type"], "enabled": up["enabled"],
                        "cycle": True, "output": tag_node(tid), "inputs": [],
                    }
                else:
                    inp["kind"] = "computed"
                    inp["calc"] = build(up["id"], path | {up["id"]}, d - 1)
                node["inputs"].append(inp)
            return node

        return build(def_id, frozenset({def_id}), depth)


@router.get("/api/calc/definitions/{def_id}/used-by")
def get_used_by(def_id: int) -> dict:
    """Calcs that consume THIS computed tag's output as an input -
    i.e. what is affected if this calc is deleted (its output tag goes
    stale for them) or renamed (cosmetic only; references are by id).

    Shape:
        {
          "def_id": 12,
          "output_tag_id": 4567,
          "consumers": [
            {"id": 20, "name": "...", "block_type": "...",
             "enabled": true, "device_name": "...", "via_label": "left"}
          ]
        }
    via_label is the operand role through which the consumer references
    this tag (first matching operand).
    """
    with SessionLocal() as db:
        calcs = db.execute(text(
            "SELECT ct.id, ct.block_type, ct.block_config, ct.enabled,"
            "       ct.output_tag_id, t.name, d.name AS device_name"
            "  FROM computed_tags ct"
            "  JOIN tags t ON t.id = ct.id"
            "  LEFT JOIN devices d ON d.id = t.device_id"
        )).mappings().all()

        by_id = {c["id"]: c for c in calcs}
        if def_id not in by_id:
            raise HTTPException(status_code=404,
                                detail=f"Computed tag {def_id} not found")

        me = by_id[def_id]
        out_tag = me["output_tag_id"] if me["output_tag_id"] is not None else me["id"]

        consumers: list[dict] = []
        for c in calcs:
            if c["id"] == def_id:
                continue
            via = None
            for okind, oval, olabel in _operands(c["block_type"], _cfg(c["block_config"])):
                if okind == "tag" and oval == out_tag:
                    via = olabel
                    break
            if via is not None:
                consumers.append({
                    "id": c["id"],
                    "name": c["name"],
                    "block_type": c["block_type"],
                    "enabled": c["enabled"],
                    "device_name": c["device_name"],
                    "via_label": via,
                })

        consumers.sort(key=lambda x: ((x["device_name"] or ""), x["name"] or ""))
        return {
            "def_id": def_id,
            "output_tag_id": out_tag,
            "consumers": consumers,
        }
