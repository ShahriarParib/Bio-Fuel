"""Operator actions require a configured token; no token is hard-coded (brief §19)."""
from __future__ import annotations

from app.main import app
from fastapi.testclient import TestClient


def _post(client, path, token=None, body=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(path, json=body or {}, headers=headers)


def test_missing_token_is_401():
    with TestClient(app) as c:
        assert _post(c, "/api/plan-now").status_code == 401


def test_guessable_tokens_are_rejected():
    with TestClient(app) as c:
        for guess in ("operator", "supervisor", "admin"):
            assert _post(c, "/api/plan-now", guess).status_code == 403


def test_operator_cannot_change_policy():
    with TestClient(app) as c:
        r = _post(c, "/api/control/policy", "change-me-operator", {"policy": "heuristic"})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "FORBIDDEN"


def test_valid_token_reaches_engine_or_reports_it_unavailable():
    with TestClient(app) as c:
        r = _post(c, "/api/plan-now", "change-me-operator")
        assert r.status_code in (202, 503)
        if r.status_code == 503:
            assert r.json()["detail"]["code"] == "ENGINE_UNAVAILABLE"


def test_bad_mode_is_422():
    with TestClient(app) as c:
        assert _post(c, "/api/control/mode", "change-me-operator", {"mode": "yolo"}).status_code == 422
