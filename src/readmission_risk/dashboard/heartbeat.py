"""The dashboard heartbeat (PRD v3 FR11): the simulated clock, the newest tick and the schedule's freshness. wall_now() is the
dashboard's ONLY wall-clock read (owner decision S6); "stale" is the ledger's rule (monitoring.freshness), "late" the
week-based S13 rule. See notes/eg-new-feature/v3-slice3-dashboard-app-2026-11-01.md (Interfaces section 4).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from readmission_risk.monitoring.freshness import (
    WEEKLY_CRON,
    newest_scheduled_success,
    stale_after,
)

from .present import short_id, status_word

HeartbeatState = Literal["ok", "late", "stale", "not_started", "no_ticks", "unknown"]
_HEADS = {
    "ok": "✅ **Fresh**", "late": "⚠️ **Late**", "not_started": "⏸️ **Schedule not started**",
    "no_ticks": "⏸️ **No monitoring tick yet**", "unknown": "❔ **Heartbeat unavailable**",
}  # fmt: skip


@dataclass(frozen=True)
class Heartbeat:
    state: HeartbeatState
    sim_date: date | None = None
    monitored_bundle: str | None = None
    last_tick_started_at: datetime | None = None
    last_tick_status: str | None = None
    history_tests_passed: bool | None = None
    predictions_tests_passed: bool | None = None
    anchor: datetime | None = None
    late_at: datetime | None = None
    stale_at: datetime | None = None

    @classmethod
    def unavailable(cls) -> Heartbeat:
        return cls("unknown")


def wall_now() -> datetime:
    return datetime.now(UTC)


def late_after(anchor: datetime) -> datetime:
    """S13: Friday 00:00 UTC of the ISO week after the anchor's ISO week (the next week's Monday-Wednesday slots plus a day
    for delayed slots)."""
    utc = anchor.astimezone(UTC)
    monday = (utc - timedelta(days=utc.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday + timedelta(days=11)


def _key(row: Mapping) -> tuple:
    return (row["started_at"], row.get("monitoring_run_id") or "")


def simulated_clock(rows: Iterable[Mapping]) -> tuple[date, str, Mapping] | None:
    """D5 in Python (queries.py's ticks/monitored_bundle/clock CTEs): the monitored bundle is the newest succeeded tick's; the
    clock is that bundle's latest succeeded sim_date; also returns the newest of that bundle's ticks on that date."""
    candidates = [
        r for r in rows
        if r.get("status") == "succeeded" and r.get("sim_date") is not None and r.get("model_bundle_id") is not None
    ]  # fmt: skip
    if not candidates:
        return None
    bundle = max(candidates, key=_key)["model_bundle_id"]
    mine = [r for r in candidates if r["model_bundle_id"] == bundle]
    clock = max(r["sim_date"] for r in mine)
    return clock, bundle, max((r for r in mine if r["sim_date"] == clock), key=_key)


def heartbeat(
    monitoring_rows: Sequence[Mapping] | None, scoring_rows: Sequence[Mapping] | None, *, now: datetime, cron: str = WEEKLY_CRON
) -> Heartbeat:
    if now.tzinfo is None:
        raise ValueError(f"now must be tz-aware; got {now!r}")
    if monitoring_rows is None:
        return Heartbeat.unavailable()
    if not monitoring_rows:
        return Heartbeat("no_ticks")
    clock = simulated_clock(monitoring_rows)
    newest = max(monitoring_rows, key=_key)
    run_id = newest.get("scoring_run_id")
    run = None
    if scoring_rows is not None and run_id is not None:
        run = next((r for r in scoring_rows if r.get("scoring_run_id") == run_id), None)
    anchor_row = newest_scheduled_success(monitoring_rows)
    anchor = None if anchor_row is None else anchor_row["started_at"].astimezone(UTC)
    late_at = None if anchor is None else late_after(anchor)
    stale_at = stale_after(monitoring_rows, cron=cron)
    if anchor is None:
        state: HeartbeatState = "not_started"
    elif now >= stale_at:
        state = "stale"
    elif now >= late_at:
        state = "late"
    else:
        state = "ok"
    return Heartbeat(
        state=state,
        sim_date=None if clock is None else clock[0],
        monitored_bundle=None if clock is None else clock[1],
        last_tick_started_at=newest["started_at"],
        last_tick_status=newest.get("status"),
        history_tests_passed=newest.get("history_tests_passed"),
        predictions_tests_passed=None if run is None else run.get("predictions_tests_passed"),
        anchor=anchor,
        late_at=late_at,
        stale_at=stale_at,
    )


def _tests(hb: Heartbeat) -> str:
    flags = (hb.history_tests_passed, hb.predictions_tests_passed)
    if all(flag is True for flag in flags):
        return "data tests passed"
    if any(flag is False for flag in flags):
        return "data tests FAILED"
    return "data tests unknown"


def heartbeat_line(hb: Heartbeat) -> str:
    """One markdown line, status as icon + bold word, then " · "-separated facts (format pinned in the design's §4)."""
    if hb.state == "stale":
        head = f"⛔ **Stale since {hb.stale_at.astimezone(UTC):%Y-%m-%d}**"
    else:
        head = _HEADS[hb.state]
    if hb.state in ("no_ticks", "unknown"):
        return head
    parts = [
        head,
        "simulated date unknown" if hb.sim_date is None else f"simulated date {hb.sim_date:%Y-%m-%d}",
        f"last tick ran {hb.last_tick_started_at.astimezone(UTC):%Y-%m-%d %H:%M} UTC ({status_word(hb.last_tick_status)})",
        _tests(hb),
        f"bundle `{short_id(hb.monitored_bundle)}`",
    ]
    if hb.state in ("late", "stale"):
        tail = f"no scheduled weekly tick since {hb.anchor:%Y-%m-%d %H:%M} UTC"
        if hb.state == "late":
            tail += f"; stale from {hb.stale_at.astimezone(UTC):%Y-%m-%d}"
        parts.append(tail)
    return " · ".join(parts)
