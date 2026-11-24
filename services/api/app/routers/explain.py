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
        "minutes_to_recover": round((i.get("mttr_s") or 0) / 60, 1) if i.get("mttr_s") else None,
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
async def _explain(request: Request, kind: str, key: str, facts: dict[str, Any], template: str) -> LlmResult:
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
        text, elapsed = await _client.complete(INSTRUCTION, prompt_facts)
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
    result = await _explain(request, "decision", str(decision_id), facts, _decision_template(facts))
    return _payload(result, facts)


@router.get("/incidents/{incident_id}/explain")
async def explain_incident(request: Request, incident_id: int):
    from .system import incidents as incidents_endpoint

    doc = await incidents_endpoint(request)
    target = next((i for i in (doc.get("items") or []) if i.get("id") == incident_id), None)
    if not target:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "No such incident"})

    facts = _incident_facts(target)
    result = await _explain(request, "incident", str(incident_id), facts, _incident_template(facts))
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
