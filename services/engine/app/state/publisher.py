"""Redis state publisher: caches snapshots, updates streams, and reports health."""
from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis
from fsip_common.logging import get_logger
from prometheus_client import Counter

from .store import WorldState

log = get_logger()

REDIS_ERRORS_TOTAL = Counter(
    "fsip_redis_errors_total",
    "Count of Redis publish and write errors",
    ["operation"],
)


class Publisher:
    def __init__(self, redis_url: str = "redis://redis:6379/0", stream_maxlen: int = 20000) -> None:
        self.redis_url = redis_url
        self.stream_maxlen = stream_maxlen
        self._redis: aioredis.Redis | None = None

    async def connect(self) -> None:
        try:
            self._redis = aioredis.from_url(
                self.redis_url,
                socket_timeout=1.5,
                socket_connect_timeout=1.5,
                decode_responses=True,
            )
            await self._redis.ping()
            log.info("redis.connected", url=self.redis_url)
        except Exception as err:
            REDIS_ERRORS_TOTAL.labels(operation="connect").inc()
            log.warning("redis.connect_failed", error=str(err))

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    async def publish_state(self, store: WorldState) -> None:
        """Publish the current WorldState snapshot to Redis string and stream."""
        if not self._redis:
            try:
                await self.connect()
            except Exception:
                pass
        if not self._redis:
            return

        public_state = store.to_public_json()
        state_json = json.dumps(public_state)
        current_tick = str(public_state["tick"])
        etag = f"{public_state['sim_run_id']}-{current_tick}"

        try:
            # 1. Cache latest full state JSON
            await self._redis.set("fsip:state", state_json)
            await self._redis.set("fsip:state:tick", current_tick)

            # 2. Add event to Redis Stream fsip:domain-events
            event_payload = {
                "event_type": "state.updated",
                "tick": current_tick,
                "etag": etag,
                "sim_run_id": str(public_state["sim_run_id"]),
            }
            await self._redis.xadd(
                "fsip:domain-events",
                event_payload,
                maxlen=self.stream_maxlen,
                approximate=True,
            )

            # 3. Update health status hash
            health_payload = json.dumps({
                "status": "Healthy" if not store.degraded else "Degraded",
                "tick": public_state["tick"],
                "engine_mode": store.engine_mode,
                "degraded_reasons": list(store.degraded_reasons),
                "stale": store.stale,
            })
            await self._redis.hset("fsip:health", "engine", health_payload)

        except Exception as err:
            REDIS_ERRORS_TOTAL.labels(operation="publish_state").inc()
            log.warning("redis.publish_state_error", error=str(err), error_type=type(err).__name__)

    async def set_json(self, key: str, value: Any) -> bool:
        """Cache a JSON document (e.g. `fsip:decisions`) so the stateless api can serve it without the engine."""
        if not self._redis:
            return False
        try:
            await self._redis.set(key, json.dumps(value, default=str))
            return True
        except Exception as err:
            REDIS_ERRORS_TOTAL.labels(operation="set_json").inc()
            log.warning("redis.set_json_error", key=key, error=str(err))
            return False

    async def event(self, event_type: str, data: dict[str, Any]) -> str | None:
        """Append a custom domain event to the Redis Stream."""
        if not self._redis:
            try:
                await self.connect()
            except Exception:
                return None
        if not self._redis:
            return None

        try:
            payload = {
                "event_type": event_type,
                "data": json.dumps(data),
            }
            stream_id = await self._redis.xadd(
                "fsip:domain-events",
                payload,
                maxlen=self.stream_maxlen,
                approximate=True,
            )
            return str(stream_id)
        except Exception as err:
            REDIS_ERRORS_TOTAL.labels(operation="event").inc()
            log.warning("redis.event_error", event_type=event_type, error=str(err))
            return None
