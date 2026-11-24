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


STATE = {
    "tick": 192, "clock_label": "Day 3 00:00", "operating_mode": "advisory", "policy": "optimizer",
    "engine_mode": "NORMAL", "alerts_open_count": {"CRITICAL": 0, "WARNING": 1},
    "metrics": {"service_level": 1.0, "served_liters": 186352.1, "unmet_liters": 0.0,
                "allocation_failures": 0, "overflow_liters_est": 0.0},
    "impact_meter": {"method": "exact replay of observed demand, no deliveries",
                     "without_platform": {"service_level": 0.461, "stations_that_ran_dry": 4}},
    "risk": [{"station_id": "station-tongi", "fuel_type": "DIESEL", "level": "HIGH", "hours_to_stockout": 6.5},
             {"station_id": "station-mirpur", "fuel_type": "PETROL", "level": "LOW", "hours_to_stockout": None}],
    "scarcity": {"DIESEL": False, "PETROL": False, "OCTANE": False},
    "slo": {"budget_remaining": 1.0, "change_freeze": False},
}


class TestShiftHandover:
    """The handover note is read once by a human who then acts on it, so its numbers have to be the shift's."""

    def test_it_reports_the_shift_not_the_sample(self):
        """The lists shown to the model are truncated; the counts must not be."""
        many = [{"id": n, "component": "redis", "status": "RESOLVED", "summary": "unreachable", "mttr_s": 15.9,
                 "timeline": []} for n in range(9)]
        f = explain._shift_facts(STATE, [], many, 96)
        assert f["incidents_that_closed_themselves_count"] == 9
        assert len(f["incidents_that_closed_themselves_examples"]) == 4      # the model only sees four
        assert "9 component incident(s)" in explain._shift_template(f)

    def test_it_counts_only_decisions_inside_the_window(self):
        decisions = [{"id": 1, "tick": 10, "status": "EXECUTED", "total_liters": 500.0},    # before the window
                     {"id": 2, "tick": 150, "status": "EXECUTED", "total_liters": 1000.0},
                     {"id": 3, "tick": 180, "status": "PENDING_APPROVAL", "total_liters": 700.0}]
        f = explain._shift_facts(STATE, decisions, [], 96)                   # window = ticks 96..192
        assert f["decisions_this_shift"] == 2
        assert f["decisions_executed"] == 1
        assert f["decisions_waiting_for_a_human"] == 1
        assert f["litres_dispatched_this_shift"] == 1700

    def test_it_tells_the_next_operator_what_to_watch_first(self):
        f = explain._shift_facts(STATE, [], [], 96)
        assert f["watch_next"][0]["station"] == "tongi"                      # the soonest stockout comes first
        assert "Watch tongi DIESEL first" in explain._shift_template(f)

    def test_a_quiet_shift_says_so_rather_than_inventing_a_worry(self):
        quiet = {**STATE, "risk": [{"station_id": "station-mirpur", "fuel_type": "PETROL", "level": "LOW"}]}
        assert "No station is at elevated risk" in explain._shift_template(explain._shift_facts(quiet, [], [], 96))

    def test_a_change_freeze_is_handed_over_explicitly(self):
        frozen = {**STATE, "slo": {"budget_remaining": 0.1, "change_freeze": True}}
        assert "change freeze is in force" in explain._shift_template(explain._shift_facts(frozen, [], [], 96))


class TestPostmortem:
    """A postmortem is read the morning after, when nobody remembers what happened."""

    def test_mitigations_and_mode_changes_are_not_the_same_thing(self):
        """A mode change is a consequence we observed, never an action we took - the labels must say so."""
        f = explain._postmortem_facts(INCIDENT | {"timeline": [
            {"kind": "detected", "text": "DOWN: unreachable"},
            {"kind": "mitigated", "text": "api reads state directly from the engine"},
            {"kind": "mode", "text": "engine mode -> NORMAL"},
            {"kind": "recovered", "text": "component healthy again"}]})
        assert f["actions_the_platform_took_to_keep_serving"] == ["api reads state directly from the engine"]
        assert f["operating_mode_transitions_observed"] == ["engine mode -> NORMAL"]
        assert f["sequence_in_order"][0].startswith("detected:")

    def test_an_unknown_cause_is_stated_as_unknown(self):
        f = explain._postmortem_facts(INCIDENT)
        assert "not recorded" in f["root_cause"]
        assert "symptom, not the root cause" in explain._postmortem_template(f)

    def test_a_resolved_incident_reports_unattended_recovery(self):
        text = explain._postmortem_template(explain._postmortem_facts(INCIDENT))
        assert "15.9 s after detection with no operator action" in text

    def test_an_open_incident_is_not_written_up_as_over(self):
        text = explain._postmortem_template(explain._postmortem_facts({**INCIDENT, "status": "OPEN", "mttr_s": None}))
        assert "still open" in text and "recovered" not in text
