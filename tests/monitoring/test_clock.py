"""The simulated clock (v2 slice 7)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from readmission_risk.monitoring.clock import ClockError, next_sim_date
from readmission_risk.monitoring.config import parse_monitoring_config, sim_grid

CONFIG = parse_monitoring_config(json.loads((Path(__file__).resolve().parents[2] / "deploy" / "monitoring.json").read_text()))
FIRST, LAST = CONFIG.first_sim_date, CONFIG.last_sim_date
WEEK = timedelta(days=7)


def test_empty_state_starts_at_first():
    assert next_sim_date([], CONFIG) == FIRST


def test_head_follows_the_latest_succeeded_date():
    assert next_sim_date([FIRST, FIRST + WEEK], CONFIG) == FIRST + 2 * WEEK


def test_a_failed_tick_does_not_advance():
    # the caller passes succeeded dates only; a failure leaves the set unchanged, so the same date is retried
    succeeded = [FIRST]
    assert next_sim_date(succeeded, CONFIG) == next_sim_date(succeeded, CONFIG) == FIRST + WEEK


def test_off_grid_state_is_refused():
    with pytest.raises(ClockError, match="off-grid"):
        next_sim_date([FIRST + timedelta(days=1)], CONFIG)


def test_exhausted_clock_refuses_the_head_but_accepts_a_replay():
    grid = sim_grid(CONFIG)
    with pytest.raises(ClockError, match="exhausted"):
        next_sim_date(grid, CONFIG)
    assert next_sim_date(grid, CONFIG, requested=LAST) == LAST
    assert next_sim_date(grid, CONFIG, requested=FIRST) == FIRST


def test_requests():
    assert next_sim_date([FIRST], CONFIG, requested=FIRST) == FIRST  # replay of a succeeded date
    assert next_sim_date([FIRST], CONFIG, requested=FIRST + WEEK) == FIRST + WEEK  # the head itself
    with pytest.raises(ClockError, match="no skipping ahead"):
        next_sim_date([FIRST], CONFIG, requested=FIRST + 2 * WEEK)
    for bad in (FIRST + timedelta(days=1), FIRST - WEEK, LAST + WEEK):
        with pytest.raises(ClockError, match="not on the simulated grid"):
            next_sim_date([FIRST], CONFIG, requested=bad)


def test_clock_error_is_a_value_error():
    assert issubclass(ClockError, ValueError)
    assert date(2023, 4, 17) == FIRST
