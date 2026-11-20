"""Validation of raw simulator payloads into strongly-typed domain models."""
from __future__ import annotations

from typing import Any

from fsip_common.logging import get_logger
from fsip_common.models import (
    Allocation,
    DemandObservation,
    Depot,
    Health,
    Instance,
    Region,
    Route,
    SimEvent,
    SimMetrics,
    Station,
    SupplyArrival,
    world_issues,
)
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from prometheus_client import Counter

from .errors import SimInvalidPayload

log = get_logger()

SIM_INVALID_RESPONSES = Counter(
    "fsip_sim_invalid_responses_total",
    "Invalid payloads returned by the simulator partitioned by resource",
    ["resource"],
)

_MODEL_MAP: dict[str, Any] = {
    "instance": (Instance, False),
    "health": (Health, False),
    "metrics": (SimMetrics, False),
    "regions": (Region, True),
    "depots": (Depot, True),
    "stations": (Station, True),
    "routes": (Route, True),
    "events": (SimEvent, True),
    "supply_arrivals": (SupplyArrival, True),
    "allocations": (Allocation, True),
    "demand_history": (DemandObservation, True),
}


class ValidatedSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=False)

    instance: Instance
    regions: list[Region]
    depots: list[Depot]
    stations: list[Station]
    routes: list[Route]
    events: list[SimEvent]
    supply_arrivals: list[SupplyArrival]
    allocations: list[Allocation]
    metrics: SimMetrics


def validate_resource(name: str, raw: Any) -> Any:
    """Validate a raw JSON payload against the expected Pydantic model.

    Raises SimInvalidPayload on failure and increments fsip_sim_invalid_responses_total.
    """
    if name not in _MODEL_MAP:
        return raw

    model_cls, is_list = _MODEL_MAP[name]
    try:
        if is_list:
            if not isinstance(raw, list):
                raise ValueError(f"Expected a list for resource '{name}', got {type(raw).__name__}")
            return TypeAdapter(list[model_cls]).validate_python(raw)
        return model_cls.model_validate(raw)
    except (ValidationError, ValueError) as err:
        SIM_INVALID_RESPONSES.labels(resource=name).inc()
        errors = err.errors() if isinstance(err, ValidationError) else [{"msg": str(err)}]
        log.error("sim.validation_error", resource=name, errors=errors)
        raise SimInvalidPayload(resource=name, errors=errors, raw=raw) from err


def cross_check(snap: ValidatedSnapshot) -> list[str]:
    """Perform consistency and sanity checks across resources."""
    return world_issues(
        depots=snap.depots,
        stations=snap.stations,
        routes=snap.routes,
        regions=snap.regions,
    )
