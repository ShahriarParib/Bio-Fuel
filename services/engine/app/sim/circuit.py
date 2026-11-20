"""Circuit breaker for external dependencies (Simulator, Intelligence, etc.).

States:
- CLOSED: Requests pass normally. Failures increment failure counter.
- OPEN: Requests immediately fail with CircuitOpenError.
- HALF_OPEN: Probe request allowed after reset_timeout. Single success closes breaker,
  failure re-opens breaker.
"""
from __future__ import annotations

import asyncio
import time
from typing import ClassVar

from fsip_common.logging import get_logger
from prometheus_client import Gauge

log = get_logger()

BREAKER_STATE_GAUGE = Gauge(
    "fsip_breaker_state",
    "Circuit breaker state: 0=CLOSED, 1=OPEN, 2=HALF_OPEN",
    ["name"],
)


class CircuitOpenError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"Circuit breaker '{name}' is OPEN")
        self.name = name


class CircuitBreaker:
    CLOSED: ClassVar[str] = "CLOSED"
    OPEN: ClassVar[str] = "OPEN"
    HALF_OPEN: ClassVar[str] = "HALF_OPEN"

    _STATE_MAP: ClassVar[dict[str, int]] = {
        "CLOSED": 0,
        "OPEN": 1,
        "HALF_OPEN": 2,
    }

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        reset_timeout: float = 15.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.state = self.CLOSED
        self.failures = 0
        self.opened_at = 0.0
        self._lock = asyncio.Lock()
        BREAKER_STATE_GAUGE.labels(name=self.name).set(self._STATE_MAP[self.CLOSED])

    def _transition(self, new_state: str) -> None:
        if new_state != self.state:
            old_state = self.state
            self.state = new_state
            BREAKER_STATE_GAUGE.labels(name=self.name).set(self._STATE_MAP[new_state])
            log.info(
                "breaker.state_change",
                name=self.name,
                old_state=old_state,
                new_state=new_state,
                failures=self.failures,
            )

    def allow(self) -> bool:
        if self.state == self.OPEN:
            if time.monotonic() - self.opened_at >= self.reset_timeout:
                self._transition(self.HALF_OPEN)
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self._transition(self.CLOSED)

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == self.HALF_OPEN or self.failures >= self.failure_threshold:
            self.opened_at = time.monotonic()
            self._transition(self.OPEN)

    async def __aenter__(self) -> CircuitBreaker:
        if not self.allow():
            raise CircuitOpenError(self.name)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> bool:
        if exc_val is None:
            self.record_success()
        return False
