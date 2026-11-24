"""Plain-language explanations of what the platform already decided.

The rule this endpoint is built around: **the model may not decide anything, and it may not introduce a number.**
Every recommendation is produced by the optimiser and checked by the constraint rules in `services/engine`. What an
operator on a night shift often lacks is not a plan but a sentence explaining it. So we take the structured facts we
already computed, hand them to Gemini with an instruction to re-word and nothing else, and then verify the output:

  1. a deterministic template answer is rendered first - it is the fallback *and* the yardstick;
  2. the model is asked to re-word the same facts (hard timeout, then cooldown);
  3. every number in the model's answer must already appear in the facts, or the answer is thrown away
     (`fsip_llm_requests_total{outcome="rejected_ungrounded"}`) and the template is served instead;
  4. the response always says which of the two the operator is reading.

So the worst case of the LLM being slow, down, or wrong is a slightly drier sentence - never a wrong litre count,
and never a changed decision.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fsip_common.llm import (
    LLM_CACHE,
    LLM_REQUESTS,
    GeminiClient,
    LlmResult,
    LlmUnavailable,
    check_grounded,
    collect_numbers,
)
from fsip_common.logging import get_logger

log = get_logger()
router = APIRouter(prefix="/api", tags=["explain"])

_client = GeminiClient()
CACHE_TTL_S = 900

INSTRUCTION = (
    "You write for a fuel depot operator on shift in Bangladesh. You are given facts a scheduling system has "
    "already computed. Re-word them as 2 to 4 short sentences of plain English: what is being done, why, and what "
    "to watch. Rules you must follow exactly: use only the numbers given to you, never compute or estimate a new "
    "one; never invent station names, times or causes; never give advice that contradicts the facts; no markdown, "
    "no bullet points, no headings, no preamble such as 'Here is'. If the facts are thin, write less."
)


# --------------------------------------------------------------------------- facts
def _pct(value: Any) -> int | None:
    """Probabilities and confidences go to the model as percentages.

    Not cosmetic: a model given 0.22 will quite correctly write "22%", and the grounding check would then
    reject a true statement. Converting here keeps the guard strict without punishing good answers.
    """
    return None if value is None else round(float(value) * 100)


def _decision_facts(d: dict[str, Any]) -> dict[str, Any]:
    exp = d.get("explanation") or {}
    impact = d.get("impact") or {}
    lines = [
        {
            "liters": round(ln["quantity"]) if ln.get("quantity") is not None else None,
            "fuel": ln.get("fuel_type"),
            "from": str(ln.get("source_depot_id", "")).removeprefix("depot-"),
            "to": str(ln.get("destination_station_id", "")).removeprefix("station-"),
            "why": ln.get("reason"),
        }
        for ln in (d.get("lines") or [])
    ]
    targets = [
        {
            "station": t.get("station_name"),
            "fuel": t.get("fuel_type"),
            "hours_until_stockout_if_nothing_is_done": t.get("projected_stockout_hours"),
            "chance_of_running_dry_before_percent": _pct(t.get("stockout_prob_before")),
            "chance_of_running_dry_after_percent": _pct(t.get("stockout_prob_after")),
        }
        for t in (impact.get("targets") or [])
    ]
    return {
        "what_is_shipped": lines,
        "total_liters": round(d["total_liters"]) if d.get("total_liters") is not None else None,
        "status": d.get("status"),
        "decided_by": "autopilot" if d.get("mode") == "autopilot" else "waiting for operator approval",
        "headline_reason": exp.get("headline"),
        "risk_before_and_after": targets,
        "stations_we_could_not_serve": exp.get("unserved") or [],
        "confidence_percent": _pct(d.get("confidence")),
        "data_was_degraded": bool(d.get("degraded")),
        "fallback_reason": d.get("fallback_reason"),
        "planner": d.get("policy_version"),
        "forecast_model": exp.get("forecast_model"),
        "rules_every_shipment_passed": exp.get("constraints") or [],
    }


def _incident_facts(i: dict[str, Any]) -> dict[str, Any]:
    return {
        "component": i.get("component"),
        "state": i.get("status"),
        "what_went_wrong": i.get("summary"),
        "severity": i.get("severity_status"),
        "seconds_to_recover": i.get("mttr_s"),
        "what_the_platform_did": [t.get("text") for t in (i.get("timeline") or []) if t.get("kind") == "mitigated"],
        "timeline": [f"{t.get('kind')}: {t.get('text')}" for t in (i.get("timeline") or [])],
    }


# --------------------------------------------------------------------------- templates (the fallback and the yardstick)
def _decision_template(f: dict[str, Any]) -> str:
    ships = f["what_is_shipped"]
    if not ships:
        return "No shipment is planned right now: every station is above its reorder level for the next 12 hours."
    parts = [f"{s['liters']:,.0f} L of {s['fuel']} from {s['from']} to {s['to']}" for s in ships
             if s.get("liters") is not None]
    who = "Autopilot has dispatched" if f["decided_by"] == "autopilot" else "The planner proposes"
    out = f"{who} {'; '.join(parts)}."
    if f.get("headline_reason"):
        out += f" Reason: {f['headline_reason']}."
    risky = [t for t in f["risk_before_and_after"]
             if (t.get("chance_of_running_dry_before_percent") or 0) > 0
             or t.get("hours_until_stockout_if_nothing_is_done")]
    if risky:
        t = risky[0]
        hrs = t.get("hours_until_stockout_if_nothing_is_done")
        when = f" would run dry in about {hrs:g} hours" if hrs else " was at risk of running dry"
        out += f" {t['station']}{when} without this."
    if f.get("stations_we_could_not_serve"):
        out += f" {len(f['stations_we_could_not_serve'])} request(s) could not be served this round."
    if f.get("data_was_degraded"):
        out += " The input data was degraded, so confidence is lower than usual and a human should review it."
    return out


def _incident_template(f: dict[str, Any]) -> str:
    did = f["what_the_platform_did"]
    out = f"The {f['component']} component reported: {f['what_went_wrong']}."
    if did:
        out += f" The platform kept working by this route: {did[0]}."
    if f.get("state") == "RESOLVED" and f.get("seconds_to_recover"):
        out += f" It recovered on its own after {f['seconds_to_recover']:g} seconds, with no operator action."
    elif f.get("state") != "RESOLVED":
        out += " It is still open, so treat the affected numbers with care."
    return out


# --------------------------------------------------------------------------- the pipeline
async def _explain(request: Request, kind: str, facts: dict[str, Any], template: str,
                   instruction: str = "") -> LlmResult:
    instruction = instruction or INSTRUCTION
    redis = getattr(request.app.state, "redis", None)
    prompt_facts = json.dumps(facts, ensure_ascii=False, sort_keys=True, default=str)
    cache_key = "fsip:llm:" + hashlib.blake2b(
        f"{kind}|{_client.model}|{prompt_facts}".encode(), digest_size=12).hexdigest()

    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached:
                LLM_CACHE.labels("hit").inc()
                return LlmResult(**json.loads(cached), cached=True)
        except Exception:                                   # a cache miss must never fail the request
            pass
    LLM_CACHE.labels("miss").inc()

    result = LlmResult(text=template, source="template", note="assistant disabled")
    try:
        text, elapsed = await _client.complete(instruction, prompt_facts)
        allowed = collect_numbers(facts)
        bad = check_grounded(text, allowed)
        if bad is not None:
            LLM_REQUESTS.labels("rejected_ungrounded").inc()
            log.warning("llm.rejected_ungrounded", kind=kind, number=bad)
            result = LlmResult(text=template, source="template", latency_ms=int(elapsed * 1000),
                               note=f"the assistant's wording contained a number ({bad}) the platform did not "
                                    f"compute, so the written summary is shown instead")
        else:
            result = LlmResult(text=text, source="gemini", model=_client.model, latency_ms=int(elapsed * 1000))
    except LlmUnavailable as err:
        result = LlmResult(text=template, source="template", note=str(err))

    if redis is not None and result.source == "gemini":
        try:
            await redis.setex(cache_key, CACHE_TTL_S,
                              json.dumps({"text": result.text, "source": result.source, "model": result.model,
                                          "latency_ms": result.latency_ms, "note": result.note}))
        except Exception:
            pass
    return result


def _payload(result: LlmResult, facts: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": result.text,
        "source": result.source,          # "gemini" = re-worded by the model, "template" = our own sentence
        "model": result.model,
        "latency_ms": result.latency_ms,
        "cached": result.cached,
        "note": result.note,
        "grounded_in": facts,             # exactly what the model was allowed to see; shown in the UI on request
    }


# --------------------------------------------------------------------------- endpoints
@router.get("/decisions/{decision_id}/explain")
async def explain_decision(request: Request, decision_id: int):
    from .decisions import _cached_decisions, _engine  # local import: avoids a cycle at module load

    target = next((d for d in await _cached_decisions(request) if d.get("id") == decision_id), None)
    if target is None:
        try:
            target = await _engine(request, "GET", f"/internal/decisions/{decision_id}")
        except Exception:
            # the engine being unreachable is not a reason to invent an explanation: say we do not have it
            target = None
    if not target:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "No such decision"})

    facts = _decision_facts(target)
    result = await _explain(request, "decision", facts, _decision_template(facts))
    return _payload(result, facts)


@router.get("/incidents/{incident_id}/explain")
async def explain_incident(request: Request, incident_id: int):
    from .system import incidents as incidents_endpoint

    doc = await incidents_endpoint(request)
    target = next((i for i in (doc.get("items") or []) if i.get("id") == incident_id), None)
    if not target:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "No such incident"})

    facts = _incident_facts(target)
    result = await _explain(request, "incident", facts, _incident_template(facts))
    return _payload(result, facts)


@router.get("/assistant")
async def assistant_status():
    """What the console shows about the assistant: whether it is on, which model, and the guarantees around it."""
    return {
        **_client.status(),
        "timeout_s": _client.timeout_s,
        "cache_ttl_s": CACHE_TTL_S,
        "guarantees": [
            "the assistant never makes or changes a decision; it re-words facts the platform computed",
            "any explanation containing a number the platform did not compute is discarded",
            "if it is slow, down or rejected, the operator gets the platform's own written summary",
        ],
    }


# --------------------------------------------------------------------------- shift handover and postmortem
SHIFT_INSTRUCTION = (
    "You write the handover note an outgoing fuel-supply operator leaves for the person taking over the next "
    "shift, in Bangladesh. You are given facts a scheduling system computed. Write 4 to 6 short sentences of plain "
    "English in this order: what the shift was like, what the platform did, what broke and whether it recovered, "
    "and what the next operator should watch. Rules you must follow exactly: use only the numbers given to you, "
    "never compute or estimate a new one; never invent stations, causes or times; no markdown, no bullet points, "
    "no headings, no preamble, no greeting, and do not address the reader by name."
)

POSTMORTEM_INSTRUCTION = (
    "You write the short postmortem an operations team reads the morning after an incident. You are given facts a "
    "monitoring system recorded. Write 3 to 5 short sentences of plain English: what failed, what someone using the "
    "system would have noticed, how the platform carried on, and how it ended. Rules you must follow exactly: use "
    "only the numbers given to you, never compute or estimate a new one; never invent a cause that is not in the "
    "facts - if the cause is unknown, say so. Only the entries under "
    "actions_the_platform_took_to_keep_serving are things the platform did; an operating-mode transition is "
    "something that was observed as a consequence, never an action taken to fix the problem. No markdown, no "
    "bullet points, no headings, no preamble, no greeting."
)


def _shift_facts(state: dict[str, Any], decisions: list[dict[str, Any]], incidents: list[dict[str, Any]],
                 window: int) -> dict[str, Any]:
    tick = int(state.get("tick") or 0)
    since = max(0, tick - window)
    mine = [d for d in decisions if int(d.get("tick") or 0) >= since]
    m = state.get("metrics") or {}
    impact = state.get("impact_meter") or {}
    without = impact.get("without_platform") or {}
    risks = sorted((r for r in (state.get("risk") or []) if r.get("level") in ("HIGH", "CRITICAL", "MEDIUM")),
                   key=lambda r: (r.get("hours_to_stockout") is None, r.get("hours_to_stockout") or 0))
    slo = state.get("slo") or {}
    resolved = [i for i in incidents if i.get("status") == "RESOLVED" and i.get("mttr_s")]
    still_open = [i for i in incidents if i.get("status") == "OPEN"]
    return {
        "shift_length_ticks": min(window, tick),
        "ticks_covered": f"{since} to {tick}",
        "clock": state.get("clock_label") or state.get("sim_time"),
        "fuel_service_level_percent": _pct(m.get("service_level")),
        "litres_delivered_to_stations": round(m.get("served_liters") or 0),
        "litres_of_demand_not_met": round(m.get("unmet_liters") or 0),
        "failed_shipments": m.get("allocation_failures"),
        "litres_lost_to_overflow": round(m.get("overflow_liters_est") or 0),
        "without_this_platform": ({
            "service_level_percent": _pct(without.get("service_level")),
            "stations_that_would_have_run_dry": without.get("stations_that_ran_dry"),
            "method": impact.get("method"),
        } if without else None),
        "decisions_this_shift": len(mine),
        "decisions_executed": sum(1 for d in mine if d.get("status") == "EXECUTED"),
        "decisions_waiting_for_a_human": sum(1 for d in mine if str(d.get("status") or "").startswith("PENDING")),
        "litres_dispatched_this_shift": round(sum(float(d.get("total_liters") or 0) for d in mine)),
        "running_in": state.get("operating_mode"),
        "planner": state.get("policy"),
        "engine_mode": state.get("engine_mode"),
        "open_alerts": state.get("alerts_open_count"),
        # counts are given explicitly: the sample lists below are truncated, and a model asked to count list
        # items would otherwise state a number that is true of the list but not of the shift
        "incidents_that_closed_themselves_count": len(resolved),
        "incidents_that_closed_themselves_examples": [
            {"component": i.get("component"), "what": i.get("summary"), "seconds_to_recover": i.get("mttr_s")}
            for i in resolved][:4],
        "incidents_still_open_count": len(still_open),
        "incidents_still_open_examples": [{"component": i.get("component"), "what": i.get("summary")}
                                          for i in still_open][:4],
        "watch_next": [
            {"station": str(r.get("station_id") or "").removeprefix("station-"), "fuel": r.get("fuel_type"),
             "risk": r.get("level"), "hours_of_cover_left": r.get("hours_to_stockout")}
            for r in risks[:3]],
        "fuel_running_short_network_wide": [f for f, on in (state.get("scarcity") or {}).items() if on],
        "service_level_budget_remaining_percent": _pct(slo.get("budget_remaining")),
        "change_freeze_in_force": slo.get("change_freeze"),
    }


def _shift_template(f: dict[str, Any]) -> str:
    out = (f"Over the last {f['shift_length_ticks']} ticks (t{f['ticks_covered']}) the network served "
           f"{f['fuel_service_level_percent']}% of demand: {f['litres_delivered_to_stations']:,} L delivered, "
           f"{f['litres_of_demand_not_met']:,} L unmet.")
    out += (f" The platform made {f['decisions_this_shift']} decisions this shift "
            f"({f['litres_dispatched_this_shift']:,} L dispatched, {f['failed_shipments']} failed shipments, "
            f"{f['litres_lost_to_overflow']:,} L lost to overflow), running in {f['running_in']} mode on the "
            f"{f['planner']} planner.")
    if f["decisions_waiting_for_a_human"]:
        out += f" {f['decisions_waiting_for_a_human']} recommendation(s) are waiting for approval."
    if f["incidents_that_closed_themselves_count"]:
        i = f["incidents_that_closed_themselves_examples"][0]
        out += (f" {f['incidents_that_closed_themselves_count']} component incident(s) opened and closed with "
                f"nobody acting, the first being {i['component']} ({i['what']}) back in "
                f"{i['seconds_to_recover']:g} s.")
    if f["incidents_still_open_count"]:
        out += (" Still open: "
                + ", ".join(f"{i['component']} ({i['what']})" for i in f["incidents_still_open_examples"]) + ".")
    if f["watch_next"]:
        w = f["watch_next"][0]
        cover = (f", about {w['hours_of_cover_left']:g} h of cover left"
                 if w.get("hours_of_cover_left") is not None else "")
        out += f" Watch {w['station']} {w['fuel']} first ({w['risk']} risk{cover})."
    else:
        out += " No station is at elevated risk right now."
    if f["fuel_running_short_network_wide"]:
        out += f" Network-wide scarcity is active for {', '.join(f['fuel_running_short_network_wide'])}."
    if f["change_freeze_in_force"]:
        out += " A change freeze is in force: policy switches need an explicit override."
    return out


def _postmortem_facts(i: dict[str, Any]) -> dict[str, Any]:
    tl = i.get("timeline") or []
    return {
        "component": i.get("component"),
        "what_failed": i.get("summary"),
        "severity": i.get("severity_status"),
        "state_now": i.get("status"),
        "seconds_from_detection_to_recovery": i.get("mttr_s"),
        "detected": next((t.get("text") for t in tl if t.get("kind") == "detected"), None),
        "actions_the_platform_took_to_keep_serving": [t.get("text") for t in tl if t.get("kind") == "mitigated"],
        "operating_mode_transitions_observed": [t.get("text") for t in tl if t.get("kind") == "mode"],
        "sequence_in_order": [f"{t.get('kind')}: {t.get('text')}" for t in tl],
        "ended": next((t.get("text") for t in tl if t.get("kind") == "recovered"), None),
        "operator_action_needed": False,
        "root_cause": "not recorded: the platform observes the symptom above, not the cause inside the component",
    }


def _postmortem_template(f: dict[str, Any]) -> str:
    out = f"The {f['component']} component failed with: {f['what_failed']}."
    if f["actions_the_platform_took_to_keep_serving"]:
        out += f" The platform carried on by this route: {f['actions_the_platform_took_to_keep_serving'][0]}."
    if f["operating_mode_transitions_observed"]:
        out += f" Operating mode moved: {'; '.join(f['operating_mode_transitions_observed'])}."
    if f["state_now"] == "RESOLVED" and f.get("seconds_from_detection_to_recovery"):
        out += (f" It recovered {f['seconds_from_detection_to_recovery']:g} s after detection with no operator "
                f"action, so no shipment waited on a human.")
    else:
        out += " It is still open, and the mitigation above is what is holding the service up."
    out += " The platform records the symptom, not the root cause, so the cause is whatever the component reported."
    return out


@router.get("/summary")
async def shift_summary(request: Request, window_ticks: int = 96):
    """The handover note for whoever takes the next shift: what happened, what broke, what to watch.

    `window_ticks` defaults to 96 - one simulated day at 15-minute ticks.
    """
    from .decisions import _cached_decisions
    from .system import _redis_json
    from .system import incidents as incidents_endpoint

    window = max(1, min(int(window_ticks), 2000))
    state = await _redis_json(request, "fsip:state") or {}
    if not state:
        raise HTTPException(status_code=503, detail={"code": "NO_STATE", "message": "no published state yet"})
    decisions = await _cached_decisions(request)
    incidents = (await incidents_endpoint(request)).get("items") or []

    facts = _shift_facts(state, decisions, incidents, window)
    result = await _explain(request, "shift", facts, _shift_template(facts), SHIFT_INSTRUCTION)
    return {**_payload(result, facts), "window_ticks": window, "tick": state.get("tick")}


@router.get("/incidents/{incident_id}/postmortem")
async def incident_postmortem(request: Request, incident_id: int):
    """The morning-after note for one incident. Same guard: it may not introduce a number, or a cause."""
    from .system import incidents as incidents_endpoint

    doc = await incidents_endpoint(request)
    target = next((i for i in (doc.get("items") or []) if i.get("id") == incident_id), None)
    if not target:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "No such incident"})
    facts = _postmortem_facts(target)
    result = await _explain(request, "postmortem", facts, _postmortem_template(facts), POSTMORTEM_INSTRUCTION)
    return _payload(result, facts)
