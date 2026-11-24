"""The explain endpoints: what an operator reads when the assistant is on, off, slow, or wrong."""
from __future__ import annotations

import httpx
import pytest
from app.routers import explain
from app.routers.explain import _decision_facts, _decision_template, _incident_facts, _incident_template
from fastapi.testclient import TestClient
from fsip_common.llm import GeminiClient

DECISION = {
    "id": 8029, "status": "EXECUTED", "mode": "autopilot", "confidence": 0.9, "degraded": False,
    "fallback_reason": None, "total_liters": 2780.0, "policy_version": "optimizer@1.0.0",
    "lines": [
        {"quantity": 1130.0, "fuel_type": "DIESEL", "source_depot_id": "depot-gazipur",
         "destination_station_id": "station-mirpur", "reason": "Mirpur Fuel Station DIESEL LOW: 52% full"},
        {"quantity": 1650.0, "fuel_type": "DIESEL", "source_depot_id": "depot-patiya",
         "destination_station_id": "station-karnaphuli", "reason": "Karnaphuli DIESEL LOW: 61% full"},
    ],
    "impact": {"horizon_hours": 12, "targets": [
        {"station_name": "Mirpur Fuel Station", "fuel_type": "DIESEL", "projected_stockout_hours": 9.5,
         "stockout_prob_before": 0.22, "stockout_prob_after": 0.0},
    ]},
    "explanation": {"headline": "Keep stations above their reorder level", "unserved": [],
                    "constraints": ["no departure on a disrupted route"], "forecast_model": "seasonal"},
}

INCIDENT = {
    "id": 9, "component": "redis", "status": "RESOLVED", "severity_status": "DOWN", "summary": "unreachable",
    "mttr_s": 15.9,
    "timeline": [{"kind": "detected", "text": "DOWN: unreachable"},
                 {"kind": "mitigated", "text": "api reads state directly from the engine"},
                 {"kind": "recovered", "text": "component healthy again"}],
}


class TestTemplates:
    """The template is the fallback AND the yardstick, so it has to stand on its own."""

    def test_a_decision_reads_like_a_sentence_an_operator_can_act_on(self):
        text = _decision_template(_decision_facts(DECISION))
        assert "1,130 L of DIESEL from gazipur to mirpur" in text
        assert "Autopilot has dispatched" in text
        assert "Mirpur Fuel Station would run dry in about 9.5 hours without this." in text

    def test_a_proposal_is_not_described_as_already_done(self):
        text = _decision_template(_decision_facts({**DECISION, "mode": "advisory", "status": "PENDING_APPROVAL"}))
        assert text.startswith("The planner proposes")

    def test_degraded_input_is_stated_explicitly(self):
        text = _decision_template(_decision_facts({**DECISION, "degraded": True}))
        assert "degraded" in text and "human should review" in text

    def test_an_empty_plan_says_so_instead_of_nothing(self):
        text = _decision_template(_decision_facts({**DECISION, "lines": [], "impact": {}}))
        assert "No shipment is planned" in text

    def test_an_incident_mentions_the_mitigation_and_the_recovery(self):
        text = _incident_template(_incident_facts(INCIDENT))
        assert "api reads state directly from the engine" in text
        assert "15.9 seconds" in text and "no operator action" in text

    def test_an_open_incident_warns_instead_of_claiming_recovery(self):
        text = _incident_template(_incident_facts({**INCIDENT, "status": "OPEN", "mttr_s": None}))
        assert "still open" in text and "recovered" not in text


class TestFacts:
    def test_the_model_is_only_shown_derived_facts_never_ids_or_tokens(self):
        facts = _decision_facts(DECISION)
        flat = str(facts)
        assert "8029" not in flat                     # no database ids
        assert "idempotency" not in flat and "token" not in flat.lower()
        assert "1130" in flat                         # but the quantities it must repeat are there


class TestEndpointBehaviour:
    """The endpoint must degrade to the template in every failure mode, and say that it did."""

    @pytest.fixture(autouse=True)
    def _no_cache_no_key(self, monkeypatch):
        monkeypatch.setattr(explain, "_client", GeminiClient(api_key=""))
        yield

    def _call(self, monkeypatch):
        async def fake_cached(_request):
            return [DECISION]
        monkeypatch.setattr("app.routers.decisions._cached_decisions", fake_cached)
        with TestClient(_app()) as c:
            return c.get("/api/decisions/8029/explain")

    def test_without_a_key_the_operator_gets_the_written_summary_and_is_told(self, monkeypatch):
        resp = self._call(monkeypatch)
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "template"
        assert body["note"]                               # the UI shows why
        assert "1,130 L of DIESEL" in body["text"]
        assert body["grounded_in"]["total_liters"] == 2780

    def test_a_wording_that_invents_a_number_is_rejected_in_favour_of_the_template(self, monkeypatch):
        async def fake_complete(_i, _f):
            return "Autopilot is sending 1,130 L, roughly 45% of capacity.", 0.4
        monkeypatch.setattr(explain, "_client", GeminiClient(api_key="k"))
        monkeypatch.setattr(explain._client, "complete", fake_complete)
        body = self._call(monkeypatch).json()
        assert body["source"] == "template"
        assert "45" in body["note"] and "did not compute" in body["note"]

    def test_a_faithful_wording_is_served_as_the_assistant_answer(self, monkeypatch):
        async def fake_complete(_i, _f):
            return "Autopilot is topping up Mirpur with 1,130 L of diesel before it runs dry.", 0.3
        monkeypatch.setattr(explain, "_client", GeminiClient(api_key="k"))
        monkeypatch.setattr(explain._client, "complete", fake_complete)
        body = self._call(monkeypatch).json()
        assert body["source"] == "gemini"
        assert body["text"].startswith("Autopilot is topping up Mirpur")

    def test_a_missing_decision_is_a_404_not_an_invented_explanation(self, monkeypatch):
        async def fake_cached(_request):
            return []
        async def fake_engine(*_a, **_k):
            raise httpx.HTTPError("engine down")
        monkeypatch.setattr("app.routers.decisions._cached_decisions", fake_cached)
        monkeypatch.setattr("app.routers.decisions._engine", fake_engine)
        with TestClient(_app()) as c:
            assert c.get("/api/decisions/404404/explain").status_code in (404, 502)

    def test_the_status_endpoint_states_the_guarantees(self):
        with TestClient(_app()) as c:
            body = c.get("/api/assistant").json()
        assert body["state"] == "disabled"
        assert any("never makes or changes a decision" in g for g in body["guarantees"])


def _app():
    from app.main import app
    return app
