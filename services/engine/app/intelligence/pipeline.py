"""Per-cycle intelligence: forecast → stockout risk → detectors → alerts → cover. Runs before the decision engine.

Detectors (brief §7 "Detection"):
- stockout risk / stockout occurred (Monte Carlo over the forecast)
- depot overflow risk: scheduled supply that will not fit in the depot (supply clipped at a full depot is lost)
- supply delay / supply shortfall (quantity reduced after we first saw it)
- route disruption and station outage, both pre-emptive (scheduled) and active; single-route stations flagged
- demand anomaly: EXPLAINED (visible multiplier / demand_spike event) vs UNEXPLAINED (CUSUM on forecast residuals)
- data quality: stale data header, invalid or missing simulator payloads
"""
from __future__ import annotations

from typing import Any

from fsip_common.logging import get_logger
from prometheus_client import Gauge

from ..alerts.manager import AlertManager
from ..decision import heuristic
from ..decision.planning import FUELS, Key, PlanningState
from ..risk.stockout import assess
from ..state.store import WorldState
from .client import ForecastService
from .counterfactual import CounterfactualMeter
from .series import base_multiplier, spike_events

log = get_logger()

STOCKOUT_PROB = Gauge("fsip_stockout_prob", "12 h stockout probability", ["station", "fuel"])
COVER_DAYS = Gauge("fsip_network_cover_days", "Network fuel cover (days of forecast demand)", ["fuel"])
STATION_INV = Gauge("fsip_station_inventory_liters", "Station inventory", ["station", "fuel"])
DEPOT_INV = Gauge("fsip_depot_inventory_liters", "Depot inventory", ["depot", "fuel"])

PREEMPT_TICKS = 16          # warn about scheduled disruptions/outages this far ahead (4 h)
SUPPLY_LOOKAHEAD = 24       # check depot room for supply arriving within 6 h
ANOMALY_HOLD_TICKS = 8
CUSUM_WARMUP_TICKS = 96     # one full day of history before "unexplained" anomalies are trusted


class Cusum:
    def __init__(self, k: float = 0.5, h: float = 5.0) -> None:
        self.k, self.h, self.pos, self.neg = k, h, 0.0, 0.0

    def update(self, z: float) -> str | None:
        self.pos = max(0.0, self.pos + z - self.k)
        self.neg = max(0.0, self.neg - z - self.k)
        if self.pos > self.h:
            self.pos = 0.0
            return "UP"
        if self.neg > self.h:
            self.neg = 0.0
            return "DOWN"
        return None


def _name(state: PlanningState, sid: str) -> str:
    return state.stations[sid].name if sid in state.stations else sid


class IntelligencePipeline:
    def __init__(self, forecast: ForecastService, alerts: AlertManager, publisher, planning_fn) -> None:
        self.forecast = forecast
        self.alerts = alerts
        self.publisher = publisher
        self.planning_fn = planning_fn          # DecisionEngine.planning_state (uses the latest forecast)
        self.cusum: dict[Key, Cusum] = {}
        self.anomaly_until: dict[Key, tuple[int, str, float]] = {}
        self.supply_first_qty: dict[str, float] = {}
        self.run_id: int | None = None
        self.last: dict[str, Any] = {}
        self.meter = CounterfactualMeter()

    async def on_cycle(self, ws: WorldState) -> None:
        if ws.snapshot is None:
            return
        tick = ws.snapshot.instance.tick
        if ws.sim_run_id != self.run_id:
            self.cusum.clear()
            self.anomaly_until.clear()
            self.supply_first_qty.clear()
            self.run_id = ws.sim_run_id
            self.meter.reset(ws.sim_run_id)
        if self.meter.start_tick is None:
            self.meter.start(tick, ws.snapshot.stations)
        self.meter.observe(ws.new_demand_rows)
        self.alerts.begin(ws.sim_run_id, tick)
        self._update_cusum(ws)
        bundle = await self.forecast.refresh(ws)
        state: PlanningState = self.planning_fn()
        risks = assess(state, bundle.p90, ws.sim_run_id, heuristic.lead_ticks)
        ws.risk = {k: r.as_dict() for k, r in risks.items()}
        await self._risk_alerts(ws, state, risks)
        await self._supply_alerts(state)
        await self._network_alerts(state)
        await self._demand_alerts(ws, state)
        await self._data_quality_alerts(ws)
        await self.alerts.end()
        ws.alerts_open_count = self.alerts.counts()
        cover = self._cover(state, risks)
        self.last = {"cover": cover, "forecast": {"model": bundle.model, "source": bundle.source,
                                                   "reason": bundle.reason, **self.forecast.accuracy()}}
        ws.extras = {**getattr(ws, "extras", {}), **self.last,
                     "risk": [r.as_dict() for r in risks.values()], "impact_meter": self.meter.summary()}
        for (sid, f), r in risks.items():
            STOCKOUT_PROB.labels(station=sid, fuel=f).set(r.prob_12h)
            STATION_INV.labels(station=sid, fuel=f).set(r.inventory)
        for d in state.depots.values():
            for f in FUELS:
                DEPOT_INV.labels(depot=d.id, fuel=f).set(float(d.inventory[f]))
        await self.publisher.set_json("fsip:alerts", self.alerts.document())

    # ------------------------------------------------------------------ stockout
    async def _risk_alerts(self, ws: WorldState, state: PlanningState, risks) -> None:
        latest: dict[Key, Any] = {}
        for key, obs in ws.demand.items():
            if obs:
                latest[key] = obs[-1]
        for key, r in risks.items():
            sid, f = key
            name = _name(state, sid)
            o = latest.get(key)
            if o is not None and o.unmet_liters > 0 and state.stations[sid].status == "OPEN":
                await self.alerts.raise_(f"stockout_occurred:{sid}:{f}", "STOCKOUT_OCCURRED", "CRITICAL",
                                         f"{name} {f} is dry: {o.unmet_liters:,.0f} L unmet in the last tick",
                                         "Customers are being turned away. Check the recommendation inbox.",
                                         "station", sid, f, {"unmet_liters": o.unmet_liters, "tick": o.tick})
            if r.level in ("HIGH", "CRITICAL"):
                eta = f"{r.hours_to_stockout:.1f} h" if r.hours_to_stockout is not None else "within 12 h"
                await self.alerts.raise_(
                    f"stockout:{sid}:{f}", "STOCKOUT_RISK", "CRITICAL" if r.level == "CRITICAL" else "WARNING",
                    f"{name} {f}: projected stockout in {eta}",
                    f"Current inventory {r.inventory:,.0f} L (+{r.inflight:,.0f} L on the way), expected demand next "
                    f"12 h {r.expected_demand_12h:,.0f} L. Probability of running dry within 12 h: {r.prob_12h:.0%}.",
                    "station", sid, f, r.as_dict())

    # ------------------------------------------------------------------ supply / depots
    async def _supply_alerts(self, state: PlanningState) -> None:
        incoming: dict[tuple[str, str], float] = {}
        for s in sorted(state.supply, key=lambda x: x.planned_tick):
            first = self.supply_first_qty.setdefault(s.id, float(s.quantity))
            if s.status == "ARRIVED":
                continue
            if s.status == "DELAYED":
                await self.alerts.raise_(
                    f"supply_delay:{s.id}", "SUPPLY_DELAY", "WARNING",
                    f"Supply {s.id} delayed: {s.quantity:,.0f} L {s.fuel_type} to {s.depot_id} "
                    f"now due t={s.planned_tick}",
                    "Depot cover is lower than planned; the plan shifts load to the other depot where possible.",
                    "depot", s.depot_id, s.fuel_type, {"planned_tick": s.planned_tick, "quantity": s.quantity})
            if float(s.quantity) < first - 1:
                await self.alerts.raise_(
                    f"supply_shortfall:{s.id}", "SUPPLY_SHORTFALL", "WARNING",
                    f"Supply {s.id} cut from {first:,.0f} L to {s.quantity:,.0f} L {s.fuel_type}",
                    f"{first - float(s.quantity):,.0f} L less fuel will reach {s.depot_id}.",
                    "depot", s.depot_id, s.fuel_type, {"was": first, "now": float(s.quantity)})
            if 0 <= s.planned_tick - state.tick <= SUPPLY_LOOKAHEAD:
                d = state.depots.get(s.depot_id)
                if d is None:
                    continue
                k = (s.depot_id, s.fuel_type)
                incoming[k] = incoming.get(k, 0.0) + float(s.quantity)
                excess = float(d.inventory[s.fuel_type]) + incoming[k] - float(d.capacity[s.fuel_type])
                if excess > 0:
                    await self.alerts.raise_(
                        f"depot_overflow:{s.id}", "DEPOT_OVERFLOW_RISK", "WARNING",
                        f"{d.name}: {excess:,.0f} L of {s.fuel_type} supply {s.id} will be lost at t={s.planned_tick}",
                        f"A full depot clips arriving supply. Dispatch at least {excess:,.0f} L of {s.fuel_type} "
                        f"from {d.name} before tick {s.planned_tick}.",
                        "depot", d.id, s.fuel_type, {"excess_liters": excess, "supply_id": s.id})
        for d in state.depots.values():
            if d.status == "CONSTRAINED":
                await self.alerts.raise_(f"depot_constrained:{d.id}", "DEPOT_CONSTRAINED", "WARNING",
                                         f"{d.name} constrained", "Dispatch from this depot is self-limited to 50% "
                                         "and the plan prefers the other depot.", "depot", d.id)

    # ------------------------------------------------------------------ routes / stations
    async def _network_alerts(self, state: PlanningState) -> None:
        routes_to: dict[str, int] = {}
        for r in state.routes.values():
            routes_to[r.destination_station_id] = routes_to.get(r.destination_station_id, 0) + 1
        for e in state.events:
            if e.status == "RESOLVED":
                continue
            ahead = e.start_tick - state.tick
            upcoming = e.status == "SCHEDULED" and 0 <= ahead <= PREEMPT_TICKS
            if e.type == "route_disruption" and (e.status == "ACTIVE" or upcoming):
                ids = e.parameters.get("route_ids") or list(state.routes)
                single = [state.routes[r].destination_station_id for r in ids
                          if r in state.routes and routes_to.get(state.routes[r].destination_station_id) == 1]
                when = f"until t={e.end_tick}" if e.status == "ACTIVE" else f"from t={e.start_tick} to t={e.end_tick}"
                extra = (f" Single-route stations cut off: {', '.join(_name(state, s) for s in single)}; pre-stock "
                         f"them before the cut." if single else "")
                await self.alerts.raise_(
                    f"route_disruption:{e.id}", "ROUTE_DISRUPTED", "WARNING" if e.status == "ACTIVE" else "INFO",
                    f"{'All routes' if not e.parameters.get('route_ids') else ', '.join(ids)} disrupted {when}",
                    "No shipment departs on a route that is disrupted at departure (that fuel would be lost)." + extra,
                    "event", str(e.id), None, {"route_ids": ids, "start_tick": e.start_tick, "end_tick": e.end_tick})
            if e.type == "station_outage" and (e.status == "ACTIVE" or upcoming):
                ids = e.parameters.get("station_ids") or list(state.stations)
                await self.alerts.raise_(
                    f"station_outage:{e.id}", "STATION_OUTAGE", "CRITICAL" if e.status == "ACTIVE" else "INFO",
                    f"Outage at {', '.join(_name(state, s) for s in ids)} "
                    f"{'until' if e.status == 'ACTIVE' else 'from t=' + str(e.start_tick) + ' to'} t={e.end_tick}",
                    "Demand there is unmet during the outage; fuel already on the way is still received.",
                    "event", str(e.id), None, {"station_ids": ids})

    # ------------------------------------------------------------------ demand anomalies
    def _update_cusum(self, ws: WorldState) -> None:
        prev = self.forecast.last
        if prev is None:
            return
        for o in ws.new_demand_rows:
            key = (o.station_id, o.fuel_type)
            if o.tick != prev.tick or key not in prev.p50:
                continue
            if len(ws.demand.get(key, ())) < CUSUM_WARMUP_TICKS:
                continue  # the profile has not seen every hour yet: residuals are model error, not anomalies
            p10, p50, p90 = prev.p10[key][0], prev.p50[key][0], prev.p90[key][0]
            sigma = max((p90 - p10) / 2.56, 1e-6)
            fired = self.cusum.setdefault(key, Cusum()).update((o.demand_liters - p50) / sigma)
            if fired:
                self.anomaly_until[key] = (o.tick + ANOMALY_HOLD_TICKS, fired, (o.demand_liters - p50) / max(p50, 1))

    async def _demand_alerts(self, ws: WorldState, state: PlanningState) -> None:
        events = list(ws.snapshot.events)
        for st in ws.snapshot.stations:
            base = base_multiplier(st, events)
            active = [(s, e, m) for s, e, m, status in spike_events(events, st) if status == "ACTIVE"]
            if active and abs(float(st.demand_multiplier) - base) > 1e-6:
                ev = next(e for e in events if e.type == "demand_spike" and e.status == "ACTIVE"
                          and (e.start_tick, e.end_tick) == active[0][:2])
                await self.alerts.raise_(
                    f"demand_spike:{st.id}:{ev.id}", "DEMAND_ANOMALY", "WARNING",
                    f"{st.name}: demand ×{st.demand_multiplier:.2f} (explained by event #{ev.id})",
                    f"Spike active until t={ev.end_tick}. The forecast already includes it, so risk and "
                    f"recommendations reflect the higher demand from this tick on.",
                    "station", st.id, None, {"explained": True, "event_id": ev.id,
                                             "multiplier": float(st.demand_multiplier)})
        for key, (until, direction, rel) in list(self.anomaly_until.items()):
            if state.tick > until:
                self.anomaly_until.pop(key)
                continue
            sid, f = key
            st = state.stations.get(sid)
            if st is None or abs(float(st.demand_multiplier) - 1.0) > 1e-6:
                continue  # already explained by a visible multiplier
            await self.alerts.raise_(
                f"demand_unexplained:{sid}:{f}", "DEMAND_ANOMALY", "WARNING",
                f"{st.name} {f}: demand {'above' if direction == 'UP' else 'below'} forecast with no known cause",
                f"CUSUM on forecast residuals fired ({rel:+.0%} last tick). No demand_spike event explains it: "
                f"check the data or a behavioural surge.", "station", sid, f,
                {"explained": False, "direction": direction})

    async def _data_quality_alerts(self, ws: WorldState) -> None:
        if ws.stale:
            await self.alerts.raise_("data_quality:stale", "DATA_QUALITY", "WARNING", "Simulator data is stale",
                                     "Responses carry x-simulator-stale. Autopilot is paused and approvals are "
                                     "blocked until fresh data returns.", "system", "simulator")
        for reason in ws.degraded_reasons:
            if reason.startswith(("validation_failed:", "missing_resources:")):
                await self.alerts.raise_(f"data_quality:{reason}", "DATA_QUALITY", "CRITICAL",
                                         f"Invalid simulator data ({reason.split(':', 1)[1]})",
                                         "The payload was rejected and quarantined; the last good copy is used.",
                                         "system", "simulator", None, {"reason": reason})

    # ------------------------------------------------------------------ cover (F-COVER)
    def _cover(self, state: PlanningState, risks) -> dict[str, Any]:
        network: dict[str, Any] = {}
        horizon = 96
        for f in FUELS:
            available = sum(float(d.inventory[f]) for d in state.depots.values())
            available += sum(float(s.inventory[f]) for s in state.stations.values())
            available += sum(i.quantity for i in state.inflight if i.fuel_type == f)
            available += sum(float(s.quantity) for s in state.supply if s.fuel_type == f and s.status != "ARRIVED")
            demand = sum(state.demand((sid, f), j) for sid in state.stations for j in range(horizon))
            cover = available / demand if demand > 0 else None
            network[f] = {"available_liters": round(available), "demand_next_24h": round(demand),
                          "cover_days": round(cover, 2) if cover is not None else None}
            if cover is not None:
                COVER_DAYS.labels(fuel=f).set(cover)
        stations = []
        for (sid, f), r in risks.items():
            hourly = r.expected_demand_12h / 12 if r.expected_demand_12h > 0 else None
            stations.append({"station_id": sid, "fuel_type": f,
                             "hours_of_cover": round((r.inventory + r.inflight) / hourly, 1) if hourly else None})
        pending = [s.planned_tick for s in state.supply if s.status != "ARRIVED"]
        return {"network": network, "stations": stations, "supply_ends_tick": max(pending) if pending else None}
