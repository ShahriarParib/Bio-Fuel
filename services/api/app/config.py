"""Configuration settings for the Operator API service."""
from __future__ import annotations

from functools import lru_cache

from fsip_common.settings import BaseServiceSettings


class ApiSettings(BaseServiceSettings):
    api_operator_token: str = "change-me-operator"
    api_rate_limit_per_min: int = 600
    jwt_secret: str = "change-me-jwt"
    demo_controls: bool = True
    engine_internal_token: str = "change-me-internal"


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
