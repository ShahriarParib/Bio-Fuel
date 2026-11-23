"""Alert lifecycle: dedupe by fingerprint, refresh while the condition holds, auto-resolve after 2 clear cycles.

Detectors call `raise_()` every cycle while their condition is true. The in-memory copy (and the Redis document
`fsip:alerts` the api serves) always carries the freshest numbers; the database is written only when an alert opens,
changes severity, or resolves, so a long incident does not write a row per tick.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fsip_common.logging import get_logger
from prometheus_client import Counter, Gauge

from ..db import repo

log = get_logger()

ALERTS_OPENED = Counter("fsip_alerts_opened_total", "Alerts opened", ["category", "severity"])
ALERTS_OPEN = Gauge("fsip_alerts_open", "Open alerts", ["severity"])
CLEAR_CYCLES_TO_RESOLVE = 2


class AlertManager:
    def __init__(self, publisher, sessionmaker_fn) -> None:
        self.publisher = publisher
        self.sessionmaker_fn = sessionmaker_fn
        self.open: dict[str, dict[str, Any]] = {}
        self.resolved: list[dict[str, Any]] = []   # recent, newest last
        self._seen: set[str] = set()
        self._clear: dict[str, int] = {}
        self._local_id = 0
        self.run_id: int | None = None
        self.tick = 0

    def begin(self, run_id: int, tick: int) -> None:
        if self.run_id is not None and run_id != self.run_id:
            self.open.clear()
            self._clear.clear()
            self.resolved.clear()
        self.run_id, self.tick = run_id, tick
        self._seen = set()

    async def _db(self, fn, *args, **kwargs):
        try:
            async with self.sessionmaker_fn()() as session:
                return await fn(session, *args, **kwargs)
        except Exception as err:
            log.warning("alert.db_write_failed", error=str(err))
            return None

    async def raise_(self, fingerprint: str, category: str, severity: str, title: str, message: str,
                     entity_type: str | None = None, entity_id: str | None = None, fuel_type: str | None = None,
                     data: dict[str, Any] | None = None) -> dict[str, Any]:
        self._seen.add(fingerprint)
        self._clear.pop(fingerprint, None)
        a = self.open.get(fingerprint)
        if a is None:
            a = {"fingerprint": fingerprint, "category": category, "severity": severity, "status": "OPEN",
                 "title": title, "message": message, "entity_type": entity_type, "entity_id": entity_id,
                 "fuel_type": fuel_type, "opened_tick": self.tick, "resolved_tick": None,
                 "opened_at": datetime.now(UTC).isoformat(), "acknowledged_by": None,
                 "related_decision_id": None, "data": data or {}}
            row = await self._db(repo.upsert_alert, sim_run_id=self.run_id, fingerprint=fingerprint,
                                 category=category, severity=severity, title=title, message=message,
                                 opened_tick=self.tick, entity_type=entity_type, entity_id=entity_id,
                                 fuel_type=fuel_type, data=data)
            if row is None:
                self._local_id -= 1
            a["id"] = row.id if row is not None else self._local_id
            self.open[fingerprint] = a
            ALERTS_OPENED.labels(category=category, severity=severity).inc()
            log.info("alert.opened", alert_id=a["id"], category=category, severity=severity, title=title)
            await self.publisher.event("alert.opened", a)
            return a
        changed = a["severity"] != severity
        a.update(severity=severity, title=title, message=message, data=data or {})
        if changed:
            await self._db(repo.upsert_alert, sim_run_id=self.run_id, fingerprint=fingerprint, category=category,
                           severity=severity, title=title, message=message, opened_tick=a["opened_tick"],
                           entity_type=entity_type, entity_id=entity_id, fuel_type=fuel_type, data=data)
            await self.publisher.event("alert.updated", a)
        return a

    async def end(self) -> None:
        """Resolve alerts whose condition has been clear for 2 consecutive cycles."""
        for fp in list(self.open):
            if fp in self._seen:
                continue
            self._clear[fp] = self._clear.get(fp, 0) + 1
            if self._clear[fp] >= CLEAR_CYCLES_TO_RESOLVE:
                a = self.open.pop(fp)
                self._clear.pop(fp, None)
                a.update(status="RESOLVED", resolved_tick=self.tick)
                if a["id"] > 0:
                    await self._db(repo.resolve_alert, a["id"], self.tick)
                self.resolved.append(a)
                self.resolved = self.resolved[-100:]
                log.info("alert.resolved", alert_id=a["id"], category=a["category"],
                         open_ticks=self.tick - a["opened_tick"])
                await self.publisher.event("alert.resolved", a)
        for sev in ("INFO", "WARNING", "CRITICAL"):
            ALERTS_OPEN.labels(severity=sev).set(sum(1 for a in self.open.values() if a["severity"] == sev))

    def acknowledge(self, alert_id: int, actor: str) -> dict[str, Any] | None:
        for a in self.open.values():
            if a["id"] == alert_id:
                a["status"] = "ACKNOWLEDGED"
                a["acknowledged_by"] = actor
                return a
        return None

    def counts(self) -> dict[str, int]:
        sevs = ("INFO", "WARNING", "CRITICAL")
        return {sev: sum(1 for a in self.open.values() if a["severity"] == sev) for sev in sevs}

    def document(self) -> dict[str, Any]:
        rank = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
        items = sorted(self.open.values(), key=lambda a: (rank.get(a["severity"], 3), -a["opened_tick"]))
        return {"open": items, "recent_resolved": list(reversed(self.resolved))[:50]}
