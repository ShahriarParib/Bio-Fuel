from __future__ import annotations

from datetime import UTC, datetime


def parse_sim_time(value: str | datetime) -> datetime:
    """Simulator sends naive ISO strings ("2026-01-01T00:15:00"); the PDF shows "+00:00". Accept both -> aware UTC."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def ticks_per_day(tick_minutes: int) -> int:
    return (24 * 60) // tick_minutes


def ticks_to_hours(ticks: float, tick_minutes: int) -> float:
    return ticks * tick_minutes / 60.0


def hours_to_ticks(hours: float, tick_minutes: int) -> int:
    return int(round(hours * 60.0 / tick_minutes))


def expected_arrival_tick(created_tick: int, transit_ticks: int) -> int:
    """VERIFIED (simulator 1.0.0): created at T -> departure_tick = T -> expected_arrival_tick = T + transit.
    Station inventory shows the fuel when the instance reports tick T + transit + 1."""
    return created_tick + transit_ticks


def format_sim_clock(tick: int, tick_minutes: int) -> str:
    """'Day 2 11:00 · t=140' style label used across the UI and logs."""
    minutes = tick * tick_minutes
    day, rem = divmod(minutes, 24 * 60)
    return f"Day {day + 1} {rem // 60:02d}:{rem % 60:02d} · t={tick}"
