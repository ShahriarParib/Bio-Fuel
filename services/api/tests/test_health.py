import httpx
import respx
from app.main import app
from fastapi.testclient import TestClient


def test_live_and_ping():
    with TestClient(app) as c:
        assert c.get("/health/live").json()["status"] == "ok"
        assert c.get("/api/ping").json()["pong"] is True


@respx.mock
def test_system_status_marks_down_components():
    respx.get("http://engine:8100/health/ready").mock(return_value=httpx.Response(200, json={}))
    respx.get("http://intelligence:8200/health/ready").mock(side_effect=httpx.ConnectError("down"))
    respx.get("http://simulator:8000/v1/health").mock(return_value=httpx.Response(200, json={}))
    with TestClient(app) as c:
        body = c.get("/api/system/status").json()
        assert body["components"]["prediction_service"]["status"] == "Down"
        assert body["components"]["decision_engine"]["status"] == "Healthy"
        assert body["overall"] == "Degraded"          # redis is not running in unit tests either


def test_request_metrics_recorded():
    with TestClient(app) as c:
        c.get("/api/ping")
        assert 'route="/api/ping"' in c.get("/metrics/").text
