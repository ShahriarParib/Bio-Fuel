"""structlog JSON logging. Usage:
    from fsip_common.logging import configure_logging, get_logger
    configure_logging("engine"); log = get_logger(); log.info("sim.sync.ok", tick=12)
Mandatory event names: see 06_RESILIENCE_AND_OBSERVABILITY.md §2.2.
"""
from __future__ import annotations

import logging
import os
import sys

import structlog


def configure_logging(service: str, level: str | None = None) -> None:
    lvl = getattr(logging, (level or os.getenv("LOG_LEVEL", "INFO")).upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=lvl)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(lvl),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service)


def get_logger(**initial):
    return structlog.get_logger(**initial)
