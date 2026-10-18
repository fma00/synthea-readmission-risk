"""preflight and simulate over the session's small trained models and gold (v2 slice 7). No GCP."""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from pathlib import Path

import pytest

from readmission_risk.monitoring.config import sim_grid
from readmission_risk.monitoring.preflight import preflight, simulate
from readmission_risk.monitoring.rule import AlertRule
from tests.monitoring.world import CONFIG

RULE = AlertRule(13, 20, (0.80, 1.25))


def _scan(scoring_env):
    """A config over the synthetic gold's 2023+ Mondays, and the preflight lines of that scan."""
    config = dataclasses.replace(CONFIG, first_sim_date=CONFIG.first_sim_date, last_sim_date=CONFIG.first_sim_date + timedelta(days=7 * 160))
    return config, preflight(scoring_env.gold_dir, Path(scoring_env.lr_bundle_uri), config)


def test_preflight_reports_every_grid_date(scoring_env):
    config, lines = _scan(scoring_env)
    assert [line.sim_date for line in lines] == sim_grid(config)
    assert all((line.n_rows is None) == (line.refusal is not None) for line in lines)
    assert any(line.n_rows for line in lines)


def test_simulate_scores_through_the_scorer_and_evaluates_each_date(scoring_env):
    config, lines = _scan(scoring_env)
    run, best = [], []
    for line in lines:  # the longest stretch of consecutive accepted weeks
        run = [*run, line] if line.n_rows else []
        best = run if len(run) > len(best) else best
    if len(best) < 2:
        pytest.skip("the synthetic gold has no two consecutive accepted weeks")
    window = dataclasses.replace(config, first_sim_date=best[0].sim_date, last_sim_date=best[-1].sim_date)
    frame = simulate(scoring_env.gold_dir, Path(scoring_env.lr_bundle_uri), window, RULE)
    assert list(frame["sim_date"]) == sim_grid(window)
    assert set(frame["verdict"]) <= {"incomplete_cohort", "insufficient_data", "ok", "alert"}
    assert (frame["coverage_required"] == 18).all()
