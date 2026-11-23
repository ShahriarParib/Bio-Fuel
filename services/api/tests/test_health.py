import httpx
import respx
from app.main import app
from fastapi.testclient import TestClient


def test_live_and_ping():
    with TestClient(app) as c:
        assert c.get("/health/live").json()["status"] == "ok"
        assert c.get("/api/ping").json()["pong"] is True


@respx.mock
def test_system_status_uses_engine_measured_health():
    """Status comes from the engine's real-traffic judgement; the simulator's /v1/health is never called."""
    sim_health = respx.get("http://simulator:8000/v1/health").mock(return_value=httpx.Response(200, json={}))
    respx.get("http://engine:8100/internal/health").mock(return_value=httpx.Response(200, json={
        "engine_mode": "DEGRADED", "mode_reasons": ["intelligence: down"],
        "components": {"simulator": {"status": "UP"}, "intelligence": {"status": "DOWN", "detail": "down"}}}))
    with TestClient(app) as c:
        body = c.get("/api/system/status").json()
    assert body["components"]["prediction_service"]["status"] == "Down"
    assert body["components"]["fuel_simulator"]["status"] == "Healthy"
    assert body["components"]["decision_engine"]["status"] == "Degraded"
    assert body["engine_mode"] == "DEGRADED" and body["mode_reasons"] == ["intelligence: down"]
    assert body["overall"] == "Degraded"
    assert not sim_health.called


@respx.mock
def test_system_status_engine_unreachable_is_down():
    respx.get("http://engine:8100/internal/health").mock(side_effect=httpx.ConnectError("down"))
    with TestClient(app) as c:
        body = c.get("/api/system/status").json()
    assert body["components"]["decision_engine"]["status"] == "Down" and body["overall"] == "Down"


def test_demo_controls_need_operator_token():
    with TestClient(app) as c:
        assert c.post("/api/demo/faults", json={"type": "unavailable"}).status_code == 401
        assert c.post("/api/demo/sim/step").status_code == 401


def test_request_metrics_recorded():
    with TestClient(app) as c:
        c.get("/api/ping")
        assert 'route="/api/ping"' in c.get("/metrics/").text
