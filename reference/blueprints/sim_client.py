"""Tested reference: resilient async simulator client + circuit breaker.
Self-test (needs a running simulator; it injects and clears faults, so use a dedicated instance):
    SIM_URL=http://localhost:8000 python3 -I reference/blueprints/sim_client.py
Deps: httpx>=0.27
Adapt into services/engine/app/sim/{circuit.py,client.py}; add prometheus metrics + structlog where marked.
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx


# ----------------------------------------------------------------------------- errors
class SimError(Exception): ...
class SimUnavailable(SimError): ...                     # network error / timeout / 5xx without envelope
class SimFaultInjected(SimError): ...                   # 503 {"error":{"code":"FAULT_INJECTED"}} or detail form
class CircuitOpenError(SimError): ...


class SimDomainError(SimError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


class SimValidationError(SimError):                      # 422 pydantic list
    def __init__(self, details: list):
        super().__init__(str(details)); self.details = details


def parse_error(resp: httpx.Response) -> SimError:
    try:
        body = resp.json()
    except ValueError:
        return SimUnavailable(f"{resp.status_code} non-json")
    if isinstance(body, dict):
        if isinstance(body.get("error"), dict):                       # injected fault envelope
            e = body["error"]
            return SimFaultInjected(e.get("message", "")) if e.get("code") == "FAULT_INJECTED" else SimUnavailable(str(e))
        det = body.get("detail")
        if isinstance(det, dict):
            if det.get("code") == "FAULT_INJECTED":                    # stream_disconnect shape
                return SimFaultInjected("stream")
            return SimDomainError(resp.status_code, det.get("code", "UNKNOWN"), det.get("message", ""))
        if isinstance(det, list):
            return SimValidationError(det)
    return SimUnavailable(f"{resp.status_code} {str(body)[:200]}")


# ----------------------------------------------------------------------------- circuit breaker
class CircuitBreaker:
    CLOSED, OPEN, HALF_OPEN = "CLOSED", "OPEN", "HALF_OPEN"

    def __init__(self, name: str, failure_threshold: int = 5, reset_timeout: float = 15.0):
        self.name, self.threshold, self.reset_timeout = name, failure_threshold, reset_timeout
        self.state, self.failures, self.opened_at = self.CLOSED, 0, 0.0
        self._half_open_probe = asyncio.Lock()

    def _transition(self, new: str) -> None:
        if new != self.state:
            # log: breaker.state_change name old new ; metric fsip_breaker_state{name} = {0,1,2}
            self.state = new

    def allow(self) -> bool:
        if self.state == self.OPEN and time.monotonic() - self.opened_at >= self.reset_timeout:
            self._transition(self.HALF_OPEN)
        return self.state != self.OPEN

    def record_success(self) -> None:
        self.failures = 0
        self._transition(self.CLOSED)

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == self.HALF_OPEN or self.failures >= self.threshold:
            self.opened_at = time.monotonic()
            self._transition(self.OPEN)


# ----------------------------------------------------------------------------- client
@dataclass
class SimResult:
    data: Any
    stale: bool
    status: int
    latency_ms: float


RETRYABLE = (SimUnavailable, SimFaultInjected)


class SimClient:
    def __init__(self, base_url: str, timeout: float = 2.0, max_retries: int = 3,
                 breaker: CircuitBreaker | None = None):
        self.http = httpx.AsyncClient(base_url=base_url, timeout=timeout,
                                      limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))
        self.max_retries = max_retries
        self.breaker = breaker or CircuitBreaker("simulator")

    async def close(self) -> None:
        await self.http.aclose()

    async def _request(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None,
                       retry: bool = True) -> SimResult:
        attempts = self.max_retries + 1 if retry else 1
        last: Exception | None = None
        for attempt in range(attempts):
            if not self.breaker.allow():
                raise CircuitOpenError(self.breaker.name)
            t0 = time.perf_counter()
            try:
                resp = await self.http.request(method, path, json=json, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = SimUnavailable(f"{type(e).__name__}: {e}")
            else:
                ms = (time.perf_counter() - t0) * 1000
                # metric: fsip_sim_request_duration_seconds{endpoint=path-template,status}
                if resp.status_code < 400:
                    self.breaker.record_success()
                    stale = resp.headers.get("x-simulator-stale", "").lower() == "true"
                    return SimResult(resp.json(), stale, resp.status_code, ms)
                err = parse_error(resp)
                if not isinstance(err, RETRYABLE):
                    self.breaker.record_success()       # the service is up; the request itself was rejected
                    raise err
                last = err
            self.breaker.record_failure()
            if attempt < attempts - 1:
                await asyncio.sleep(min(2.0, 0.2 * 2 ** attempt) * (0.5 + random.random()))   # exp backoff + jitter
        raise last  # type: ignore[misc]

    # read API
    async def instance(self): return await self._request("GET", "/v1/instance")
    async def depots(self): return await self._request("GET", "/v1/depots")
    async def stations(self): return await self._request("GET", "/v1/stations")
    async def routes(self): return await self._request("GET", "/v1/routes")
    async def events(self): return await self._request("GET", "/v1/events")
    async def supply_arrivals(self): return await self._request("GET", "/v1/supply-arrivals")
    async def allocations(self): return await self._request("GET", "/v1/allocations")
    async def metrics(self): return await self._request("GET", "/v1/metrics")
    async def demand_history(self, station_id: str | None = None, limit: int = 200):
        p = {"limit": max(1, min(2000, limit))}
        if station_id: p["station_id"] = station_id
        return await self._request("GET", "/v1/demand-history", params=p)

    async def snapshot(self) -> dict[str, SimResult | Exception]:
        """Parallel read of all resources; failures are returned per resource (keep last-good for those)."""
        names = ["instance", "depots", "stations", "routes", "events", "supply_arrivals", "allocations", "metrics"]
        res = await asyncio.gather(*(getattr(self, n)() for n in names), return_exceptions=True)
        return dict(zip(names, res))

    # write API (safe to retry: same idempotency key + same body => same allocation, HTTP 201)
    async def create_allocation(self, body: dict) -> SimResult:
        return await self._request("POST", "/v1/allocations", json=body)

    async def cancel_allocation(self, alloc_id: int) -> SimResult:
        return await self._request("POST", f"/v1/allocations/{alloc_id}/cancel")

    # admin (tests / harness / demo controls only)
    async def admin(self, method: str, path: str, json: dict | None = None) -> SimResult:
        return await self._request(method, f"/admin/{path}", json=json, retry=False)


# ----------------------------------------------------------------------------- self-test
async def _selftest(url: str) -> None:
    c = SimClient(url, breaker=CircuitBreaker("simulator", failure_threshold=3, reset_timeout=2))
    await c.admin("POST", "reset"); await c.admin("POST", "faults/clear")
    snap = await c.snapshot()
    assert all(isinstance(v, SimResult) for v in snap.values()), snap
    assert snap["instance"].data["tick_minutes"] > 0
    body = {"idempotency_key": "bp-1", "source_depot_id": "depot-gazipur", "destination_station_id": "station-mirpur",
            "route_id": "route-gazipur-mirpur", "fuel_type": "DIESEL", "quantity": 1000}
    a1 = await c.create_allocation(body); a2 = await c.create_allocation(body)
    assert a1.data["id"] == a2.data["id"] and a2.status in (200, 201)          # idempotent replay
    try:
        await c.create_allocation({**body, "quantity": 999}); raise AssertionError("expected mismatch")
    except SimDomainError as e:
        assert e.code == "IDEMPOTENCY_KEY_MISMATCH", e.code
    try:
        await c.create_allocation({**body, "idempotency_key": "bp-2", "fuel_type": "KEROSENE"}); raise AssertionError
    except SimValidationError:
        pass
    await c.admin("POST", "faults", {"type": "stale_data", "duration_seconds": 5})
    assert (await c.instance()).stale is True
    await c.admin("POST", "faults/clear")
    await c.admin("POST", "faults", {"type": "unavailable", "duration_seconds": 3})
    t0 = time.monotonic()
    try:
        await c.depots(); raise AssertionError("expected failure")
    except (SimFaultInjected, CircuitOpenError):
        pass
    assert c.breaker.state == CircuitBreaker.OPEN, c.breaker.state
    try:
        await c.depots(); raise AssertionError("expected open")
    except CircuitOpenError:
        pass
    await asyncio.sleep(3.5)                                                   # fault expires + reset timeout
    r = await c.depots()                                                       # half-open probe succeeds
    assert c.breaker.state == CircuitBreaker.CLOSED and r.status == 200
    await c.admin("POST", "reset")
    await c.close()
    print(f"sim_client blueprint: all self-tests passed ({time.monotonic() - t0:.1f}s fault section)")


if __name__ == "__main__":
    asyncio.run(_selftest(os.environ.get("SIM_URL", "http://localhost:8000")))
