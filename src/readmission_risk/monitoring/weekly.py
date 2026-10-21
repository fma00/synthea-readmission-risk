"""The weekly tick's skip rule (pure). GitHub's scheduler drops most cron slots for this repository, so the weekly cron
fires hourly Monday to Wednesday and the clock skips a scheduled run once this week's weekly tick has succeeded, or once
RETRY_BUDGET weekly attempts this week did not succeed.

Invariant: skip eligibility is decided by the run's event (`schedule`); the budget counts rows by their recorded
`trigger`, which comes from GITHUB_EVENT_NAME (warehouse/run.py::_trigger_and_url). Both come from the same Actions
environment, so they agree in Actions; on the laptop the event is never `schedule`. See
notes/eg-new-feature/monitoring-burn-in-timer-and-weekly-retry-2026-10-20.md (Part 2, weekly.py).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from .config import MonitoringConfig

RETRY_BUDGET = 3


def week_start(at: datetime) -> datetime:
    """Monday 00:00 UTC of `at`'s ISO week."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("at must be timezone-aware")
    day = at.astimezone(UTC).date()
    monday = day - timedelta(days=day.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=UTC)


def _weekly_attempts(ticks: pd.DataFrame, config: MonitoringConfig, at: datetime) -> pd.DataFrame:
    start = pd.Timestamp(week_start(at))
    started = pd.to_datetime(ticks["started_at"], utc=True)
    cadence = ticks["cadence"]
    mask = (
        (ticks["trigger"] == "schedule")
        & ((cadence == "weekly") | cadence.isna())
        & (ticks["model_bundle_id"] == config.model_bundle_id)
        & (started >= start)
        & (started < start + pd.Timedelta(days=7))
    )
    return ticks.loc[mask].assign(_started=started[mask])


def weekly_skip_reason(ticks: pd.DataFrame, config: MonitoringConfig, at: datetime) -> str | None:
    """Why a scheduled run at `at` does nothing, or None if it should tick. Does not modify `ticks`."""
    if ticks.empty:
        week_start(at)  # still refuse a naive `at`
        return None
    attempts = _weekly_attempts(ticks, config, at)
    succeeded = attempts[attempts["status"] == "succeeded"].sort_values(["_started", "monitoring_run_id"], kind="stable")
    if not succeeded.empty:
        first = succeeded.iloc[0]
        url = first.get("github_run_url")
        ref = url if isinstance(url, str) and url else first["monitoring_run_id"]
        return f"this week's weekly tick already succeeded ({ref})"
    if int(attempts["status"].isin(["failed", "refused"]).sum()) >= RETRY_BUDGET:
        return f"retry budget spent: {RETRY_BUDGET} weekly attempts this week did not succeed"
    return None
