"""SQLAlchemy 2 declarative database models for TimescaleDB / PostgreSQL."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BIGINT,
    BOOLEAN,
    DOUBLE_PRECISION,
    INT,
    TEXT,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SimRun(Base):
    __tablename__ = "sim_runs"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    scenario_id: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    seed: Mapped[int | None] = mapped_column(BIGINT, nullable=True)
    tick_minutes: Mapped[int | None] = mapped_column(INT, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_tick: Mapped[int | None] = mapped_column(INT, nullable=True)
    last_tick: Mapped[int | None] = mapped_column(INT, nullable=True)


class WorldSnapshot(Base):
    __tablename__ = "world_snapshots"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    sim_run_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("sim_runs.id"), nullable=False)
    tick: Mapped[int] = mapped_column(INT, nullable=False)
    sim_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    depots: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    stations: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    routes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    events: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    supply: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    stale: Mapped[bool] = mapped_column(BOOLEAN, default=False)
    degraded: Mapped[bool] = mapped_column(BOOLEAN, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("sim_run_id", "tick", name="uq_world_snapshots_run_tick"),
    )


class DemandObservationModel(Base):
    __tablename__ = "demand_observations"

    sim_run_id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    sim_row_id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    station_id: Mapped[str] = mapped_column(TEXT, nullable=False)
    fuel_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    tick: Mapped[int] = mapped_column(INT, nullable=False)
    sim_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    demand_liters: Mapped[float] = mapped_column(DOUBLE_PRECISION, default=0.0)
    served_liters: Mapped[float] = mapped_column(DOUBLE_PRECISION, default=0.0)
    unmet_liters: Mapped[float] = mapped_column(DOUBLE_PRECISION, default=0.0)

    __table_args__ = (
        Index("ix_demand_obs_lookup", "sim_run_id", "station_id", "fuel_type", "tick"),
    )


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    sim_run_id: Mapped[int] = mapped_column(BIGINT, nullable=False)
    tick: Mapped[int] = mapped_column(INT, nullable=False)
    policy: Mapped[str] = mapped_column(TEXT, default="optimizer")
    policy_version: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    trigger: Mapped[str] = mapped_column(TEXT, default="scheduled")
    status: Mapped[str] = mapped_column(TEXT, default="PROPOSED")
    mode: Mapped[str] = mapped_column(TEXT, default="advisory")
    confidence: Mapped[float] = mapped_column(DOUBLE_PRECISION, default=1.0)
    degraded: Mapped[bool] = mapped_column(BOOLEAN, default=False)
    fallback_reason: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    objective: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    impact: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    narrative: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    expires_at_tick: Mapped[int | None] = mapped_column(INT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    lines: Mapped[list[DecisionLine]] = relationship("DecisionLine", back_populates="decision")


class DecisionLine(Base):
    __tablename__ = "decision_lines"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    decision_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("decisions.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(INT, nullable=False)
    source_depot_id: Mapped[str] = mapped_column(TEXT, nullable=False)
    destination_station_id: Mapped[str] = mapped_column(TEXT, nullable=False)
    route_id: Mapped[str] = mapped_column(TEXT, nullable=False)
    fuel_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    quantity: Mapped[float] = mapped_column(DOUBLE_PRECISION, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(TEXT, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(TEXT, default="PLANNED")
    sim_allocation_id: Mapped[int | None] = mapped_column(BIGINT, nullable=True)
    sim_error_code: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    expected_arrival_tick: Mapped[int | None] = mapped_column(INT, nullable=True)
    actual_arrival_tick: Mapped[int | None] = mapped_column(INT, nullable=True)
    received_liters: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    reason: Mapped[str | None] = mapped_column(TEXT, nullable=True)

    decision: Mapped[Decision] = relationship("Decision", back_populates="lines")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    sim_run_id: Mapped[int] = mapped_column(BIGINT, nullable=False)
    fingerprint: Mapped[str] = mapped_column(TEXT, nullable=False)
    category: Mapped[str] = mapped_column(TEXT, nullable=False)
    severity: Mapped[str] = mapped_column(TEXT, nullable=False)
    title: Mapped[str] = mapped_column(TEXT, nullable=False)
    message: Mapped[str] = mapped_column(TEXT, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    entity_id: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    fuel_type: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    status: Mapped[str] = mapped_column(TEXT, default="OPEN")
    opened_tick: Mapped[int] = mapped_column(INT, nullable=False)
    resolved_tick: Mapped[int | None] = mapped_column(INT, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    related_decision_id: Mapped[int | None] = mapped_column(BIGINT, nullable=True)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(TEXT, nullable=False)
    component: Mapped[str] = mapped_column(TEXT, nullable=False)
    status: Mapped[str] = mapped_column(TEXT, default="OPEN")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    mitigated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str] = mapped_column(TEXT, default="")
    timeline: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, default=list)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    actor: Mapped[str] = mapped_column(TEXT, nullable=False)
    action: Mapped[str] = mapped_column(TEXT, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    entity_id: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class Quarantine(Base):
    __tablename__ = "quarantine"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resource: Mapped[str] = mapped_column(TEXT, nullable=False)
    # dict or list; a `dict | list` union breaks SQLAlchemy's string annotations
    errors: Mapped[Any] = mapped_column(JSONB, nullable=False)
    raw_body: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    sim_tick: Mapped[int | None] = mapped_column(INT, nullable=True)
