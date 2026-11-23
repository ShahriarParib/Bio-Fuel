"""Domain SLO (fuel service level >= 99 % over 96 ticks) and the error-budget policy."""
import asyncio
from types import SimpleNamespace

from app.state.slo import DomainSlo, budget_remaining, burn_rate, enforce


def test_burn_rate_and_budget():
    assert abs(burn_rate(0.01, 0.99) - 1.0) < 1e-9 and budget_remaining(0.01, 0.99) < 1e-9
    assert round(budget_remaining(0.0025, 0.99), 3) == 0.75


def test_window_and_reset():
    s = DomainSlo(window_ticks=4)
    for t in range(10):                          # perfect service
        st = s.observe(1, t, served=1000.0 * t, unmet=0.0)
    assert st.sli == 1.0 and st.budget == 1.0 and st.window_ticks == 4
    st = s.observe(1, 10, served=10_000.0, unmet=300.0)   # 300 L unmet in the window → SLI < 99 %
    assert st.sli < 0.99 and st.budget == 0.0 and not st.autopilot_allowed
    st = s.observe(2, 0, served=0.0, unmet=0.0)           # new world: window restarts
    assert st.sli is None and st.autopilot_allowed


class Pub:
    def __init__(self):
        self.events = []

    async def event(self, kind, data):
        self.events.append((kind, data))


def _ws(tick, served, unmet, mode="autopilot"):
    met = SimpleNamespace(served_demand_liters=served, unmet_demand_liters=unmet)
    return SimpleNamespace(snapshot=SimpleNamespace(metrics=met, instance=SimpleNamespace(tick=tick)),
                           sim_run_id=1, operating_mode=mode, extras={})


def test_budget_exhaustion_freezes_changes_but_keeps_autopilot():
    """F1 drill lesson: switching the remedy off after an outage made it worse (99 % → 81 %)."""
    s, pub = DomainSlo(window_ticks=96), Pub()
    ws = _ws(0, 0.0, 0.0)
    asyncio.run(enforce(ws, s, pub))
    ws.snapshot = _ws(1, 1000.0, 0.0).snapshot
    asyncio.run(enforce(ws, s, pub))
    assert not ws.extras["slo"]["change_freeze"]
    ws.snapshot = _ws(2, 1000.0, 500.0).snapshot             # budget blown
    asyncio.run(enforce(ws, s, pub))
    assert ws.operating_mode == "autopilot"                   # the fix keeps running
    assert ws.extras["slo"]["change_freeze"] and "freeze started" in ws.extras["slo"]["last_action"]
    ws.snapshot = _ws(3, 1100.0, 500.0).snapshot
    asyncio.run(enforce(ws, s, pub))
    assert len(pub.events) == 1                               # announced once per crossing
    s2 = DomainSlo(window_ticks=96)                           # new world: freeze lifted
    ws.snapshot = _ws(0, 0.0, 0.0).snapshot
    ws.sim_run_id = 2
    asyncio.run(enforce(ws, s2, pub))
    assert not ws.extras["slo"]["change_freeze"] and "lifted" in ws.extras["slo"]["last_action"]


def test_observe_mode_never_freezes():
    s, ws = DomainSlo(), _ws(0, 0.0, 0.0)
    asyncio.run(enforce(ws, s, Pub(), mode="observe"))
    ws.snapshot = _ws(1, 100.0, 900.0).snapshot
    asyncio.run(enforce(ws, s, Pub(), mode="observe"))
    assert not ws.extras["slo"]["change_freeze"] and ws.extras["slo"]["mode"] == "observe"
