"""Submit decision lines to the simulator and map every outcome to a line status (05 §4.5, 02 §3).

The engine is the only writer to the simulator. Retries reuse the same idempotency key, so a timeout followed by a
retry can never create a second shipment.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fsip_common.logging import get_logger
from prometheus_client import Counter

from ..sim.circuit import CircuitOpenError
from ..sim.client import SimClient
from ..sim.errors import SimDomainError, SimError, SimValidationError

log = get_logger()

ALLOCATIONS_SUBMITTED = Counter("fsip_allocations_submitted_total", "Allocation POST outcomes", ["result"])

# simulator 409 code → our line status (DISPATCH_CAPACITY_EXCEEDED is retried by the next cycle as a new line)
CODE_TO_STATUS = {
    "DISPATCH_CAPACITY_EXCEEDED": "DEFERRED",
}


@dataclass
class SubmitOutcome:
    status: str                  # SUBMITTED | DEFERRED | REJECTED_BY_SIM
    sim_allocation_id: int | None = None
    sim_error_code: str | None = None
    expected_arrival_tick: int | None = None
    reason: str | None = None


def outcome_from_response(data: dict[str, Any]) -> SubmitOutcome:
    return SubmitOutcome(status="SUBMITTED", sim_allocation_id=data.get("id"),
                         expected_arrival_tick=data.get("expected_arrival_tick"))


def outcome_from_error(err: Exception) -> SubmitOutcome:
    if isinstance(err, SimDomainError):
        status = CODE_TO_STATUS.get(err.code, "REJECTED_BY_SIM")
        return SubmitOutcome(status=status, sim_error_code=err.code, reason=str(err))
    if isinstance(err, SimValidationError):
        return SubmitOutcome(status="REJECTED_BY_SIM", sim_error_code="VALIDATION_ERROR", reason=str(err)[:300])
    if isinstance(err, CircuitOpenError):
        return SubmitOutcome(status="DEFERRED", sim_error_code="SIM_BREAKER_OPEN", reason="simulator breaker open")
    # unavailable / fault / timeout after retries: nothing was created for certain → defer, re-plan next cycle
    return SubmitOutcome(status="DEFERRED", sim_error_code="SIM_UNAVAILABLE", reason=str(err)[:300])


async def submit_line(client: SimClient, line: dict[str, Any]) -> SubmitOutcome:
    body = {
        "idempotency_key": line["idempotency_key"],
        "source_depot_id": line["source_depot_id"],
        "destination_station_id": line["destination_station_id"],
        "route_id": line["route_id"],
        "fuel_type": line["fuel_type"],
        "quantity": round(float(line["quantity"]), 1),
    }
    try:
        res = await client.create_allocation(body)
        out = outcome_from_response(res.data if isinstance(res.data, dict) else {})
    except (SimError, CircuitOpenError) as err:
        out = outcome_from_error(err)
    ALLOCATIONS_SUBMITTED.labels(result=out.sim_error_code or out.status).inc()
    log.info("allocation.submitted" if out.status == "SUBMITTED" else "allocation.rejected",
             key=body["idempotency_key"], status=out.status, code=out.sim_error_code,
             route=body["route_id"], fuel=body["fuel_type"], quantity=body["quantity"])
    return out


def decision_status_after_execution(line_statuses: list[str]) -> str:
    submitted = sum(1 for s in line_statuses if s == "SUBMITTED")
    if submitted == len(line_statuses) and submitted > 0:
        return "EXECUTED"
    if submitted > 0:
        return "PARTIAL"
    return "FAILED"
