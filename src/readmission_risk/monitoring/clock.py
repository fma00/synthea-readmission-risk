"""The simulated clock: the next sim date is the grid date after the latest succeeded one. The clock moves only on success,
so a failed or dropped tick is retried on the same date, and re-processing a date replaces its partition and appends an
identical evaluation. See notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md (Interfaces > clock.py).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

from .config import MonitoringConfig, on_grid


class ClockError(ValueError):
    pass


def next_sim_date(succeeded: Iterable[date], config: MonitoringConfig, *, requested: date | None = None) -> date:
    done = set(succeeded)
    off = sorted(d for d in done if not on_grid(config, d))
    if off:
        raise ClockError(f"succeeded monitoring rows hold off-grid sim dates {[d.isoformat() for d in off]}")
    exhausted = bool(done) and max(done) == config.last_sim_date
    head = config.first_sim_date if not done else max(done) + timedelta(days=config.step_days)
    if requested is None:
        if exhausted:
            raise ClockError(f"simulated clock exhausted at {config.last_sim_date.isoformat()}")
        return head
    if not on_grid(config, requested):
        raise ClockError(
            f"sim date {requested.isoformat()} is not on the simulated grid ({config.first_sim_date.isoformat()} + "
            f"multiples of {config.step_days} days, up to {config.last_sim_date.isoformat()})"
        )
    if not exhausted and requested > head:
        raise ClockError(f"sim date {requested.isoformat()} is after the clock head {head.isoformat()} (no skipping ahead)")
    return requested
