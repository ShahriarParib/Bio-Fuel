"""Domain SLO + error-budget policy (12 F-SLO).

SLO: fuel service level = served / (served + unmet) litres over the last 96 ticks (one simulated day) >= 99 %.
Policy (SRE error-budget policy: freeze risky changes, keep fixing): when less than 25 % of the budget is left,
  - a CHANGE FREEZE starts: switching the decision policy (optimizer/heuristic) needs a supervisor;
  - operators are paged (Prometheus burn-rate alerts FuelServiceLevelFast/SlowBurn → Alertmanager → UI);
  - autopilot KEEPS running: recovery shipments are the fix, not the risk.
Why not "autopilot off" (the first version): the F1 drill showed it backfires. A 75 s simulator outage spent the budget
(no shipments could be made), the policy switched autopilot off exactly when the simulator came back, nobody was
approving, and the service level fell from 99 % to 81 %. Turning off the remedy is not a safety measure.

Prometheus computes the same SLI (slo.yml) for burn-rate alerts; the engine computes it from ticks so the policy does
not depend on the observability stack being up.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from fsip_common.logging import get_logger
from prometheus_client import Gauge

log = get_logger()

BUDGET = Gauge("fsip_slo_budget_remaining", "Error budget remaining (1 = untouched, 0 = spent)", ["slo"])
SLI = Gauge("fsip_slo_sli", "SLI over the SLO window", ["slo"])

TARGET = 0.99
WINDOW_TICKS = 96
POLICY_MIN_BUDGET = 0.25


def burn_rate(error_ratio: float, slo: float) -> float:
    """observed error ratio / allowed error ratio (1 = spending the budget exactly over the window)."""
    return error_ratio / max(1e-9, 1 - slo)


def budget_remaining(error_ratio: float, slo: float) -> float:
    return max(0.0, 1.0 - burn_rate(error_ratio, slo))


@dataclass
class SloStatus:
    sli: float | None
    budget: float
    burn: float
    window_ticks: int
    autopilot_allowed: bool  # kept for the API shape: True = no change freeze

    def as_dict(self) -> dict:
        return {"slo": "fuel_service_level", "target": TARGET, "window_ticks": self.window_ticks,
                "sli": None if self.sli is None else round(self.sli, 4), "budget_remaining": round(self.budget, 3),
                "burn_rate": round(self.burn, 2), "autopilot_allowed": True,
                "change_freeze": not self.autopilot_allowed,
                "policy": f"budget < {POLICY_MIN_BUDGET:.0%} → change freeze + page operators; autopilot keeps fixing"}


class DomainSlo:
    """Rolling window of cumulative (served, unmet) samples, one per tick."""

    def __init__(self, window_ticks: int = WINDOW_TICKS) -> None:
        self.window_ticks = window_ticks
        self.samples: deque[tuple[int, float, float]] = deque()
        self.run_id: int | None = None

    def observe(self, run_id: int, tick: int, served: float, unmet: float) -> SloStatus:
        if run_id != self.run_id or (self.samples and tick < self.samples[-1][0]):
            self.samples.clear()  # new world: counters restart
            self.run_id = run_id
        if not self.samples or tick > self.samples[-1][0]:
            self.samples.append((tick, served, unmet))
        while len(self.samples) > 1 and tick - self.samples[0][0] > self.window_ticks:
            self.samples.popleft()
        return self.status()

    def status(self) -> SloStatus:
        if len(self.samples) < 2:
            return SloStatus(None, 1.0, 0.0, 0, True)
        (t0, s0, u0), (t1, s1, u1) = self.samples[0], self.samples[-1]
        served, unmet = max(0.0, s1 - s0), max(0.0, u1 - u0)
        total = served + unmet
        if total <= 0:
            return SloStatus(None, 1.0, 0.0, t1 - t0, True)
        err = unmet / total
        budget = budget_remaining(err, TARGET)
        return SloStatus(1 - err, budget, burn_rate(err, TARGET), t1 - t0, budget >= POLICY_MIN_BUDGET)


async def enforce(ws, slo: DomainSlo, publisher, mode: str = "enforce") -> SloStatus | None:
    """Cycle hook: update the SLO, publish it, and start/end the change freeze when the budget crosses the floor."""
    if ws.snapshot is None:
        return None
    met = ws.snapshot.metrics
    st = slo.observe(ws.sim_run_id, ws.snapshot.instance.tick, met.served_demand_liters, met.unmet_demand_liters)
    BUDGET.labels(slo="fuel_service_level").set(st.budget)
    if st.sli is not None:
        SLI.labels(slo="fuel_service_level").set(st.sli)
    doc = st.as_dict()
    doc["mode"] = mode
    if mode != "enforce":
        doc["change_freeze"] = False
    prev = ws.extras.get("slo") or {}
    doc["last_action"] = prev.get("last_action")
    tick = ws.snapshot.instance.tick
    if doc["change_freeze"] and not prev.get("change_freeze"):
        doc["last_action"] = f"change freeze started at tick {tick} (budget {st.budget:.0%}); autopilot keeps running"
        log.warning("slo.change_freeze_started", budget=round(st.budget, 3), sli=doc["sli"])
        await publisher.event("slo.change_freeze", {"active": True, "budget": st.budget})
    elif prev.get("change_freeze") and not doc["change_freeze"]:
        doc["last_action"] = f"change freeze lifted at tick {tick} (budget {st.budget:.0%})"
        log.info("slo.change_freeze_lifted", budget=round(st.budget, 3))
        await publisher.event("slo.change_freeze", {"active": False, "budget": st.budget})
    ws.extras["slo"] = doc
    return st
