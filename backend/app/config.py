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

    # Phase 21 — auth settings.
    # jwt_secret signs session tokens. The dev default below is INSECURE;
    # set JWT_SECRET to a long random value in production (see .env.example).
    # validate() below refuses to start in production with the default.
    jwt_secret: str = "dev-insecure-change-me-in-production"
    auth_token_ttl_min: int = 720  # 12h — one plant shift + margin

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()  # type: ignore[call-arg]

# Phase 21 — fail fast if production runs with the insecure default JWT secret.
if settings.app_env == "production" and settings.jwt_secret == "dev-insecure-change-me-in-production":
    raise RuntimeError(
        "JWT_SECRET is still the insecure development default but APP_ENV=production. "
        "Set JWT_SECRET to a strong random value (e.g. `openssl rand -hex 32`)."
    )
