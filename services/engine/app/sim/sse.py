"""Server-Sent Events (SSE) listener for real-time simulator push events."""
from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from fsip_common.logging import get_logger
from httpx_sse import aconnect_sse
from prometheus_client import Counter, Gauge

log = get_logger()

SSE_CONNECTED_GAUGE = Gauge(
    "fsip_sse_connected",
    "Whether SSE listener is currently connected to simulator: 1=connected, 0=disconnected",
)
SSE_RECONNECTS_TOTAL = Counter(
    "fsip_sse_reconnects_total",
    "Total count of SSE reconnect attempts",
)


class SSEListener:
    def __init__(
        self,
        base_url: str = "http://simulator:8000",
        on_tick: Callable[[int], Awaitable[None]] | None = None,
        on_notice: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_connect: Callable[[], Awaitable[None]] | None = None,
        reconnect_max_sec: float = 30.0,
        silence_timeout_sec: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.on_tick = on_tick
        self.on_notice = on_notice
        self.on_connect = on_connect
        self.reconnect_max_sec = reconnect_max_sec
        self.silence_timeout_sec = silence_timeout_sec

        self.connected = False
        self.reconnects = 0
        self._running = False
        SSE_CONNECTED_GAUGE.set(0)

    async def run(self) -> None:
        """Run the SSE listener loop continuously with backoff on failure and silence timeout."""
        self._running = True
        consecutive_failures = 0

        while self._running:
            url = f"{self.base_url}/v1/stream"
            try:
                log.info("sse.connecting", url=url)
                timeout = httpx.Timeout(connect=5.0, read=self.silence_timeout_sec, write=5.0, pool=5.0)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with aconnect_sse(client, "GET", url) as event_source:
                        self.connected = True
                        consecutive_failures = 0
                        SSE_CONNECTED_GAUGE.set(1)
                        log.info("sse.connected", url=url)

                        if self.on_connect:
                            try:
                                await self.on_connect()
                            except Exception as e:
                                log.warning("sse.on_connect_failed", error=str(e))

                        async for sse_event in event_source.aiter_sse():
                            if not self._running:
                                break
                            await self._handle_event(sse_event.event, sse_event.data)

            except (TimeoutError, httpx.TimeoutException, httpx.TransportError) as err:
                log.warning("sse.disconnected", error=str(err), error_type=type(err).__name__)
            except asyncio.CancelledError:
                self.connected = False
                SSE_CONNECTED_GAUGE.set(0)
                raise
            except Exception as e:
                log.error("sse.unexpected_error", error=str(e), error_type=type(e).__name__)

            self.connected = False
            SSE_CONNECTED_GAUGE.set(0)
            self.reconnects += 1
            SSE_RECONNECTS_TOTAL.inc()
            consecutive_failures += 1

            if not self._running:
                break

            backoff = min(self.reconnect_max_sec, (1.5 ** min(consecutive_failures, 8))) * (0.8 + 0.4 * random.random())
            log.info("sse.reconnect_wait", wait_seconds=round(backoff, 2), attempt=consecutive_failures)
            await asyncio.sleep(backoff)

    def stop(self) -> None:
        self._running = False
        self.connected = False
        SSE_CONNECTED_GAUGE.set(0)

    async def _handle_event(self, event_type: str, raw_data: str) -> None:
        """Process incoming SSE event string and dispatch to callbacks."""
        data: dict[str, Any] = {}
        if raw_data:
            try:
                data = json.loads(raw_data)
            except Exception:
                pass

        if event_type == "simulation.tick":
            tick = data.get("tick")
            if tick is not None and self.on_tick:
                try:
                    await self.on_tick(int(tick))
                except Exception as err:
                    log.error("sse.on_tick_error", tick=tick, error=str(err))

        elif event_type == "simulator.notice":
            if self.on_notice:
                try:
                    await self.on_notice(data)
                except Exception as err:
                    log.error("sse.on_notice_error", error=str(err))

        elif event_type in ("allocation.status_changed", "inventory.updated"):
            tick = data.get("tick")
            if tick is not None and self.on_tick:
                try:
                    await self.on_tick(int(tick))
                except Exception as err:
                    log.error("sse.on_tick_coalesced_error", event=event_type, error=str(err))
