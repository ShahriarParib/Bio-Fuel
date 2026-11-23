"""Resilient asynchronous client for the BUP Fuel Supply Simulator."""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx
from fsip_common.logging import get_logger
from prometheus_client import Counter, Histogram

from .circuit import CircuitBreaker, CircuitOpenError
from .errors import (
    SimFaultInjected,
    SimUnavailable,
    parse_error,
)

log = get_logger()

SIM_REQUEST_DURATION = Histogram(
    "fsip_sim_request_duration_seconds",
    "Simulator request latency in seconds",
    ["endpoint", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0),
)

SIM_ERRORS_TOTAL = Counter(
    "fsip_sim_errors_total",
    "Simulator errors partitioned by endpoint and error code",
    ["endpoint", "code"],
)


@dataclass
class SimResult:
    data: Any
    stale: bool
    status: int
    latency_ms: float


RETRYABLE = (SimUnavailable, SimFaultInjected)


class SimClient:
    def __init__(
        self,
        base_url: str = "http://simulator:8000",
        timeout: float = 2.0,
        max_retries: int = 3,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.breaker = breaker or CircuitBreaker("simulator")
        self.http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        retry: bool = True,
        endpoint_label: str | None = None,
    ) -> SimResult:
        ep_label = endpoint_label or path
        attempts = self.max_retries + 1 if retry else 1
        last_exc: Exception | None = None

        for attempt in range(attempts):
            if not self.breaker.allow():
                raise CircuitOpenError(self.breaker.name)

            t0 = time.perf_counter()
            status_label = "0"
            try:
                resp = await self.http.request(method, path, json=json, params=params)
                status_label = str(resp.status_code)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = SimUnavailable(f"{type(e).__name__}: {e}")
                status_label = "timeout" if isinstance(e, httpx.TimeoutException) else "error"
            else:
                elapsed_s = time.perf_counter() - t0
                latency_ms = elapsed_s * 1000.0
                SIM_REQUEST_DURATION.labels(endpoint=ep_label, status=status_label).observe(elapsed_s)

                if resp.status_code < 400:
                    self.breaker.record_success()
                    stale = resp.headers.get("x-simulator-stale", "").lower() == "true"
                    return SimResult(
                        data=resp.json(),
                        stale=stale,
                        status=resp.status_code,
                        latency_ms=latency_ms,
                    )

                err = parse_error(resp)
                code = getattr(err, "code", type(err).__name__)
                SIM_ERRORS_TOTAL.labels(endpoint=ep_label, code=str(code)).inc()

                if not isinstance(err, RETRYABLE):
                    # Domain errors (404, 409, 422) mean simulator is healthy and answered
                    self.breaker.record_success()
                    raise err

                last_exc = err

            elapsed_s = time.perf_counter() - t0
            SIM_REQUEST_DURATION.labels(endpoint=ep_label, status=status_label).observe(elapsed_s)
            self.breaker.record_failure()

            if attempt < attempts - 1:
                backoff = min(2.0, 0.2 * (2**attempt)) * (0.5 + random.random())
                await asyncio.sleep(backoff)

        raise last_exc or SimUnavailable("Unknown simulator request error")

    # --- Read API ---
    async def health(self) -> SimResult:
        return await self._request("GET", "/v1/health", endpoint_label="/v1/health")

    async def instance(self) -> SimResult:
        return await self._request("GET", "/v1/instance", endpoint_label="/v1/instance")

    async def regions(self) -> SimResult:
        return await self._request("GET", "/v1/regions", endpoint_label="/v1/regions")

    async def depots(self) -> SimResult:
        return await self._request("GET", "/v1/depots", endpoint_label="/v1/depots")

    async def depot(self, depot_id: str) -> SimResult:
        return await self._request("GET", f"/v1/depots/{depot_id}", endpoint_label="/v1/depots/:id")

    async def stations(self) -> SimResult:
        return await self._request("GET", "/v1/stations", endpoint_label="/v1/stations")

    async def station(self, station_id: str) -> SimResult:
        return await self._request("GET", f"/v1/stations/{station_id}", endpoint_label="/v1/stations/:id")

    async def routes(self) -> SimResult:
        return await self._request("GET", "/v1/routes", endpoint_label="/v1/routes")

    async def events(self) -> SimResult:
        return await self._request("GET", "/v1/events", endpoint_label="/v1/events")

    async def supply_arrivals(self) -> SimResult:
        return await self._request("GET", "/v1/supply-arrivals", endpoint_label="/v1/supply-arrivals")

    async def allocations(self) -> SimResult:
        return await self._request("GET", "/v1/allocations", endpoint_label="/v1/allocations")

    async def metrics(self) -> SimResult:
        return await self._request("GET", "/v1/metrics", endpoint_label="/v1/metrics")

    async def demand_history(self, station_id: str | None = None, limit: int = 200) -> SimResult:
        params: dict[str, Any] = {"limit": max(1, min(2000, limit))}
        if station_id:
            params["station_id"] = station_id
        return await self._request(
            "GET",
            "/v1/demand-history",
            params=params,
            endpoint_label="/v1/demand-history",
        )

    async def snapshot(self) -> dict[str, SimResult | Exception]:
        """Fetch all core state endpoints in parallel.

        Failures are captured per resource rather than aborting the entire cycle.
        """
        names = [
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
        calls = [
            self.instance(),
            self.regions(),
            self.depots(),
            self.stations(),
            self.routes(),
            self.events(),
            self.supply_arrivals(),
            self.allocations(),
            self.metrics(),
        ]
        results = await asyncio.gather(*calls, return_exceptions=True)
        return dict(zip(names, results, strict=False))

    # --- Write API (POST allocations, cancel) ---
    async def create_allocation(self, body: dict[str, Any]) -> SimResult:
        """Create an allocation.

        Retries are safe because the idempotency_key is identical.
        """
        return await self._request("POST", "/v1/allocations", json=body, endpoint_label="/v1/allocations")

    async def cancel_allocation(self, alloc_id: int) -> SimResult:
        return await self._request(
            "POST",
            f"/v1/allocations/{alloc_id}/cancel",
            endpoint_label="/v1/allocations/:id/cancel",
        )

    # --- Admin API (Testing / Harness / Demo controls only) ---
    async def admin_step(self) -> SimResult:
        return await self._request("POST", "/admin/step", retry=False, endpoint_label="/admin/step")

    async def admin_reset(self) -> SimResult:
        return await self._request("POST", "/admin/reset", retry=False, endpoint_label="/admin/reset")

    async def admin_run(self) -> SimResult:
        return await self._request("POST", "/admin/run", retry=False, endpoint_label="/admin/run")

    async def admin_pause(self) -> SimResult:
        return await self._request("POST", "/admin/pause", retry=False, endpoint_label="/admin/pause")

    async def admin_events_create(self, event: dict[str, Any]) -> SimResult:
        return await self._request("POST", "/admin/events", json=event, retry=False, endpoint_label="/admin/events")

    async def admin_faults_create(self, fault: dict[str, Any]) -> SimResult:
        return await self._request("POST", "/admin/faults", json=fault, retry=False, endpoint_label="/admin/faults")

    async def admin_faults_clear(self) -> SimResult:
        return await self._request("POST", "/admin/faults/clear", retry=False, endpoint_label="/admin/faults/clear")

    async def admin_audit(self, limit: int = 100) -> SimResult:
        return await self._request(
            "GET",
            "/admin/audit",
            params={"limit": limit},
            retry=False,
            endpoint_label="/admin/audit",
        )
