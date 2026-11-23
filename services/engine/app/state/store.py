"""In-memory WorldState store and public JSON representation."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fsip_common.logging import get_logger
from fsip_common.models import (
    DemandObservation,
)
from fsip_common.timeutil import format_sim_clock

from ..sim.client import SimResult
from ..sim.validation import ValidatedSnapshot, validate_resource

log = get_logger()


@dataclass
class WorldState:
    sim_run_id: int = 1
    snapshot: ValidatedSnapshot | None = None
    demand: dict[tuple[str, str], deque[DemandObservation]] = field(default_factory=dict)
    new_demand_rows: list[DemandObservation] = field(default_factory=list)  # rows added by the latest cycle
    last_good_at: dict[str, float] = field(default_factory=dict)
    stale: bool = False
    degraded_reasons: set[str] = field(default_factory=set)

    engine_mode: str = "NORMAL"  # NORMAL | DEGRADED | SAFE_HOLD | NO_FEASIBLE_ROUTES
    operating_mode: str = "advisory"  # advisory | autopilot
    policy: str = "optimizer"  # optimizer | heuristic

    scarcity: dict[str, bool] = field(
        default_factory=lambda: {"DIESEL": False, "PETROL": False, "OCTANE": False}
    )
    risk: dict[tuple[str, str], Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)  # risk list, cover, forecast status (intelligence pipeline)
    pending_recommendations: list[dict[str, Any]] = field(default_factory=list)
    bottleneck: dict[str, Any] | None = None
    alerts_open_count: dict[str, int] = field(
        default_factory=lambda: {"INFO": 0, "WARNING": 0, "CRITICAL": 0}
    )

    last_sync_time: float = field(default_factory=time.monotonic)
    last_tick: int = 0

    @property
    def degraded(self) -> bool:
        return bool(self.degraded_reasons) or self.stale

    def apply_snapshot(self, raw_results: dict[str, SimResult | Exception]) -> None:
        """Apply newly polled/pushed resource responses into ValidatedSnapshot.

        Preserves last-good data when an individual resource request fails.
        """
        now = time.monotonic()
        new_data: dict[str, Any] = {}
        stale_flag = False

        resource_keys = [
            "instance",
            "regions",
            "depots",
            "stations",
            "routes",
            "events",
            "supply_arrivals",
            "allocations",
            "metrics",
        ]

        # Carry forward existing snapshot components if present
        if self.snapshot is not None:
            for k in resource_keys:
                new_data[k] = getattr(self.snapshot, k)

        for name, res in raw_results.items():
            if isinstance(res, Exception):
                self.degraded_reasons.add(f"resource_error:{name}")
                continue

            if isinstance(res, SimResult):
                if res.stale:
                    stale_flag = True

                try:
                    validated = validate_resource(name, res.data)
                    new_data[name] = validated
                    self.last_good_at[name] = now
                    # Clear previous degraded reasons for this resource (both transport and validation)
                    self.degraded_reasons.discard(f"resource_error:{name}")
                    self.degraded_reasons.discard(f"validation_failed:{name}")
                except Exception as val_err:
                    self.degraded_reasons.add(f"validation_failed:{name}")
                    log.warning("store.validation_failed", resource=name, error=str(val_err))

        self.stale = stale_flag

        # Construct new snapshot if all required parts exist
        if all(k in new_data for k in resource_keys):
            self.snapshot = ValidatedSnapshot(
                instance=new_data["instance"],
                regions=new_data["regions"],
                depots=new_data["depots"],
                stations=new_data["stations"],
                routes=new_data["routes"],
                events=new_data["events"],
                supply_arrivals=new_data["supply_arrivals"],
                allocations=new_data["allocations"],
                metrics=new_data["metrics"],
            )
            self.last_sync_time = now
            self.last_tick = self.snapshot.instance.tick
            # a complete snapshot exists again: boot-time "missing resources" no longer applies
            self.degraded_reasons = {r for r in self.degraded_reasons if not r.startswith("missing_resources:")}
        else:
            missing = [k for k in resource_keys if k not in new_data]
            self.degraded_reasons.add(f"missing_resources:{','.join(missing)}")

    def ingest_demand(self, observations: list[DemandObservation]) -> int:
        """Insert observations into ring buffers (maxlen 2000 per (station, fuel)).

        Returns count of new rows ingested.
        """
        # /v1/demand-history is newest-first: walk it oldest-first, otherwise only the newest row per series
        # passes the "id > last id" check and every older row of a coalesced cycle is silently dropped.
        new_rows: list[DemandObservation] = []
        for obs in sorted(observations, key=lambda o: o.id):
            key = (obs.station_id, obs.fuel_type)
            if key not in self.demand:
                self.demand[key] = deque(maxlen=2000)
            buf = self.demand[key]
            if not buf or obs.id > buf[-1].id:
                buf.append(obs)
                new_rows.append(obs)
        self.new_demand_rows = new_rows
        return len(new_rows)

    def inflight_allocations(self) -> list[tuple[str, str, int, float]]:
        """Return active allocations as (station_id, fuel_type, expected_arrival_tick, quantity)."""
        if not self.snapshot:
            return []
        items: list[tuple[str, str, int, float]] = []
        for a in self.snapshot.allocations:
            if a.status in ("PENDING", "IN_TRANSIT"):
                target_tick = a.expected_arrival_tick or (a.created_tick + 2)
                items.append((a.destination_station_id, a.fuel_type, target_tick, a.quantity))
        return items

    def to_public_json(self) -> dict[str, Any]:
        """Format the current state into the public contract defined in 21_API_CONTRACTS.md §2.1."""
        now_utc = datetime.now(UTC).isoformat()
        if not self.snapshot:
            return {
                "sim_run_id": self.sim_run_id,
                "tick": self.last_tick,
                "sim_time": now_utc,
                "tick_minutes": 15,
                "sim_status": "PAUSED",
                "clock_label": format_sim_clock(self.last_tick, 15),
                "generated_at": now_utc,
                "age_seconds": round(time.monotonic() - self.last_sync_time, 2),
                "stale": self.stale,
                "degraded": True,
                "degraded_reasons": list(self.degraded_reasons) or ["no_snapshot_available"],
                "engine_mode": self.engine_mode,
                "operating_mode": self.operating_mode,
                "policy": self.policy,
                "scarcity": self.scarcity,
                "metrics": {
                    "service_level": 1.0,
                    "served_liters": 0.0,
                    "unmet_liters": 0.0,
                    "allocation_liters": 0.0,
                    "allocation_failures": 0,
                    "overflow_liters_est": 0.0,
                    "depot_clipped_liters_est": 0.0,
                },
                "depots": [],
                "stations": [],
                "routes": [],
                "events": [],
                "supply_upcoming": [],
                "allocations_active": [],
                "alerts_open_count": self.alerts_open_count,
                "pending_recommendations": self.pending_recommendations,
                "bottleneck": self.bottleneck,
                "system": {
                    "simulator": "DOWN",
                    "sse": "DISCONNECTED",
                    "intelligence": "UNKNOWN",
                    "db": "UNKNOWN",
                    "redis": "UNKNOWN",
                    "breakers": {"simulator": "OPEN"},
                    "forecaster": "seasonal@builtin",
                },
            }

        inst = self.snapshot.instance
        met = self.snapshot.metrics
        age_s = round(time.monotonic() - self.last_sync_time, 2)
        degraded = bool(self.degraded_reasons) or self.stale

        active_allocs = [
            a.model_dump()
            for a in self.snapshot.allocations
            if a.status in ("PENDING", "IN_TRANSIT")
        ]
        upcoming_supply = [
            s.model_dump()
            for s in self.snapshot.supply_arrivals
            if s.status in ("SCHEDULED", "DELAYED")
        ]

        return {
            "sim_run_id": self.sim_run_id,
            "tick": inst.tick,
            "sim_time": inst.sim_time.isoformat(),
            "tick_minutes": inst.tick_minutes,
            "sim_status": inst.status,
            "clock_label": format_sim_clock(inst.tick, inst.tick_minutes),
            "generated_at": now_utc,
            "age_seconds": age_s,
            "stale": self.stale,
            "degraded": degraded,
            "degraded_reasons": sorted(self.degraded_reasons),
            "engine_mode": self.engine_mode,
            "operating_mode": self.operating_mode,
            "policy": self.policy,
            "scarcity": self.scarcity,
            "metrics": {
                "service_level": met.service_level,
                "served_liters": met.served_demand_liters,
                "unmet_liters": met.unmet_demand_liters,
                "allocation_liters": met.allocation_liters,
                "allocation_failures": met.allocation_failures,
                "overflow_liters_est": 0.0,
                "depot_clipped_liters_est": 0.0,
            },
            "depots": [d.model_dump() for d in self.snapshot.depots],
            "stations": [s.model_dump() for s in self.snapshot.stations],
            "routes": [r.model_dump() for r in self.snapshot.routes],
            "events": [e.model_dump() for e in self.snapshot.events],
            "supply_upcoming": upcoming_supply,
            "allocations_active": active_allocs,
            "alerts_open_count": self.alerts_open_count,
            "pending_recommendations": self.pending_recommendations,
            "bottleneck": self.bottleneck,
            "risk": self.extras.get("risk", []),
            "cover": self.extras.get("cover"),
            "forecast": self.extras.get("forecast"),
            "impact_meter": self.extras.get("impact_meter"),
            "system": {
                "simulator": "UP",
                "sse": "CONNECTED",
                "intelligence": "UP",
                "db": "UP",
                "redis": "UP",
                "breakers": {"simulator": "CLOSED"},
                "forecaster": (self.extras.get("forecast") or {}).get("model", "seasonal@builtin"),
            },
        }
