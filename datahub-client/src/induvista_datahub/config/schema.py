"""Pydantic models for config.toml.

Strict-by-default — unknown keys raise rather than being silently
dropped, so typos are caught at startup instead of at "why didn't my
setting take effect?" debug time later.

OPC connection shape uses a discriminated union on `kind` so the same
config slot can hold either UA endpoints or DA prog-IDs.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# [server]
# ---------------------------------------------------------------------------

class ServerConfig(BaseModel):
    """INDUVISTA backend connection settings."""
    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        default="http://localhost:8000",
        description="Base URL of the INDUVISTA backend (no trailing slash needed).",
    )
    api_key: str = Field(
        default="",
        description="Bearer token minted via POST /api/admin/api-keys. "
                    "Empty means the pusher is in 'unconfigured' state and won't fire.",
    )
    push_interval_sec: float = Field(
        default=5.0, gt=0,
        description="How often the pusher drains the store-forward buffer.",
    )
    batch_size: int = Field(
        default=500, ge=1, le=5000,
        description="Max samples per /api/ingest POST.",
    )
    request_timeout_sec: float = Field(
        default=10.0, gt=0,
        description="HTTP timeout per push attempt.",
    )


# ---------------------------------------------------------------------------
# [opc] — discriminated union over kind=ua / kind=da
# ---------------------------------------------------------------------------

class OpcUaConnection(BaseModel):
    """OPC UA endpoint."""
    model_config = ConfigDict(extra="forbid")

    kind: Literal["ua"] = "ua"
    name: str = Field(..., min_length=1, max_length=64)
    endpoint: str = Field(..., description="opc.tcp://host:port/path")
    security_policy: str = Field(
        default="None",
        description="None / Basic128Rsa15 / Basic256 / Basic256Sha256.",
    )
    username: str = ""
    password: str = ""
    # Phase OPC.3 — UA subscription tuning.
    publishing_interval_ms: int = Field(
        default=1000, ge=50, le=60000,
        description="How often the server publishes data changes to us. "
                    "Lower = more responsive but more network traffic.",
    )
    reconnect_min_sec: float = Field(
        default=1.0, gt=0,
        description="Initial backoff after a disconnect before retrying connect.",
    )
    reconnect_max_sec: float = Field(
        default=60.0, gt=0,
        description="Cap on the exponential backoff between reconnect attempts.",
    )


class OpcDaConnection(BaseModel):
    """OPC DA COM server."""
    model_config = ConfigDict(extra="forbid")

    kind: Literal["da"] = "da"
    name: str = Field(..., min_length=1, max_length=64)
    prog_id: str = Field(..., description="e.g. 'opcserversim.Instance.1'")
    host: str = Field(
        default="localhost",
        description="DCOM host. 'localhost' for the local machine.",
    )
    # Phase OPC.3 — DA group update rate.
    update_rate_ms: int = Field(
        default=1000, ge=50, le=60000,
        description="Server-side group scan rate for DataChange callbacks.",
    )
    # Phase OPC.3 — path to the Python interpreter that hosts the 32-bit
    # DA bridge subprocess. Default uses the same interpreter as the
    # main app (sys.executable). On 64-bit Python with a 32-bit DA
    # server, point this at a 32-bit Python install (e.g.
    # "C:\\Python311-32\\python.exe").
    bridge_python_path: str = Field(
        default="",
        description="Override path to 32-bit Python for the DA bridge. "
                    "Empty means use sys.executable (same as main app).",
    )


# A connection is either UA or DA — Pydantic picks based on `kind`.
OpcConnection = Annotated[
    Union[OpcUaConnection, OpcDaConnection],
    Field(discriminator="kind"),
]


class OpcConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connections: list[OpcConnection] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# [tag_mappings]
# ---------------------------------------------------------------------------

class TagMapping(BaseModel):
    """One row of the OPC node -> INDUVISTA tag_id mapping."""
    model_config = ConfigDict(extra="forbid")

    connection: str = Field(..., description="Matches OpcUaConnection.name / OpcDaConnection.name.")
    # For UA: NodeId string like 'ns=2;s=Pressure'
    # For DA: ItemId string like 'Random.Real8'
    node_id: str = Field(..., min_length=1)
    induvista_tag_id: int = Field(..., ge=1)


class TagMappingsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mappings: list[TagMapping] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# [logging]
# ---------------------------------------------------------------------------

class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    backup_count: int = Field(default=5, ge=0)


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

class AppConfig(BaseModel):
    """Root config model — corresponds to the whole config.toml file."""
    model_config = ConfigDict(extra="forbid")

    server: ServerConfig = Field(default_factory=ServerConfig)
    opc: OpcConfig = Field(default_factory=OpcConfig)
    tag_mappings: TagMappingsConfig = Field(default_factory=TagMappingsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @classmethod
    def with_defaults(cls) -> "AppConfig":
        """Construct an AppConfig populated entirely from defaults.
        Used by ConfigManager when no config file exists yet."""
        return cls()
