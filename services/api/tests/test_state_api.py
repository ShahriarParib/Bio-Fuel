"""Tests for operator state API and node retrieval."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from app.main import app


def test_api_state_returns_valid_json():
    with TestClient(app) as client:
        resp = client.get("/api/state")
        assert resp.status_code == 200
        data = resp.json()
        assert "sim_run_id" in data
        assert "tick" in data
        assert "stations" in data
        assert "depots" in data
        assert "metrics" in data
        assert "ETag" in resp.headers


def test_api_state_if_none_match_304():
    with TestClient(app) as client:
        resp1 = client.get("/api/state")
        etag = resp1.headers.get("ETag")
        if etag:
            resp2 = client.get("/api/state", headers={"If-None-Match": etag})
            assert resp2.status_code == 304


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
