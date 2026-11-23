from app.main import app
from fastapi.testclient import TestClient


def test_live_ready_models():
    with TestClient(app) as c:
        assert c.get("/health/live").json()["status"] == "ok"
        assert c.get("/health/ready").json()["model"]["name"] == "seasonal"
        assert c.get("/models").status_code == 200
        assert "fsip_model_info" in c.get("/metrics/").text
