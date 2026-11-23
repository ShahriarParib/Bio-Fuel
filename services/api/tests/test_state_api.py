"""Tests for operator state API and node retrieval."""
from __future__ import annotations

from app.main import app
from fastapi.testclient import TestClient


def test_api_state_without_cache_is_valid_and_never_cached():
    """No Redis and no engine (unit test): the empty fallback state, marked degraded and not cacheable."""
    with TestClient(app) as client:
        resp = client.get("/api/state")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("sim_run_id", "tick", "stations", "depots", "metrics"):
            assert key in data
        assert "ETag" not in resp.headers and resp.headers["Cache-Control"] == "no-store"
        assert resp.headers["X-Degraded"] == "true"


class FakeRedis:
    """What the engine publishes: the document, its content-hash ETag and the tick."""

    def __init__(self):
        self.kv = {"fsip:state": '{"sim_run_id": 3, "tick": 41, "degraded": false, "engine_mode": "NORMAL"}',
                   "fsip:state:etag": "abc123", "fsip:state:tick": "41"}
        self.reads = []

    async def get(self, k):
        self.reads.append(k)
        return self.kv.get(k)

    async def mget(self, *ks):
        self.reads.extend(ks)
        return [self.kv.get(k) for k in ks]

    async def eval(self, *a):
        return 1

    async def ping(self):
        return True

    async def aclose(self):
        pass


def test_api_state_fast_path_raw_bytes_and_content_etag():
    with TestClient(app) as client:
        fake = FakeRedis()
        app.state.redis = fake
        r = client.get("/api/state")
        assert r.status_code == 200 and r.json()["tick"] == 41
        assert r.headers["ETag"] == '"abc123"' and r.headers["X-Data-Tick"] == "41"
        fake.reads.clear()
        r2 = client.get("/api/state", headers={"If-None-Match": '"abc123"'})
        assert r2.status_code == 304
        assert fake.reads == ["fsip:state:etag"]            # the document itself was not read
        # same tick, but the engine published a change (e.g. SAFE_HOLD): new content hash → full response
        fake.kv["fsip:state"] = fake.kv["fsip:state"].replace("NORMAL", "SAFE_HOLD")
        fake.kv["fsip:state:etag"] = "def456"
        r3 = client.get("/api/state", headers={"If-None-Match": '"abc123"'})
        assert r3.status_code == 200 and r3.json()["engine_mode"] == "SAFE_HOLD"


def test_api_station_not_found():
    with TestClient(app) as client:
        resp = client.get("/api/stations/station-ghost")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "NOT_FOUND"


def test_api_depot_not_found():
    with TestClient(app) as client:
        resp = client.get("/api/depots/depot-ghost")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "NOT_FOUND"
