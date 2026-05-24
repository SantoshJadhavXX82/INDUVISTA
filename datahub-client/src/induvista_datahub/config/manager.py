"""Load / save config.toml.

`tomllib` (Python 3.11+ stdlib) is read-only. For writes we render
TOML by hand — the file is small, the format is forgiving, and adding
a third-party TOML-writer dep (`tomli-w`, `tomlkit`) just for this is
overkill at this scale.

If the file becomes more elaborate (e.g. preserving user comments,
inline tables for tag mappings), swap in `tomlkit` then.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from induvista_datahub.config.schema import AppConfig


log = logging.getLogger(__name__)


class ConfigManager:
    """Owns the config.toml file. Hands out AppConfig instances.

    Threading: load_or_init() / save() are NOT thread-safe — call them
    from the UI thread only. If background workers need a snapshot of
    the config, the UI thread reads it once and passes the immutable
    Pydantic model in.
    """

    def __init__(self, config_path: Path) -> None:
        self.path = config_path
        self._cache: AppConfig | None = None

    # ── Loading ───────────────────────────────────────────────────────

    def load_or_init(self) -> AppConfig:
        """Read config.toml; if missing or unparseable, fall back to
        defaults and write them out so the file exists for the user
        to edit. Returns the resulting AppConfig."""
        if self.path.exists():
            try:
                cfg = self._read_from_disk()
                self._cache = cfg
                return cfg
            except (tomllib.TOMLDecodeError, ValidationError, OSError) as e:
                # Don't crash on bad config — log loudly and use defaults.
                # The user can fix the file and restart.
                log.error(
                    "Config file at %s is invalid (%s: %s); using defaults",
                    self.path, type(e).__name__, e,
                )
                # Don't overwrite a broken-but-existing file — preserves
                # whatever the user typed for inspection. Just hand
                # back defaults for this session.
                cfg = AppConfig.with_defaults()
                self._cache = cfg
                return cfg

        # First run — no file. Write defaults out so the user has a
        # template to edit.
        log.info("No config at %s; creating with defaults", self.path)
        cfg = AppConfig.with_defaults()
        self._cache = cfg
        try:
            self.save(cfg)
        except OSError as e:
            log.warning("Could not write initial config (%s); continuing with in-memory defaults", e)
        return cfg

    def _read_from_disk(self) -> AppConfig:
        with self.path.open("rb") as f:
            raw = tomllib.load(f)
        return AppConfig.model_validate(raw)

    # ── Saving ────────────────────────────────────────────────────────

    def save(self, cfg: AppConfig) -> None:
        """Write cfg to disk. Atomic via write-to-tmp + rename, so
        a crash mid-write doesn't leave a half-baked file."""
        rendered = _render_toml(cfg.model_dump(mode="json"))
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(rendered, encoding="utf-8")
        tmp.replace(self.path)
        self._cache = cfg
        log.info("Config saved to %s", self.path)

    # ── Cached read ───────────────────────────────────────────────────

    def current(self) -> AppConfig:
        """Return the most recently loaded/saved config without
        re-reading disk. Crashes if load_or_init() was never called."""
        if self._cache is None:
            raise RuntimeError("ConfigManager.current() called before load_or_init()")
        return self._cache


# ---------------------------------------------------------------------------
# Hand-rolled TOML writer
# ---------------------------------------------------------------------------
# Keep it deliberately simple: top-level tables, nested tables for the
# fixed sections (server, opc, etc.), and array-of-tables for lists.

def _render_toml(data: dict[str, Any]) -> str:
    """Render a JSON-shaped dict back as TOML. Handles the fixed
    section shape of AppConfig; not a general-purpose TOML writer."""
    out: list[str] = []
    out.append(
        "# InduVista DataHub — config.toml\n"
        "# Edit while the app is stopped; restart to pick up changes.\n"
        "# Section [server] holds the INDUVISTA URL + API key.\n"
        "# Section [opc.connections] is an array-of-tables; see docs/ARCHITECTURE.md.\n"
    )

    for section, body in data.items():
        if isinstance(body, dict):
            out.append(_render_section(section, body))
        else:
            out.append(f"{section} = {_to_toml_value(body)}")
    return "\n".join(out) + "\n"


def _render_section(name: str, body: dict[str, Any]) -> str:
    """Render one top-level section (e.g. [server], [logging])."""
    lines: list[str] = [f"[{name}]"]
    # Scalars first, then arrays-of-tables at the end.
    array_keys: list[str] = []
    for k, v in body.items():
        # ALL list-typed values are deferred to the array-of-tables
        # pass. Critical: even an empty list must be deferred (not
        # rendered as `key = []`), because TOML refuses to add
        # `[[section.key]]` entries to a key that was already defined
        # as an inline array. We just omit empty arrays entirely;
        # Pydantic defaults missing arrays to [] on load, so the round
        # trip is preserved.
        if isinstance(v, list):
            array_keys.append(k)
            continue
        if isinstance(v, dict):
            # Nested dict that ISN'T a list — render as [section.sub].
            lines.append("")
            lines.append(_render_section(f"{name}.{k}", v))
            continue
        lines.append(f"{k} = {_to_toml_value(v)}")
    # Array-of-tables pass. Empty lists are skipped entirely (see
    # above for the TOML reason). Users add [[section.key]] blocks
    # freely after the file is written.
    for k in array_keys:
        v = body[k]
        if not v:
            continue
        for entry in v:
            lines.append("")
            lines.append(f"[[{name}.{k}]]")
            for ek, ev in entry.items():
                lines.append(f"{ek} = {_to_toml_value(ev)}")
    return "\n".join(lines) + "\n"


def _to_toml_value(v: Any) -> str:
    """Render a scalar as a TOML literal. Strings are double-quoted,
    booleans lowercased, numbers as-is, None becomes empty string
    (Pydantic should never emit None for fields with defaults, but
    we belt-and-suspender)."""
    if v is None:
        return '""'
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(v, list):
        return "[" + ", ".join(_to_toml_value(x) for x in v) + "]"
    # Fallback — shouldn't hit this for our schema.
    return f'"{v}"'
