"""Impact Meter: the EXACT "what if nobody had run this network" counterfactual, live.

Demand in the simulator is generated from (seed, tick, station, fuel) and the event multipliers; it does not depend on
our shipments, and `/v1/demand-history` records true demand even when a station is dry. So the do-nothing world can be
replayed exactly from observed demand: a shadow tank per station and fuel starts at the inventory we first saw and
loses each tick's demand, with no deliveries. Its shortfall is what customers would have lost without the platform.
(Only "do nothing" is exact; comparing against another policy needs the twin and is labelled an estimate.)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from prometheus_client import Gauge

UNMET_PREVENTED = Gauge("fsip_unmet_prevented_liters", "Unmet demand prevented vs the exact do-nothing counterfactual")
UNMET_WITHOUT = Gauge("fsip_counterfactual_unmet_liters", "Unmet demand in the do-nothing counterfactual")


@dataclass
class CounterfactualMeter:
    run_id: int | None = None
    start_tick: int | None = None
    shadow: dict[tuple[str, str], float] = field(default_factory=dict)
    first_dry: dict[tuple[str, str], int] = field(default_factory=dict)
    unmet_without: float = 0.0
    unmet_with: float = 0.0
    demand_total: float = 0.0

    def reset(self, run_id: int) -> None:
        self.__init__()  # type: ignore[misc]
        self.run_id = run_id

    def start(self, tick: int, stations) -> None:
        self.start_tick = tick
        for st in stations:
            for f, inv in st.inventory.items():
                self.shadow[(st.id, f)] = float(inv)

    def observe(self, rows) -> None:
        """Feed newly ingested demand rows (oldest first)."""
        if self.start_tick is None:
            return
        for o in rows:
            if o.tick < self.start_tick:
                continue
            key = (o.station_id, o.fuel_type)
            if key not in self.shadow:
                continue
            self.demand_total += o.demand_liters
            self.unmet_with += o.unmet_liters
            left = self.shadow[key] - o.demand_liters
            if left < 0:
                self.unmet_without += -left
                left = 0.0
                self.first_dry.setdefault(key, o.tick)
            self.shadow[key] = left
        UNMET_PREVENTED.set(max(0.0, self.unmet_without - self.unmet_with))
        UNMET_WITHOUT.set(self.unmet_without)

    def summary(self) -> dict:
        if self.start_tick is None:
            return {"available": False}
        dt = self.demand_total or 1.0
        stations_dry = sorted({k[0] for k in self.first_dry})
        return {
            "available": True, "since_tick": self.start_tick,
            "method": "exact replay of observed demand, no deliveries",
            "demand_liters": round(self.demand_total),
            "without_platform": {"unmet_liters": round(self.unmet_without),
                                 "service_level": round(1 - self.unmet_without / dt, 4),
                                 "stations_that_ran_dry": len(stations_dry),
                                 "first_dry": [{"station_id": k[0], "fuel_type": k[1], "tick": t}
                                               for k, t in sorted(self.first_dry.items(), key=lambda x: x[1])[:6]]},
            "with_platform": {"unmet_liters": round(self.unmet_with),
                              "service_level": round(1 - self.unmet_with / dt, 4)},
            "unmet_prevented_liters": round(max(0.0, self.unmet_without - self.unmet_with)),
        }
