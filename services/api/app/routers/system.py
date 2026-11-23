"""System status, incidents and the demo Control endpoints.

Status comes from the engine's HealthMonitor (real traffic: breaker, error ratio, p95, stale header), published in the
state document. The simulator's /v1/health is never used: it bypasses fault injection and says "ok" while every real
call fails.

Demo controls (/api/demo/*) are the only place outside tests/harness/chaos scripts that talks to the simulator's
/admin endpoints. They need DEMO_CONTROLS=true and the operator token, and every call is audit-logged.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fsip_common.logging import get_logger
from pydantic import BaseModel, Field

from ..auth import Actor, require_operator
from ..config import get_settings

log = get_logger()
router = APIRouter(prefix="/api", tags=["system"])

_LABEL = {"UP": "Healthy", "DEGRADED": "Degraded", "DOWN": "Down"}


async def _redis_json(request: Request, key: str) -> Any:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return None
    try:
        raw = await redis.get(key)
        return json.loads(raw) if raw else None
    except Exception as err:
        log.warning("api.redis_read_failed", key=key, error=str(err))
        return None


async def _engine_get(request: Request, path: str, timeout: float = 2.0) -> Any:
    s = get_settings()
    try:
        r = await request.app.state.http.get(f"{s.engine_internal_url}{path}", timeout=timeout,
                                             headers={"X-Internal-Token": s.engine_internal_token})
        return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


async def _redis_ok(request: Request) -> bool:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return False
    try:
        return bool(await redis.ping())
    except Exception:
        return False


@router.get("/system/status")
async def system_status(request: Request):
    from ..main import api_window_stats  # late import: main imports this router

    state = await _redis_json(request, "fsip:state")
    engine = await _engine_get(request, "/internal/health")
    redis_up = await _redis_ok(request)
    system = (engine or {}) if engine else ((state or {}).get("system") or {})
    comps_raw: dict[str, Any] = dict(system.get("components") or {})
    components: dict[str, Any] = {}
    names = {"simulator": "fuel_simulator", "sse": "live_stream", "intelligence": "prediction_service",
             "db": "database", "redis": "redis", "optimizer": "optimizer", "data_quality": "data_quality"}
    for k, label in names.items():
        c = comps_raw.get(k)
        if c is not None:
            components[label] = {**c, "status": _LABEL.get(c.get("status"), "Unknown"), "raw_status": c.get("status")}
    components["decision_engine"] = ({"status": "Healthy" if (engine or {}).get("engine_mode") == "NORMAL"
                                      else "Degraded", "engine_mode": engine.get("engine_mode")}
                                     if engine else {"status": "Down", "detail": "engine unreachable"})
    # the api's own view of Redis wins (it is what the api depends on)
    components["redis"] = {**components.get("redis", {}), "status": "Healthy" if redis_up else "Down"}
    stats = api_window_stats()
    components["backend_api"] = {"status": "Healthy", "p95_ms": stats["p95_ms"], "error_rate": stats["error_rate"]}

    statuses = [c["status"] for c in components.values()]
    if "Down" in (components["decision_engine"]["status"], components.get("fuel_simulator", {}).get("status")):
        overall = "Down"
    elif any(st != "Healthy" for st in statuses):
        overall = "Degraded"
    else:
        overall = "Healthy"
    mode = (engine or {}).get("engine_mode") or (state or {}).get("engine_mode", "UNKNOWN")
    return {
        "overall": overall,
        "engine_mode": mode,
        "mode_reasons": system.get("mode_reasons", []),
        "sim_down_for_s": system.get("sim_down_for_s", 0.0),
        "no_route_stations": system.get("no_route_stations", []),
        "data_tick": (state or {}).get("tick"),
        "data_age_seconds": (state or {}).get("age_seconds"),
        "components": components,
        "versions": {"api": os.getenv("APP_VERSION", "dev"), "git_sha": os.getenv("GIT_SHA", "unknown")},
        "p95_latency_ms": stats["p95_ms"],
        "error_rate": stats["error_rate"],
        "checked_at": time.time(),
    }


@router.get("/incidents")
async def incidents(request: Request):
    doc = await _redis_json(request, "fsip:incidents")
    if doc is None:
        doc = await _engine_get(request, "/internal/incidents")
    return doc or {"items": [], "open": 0}


# ------------------------------------------------------------------ demo controls
def _demo_enabled() -> None:
    if not get_settings().demo_controls:
        raise HTTPException(status_code=403, detail={"code": "DEMO_CONTROLS_DISABLED",
                                                     "message": "set DEMO_CONTROLS=true to use the Control page"})


async def _admin(request: Request, method: str, path: str, body: dict | None = None) -> Any:
    s = get_settings()
    try:
        r = await request.app.state.http.request(method, f"{s.sim_base_url}{path}", json=body, timeout=5.0)
    except httpx.HTTPError as err:
        raise HTTPException(status_code=503, detail={"code": "SIM_UNREACHABLE", "message": str(err)}) from None
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail={"code": "SIM_REJECTED", "message": r.text[:300]})
    try:
        return r.json()
    except ValueError:
        return {"ok": True}


def _audit(actor: Actor, action: str, payload: Any) -> None:
    log.info("audit.demo_control", actor=actor.name, role=actor.role, action=action, payload=payload)


@router.post("/demo/sim/{action}")
async def sim_control(action: Literal["run", "pause", "step", "reset"], request: Request,
                      actor: Actor = Depends(require_operator), _: None = Depends(_demo_enabled)):
    if action == "reset":
        await _admin(request, "POST", "/admin/faults/clear")
    out = await _admin(request, "POST", f"/admin/{action}")
    _audit(actor, f"sim.{action}", out)
    return out


class FaultBody(BaseModel):
    type: Literal["unavailable", "error_rate", "latency", "stale_data", "stream_disconnect"]
    duration_seconds: float = Field(default=60, gt=0, le=3600)
    rate: float | None = Field(default=None, ge=0, le=1)
    delay_ms: int | None = Field(default=None, ge=0, le=10000)


@router.post("/demo/faults")
async def inject_fault(body: FaultBody, request: Request, actor: Actor = Depends(require_operator),
                       _: None = Depends(_demo_enabled)):
    params: dict[str, Any] = {}
    if body.rate is not None:
        params["rate"] = body.rate
    if body.delay_ms is not None:
        params["delay_ms"] = body.delay_ms
    fault = {"type": body.type, "duration_seconds": body.duration_seconds, "parameters": params}
    out = await _admin(request, "POST", "/admin/faults", fault)
    _audit(actor, "fault.inject", {**fault, "injected_at": time.time()})
    return {"fault": fault, "injected_at": time.time(), "simulator": out}


@router.post("/demo/faults/clear")
async def clear_faults(request: Request, actor: Actor = Depends(require_operator), _: None = Depends(_demo_enabled)):
    out = await _admin(request, "POST", "/admin/faults/clear")
    _audit(actor, "fault.clear", out)
    return out


@router.get("/demo/faults")
async def list_faults(request: Request, _: None = Depends(_demo_enabled)):
    return await _admin(request, "GET", "/admin/faults")


PRESETS: dict[str, list[dict[str, Any]]] = {
    "demand_spike": [{"type": "demand_spike", "offset": 1, "duration_ticks": 24, "parameters": {"multiplier": 1.8}}],
    "route_cut": [{"type": "route_disruption", "offset": 1, "duration_ticks": 32,
                   "parameters": {"route_ids": ["route-gazipur-mirpur"]}}],
    "single_route_station_cut": [{"type": "route_disruption", "offset": 1, "duration_ticks": 24,
                                  "parameters": {"route_ids": ["route-gazipur-tongi"]}}],
    "supply_delay": [{"type": "shipment_delay", "offset": 1, "duration_ticks": 1, "parameters": {"delay_ticks": 12}}],
    "supply_shortfall": [{"type": "supply_shortfall", "offset": 1, "duration_ticks": 1, "parameters": {"factor": 0.5}}],
    "station_outage": [{"type": "station_outage", "offset": 1, "duration_ticks": 8,
                        "parameters": {"station_ids": ["station-karnaphuli"]}}],
    "depot_constraint": [{"type": "depot_constraint", "offset": 1, "duration_ticks": 16, "parameters": {}}],
}
PRESETS["combined"] = PRESETS["demand_spike"] + PRESETS["route_cut"] + PRESETS["supply_delay"]


class EventBody(BaseModel):
    preset: str = Field(pattern="^(" + "|".join([*PRESETS, "combined"]) + ")$")


@router.post("/demo/events")
async def inject_event(body: EventBody, request: Request, actor: Actor = Depends(require_operator),
                       _: None = Depends(_demo_enabled)):
    state = await _redis_json(request, "fsip:state") or {}
    tick = int(state.get("tick") or 0)
    created = []
    for spec in PRESETS[body.preset]:
        ev = {"type": spec["type"], "start_tick": tick + spec["offset"], "duration_ticks": spec["duration_ticks"],
              "parameters": spec["parameters"]}
        created.append(await _admin(request, "POST", "/admin/events", ev))
    _audit(actor, "event.inject", {"preset": body.preset, "tick": tick})
    return {"preset": body.preset, "at_tick": tick, "created": created}


# ------------------------------------------------------------------ Alertmanager → SYSTEM alerts
SYSTEM_ALERTS_KEY = "fsip:system-alerts"


def _require_internal(request: Request) -> None:
    import hmac
    auth = request.headers.get("authorization", "")
    token = auth.split(" ", 1)[1] if auth.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(token.encode(), get_settings().engine_internal_token.encode()):
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "bad internal token"})


def merge_alertmanager(doc: dict[str, Any], payload: dict[str, Any], now: float) -> dict[str, Any]:
    """Pure: apply one Alertmanager webhook payload to the {fingerprint: alert} document."""
    items = dict(doc.get("items") or {})
    for a in payload.get("alerts", []):
        fp = a.get("fingerprint") or a.get("labels", {}).get("alertname", "unknown")
        labels, ann = a.get("labels", {}), a.get("annotations", {})
        sev = {"critical": "CRITICAL", "warning": "WARNING"}.get(labels.get("severity", ""), "INFO")
        firing = a.get("status") == "firing"
        prev = items.get(fp, {})
        items[fp] = {
            "id": prev.get("id") or -(abs(hash(fp)) % 10**6) - 1,  # negative ids: not engine alerts (ack not needed)
            "fingerprint": f"system:{fp}", "category": "SYSTEM", "severity": sev,
            "status": "OPEN" if firing else "RESOLVED",
            "title": labels.get("alertname", "alert"), "message": ann.get("summary", ""),
            "entity_type": "platform", "entity_id": labels.get("job") or labels.get("slo"),
            "opened_at": a.get("startsAt"), "resolved_at": None if firing else a.get("endsAt"),
            "data": {"labels": labels, "source": "prometheus/alertmanager"}, "updated": now,
        }
    # keep resolved ones for 1 h
    items = {k: v for k, v in items.items() if v["status"] == "OPEN" or now - v["updated"] < 3600}
    return {"items": items}


@router.post("/internal/alertmanager", include_in_schema=False)
async def alertmanager_webhook(request: Request):
    _require_internal(request)
    payload = await request.json()
    redis = getattr(request.app.state, "redis", None)
    doc = await _redis_json(request, SYSTEM_ALERTS_KEY) or {}
    doc = merge_alertmanager(doc, payload, time.time())
    if redis is not None:
        try:
            await redis.set(SYSTEM_ALERTS_KEY, json.dumps(doc))
        except Exception as err:
            log.warning("api.system_alerts_store_failed", error=str(err))
    log.info("alertmanager.received", alerts=[a.get("labels", {}).get("alertname") for a in payload.get("alerts", [])],
             status=payload.get("status"))
    return {"ok": True, "stored": len(doc["items"])}


async def system_alerts(request: Request) -> list[dict[str, Any]]:
    doc = await _redis_json(request, SYSTEM_ALERTS_KEY) or {}
    return list((doc.get("items") or {}).values())
