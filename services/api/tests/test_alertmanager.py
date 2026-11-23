from app.main import app
from app.routers.system import merge_alertmanager
from fastapi.testclient import TestClient

FIRING = {"status": "firing", "alerts": [{"status": "firing", "fingerprint": "abc", "startsAt": "2026-11-25T10:00:00Z",
                                          "labels": {"alertname": "SimulatorDown", "severity": "critical"},
                                          "annotations": {"summary": "breaker OPEN"}}]}


def test_merge_opens_then_resolves_keeping_the_id():
    doc = merge_alertmanager({}, FIRING, 1000.0)
    a = doc["items"]["abc"]
    assert a["category"] == "SYSTEM" and a["severity"] == "CRITICAL" and a["status"] == "OPEN" and a["id"] < 0
    resolved = {"alerts": [{**FIRING["alerts"][0], "status": "resolved", "endsAt": "2026-11-25T10:01:00Z"}]}
    doc2 = merge_alertmanager(doc, resolved, 1060.0)
    assert doc2["items"]["abc"]["status"] == "RESOLVED" and doc2["items"]["abc"]["id"] == a["id"]
    assert merge_alertmanager(doc2, {"alerts": []}, 1060.0 + 3601)["items"] == {}   # resolved ones age out


def test_webhook_requires_the_internal_token():
    with TestClient(app) as c:
        assert c.post("/api/internal/alertmanager", json=FIRING).status_code == 401
        ok = c.post("/api/internal/alertmanager", json=FIRING, headers={"Authorization": "Bearer change-me-internal"})
        assert ok.status_code == 200
