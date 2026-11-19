import httpx
import respx
from fastapi.testclient import TestClient

from app.main import app


def test_live():
    with TestClient(app) as c:
        assert c.get("/health/live").json() == {"status": "ok", "service": "engine"}


@respx.mock
def test_ready_ok_and_fail():
    route = respx.get("http://simulator:8000/v1/health")
    with TestClient(app) as c:
        route.mock(return_value=httpx.Response(200, json={"status": "ok"}))
        assert c.get("/health/ready").status_code == 200
        route.mock(side_effect=httpx.ConnectError("down"))
        r = c.get("/health/ready")
        assert r.status_code == 503 and r.json()["detail"]["code"] == "SIM_UNREACHABLE"


def test_debug_requires_token():
    with TestClient(app) as c:
        assert c.get("/debug/state").status_code == 401


def test_metrics_exposed():
    with TestClient(app) as c:
        assert "fsip_engine_info" in c.get("/metrics/").text
