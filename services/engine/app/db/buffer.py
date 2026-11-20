"""Asynchronous non-blocking write buffer for database persistence."""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from fsip_common.logging import get_logger
from prometheus_client import Counter, Gauge
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = get_logger()

DB_BUFFERED_WRITES = Gauge(
    "fsip_db_buffered_writes",
    "Current count of write operations waiting in the memory buffer",
)

DB_DROPPED_WRITES = Counter(
    "fsip_db_dropped_writes_total",
    "Total count of database write operations dropped due to buffer overflow",
    ["kind"],
)


class WriteBuffer:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        max_items: int = 5000,
        batch_size: int = 50,
        flush_interval_sec: float = 0.5,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.max_items = max_items
        self.batch_size = batch_size
        self.flush_interval_sec = flush_interval_sec

        self._queue: deque[tuple[str, Callable[[AsyncSession], Awaitable[None]]]] = deque()
        self._running = False
        self._notify_event = asyncio.Event()

    def submit(self, fn: Callable[[AsyncSession], Awaitable[None]], kind: str = "generic") -> None:
        """Submit an async write operation to the queue. Non-blocking."""
        if len(self._queue) >= self.max_items:
            dropped_kind, _ = self._queue.popleft()
            DB_DROPPED_WRITES.labels(kind=dropped_kind).inc()
            log.warning("db.buffer_overflow_dropped", dropped_kind=dropped_kind, max_items=self.max_items)

        self._queue.append((kind, fn))
        DB_BUFFERED_WRITES.set(len(self._queue))
        self._notify_event.set()

    async def run(self) -> None:
        """Background worker that commits writes in batches with backoff on DB outage."""
        self._running = True
        consecutive_errors = 0

        while self._running:
            try:
                if not self._queue:
                    # Wait for items or timeout
                    try:
                        await asyncio.wait_for(self._notify_event.wait(), timeout=self.flush_interval_sec)
                    except asyncio.TimeoutError:
                        pass
                    self._notify_event.clear()

                if not self._queue or not self._running:
                    continue

                # Pop up to batch_size items
                batch: list[tuple[str, Callable[[AsyncSession], Awaitable[None]]]] = []
                while self._queue and len(batch) < self.batch_size:
                    batch.append(self._queue.popleft())
                DB_BUFFERED_WRITES.set(len(self._queue))

                # Execute batch in a single transaction
                try:
                    async with self.sessionmaker() as session:
                        for kind, fn in batch:
                            await fn(session)
                    consecutive_errors = 0
                except Exception as db_err:
                    consecutive_errors += 1
                    # Re-queue batch to front so they aren't lost
                    for item in reversed(batch):
                        self._queue.appendleft(item)
                    DB_BUFFERED_WRITES.set(len(self._queue))

                    backoff = min(15.0, 0.5 * (2 ** min(consecutive_errors, 5)))
                    log.warning(
                        "db.batch_write_retry",
                        error=str(db_err),
                        error_type=type(db_err).__name__,
                        queued=len(self._queue),
                        retry_in_sec=round(backoff, 2),
                    )
                    await asyncio.sleep(backoff)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("db.buffer_unexpected_error", error=str(e), error_type=type(e).__name__)
                await asyncio.sleep(1.0)

    def stop(self) -> None:
        self._running = False
        self._notify_event.set()
