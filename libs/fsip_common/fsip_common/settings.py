from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    """Common env settings. Each service subclasses this and adds its own fields (names = env vars, any case)."""
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    app_version: str = "dev"
    git_sha: str = "unknown"
    log_level: str = "INFO"
    sim_base_url: str = "http://simulator:8000"
    intelligence_url: str = "http://intelligence:8200"
    engine_internal_url: str = "http://engine:8100"
    engine_internal_token: str = "change-me-internal"
    database_url: str = "postgresql+asyncpg://fsip:change-me@timescaledb:5432/fsip"
    redis_url: str = "redis://redis:6379/0"
