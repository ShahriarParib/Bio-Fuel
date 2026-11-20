"""Async repository functions for database operations."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fsip_common.logging import get_logger
from fsip_common.models import DemandObservation
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Alert,
    AuditLog,
    Decision,
    DecisionLine,
    DemandObservationModel,
    Incident,
    Quarantine,
    SimRun,
    WorldSnapshot,
)

log = get_logger()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_sim_run(
    session: AsyncSession,
    scenario_id: str | None = None,
    seed: int | None = None,
    tick_minutes: int | None = 15,
    first_tick: int | None = 0,
) -> SimRun:
    run = SimRun(
        scenario_id=scenario_id,
        seed=seed,
        tick_minutes=tick_minutes,
        first_tick=first_tick,
        last_tick=first_tick,
        started_at=_now(),
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def close_sim_run(session: AsyncSession, run_id: int, last_tick: int) -> None:
    stmt = (
        update(SimRun)
        .where(SimRun.id == run_id)
        .values(ended_at=_now(), last_tick=last_tick)
    )
    await session.execute(stmt)
    await session.commit()


async def upsert_snapshot(
    session: AsyncSession,
    sim_run_id: int,
    tick: int,
    sim_time: datetime,
    depots: list[dict[str, Any]] | None = None,
    stations: list[dict[str, Any]] | None = None,
    routes: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    supply: list[dict[str, Any]] | None = None,
    metrics: dict[str, Any] | None = None,
    stale: bool = False,
    degraded: bool = False,
) -> WorldSnapshot:
    stmt = (
        insert(WorldSnapshot)
        .values(
            sim_run_id=sim_run_id,
            tick=tick,
            sim_time=sim_time,
            depots=depots,
            stations=stations,
            routes=routes,
            events=events,
            supply=supply,
            metrics=metrics,
            stale=stale,
            degraded=degraded,
            created_at=_now(),
        )
        .on_conflict_do_update(
            index_elements=["sim_run_id", "tick"],
            set_={
                "sim_time": sim_time,
                "depots": depots,
                "stations": stations,
                "routes": routes,
                "events": events,
                "supply": supply,
                "metrics": metrics,
                "stale": stale,
                "degraded": degraded,
            },
        )
        .returning(WorldSnapshot)
    )
    res = await session.execute(stmt)
    await session.commit()
    return res.scalar_one()


async def insert_demand_rows(
    session: AsyncSession,
    sim_run_id: int,
    observations: list[DemandObservation],
) -> int:
    if not observations:
        return 0

    values = [
        {
            "sim_run_id": sim_run_id,
            "sim_row_id": obs.id,
            "station_id": obs.station_id,
            "fuel_type": obs.fuel_type,
            "tick": obs.tick,
            "sim_time": obs.sim_time,
            "demand_liters": obs.demand_liters,
            "served_liters": obs.served_liters,
            "unmet_liters": obs.unmet_liters,
        }
        for obs in observations
    ]

    stmt = insert(DemandObservationModel).values(values).on_conflict_do_nothing()
    res = await session.execute(stmt)
    await session.commit()
    return res.rowcount or 0


async def upsert_alert(
    session: AsyncSession,
    sim_run_id: int,
    fingerprint: str,
    category: str,
    severity: str,
    title: str,
    message: str,
    opened_tick: int,
    entity_type: str | None = None,
    entity_id: str | None = None,
    fuel_type: str | None = None,
    data: dict[str, Any] | None = None,
    related_decision_id: int | None = None,
) -> Alert:
    # Check if open alert with fingerprint exists
    q = select(Alert).where(Alert.fingerprint == fingerprint, Alert.status != "RESOLVED")
    existing = (await session.execute(q)).scalar_one_or_none()

    if existing:
        existing.severity = severity
        existing.title = title
        existing.message = message
        existing.data = data
        await session.commit()
        await session.refresh(existing)
        return existing

    new_alert = Alert(
        sim_run_id=sim_run_id,
        fingerprint=fingerprint,
        category=category,
        severity=severity,
        title=title,
        message=message,
        opened_tick=opened_tick,
        entity_type=entity_type,
        entity_id=entity_id,
        fuel_type=fuel_type,
        status="OPEN",
        opened_at=_now(),
        data=data,
        related_decision_id=related_decision_id,
    )
    session.add(new_alert)
    await session.commit()
    await session.refresh(new_alert)
    return new_alert


async def resolve_alert(session: AsyncSession, alert_id: int, resolved_tick: int) -> None:
    stmt = (
        update(Alert)
        .where(Alert.id == alert_id)
        .values(status="RESOLVED", resolved_tick=resolved_tick, resolved_at=_now())
    )
    await session.execute(stmt)
    await session.commit()


async def create_decision(
    session: AsyncSession,
    decision_data: dict[str, Any],
    lines_data: list[dict[str, Any]],
) -> Decision:
    dec = Decision(**decision_data)
    session.add(dec)
    await session.flush()

    for idx, line_dict in enumerate(lines_data):
        line = DecisionLine(
            decision_id=dec.id,
            line_no=idx + 1,
            **line_dict,
        )
        session.add(line)

    await session.commit()
    await session.refresh(dec)
    return dec


async def insert_incident(
    session: AsyncSession,
    kind: str,
    component: str,
    summary: str,
    timeline: list[dict[str, Any]] | None = None,
) -> Incident:
    inc = Incident(
        kind=kind,
        component=component,
        summary=summary,
        status="OPEN",
        started_at=_now(),
        timeline=timeline or [],
    )
    session.add(inc)
    await session.commit()
    await session.refresh(inc)
    return inc


async def audit_log(
    session: AsyncSession,
    actor: str,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    entry = AuditLog(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
        at=_now(),
    )
    session.add(entry)
    await session.commit()


async def insert_quarantine(
    session: AsyncSession,
    resource: str,
    errors: Any,
    raw_body: str | None = None,
    sim_tick: int | None = None,
) -> Quarantine:
    q = Quarantine(
        resource=resource,
        errors=errors,
        raw_body=raw_body[:65535] if raw_body else None,
        sim_tick=sim_tick,
        at=_now(),
    )
    session.add(q)
    await session.commit()
    await session.refresh(q)
    return q
