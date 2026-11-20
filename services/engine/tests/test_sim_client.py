"""Unit tests for CircuitBreaker, SimClient, Validation, WorldState, and SyncLoop."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from app.sim.circuit import CircuitBreaker, CircuitOpenError
from app.sim.client import SimClient, SimResult
from app.sim.errors import (
    SimDomainError,
    SimFaultInjected,
    SimUnavailable,
    SimValidationError,
    parse_error,
)
from app.sim.validation import validate_resource
from app.state.store import WorldState
from app.state.sync import SyncLoop
from fsip_common.models import DemandObservation, Instance


# --- 1. CircuitBreaker Tests ---
def test_circuit_breaker_transitions():
    cb = CircuitBreaker("test-sim", failure_threshold=3, reset_timeout=0.1)
    assert cb.state == CircuitBreaker.CLOSED
    assert cb.allow() is True

    # 1st and 2nd failure
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitBreaker.CLOSED

    # 3rd failure triggers OPEN
    cb.record_failure()
    assert cb.state == CircuitBreaker.OPEN
    assert cb.allow() is False

    # Wait for reset timeout
    time.sleep(0.12)
    assert cb.allow() is True
    assert cb.state == CircuitBreaker.HALF_OPEN

    # Success closes the breaker
    cb.record_success()
    assert cb.state == CircuitBreaker.CLOSED
    assert cb.failures == 0


# --- 2. Error Envelope Parsing Tests ---
def test_parse_error_fault_injected():
    resp = httpx.Response(503, json={"error": {"code": "FAULT_INJECTED", "message": "Chaos test"}})
    err = parse_error(resp)
    assert isinstance(err, SimFaultInjected)
    assert "Chaos test" in str(err)


def test_parse_error_domain_error():
    resp = httpx.Response(409, json={"detail": {"code": "ROUTE_DISRUPTED", "message": "Route down"}})
    err = parse_error(resp)
    assert isinstance(err, SimDomainError)
    assert err.code == "ROUTE_DISRUPTED"
    assert err.status == 409


def test_parse_error_validation_error():
    resp = httpx.Response(422, json={"detail": [{"loc": ["body", "fuel"], "msg": "invalid"}]})
    err = parse_error(resp)
    assert isinstance(err, SimValidationError)
    assert len(err.details) == 1


def test_parse_error_non_json():
    resp = httpx.Response(502, text="Bad Gateway")
    err = parse_error(resp)
    assert isinstance(err, SimUnavailable)


# --- 3. SimClient Retry & Stale Detection Tests ---
@pytest.mark.asyncio
@respx.mock
async def test_sim_client_retry_and_succeed():
    cb = CircuitBreaker("test-client", failure_threshold=5, reset_timeout=1.0)
    client = SimClient(base_url="http://test-sim:8000", max_retries=2, breaker=cb)

    route = respx.get("http://test-sim:8000/v1/instance")
    route.side_effect = [
        httpx.Response(503, json={"error": {"code": "FAULT_INJECTED", "message": "temporary"}}),
        httpx.Response(200, json={"tick": 10, "status": "RUNNING"}),
    ]

    res = await client.instance()
    assert res.status == 200
    assert res.data["tick"] == 10
    assert cb.state == CircuitBreaker.CLOSED
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_sim_client_domain_error_not_retried():
    cb = CircuitBreaker("test-client", failure_threshold=3, reset_timeout=1.0)
    client = SimClient(base_url="http://test-sim:8000", max_retries=3, breaker=cb)

    respx.post("http://test-sim:8000/v1/allocations").mock(
        return_value=httpx.Response(409, json={"detail": {"code": "INSUFFICIENT_INVENTORY", "message": "Empty"}})
    )

    with pytest.raises(SimDomainError) as exc_info:
        await client.create_allocation({"dummy": True})

    assert exc_info.value.code == "INSUFFICIENT_INVENTORY"
    # Domain error counts as operational success for breaker (service is alive)
    assert cb.state == CircuitBreaker.CLOSED
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_sim_client_stale_header_detection():
    client = SimClient(base_url="http://test-sim:8000", max_retries=1)

    respx.get("http://test-sim:8000/v1/instance").mock(
        return_value=httpx.Response(
            200,
            json={"tick": 5},
            headers={"X-Simulator-Stale": "true"},
        )
    )

    res = await client.instance()
    assert res.stale is True
    await client.close()


# --- 4. WorldState Store Tests ---
def test_world_state_ingest_demand():
    store = WorldState()
    now = datetime.now(timezone.utc)

    obs1 = DemandObservation(
        id=1,
        station_id="station-mirpur",
        fuel_type="DIESEL",
        tick=1,
        sim_time=now,
        demand_liters=100.0,
        served_liters=100.0,
        unmet_liters=0.0,
    )
    obs2 = DemandObservation(
        id=2,
        station_id="station-mirpur",
        fuel_type="DIESEL",
        tick=2,
        sim_time=now,
        demand_liters=150.0,
        served_liters=150.0,
        unmet_liters=0.0,
    )

    ingested = store.ingest_demand([obs1, obs2])
    assert ingested == 2
    assert len(store.demand[("station-mirpur", "DIESEL")]) == 2

    # Ingesting same observation again ignores duplicates
    ingested_dup = store.ingest_demand([obs1, obs2])
    assert ingested_dup == 0
    assert len(store.demand[("station-mirpur", "DIESEL")]) == 2


def test_world_state_to_public_json():
    store = WorldState(sim_run_id=2)
    public = store.to_public_json()

    assert public["sim_run_id"] == 2
    assert "metrics" in public
    assert "stations" in public
    assert "depots" in public
    assert "clock_label" in public
    assert public["engine_mode"] == "NORMAL"


# --- 5. SyncLoop Coalescing Tests ---
@pytest.mark.asyncio
async def test_sync_loop_coalescing():
    client = AsyncMock()
    # Mock snapshot response
    snap_result = {
        "instance": SimResult(
            data={
                "id": 1,
                "scenario_id": "base",
                "scenario_version": "1.0",
                "seed": 42,
                "sim_time": "2026-01-01T00:00:00Z",
                "tick": 5,
                "tick_minutes": 15,
                "status": "RUNNING",
            },
            stale=False,
            status=200,
            latency_ms=5.0,
        ),
        "regions": SimResult(data=[], stale=False, status=200, latency_ms=5.0),
        "depots": SimResult(data=[], stale=False, status=200, latency_ms=5.0),
        "stations": SimResult(data=[], stale=False, status=200, latency_ms=5.0),
        "routes": SimResult(data=[], stale=False, status=200, latency_ms=5.0),
        "events": SimResult(data=[], stale=False, status=200, latency_ms=5.0),
        "supply_arrivals": SimResult(data=[], stale=False, status=200, latency_ms=5.0),
        "allocations": SimResult(data=[], stale=False, status=200, latency_ms=5.0),
        "metrics": SimResult(
            data={
                "served_demand_liters": 0.0,
                "unmet_demand_liters": 0.0,
                "service_level": 1.0,
                "allocation_liters": 0.0,
                "allocation_failures": 0,
            },
            stale=False,
            status=200,
            latency_ms=5.0,
        ),
    }

    async def _slow_snapshot():
        await asyncio.sleep(0.05)
        return snap_result

    client.snapshot.side_effect = _slow_snapshot
    client.demand_history.return_value = SimResult(data=[], stale=False, status=200, latency_ms=2.0)

    store = WorldState()
    sync_loop = SyncLoop(client=client, store=store, poll_interval=10.0)

    # Trigger multiple concurrent cycle requests while snapshot is running
    t1 = asyncio.create_task(sync_loop.request_cycle(1))
    t2 = asyncio.create_task(sync_loop.request_cycle(2))
    t3 = asyncio.create_task(sync_loop.request_cycle(3))

    await asyncio.gather(t1, t2, t3)

    # Coalescing ensures at most 2 snapshots were run instead of 3
    assert client.snapshot.call_count in (1, 2)
