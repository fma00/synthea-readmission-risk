"""The run ledger and its badge (v2 slice 7)."""

from __future__ import annotations

import pandas as pd
import pytest

from readmission_risk.monitoring.ledger import (
    HEADER,
    cron_state,
    last_tick_badge,
    render_ledger,
)
from tests.monitoring.world import CONFIG


def _tick(i, *, status="succeeded", trigger="schedule", cadence="burn_in", verdict="ok", started="2026-10-20T13:07:30Z", **extra):
    row = {
        "monitoring_run_id": f"m{i}", "status": status, "failed_stage": None if status == "succeeded" else "score",
        "started_at": pd.Timestamp(started), "sim_date": pd.Timestamp("2023-08-14").date(), "cadence": cadence,
        "trigger": trigger, "verdict": verdict if status == "succeeded" else None, "oe_ratio": 1.02, "oe_ci_low": 0.75,
        "oe_ci_high": 1.36, "observed": 41, "expected": 40.2, "n_matured": 2100, "auc": 0.88, "expected_immature": 12.3,
        "github_run_url": f"https://github.com/o/r/actions/runs/{i}", "scoring_run_id": f"r{i}",
    }  # fmt: skip
    return {**row, **extra}


RUNS = pd.DataFrame([
    {"scoring_run_id": "r1", "started_at": pd.Timestamp("2026-10-20T13:05Z"), "run_date": "2023-08-14", "status": "succeeded",
     "trigger": "schedule", "github_run_url": None},
    {"scoring_run_id": "x", "started_at": pd.Timestamp("2026-10-19T10:00Z"), "run_date": "2025-06-30", "status": "succeeded",
     "trigger": "workflow_dispatch", "github_run_url": "https://github.com/o/r/actions/runs/9"},
])  # fmt: skip


def test_header_ordering_and_determinism():
    ticks = pd.DataFrame([_tick(1), _tick(2, started="2026-10-20T13:37:30Z", status="failed", cadence="burn_in")])
    text = render_ledger(ticks, RUNS, config=CONFIG)
    assert HEADER in text and "not weeks of unattended operation" in text and "Simulated clock" in text
    table = [line for line in text.splitlines() if line.startswith("| 2026-")]
    assert table[0].startswith("| 2026-10-20 13:37 UTC | burn-in (accelerated cadence)") and "failed (score)" in table[0]
    assert "1.020 [0.750, 1.360]" in table[1] and "41 / 40.2" in table[1]
    assert render_ledger(ticks.iloc[::-1], RUNS, config=CONFIG) == text  # input order does not matter
    others = text.split("## Other scoring runs")[1]
    assert "2025-06-30" in others and "| 2023-08-14" not in others  # r1 is referenced by a tick


def test_labels_and_missing_values():
    ticks = pd.DataFrame([_tick(1, cadence="catch_up", trigger="laptop", github_run_url=None, oe_ratio=None, auc=float("nan"))])
    text = render_ledger(ticks, RUNS.iloc[0:0], config=CONFIG)
    row = next(line for line in text.splitlines() if line.startswith("| 2026-"))
    assert "catch-up (laptop)" in row and row.endswith("| laptop |") and "| - |" in row


def test_badge_cases():
    assert last_tick_badge(pd.DataFrame(), cron=None)["color"] == "lightgrey"
    ticks = pd.DataFrame([_tick(1)])
    badge = last_tick_badge(ticks, cron="7,37 13-18 * * *")
    assert badge["schemaVersion"] == 1 and badge["color"] == "brightgreen"
    assert badge["message"] == "2026-10-20 13:07 UTC · sim 2023-08-14 · stale after 2026-10-20 14:07 UTC"
    weekly = last_tick_badge(ticks, cron="17 6 * * 1")["message"]
    assert weekly.endswith("stale after 2026-11-03 13:07 UTC")
    hourly = last_tick_badge(ticks, cron="7 13-22 * * *")["message"]
    assert hourly.endswith("stale after 2026-10-20 15:07 UTC")
    laptop = pd.DataFrame([_tick(1, trigger="laptop", cadence="catch_up")])
    assert last_tick_badge(laptop, cron="17 6 * * 1")["message"].endswith("schedule not started")
    assert last_tick_badge(laptop, cron=None)["message"].endswith("dispatch only")
    clock_failure = pd.DataFrame([_tick(1), _tick(2, status="refused", started="2026-10-20T14:00Z", sim_date=None)])
    assert last_tick_badge(clock_failure, cron="17 6 * * 1")["color"] == "red"


def test_cron_state():
    assert cron_state("on:\n  workflow_dispatch:\n", CONFIG) is None
    assert cron_state('  schedule:\n    - cron: "7,37 13-18 * * *"\n', CONFIG) == "7,37 13-18 * * *"
    assert cron_state('  schedule:\n    - cron: "17 6 * * 1"\n', CONFIG) == "17 6 * * 1"
    with pytest.raises(ValueError, match="not one of"):
        cron_state('    - cron: "0 0 * * *"\n', CONFIG)
    with pytest.raises(ValueError, match="2 cron lines"):
        cron_state('    - cron: "17 6 * * 1"\n    - cron: "7,37 13-18 * * *"\n', CONFIG)
