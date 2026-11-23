"""Synchronization loop continuously mirroring the simulator into WorldState."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fsip_common.logging import get_logger
from prometheus_client import Counter, Gauge, Histogram

from ..sim.client import SimClient, SimResult
from ..sim.validation import validate_resource
from .store import WorldState

log = get_logger()

CYCLE_DURATION = Histogram(
    "fsip_cycle_duration_seconds",
    "Duration of a full sync/decision cycle in seconds",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

TICKS_BEHIND_GAUGE = Gauge(
    "fsip_ticks_behind",
    "Difference between the simulator's current tick and the last processed engine tick",
)

LOOP_RESTARTS_TOTAL = Counter(
    "fsip_loop_restarts_total",
    "Count of supervisor restarts for background loops",
    ["loop"],
)


class SyncLoop:
    def __init__(
        self,
        client: SimClient,
        store: WorldState,
        hooks: list[Callable[[WorldState], Awaitable[None]]] | None = None,
        poll_interval: float = 1.0,
        on_run_start: Callable[[Any, bool], Awaitable[None]] | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.hooks = hooks or []
        self.poll_interval = poll_interval
        # called with (instance, is_reset) on the first successful sync and on every detected reset
        self.on_run_start = on_run_start
        self._run_ready = False

        self._running = False
        self._is_cycling = False
        self._pending_tick: int | None = None
        self._last_processed_tick = -1
        self._last_seed: int | None = None
        self._last_scenario: str | None = None
        self._lock = asyncio.Lock()

    async def run(self) -> None:
        """Periodic fallback poll loop.

        Runs every poll_interval to verify current tick and catch up if SSE drops.
        """
        self._running = True
        log.info("sync_loop.started", poll_interval=self.poll_interval)

        # Immediate initial full sync on startup
        await self.request_cycle()

        while self._running:
            try:
                await asyncio.sleep(self.poll_interval)
                if not self._running:
                    break

                # Poll instance to check current tick
                inst_res = await self.client.instance()
                if isinstance(inst_res, SimResult) and inst_res.data:
                    current_tick = inst_res.data.get("tick", 0)
                    # `!=` not `>`: after /admin/reset the tick goes BACK to 0; with a paused simulator no new tick
                    # would ever arrive, so the reset (new run, cleared state) would never be noticed.
                    if current_tick != self._last_processed_tick:
                        await self.request_cycle(current_tick)
            except asyncio.CancelledError:
                break
            except Exception as err:
                log.warning("sync_loop.poll_error", error=str(err), error_type=type(err).__name__)

    def stop(self) -> None:
        self._running = False

    async def request_cycle(self, tick: int | None = None) -> None:
        """Request a synchronization cycle with strict coalescing.

        If a cycle is already executing, stores the latest pending tick and ensures
        exactly one additional cycle runs afterward.
        """
        async with self._lock:
            if tick is not None:
                self._pending_tick = max(self._pending_tick or 0, tick)

            if self._is_cycling:
                # Cycle already running, will pick up _pending_tick when finished
                return
            self._is_cycling = True

        try:
            while True:
                target_tick = self._pending_tick
                self._pending_tick = None
                await self._execute_cycle(target_tick)

                async with self._lock:
                    if self._pending_tick is None:
                        self._is_cycling = False
                        break
        except Exception as e:
            async with self._lock:
                self._is_cycling = False
            log.error("sync_loop.cycle_fatal", error=str(e), error_type=type(e).__name__)

    async def full_sync(self) -> None:
        """Force an immediate full synchronization cycle."""
        await self.request_cycle()

    async def _execute_cycle(self, hint_tick: int | None) -> None:
        """Run one synchronized snapshot, demand ingestion, reset check, and notify hooks."""
        t0 = time.perf_counter()

        # 1. Fetch parallel snapshot from simulator
        raw_snap = await self.client.snapshot()
        self.store.apply_snapshot(raw_snap)

        if not self.store.snapshot:
            log.warning("sync_loop.empty_snapshot", hint_tick=hint_tick)
            return

        inst = self.store.snapshot.instance
        current_tick = inst.tick

        # Track lag
        lag = 0
        if hint_tick is not None and hint_tick > current_tick:
            lag = hint_tick - current_tick
        TICKS_BEHIND_GAUGE.set(lag)

        # 2. Check for simulator reset
        # Reset detected if tick dropped, seed changed, or scenario changed
        is_reset = False
        if self._last_processed_tick > 0 and current_tick < self._last_processed_tick:
            is_reset = True
        elif self._last_seed is not None and inst.seed != self._last_seed:
            is_reset = True
        elif self._last_scenario is not None and inst.scenario_id != self._last_scenario:
            is_reset = True

        if is_reset:
            self._handle_reset(inst.tick, inst.seed, inst.scenario_id)

        # The run id must be settled (resumed or created in the DB) before anything is persisted or any
        # idempotency key is built for this world.
        if (is_reset or not self._run_ready) and self.on_run_start is not None:
            await self.on_run_start(inst, is_reset)
        self._run_ready = True

        # 3. Ingest recent demand observations (12 rows per tick). Computed BEFORE moving the cursor, otherwise
        # coalesced ticks (fast simulator, slow cycle) would silently lose demand rows. First sync / reset
        # fetches the full window the simulator allows.
        if is_reset or self._last_processed_tick < 0:
            limit = 2000
        else:
            ticks_elapsed = max(1, current_tick - self._last_processed_tick)
            limit = min(2000, 12 * ticks_elapsed + 12)

        self._last_processed_tick = current_tick
        self._last_seed = inst.seed
        self._last_scenario = inst.scenario_id
        self.store.new_demand_rows = []
        try:
            dh_res = await self.client.demand_history(limit=limit)
            if isinstance(dh_res, SimResult) and isinstance(dh_res.data, list):
                raw_obs = validate_resource("demand_history", dh_res.data)
                if isinstance(raw_obs, list):
                    new_count = self.store.ingest_demand(raw_obs)
                    # must clear on success: left sticky, it kept every decision at confidence 0.5 after an outage, so
                    # autopilot silently never executed again (found by re-running the F1 drill with autopilot on)
                    self.store.degraded_reasons.discard("demand_fetch_failed")
                    log.debug("sync.demand_ingested", new_count=new_count, tick=current_tick)
        except Exception as err:
            self.store.degraded_reasons.add("demand_fetch_failed")
            log.warning("sync.demand_fetch_failed", error=str(err))

        duration_s = time.perf_counter() - t0
        CYCLE_DURATION.observe(duration_s)

        log.info(
            "sim.sync.ok",
            tick=current_tick,
            sim_run_id=self.store.sim_run_id,
            duration_ms=round(duration_s * 1000, 1),
            degraded=self.store.degraded,
            stale=self.store.stale,
        )

        # 4. Invoke registered downstream pipeline hooks (e.g. risk, optimizer, persistence, publisher)
        for hook in self.hooks:
            try:
                await hook(self.store)
            except Exception as hook_err:
                log.error("sync.hook_error", hook=getattr(hook, "__name__", str(hook)), error=str(hook_err))

    def _handle_reset(self, new_tick: int, new_seed: int, new_scenario: str) -> None:
        """Handle a detected simulator reset."""
        old_run = self.store.sim_run_id
        if self.on_run_start is None:
            # no run registry (tests / DB-less mode): a local counter is enough
            self.store.sim_run_id += 1
        self.store.demand.clear()
        self.store.risk.clear()
        self.store.pending_recommendations.clear()
        self.store.degraded_reasons.clear()

        log.info(
            "reset.detected",
            old_sim_run_id=old_run,
            new_sim_run_id=self.store.sim_run_id,
            tick=new_tick,
            seed=new_seed,
            scenario=new_scenario,
        )
