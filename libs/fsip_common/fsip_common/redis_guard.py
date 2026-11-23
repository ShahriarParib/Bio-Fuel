"""Circuit breaker around an async Redis client.

Found by the F10 drill: with the Redis container stopped, every command tried to reconnect, and each reconnect did a
DNS lookup for "redis" that takes ~8 s to fail (Docker's resolver forwards unknown names upstream). Those lookups run
in the default thread pool, so they starved the lookups for "simulator" and "intelligence": the simulator breaker
opened and forecasts timed out although both were healthy. A dead cache took down healthy dependencies.

GuardedRedis fails fast for `cooldown_s` after any error and lets one probe through afterwards, so at most one
lookup is in flight while Redis is down.
"""
from __future__ import annotations

import time
from typing import Any


class RedisUnavailable(ConnectionError):
    pass


class GuardedRedis:
    def __init__(self, client: Any, cooldown_s: float = 5.0) -> None:
        self._client = client
        self.cooldown_s = cooldown_s
        self._open = False          # True after a failure: calls fail fast
        self._retry_at = 0.0
        self._probing = False
        self.failures = 0

    @property
    def available(self) -> bool:
        return not self._open

    def _wrap(self, fn):
        async def call(*args, **kwargs):
            probe = False
            if self._open:
                if self._probing or time.monotonic() < self._retry_at:
                    raise RedisUnavailable("redis circuit open (recent failure)")
                self._probing = probe = True
            try:
                out = await fn(*args, **kwargs)
            except Exception:
                self.failures += 1
                self._open, self._retry_at = True, time.monotonic() + self.cooldown_s
                raise
            finally:
                if probe:
                    self._probing = False
            self._open = False
            return out

        return call

    async def aclose(self) -> None:
        await self._client.aclose()

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._client, name)
        if callable(attr) and name not in ("pipeline", "pubsub"):
            return self._wrap(attr)
        return attr
