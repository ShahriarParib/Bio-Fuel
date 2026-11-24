"""Configuration settings for the Operator API service."""
from __future__ import annotations

from functools import lru_cache

from fsip_common.settings import BaseServiceSettings


class ApiSettings(BaseServiceSettings):
    api_operator_token: str = "change-me-operator"
    api_supervisor_token: str = "change-me-supervisor"
    api_rate_limit_per_min: int = 600
    demo_controls: bool = True
    engine_internal_token: str = "change-me-internal"
    # the assistant is optional: with no key the explain endpoints serve the platform's own written summaries
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_spare_model: str = "gemini-flash-lite-latest"
    llm_timeout_sec: float = 4.0


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
