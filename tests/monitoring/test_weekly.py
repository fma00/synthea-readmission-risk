"""The weekly tick's skip rule (monitoring/weekly.py). Pure; no BigQuery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pandas as pd
import pytest

from readmission_risk.monitoring.weekly import (
    RETRY_BUDGET,
    week_start,
    weekly_skip_reason,
)
from tests.monitoring.world import CONFIG

TUESDAY = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)  # week of Monday 2026-10-26


def _row(i, *, status="succeeded", trigger="schedule", cadence="weekly", started="2026-10-26T00:20:00Z", bundle=None, url="auto"):
    return {"monitoring_run_id": f"m{i}", "status": status, "trigger": trigger, "cadence": cadence,
            "model_bundle_id": bundle or CONFIG.model_bundle_id, "started_at": pd.Timestamp(started),
            "github_run_url": f"https://github.com/o/r/actions/runs/{i}" if url == "auto" else url}  # fmt: skip


def _reason(*rows, at=TUESDAY):
    return weekly_skip_reason(pd.DataFrame(list(rows)), CONFIG, at)


def test_week_start():
    assert week_start(datetime(2026, 10, 26, 0, 0, tzinfo=UTC)) == datetime(2026, 10, 26, tzinfo=UTC)
    assert week_start(datetime(2026, 11, 1, 23, 59, tzinfo=UTC)) == datetime(2026, 10, 26, tzinfo=UTC)
    # 01:00 on Monday at +02:00 is Sunday 23:00 UTC: the previous week
    assert week_start(datetime(2026, 10, 26, 1, 0, tzinfo=timezone(timedelta(hours=2)))) == datetime(2026, 10, 19, tzinfo=UTC)
    with pytest.raises(ValueError, match="timezone-aware"):
        week_start(datetime(2026, 10, 26))


def test_success_skips_with_the_earliest_success_and_its_url():
    assert _reason(_row(1)) == "this week's weekly tick already succeeded (https://github.com/o/r/actions/runs/1)"
    rows = [_row(2, started="2026-10-26T05:00Z"), _row("b", started="2026-10-26T01:00Z"), _row("a", started="2026-10-26T01:00Z", url=None)]
    assert _reason(*rows) == "this week's weekly tick already succeeded (ma)"  # tie on started_at -> monitoring_run_id; no URL -> id


def test_budget_and_precedence():
    failed = [_row(i, status=s) for i, s in enumerate(["failed", "refused", "failed"])]
    assert _reason(*failed[:2]) is None
    assert _reason(*failed) == f"retry budget spent: {RETRY_BUDGET} weekly attempts this week did not succeed"
    assert _reason(*failed, _row(9)).startswith("this week's weekly tick already succeeded")


def test_rows_that_never_count():
    never = [
        _row(1, started="2026-10-25T23:59Z"),  # the previous week
        _row(2, started="2026-11-02T00:00Z"),  # the next week (upper bound)
        _row(3, bundle="ab" * 32),  # another bundle
        _row(4, trigger="workflow_dispatch", cadence="dispatch"),
        _row(5, trigger="laptop", cadence="catch_up"),
        _row(6, cadence="burn_in"),  # the historical scheduled burn-in
        _row(7, trigger="workflow_dispatch", cadence=None, status="refused"),
        _row(8, trigger="laptop", cadence="weekly"),
        _row(9, trigger=None, cadence=None, status="refused"),
    ]
    assert _reason(*never) is None
    assert _reason(*[_row(i, trigger="workflow_dispatch", cadence=None, status="refused") for i in range(5)]) is None


def test_null_and_nan_cadence_scheduled_refusals_count_and_input_is_unchanged():
    rows = pd.DataFrame([_row(1, cadence=None, status="refused"), _row(2, cadence=float("nan"), status="refused"),
                         _row(3, status="failed")])  # fmt: skip
    before = rows.copy()
    assert weekly_skip_reason(rows, CONFIG, TUESDAY).startswith("retry budget spent")
    pd.testing.assert_frame_equal(rows, before)


def test_empty_frame():
    assert weekly_skip_reason(pd.DataFrame(), CONFIG, TUESDAY) is None
    with pytest.raises(ValueError, match="timezone-aware"):
        weekly_skip_reason(pd.DataFrame(), CONFIG, datetime(2026, 10, 27))
