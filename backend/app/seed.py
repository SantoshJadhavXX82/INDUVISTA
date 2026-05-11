"""Idempotent seeder for the three-tier config layout.

Reads from /app/config/{channels,device_templates,devices}/*.json and
materializes the result into protocol_connectors / channels / devices /
register_blocks / tags rows.

Layout:
  config/
    channels/<name>.json           — transport-layer config (one per network)
    device_templates/<id>.json     — register map + tags (reusable across devices)
    devices/<name>.json            — device instance: channel + template refs,
                                     host/port/unit_id, duty_role

The seeder is the projection layer: templates exist only at config time
(no templates table in the DB). At seed time, each device's referenced
template is expanded into per-device register_blocks and tags rows.

Re-run safely. Uses ON CONFLICT on natural unique keys so existing rows
update in place rather than duplicate.

Usage:
  docker compose run --rm backend python -m app.seed
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db import engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
)
log = logging.getLogger("seed")

CONFIG_ROOT = Path(os.environ.get("CONFIG_ROOT", "/app/config"))


def _load_json_dir(path: Path) -> list[dict]:
    """Read every *.json under `path` (non-recursive). Returns parsed dicts."""
    if not path.exists():
        return []
    out = []
    for p in sorted(path.glob("*.json")):
        with open(p, "r", encoding="utf-8") as f:
            out.append(json.load(f))
    return out


def _upsert_protocol_connector(conn, code: str) -> int:
    return conn.execute(text("""
        INSERT INTO protocol_connectors (code, name, description)
        VALUES (:code, :name, :description)
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
    """), {
        "code": code,
        "name": code.title(),
        "description": f"{code.title()} protocol connector",
    }).scalar_one()


def _upsert_channel(conn, channel: dict, connector_id: int) -> int:
    return conn.execute(text("""
        INSERT INTO channels (
            protocol_connector_id, name, description, transport
        )
        VALUES (:pc_id, :name, :description, :transport)
        ON CONFLICT (name) DO UPDATE
            SET description = EXCLUDED.description,
                transport = EXCLUDED.transport,
                protocol_connector_id = EXCLUDED.protocol_connector_id
        RETURNING id
    """), {
        "pc_id": connector_id,
        "name": channel["name"],
        "description": channel.get("description"),
        "transport": channel.get("transport", "tcp"),
    }).scalar_one()


def _upsert_device(conn, device: dict, channel_id: int) -> int:
    # Allow MODBUS_DEVICE_HOST env override so the same JSON works for
    # docker-compose dev vs. pointing at a real PLC.
    host = os.environ.get("MODBUS_DEVICE_HOST", device["host"])
    return conn.execute(text("""
        INSERT INTO devices (
            channel_id, name, description, protocol,
            host, port, unit_id
        )
        VALUES (
            :ch_id, :name, :description, :protocol,
            :host, :port, :unit_id
        )
        ON CONFLICT (name) DO UPDATE SET
            channel_id  = EXCLUDED.channel_id,
            description = EXCLUDED.description,
            host        = EXCLUDED.host,
            port        = EXCLUDED.port,
            unit_id     = EXCLUDED.unit_id
        RETURNING id
    """), {
        "ch_id": channel_id,
        "name": device["name"],
        "description": device.get("description"),
        "protocol": device.get("protocol", "modbus_tcp"),
        "host": host,
        "port": device["port"],
        "unit_id": device["unit_id"],
    }).scalar_one()


def _upsert_register_blocks(conn, device_id: int, blocks: list[dict]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for block in blocks:
        bid = conn.execute(text("""
            INSERT INTO register_blocks (
                device_id, name, function_code, start_address, count
            )
            VALUES (:device_id, :name, :fc, :start, :count)
            ON CONFLICT (device_id, function_code, start_address) DO UPDATE
                SET name = EXCLUDED.name, count = EXCLUDED.count
            RETURNING id
        """), {
            "device_id": device_id,
            "name": block["name"],
            "fc": block["function_code"],
            "start": block["start_address"],
            "count": block["count"],
        }).scalar_one()
        ids[block["name"]] = bid
    return ids


def _upsert_tag(conn, device_id: int, block_id_for_tag: int | None, tag: dict):
    conn.execute(text("""
        INSERT INTO tags (
            device_id, register_block_id, name, description,
            data_type, byte_order, function_code,
            address, register_count,
            engineering_unit, scale, "offset",
            min_value, max_value
        )
        VALUES (
            :device_id, :block_id, :name, :description,
            :data_type, :byte_order, :fc,
            :address, :register_count,
            :engineering_unit, :scale, :offset,
            :min_value, :max_value
        )
        ON CONFLICT (device_id, name) DO UPDATE SET
            register_block_id = EXCLUDED.register_block_id,
            description       = EXCLUDED.description,
            data_type         = EXCLUDED.data_type,
            byte_order        = EXCLUDED.byte_order,
            function_code     = EXCLUDED.function_code,
            address           = EXCLUDED.address,
            register_count    = EXCLUDED.register_count,
            engineering_unit  = EXCLUDED.engineering_unit,
            scale             = EXCLUDED.scale,
            "offset"          = EXCLUDED."offset",
            min_value         = EXCLUDED.min_value,
            max_value         = EXCLUDED.max_value
    """), {
        "device_id": device_id,
        "block_id": block_id_for_tag,
        "name": tag["name"],
        "description": tag.get("description"),
        "data_type": tag["data_type"],
        "byte_order": tag["byte_order"],
        "fc": tag["function_code"],
        "address": tag["address"],
        "register_count": tag["register_count"],
        "engineering_unit": tag.get("engineering_unit"),
        "scale": tag.get("scale") or 1.0,
        "offset": tag.get("offset") or 0.0,
        "min_value": tag.get("min_value"),
        "max_value": tag.get("max_value"),
    })


def main() -> int:
    log.info("Config root: %s", CONFIG_ROOT)
    channels = _load_json_dir(CONFIG_ROOT / "channels")
    templates = _load_json_dir(CONFIG_ROOT / "device_templates")
    devices = _load_json_dir(CONFIG_ROOT / "devices")

    if not channels:
        log.error("No channels found under %s/channels", CONFIG_ROOT)
        return 1
    if not templates:
        log.error("No device templates found under %s/device_templates", CONFIG_ROOT)
        return 1
    if not devices:
        log.error("No devices found under %s/devices", CONFIG_ROOT)
        return 1

    log.info(
        "Discovered %d channel(s), %d template(s), %d device(s)",
        len(channels), len(templates), len(devices),
    )

    templates_by_id: dict[str, dict] = {t["template_id"]: t for t in templates}

    with engine.begin() as conn:
        # ---- 1. Protocol connectors (one per unique value across channels) ----
        connector_ids: dict[str, int] = {}
        for ch in channels:
            code = ch.get("protocol_connector", "modbus")
            if code not in connector_ids:
                connector_ids[code] = _upsert_protocol_connector(conn, code)
                log.info("Connector %r → id %d", code, connector_ids[code])

        # ---- 2. Channels ----
        channel_ids: dict[str, int] = {}
        for ch in channels:
            cid = _upsert_channel(
                conn, ch, connector_ids[ch.get("protocol_connector", "modbus")],
            )
            channel_ids[ch["name"]] = cid
            log.info("Channel %r → id %d", ch["name"], cid)

        # ---- 3. Devices (with template expansion) ----
        for dev in devices:
            channel_name = dev["channel"]
            template_name = dev["template"]

            if channel_name not in channel_ids:
                log.error(
                    "Device %r references unknown channel %r",
                    dev["name"], channel_name,
                )
                return 1
            if template_name not in templates_by_id:
                log.error(
                    "Device %r references unknown template %r",
                    dev["name"], template_name,
                )
                return 1

            template = templates_by_id[template_name]
            device_id = _upsert_device(conn, dev, channel_ids[channel_name])
            log.info(
                "Device %r → id %d  (channel=%r template=%r)",
                dev["name"], device_id, channel_name, template_name,
            )

            block_ids = _upsert_register_blocks(
                conn, device_id, template["register_blocks"],
            )
            log.info("  %d block(s) upserted", len(block_ids))

            tag_to_block: dict[str, int] = {}
            for block in template["register_blocks"]:
                for tag_name in block["tags"]:
                    tag_to_block[tag_name] = block_ids[block["name"]]

            skipped_no_block = 0
            for tag in template["tags"]:
                block_id = tag_to_block.get(tag["name"])
                if block_id is None:
                    skipped_no_block += 1
                _upsert_tag(conn, device_id, block_id, tag)

            log.info(
                "  %d tag(s) upserted (%d without a register_block — writables)",
                len(template["tags"]), skipped_no_block,
            )

        # ---- 4. Groups & memberships (Phase 6) ----------------------------
        # Templates carry a single `group` string per tag. Map those to the
        # `groups` table (group_type='CUSTOM' since templates don't
        # distinguish AREA/EQUIPMENT/etc) and populate tag_group_memberships.
        # Idempotent and additive: re-runs won't duplicate (UNIQUE/PK
        # constraints + ON CONFLICT DO NOTHING) and won't remove existing
        # memberships either (so memberships added via a future API survive
        # re-seeding).

        # 4a. Collect every distinct group name referenced by any template.
        all_group_names: set[str] = set()
        for tmpl in templates:
            for tag in tmpl["tags"]:
                g = tag.get("group")
                if g:
                    all_group_names.add(g)

        group_name_to_id: dict[str, int] = {}
        for name in sorted(all_group_names):
            gid = conn.execute(text("""
                INSERT INTO groups (name, group_type)
                VALUES (:name, 'CUSTOM')
                ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
            """), {"name": name}).scalar_one()
            group_name_to_id[name] = gid
        log.info("Groups: %d total (%d distinct)", len(group_name_to_id), len(all_group_names))

        # 4b. Build (device_id, tag_name) → tag_id lookup so we can resolve
        # memberships without modifying _upsert_tag's signature.
        tag_id_lookup = {
            (r["device_id"], r["name"]): r["id"]
            for r in conn.execute(
                text("SELECT id, device_id, name FROM tags"),
            ).mappings().all()
        }
        device_id_by_name = {
            r["name"]: r["id"]
            for r in conn.execute(
                text("SELECT id, name FROM devices"),
            ).mappings().all()
        }

        # 4c. For each device's template, insert tag→group memberships.
        membership_inserts = 0
        for dev in devices:
            device_id = device_id_by_name.get(dev["name"])
            template = templates_by_id.get(dev["template"])
            if device_id is None or template is None:
                continue
            for tag in template["tags"]:
                group_name = tag.get("group")
                if not group_name:
                    continue
                tag_id = tag_id_lookup.get((device_id, tag["name"]))
                if tag_id is None:
                    continue
                group_id = group_name_to_id[group_name]
                result = conn.execute(text("""
                    INSERT INTO tag_group_memberships (tag_id, group_id)
                    VALUES (:tag_id, :group_id)
                    ON CONFLICT (tag_id, group_id) DO NOTHING
                """), {"tag_id": tag_id, "group_id": group_id})
                membership_inserts += result.rowcount or 0
        log.info(
            "Memberships: %d new (existing memberships preserved)",
            membership_inserts,
        )

    log.info("✓ Seed complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
