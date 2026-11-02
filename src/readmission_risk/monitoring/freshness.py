"""The schedule facts shared by the run ledger and the dashboard heartbeat (PRD v3 FR11): the weekly cron, its period, the
cadence labels and THE "stale after" rule. Standard library only and no package import, so the dashboard can use it (its
import boundary bans readmission_risk.bundles, which monitoring/config.py imports). See
notes/eg-new-feature/v3-slice3-dashboard-app-2026-11-01.md (Interfaces section 1, E3).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta

WEEKLY_CRON = "17 * * * 1-3"  # weekly tick with hourly retry slots Monday-Wednesday; the clock skips once the week's tick succeeded or 3 attempts failed (weekly.py)
_PERIODS = {WEEKLY_CRON: timedelta(days=7)}
CADENCE_LABELS = {"weekly": "weekly", "dispatch": "dispatch", "catch_up": "catch-up (laptop)"}


def period_of(cron: str) -> timedelta:
    if cron not in _PERIODS:
        raise ValueError(f"unknown monitoring cron {cron!r}")
    return _PERIODS[cron]


def cadence_label(cadence, trigger) -> str:
    """burn_in is labelled by how it was triggered: GitHub's scheduler (2026-10-19/20) or a dispatch (the laptop timer)."""
    if cadence == "burn_in":
        return "burn-in (scheduled, accelerated)" if trigger == "schedule" else "burn-in (dispatched, accelerated)"
    return CADENCE_LABELS.get(cadence, "-")


def newest_scheduled_success(rows: Iterable[Mapping]) -> Mapping | None:
    """The row with the max (started_at, monitoring_run_id) among the succeeded weekly ticks fired by the scheduler. Burn-in
    rows (even scheduled ones), dispatches, catch-ups and failed attempts never qualify; None without such a row. Every
    qualifying started_at must be a tz-aware datetime (pd.Timestamp qualifies): ValueError otherwise."""
    qualifying = [
        row for row in rows
        if row.get("trigger") == "schedule" and row.get("cadence") == "weekly" and row.get("status") == "succeeded"
    ]  # fmt: skip
    for row in qualifying:
        started = row.get("started_at")
        if not isinstance(started, datetime) or started.tzinfo is None:
            raise ValueError(f"started_at must be a tz-aware datetime; got {started!r}")
    if not qualifying:
        return None
    return max(qualifying, key=lambda row: (row["started_at"], row.get("monitoring_run_id") or ""))


def stale_after(rows: Iterable[Mapping], *, cron: str) -> datetime | None:
    """The newest scheduled success's started_at (UTC) + 2 periods of `cron`; None without one."""
    anchor = newest_scheduled_success(rows)
    if anchor is None:
        return None
    return anchor["started_at"].astimezone(UTC) + 2 * period_of(cron)
