"""Component health, engine mode machine and incidents (brief §12; docs/architecture.md §2, §4).

Health is measured from REAL traffic: the simulator's /v1/health bypasses fault injection and answers "ok" while every
real call returns 503, so it is never used here. Pure functions do the judging (unit-tested without I/O); the
HealthMonitor loop only collects inputs, applies them and publishes.

Modes (precedence top to bottom):
- SAFE_HOLD           simulator DOWN for >= safe_hold_after_s: no writes, cached state shown with its age.
- NO_FEASIBLE_ROUTES  some station has no route a shipment could depart on: advisory only, operator told which.
- DEGRADED            any component not UP, or data stale: autopilot suspended, recommendations need a human.
- NORMAL
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fsip_common.logging import get_logger
from prometheus_client import Counter, Gauge

log = get_logger()

UP, DEGRADED, DOWN = "UP", "DEGRADED", "DOWN"
MODES = ("NORMAL", "DEGRADED", "SAFE_HOLD", "NO_FEASIBLE_ROUTES")

ENGINE_MODE = Gauge("fsip_engine_mode", "Engine mode (1 = current)", ["mode"])
COMPONENT_UP = Gauge("fsip_component_status", "Component status: 2=UP 1=DEGRADED 0=DOWN", ["component"])
INCIDENTS_TOTAL = Counter("fsip_incidents_total", "Incidents opened", ["component"])
_STATUS_NUM = {UP: 2, DEGRADED: 1, DOWN: 0}

# what the platform does automatically when a component fails (shown in the incident timeline and the UI)
MITIGATIONS = {
    "simulator": "retries + circuit breaker; serving cached last-good state; no writes to the simulator",
    "sse": "polling fallback (/v1/instance every 1 s); reconnect with backoff",
    "intelligence": "built-in seasonal fallback forecaster; confidence lowered, human review required",
    "db": "history writes buffered in memory (max 5,000) and flushed on recovery",
    "redis": "api reads state directly from the engine; live updates by polling",
    "optimizer": "heuristic policy used for this cycle; optimizer retried next cycle",
    "data_quality": "invalid payload rejected and quarantined; last-good value kept for that resource",
}


@dataclass
class Component:
    status: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "detail": self.detail, **self.data}


# ---------------------------------------------------------------- pure judges
def judge_simulator(stats: dict[str, Any], breaker_state: str, stale: bool) -> Component:
    """From real traffic: breaker, error ratio and p95 of the last 30 s, and the stale header."""
    er, p95, last_ok = stats.get("error_rate"), stats.get("p95_ms"), stats.get("last_ok_age_s")
    data = {"error_rate": er, "p95_ms": p95, "last_ok_age_s": last_ok, "breaker": breaker_state,
            "requests_30s": stats.get("requests", 0)}
    if breaker_state != "CLOSED" or (last_ok is not None and last_ok > 10) or (er is not None and er >= 0.9):
        return Component(DOWN, f"unreachable (breaker {breaker_state}, last success {last_ok} s ago)", data)
    if stale:
        return Component(DEGRADED, "simulator reports STALE data (x-simulator-stale)", data)
    if er is not None and er >= 0.2:
        return Component(DEGRADED, f"flaky: error rate {er:.0%} over 30 s", data)
    if p95 is not None and p95 > 1000:
        return Component(DEGRADED, f"slow: p95 {p95:.0f} ms", data)
    return Component(UP, "", data)


def judge_data_quality(validation_errors: dict[str, str]) -> Component:
    if not validation_errors:
        return Component(UP)
    first = sorted(validation_errors.items())[0]
    return Component(DEGRADED, f"invalid {first[0]}: {first[1]}", {"resources": sorted(validation_errors)})


def unreachable_stations(snapshot, events, tick: int) -> list[str]:
    """Stations for which every route is disrupted now (or scheduled to be at departure)."""
    if snapshot is None:
        return []
    from ..decision.constraints import _event_covers  # local import: keeps this module import-light for tests

    def blocked(r) -> bool:
        return r.status != "AVAILABLE" or any(
            e.type == "route_disruption" and _event_covers(e, "route_ids", r.id, tick, tick + 1) for e in events)

    by_station: dict[str, list[bool]] = {}
    for r in snapshot.routes:
        by_station.setdefault(r.destination_station_id, []).append(blocked(r))
    names = {s.id: s.name for s in snapshot.stations}
    return sorted(names.get(sid, sid) for sid, b in by_station.items() if b and all(b))


def compute_mode(components: dict[str, Component], sim_down_for_s: float, stale: bool,
                 no_route_stations: list[str], safe_hold_after_s: float = 60.0) -> tuple[str, list[str]]:
    reasons = [f"{name}: {c.detail or c.status}" for name, c in components.items() if c.status != UP]
    if stale and not any(r.startswith("simulator:") for r in reasons):
        reasons.insert(0, "simulator: STALE data")
    if components.get("simulator", Component(UP)).status == DOWN and sim_down_for_s >= safe_hold_after_s:
        return "SAFE_HOLD", [f"simulator down for {sim_down_for_s:.0f} s (>= {safe_hold_after_s:.0f} s)"] + reasons
    if no_route_stations:
        return "NO_FEASIBLE_ROUTES", [f"no feasible route to {', '.join(no_route_stations)}"] + reasons
    if reasons:
        return "DEGRADED", reasons
    return "NORMAL", []


# ---------------------------------------------------------------- incidents
def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="milliseconds")


class IncidentTracker:
    """One open incident per component. Opens after `open_after` consecutive bad samples, resolves after
    `close_after` consecutive UP samples (hysteresis, so a 50% error rate does not open 30 incidents)."""

    def __init__(self, open_after: int = 2, close_after: int = 3, keep: int = 50) -> None:
        self.open_after, self.close_after, self.keep = open_after, close_after, keep
        self.items: list[dict[str, Any]] = []
        self.open: dict[str, dict[str, Any]] = {}
        self._bad: dict[str, int] = {}
        self._good: dict[str, int] = {}
        self._next_id = 1

    def update(self, components: dict[str, Component], now: float, mode: str) -> list[dict[str, Any]]:
        """Apply one sample; returns the incidents that changed (opened, escalated, resolved)."""
        changed = []
        for name, c in components.items():
            inc = self.open.get(name)
            if c.status != UP:
                self._bad[name] = self._bad.get(name, 0) + 1
                self._good[name] = 0
                if inc is None and self._bad[name] >= self.open_after:
                    inc = self._open(name, c, now)
                    changed.append(inc)
                elif inc is not None and c.status != inc["severity_status"]:
                    inc["severity_status"] = c.status
                    inc["timeline"].append({"at": _iso(now), "kind": "changed", "text": f"{c.status}: {c.detail}"})
                    changed.append(inc)
            else:
                self._good[name] = self._good.get(name, 0) + 1
                self._bad[name] = 0
                if inc is not None and self._good[name] >= self.close_after:
                    self._resolve(inc, now)
                    changed.append(inc)
        for inc in self.open.values():  # record mode escalations (e.g. SAFE_HOLD) on the open incidents
            if mode != inc.get("_last_mode"):
                if inc.get("_last_mode") is not None:
                    inc["timeline"].append({"at": _iso(now), "kind": "mode", "text": f"engine mode → {mode}"})
                    changed.append(inc)
                inc["_last_mode"] = mode
        return changed

    def _open(self, name: str, c: Component, now: float) -> dict[str, Any]:
        inc = {"id": self._next_id, "component": name, "status": "OPEN", "severity_status": c.status,
               "summary": c.detail or c.status, "opened_at": _iso(now), "opened_ts": now,
               "mitigated_at": _iso(now), "resolved_at": None, "resolved_ts": None, "mttr_s": None,
               "timeline": [{"at": _iso(now), "kind": "detected", "text": f"{c.status}: {c.detail}"},
                            {"at": _iso(now), "kind": "mitigated", "text": MITIGATIONS.get(name, "fallback")}]}
        self._next_id += 1
        self.open[name] = inc
        self.items.append(inc)
        self.items = self.items[-self.keep:]
        INCIDENTS_TOTAL.labels(component=name).inc()
        log.warning("incident.opened", incident_id=inc["id"], component=name, detail=c.detail)
        return inc

    def _resolve(self, inc: dict[str, Any], now: float) -> None:
        inc["status"] = "RESOLVED"
        inc["resolved_at"], inc["resolved_ts"] = _iso(now), now
        inc["mttr_s"] = round(now - inc["opened_ts"], 1)
        inc["timeline"].append({"at": _iso(now), "kind": "recovered", "text": "component healthy again"})
        self.open.pop(inc["component"], None)
        log.info("incident.resolved", incident_id=inc["id"], component=inc["component"], mttr_s=inc["mttr_s"])

    def document(self) -> dict[str, Any]:
        items = [{k: v for k, v in i.items() if not k.startswith("_")} for i in reversed(self.items)]
        return {"items": items, "open": sum(1 for i in items if i["status"] == "OPEN")}


# ---------------------------------------------------------------- the loop
class HealthMonitor:
    def __init__(self, store, sim_client, sse, forecast, decision_engine, publisher, write_buffer, sessionmaker_fn,
                 *, interval_s: float = 1.0, safe_hold_after_s: float = 60.0) -> None:
        self.store, self.sim, self.sse, self.forecast = store, sim_client, sse, forecast
        self.engine, self.publisher, self.buffer, self.sessionmaker_fn = decision_engine, publisher, write_buffer, \
            sessionmaker_fn
        self.interval_s, self.safe_hold_after_s = interval_s, safe_hold_after_s
        self.incidents = IncidentTracker()
        self.components: dict[str, Component] = {}
        self._sim_down_since: float | None = None
        self._db = Component(UP)
        self._redis = Component(UP)
        self._last_probe = 0.0
        self._probe_task: asyncio.Task | None = None
        self._running = False

    async def _probe_db(self) -> Component:
        try:
            from sqlalchemy import text
            async with self.sessionmaker_fn()() as session:
                await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=1.5)
            backlog = getattr(self.buffer, "pending", 0)
            if backlog > 200:
                return Component(DEGRADED, f"write backlog {backlog}", {"buffered_writes": backlog})
            return Component(UP, "", {"buffered_writes": backlog})
        except Exception as err:
            backlog = getattr(self.buffer, "pending", 0)
            return Component(DOWN, f"unreachable ({type(err).__name__}); {backlog} writes buffered",
                             {"buffered_writes": backlog})

    async def _probe_redis(self) -> Component:
        ok = await self.publisher.ping()
        return Component(UP) if ok else Component(DOWN, "unreachable")

    async def _probe(self) -> None:
        self._db, self._redis = await asyncio.gather(self._probe_db(), self._probe_redis())

    def _intelligence(self) -> Component:
        fc = getattr(self.forecast, "last", None)
        status = getattr(self.forecast, "status", "UNKNOWN")
        if status == "Down":
            reason = getattr(fc, "reason", None) or "unreachable"
            return Component(DOWN, f"prediction service down ({reason}); fallback forecaster active")
        if fc is not None and getattr(fc, "source", "") == "fallback":
            return Component(DEGRADED, f"fallback forecast ({getattr(fc, 'reason', '')})")
        return Component(UP)

    def _optimizer(self) -> Component:
        if self.store.policy == "optimizer" and self.engine.last_fallback_reason:
            return Component(DEGRADED, self.engine.last_fallback_reason)
        return Component(UP)

    async def sample(self, now_mono: float | None = None, now_wall: float | None = None) -> None:
        now_mono = time.monotonic() if now_mono is None else now_mono
        now_wall = time.time() if now_wall is None else now_wall
        # DB/Redis probes run in the background: a dead dependency can take seconds to fail (DNS), and the 1 s
        # sampling of everything else must not stall behind it
        if now_mono - self._last_probe >= 3.0 and (self._probe_task is None or self._probe_task.done()):
            self._last_probe = now_mono
            self._probe_task = asyncio.create_task(self._probe())
        elif self._probe_task is not None and not self._probe_task.done() and now_mono - self._last_probe > 4.0:
            # Redis fails fast (GuardedRedis), so a probe still hanging after 4 s means the DB is not answering
            pending = getattr(self.buffer, "pending", 0)
            self._db = Component(DOWN, f"no answer for {now_mono - self._last_probe:.0f} s; {pending} writes buffered",
                                 {"buffered_writes": pending})
        sim = judge_simulator(self.sim.traffic_stats(), self.sim.breaker.state, self.store.stale)
        comps = {
            "simulator": sim,
            "sse": Component(UP) if self.sse.connected else Component(DEGRADED, "live stream down; polling mode",
                                                                     {"reconnects": self.sse.reconnects}),
            "intelligence": self._intelligence(),
            "db": self._db,
            "redis": self._redis,
            "optimizer": self._optimizer(),
            "data_quality": judge_data_quality(self.store.extras.get("validation_errors", {})),
        }
        if sim.status == DOWN:
            self._sim_down_since = self._sim_down_since or now_mono
        else:
            self._sim_down_since = None
        down_for = now_mono - self._sim_down_since if self._sim_down_since else 0.0
        snap = self.store.snapshot
        blocked = unreachable_stations(snap, snap.events, snap.instance.tick) if snap and sim.status != DOWN else []
        mode, reasons = compute_mode(comps, down_for, self.store.stale, blocked, self.safe_hold_after_s)
        changed_mode = mode != self.store.engine_mode
        if changed_mode:
            log.warning("engine.mode_changed", old=self.store.engine_mode, new=mode, reasons=reasons)
            await self.publisher.event("mode.changed", {"engine_mode": mode, "reasons": reasons})
        self.store.engine_mode = mode
        for m in MODES:
            ENGINE_MODE.labels(mode=m).set(1 if m == mode else 0)
        for name, c in comps.items():
            COMPONENT_UP.labels(component=name).set(_STATUS_NUM[c.status])
        self.components = comps
        changed = self.incidents.update(comps, now_wall, mode)
        self.store.extras["system"] = {
            "mode_reasons": reasons, "sim_down_for_s": round(down_for, 1), "no_route_stations": blocked,
            "components": {k: c.as_dict() for k, c in comps.items()},
            "incidents_open": len(self.incidents.open),
        }
        if changed:
            await self.publisher.set_json("fsip:incidents", self.incidents.document())
            for inc in changed:
                await self.publisher.event("incident.updated", {"id": inc["id"], "component": inc["component"],
                                                                "status": inc["status"]})
        # the sync loop publishes on every cycle; while the simulator is down there are no cycles, so publish here
        # to keep the UI's mode, age and banner moving
        if changed_mode or changed or now_mono - self.store.last_sync_time > 2.0:
            await self.publisher.publish_state(self.store)

    async def run(self) -> None:
        self._running = True
        await self.publisher.set_json("fsip:incidents", self.incidents.document())
        while self._running:
            try:
                await self.sample()
            except Exception as err:  # never let monitoring die; the supervisor would restart it anyway
                log.error("health.sample_failed", error=str(err), error_type=type(err).__name__)
            await asyncio.sleep(self.interval_s)

    def stop(self) -> None:
        self._running = False
