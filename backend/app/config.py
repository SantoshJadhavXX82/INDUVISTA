"""Application settings, loaded from environment variables.

Compose loads .env into the container's environment via the env_file
directive in docker-compose.yml. For local non-Docker runs, pydantic-settings
also reads ./.env directly if present. Either way, every value below comes
from env vars at startup — no hard-coded secrets.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App identity
    app_name: str = "InduVista"
    app_env: str = "development"
    app_timezone: str = "Asia/Kolkata"

    # HA role marker. Single-node deployments leave this as "active". The
    # passive node in a future HA setup will set NODE_ROLE=passive in its
    # environment. See docs/architecture/0001-ha-active-passive.md.
    node_role: str = "active"

    # Required: connection string for SQLAlchemy
    database_url: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()  # type: ignore[call-arg]
