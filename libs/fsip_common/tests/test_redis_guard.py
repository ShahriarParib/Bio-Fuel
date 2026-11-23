import asyncio

import pytest
from fsip_common import redis_guard
from fsip_common.redis_guard import GuardedRedis, RedisUnavailable


class Fake:
    def __init__(self):
        self.calls, self.fail = 0, True

    async def get(self, key):
        self.calls += 1
        if self.fail:
            raise ConnectionError("down")
        return "v"


def test_fails_fast_after_a_failure_then_probes(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(redis_guard.time, "monotonic", lambda: clock[0])
    fake = Fake()
    g = GuardedRedis(fake, cooldown_s=5)

    async def run():
        with pytest.raises(ConnectionError):
            await g.get("k")
        for _ in range(20):                      # 20 callers during the cooldown: none reaches Redis
            with pytest.raises(RedisUnavailable):
                await g.get("k")
        assert fake.calls == 1 and not g.available
        clock[0] += 5.1
        fake.fail = False
        assert await g.get("k") == "v"           # one probe succeeds → closed again
        assert g.available and fake.calls == 2
        await asyncio.gather(*(g.get("k") for _ in range(5)))   # healthy: concurrent calls all pass
        assert fake.calls == 7

    asyncio.run(run())
