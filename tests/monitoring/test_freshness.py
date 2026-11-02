"""The shared schedule facts and THE "stale after" rule (v3 slice 3, Verification V-F): moved out of config.py/ledger.py so the
dashboard can import them; the ledger's output is byte-identical to the pre-refactor code at b46a940."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone

import pandas as pd
import pytest

from readmission_risk.monitoring import config, freshness, ledger
from readmission_risk.monitoring.freshness import (
    WEEKLY_CRON,
    newest_scheduled_success,
    stale_after,
)
from tests.monitoring.test_ledger import RUNS, _tick
from tests.monitoring.world import CONFIG


def _row(i, started, *, trigger="schedule", cadence="weekly", status="succeeded"):
    return {"monitoring_run_id": f"m{i}", "started_at": started, "trigger": trigger, "cadence": cadence, "status": status}


MONDAY = datetime(2026, 10, 26, 4, 44, tzinfo=UTC)


def test_stale_after_is_the_newest_scheduled_weekly_success_plus_two_periods():
    rows = [_row(1, MONDAY - timedelta(days=7)), _row(2, MONDAY)]
    assert stale_after(rows, cron=WEEKLY_CRON) == MONDAY + timedelta(days=14)


@pytest.mark.parametrize(
    "ignored",
    [
        {"trigger": "schedule", "cadence": "burn_in"}, {"trigger": "workflow_dispatch", "cadence": "burn_in"},
        {"trigger": "workflow_dispatch", "cadence": "dispatch"}, {"trigger": "laptop", "cadence": "catch_up"},
        {"status": "failed"},
    ],
)  # fmt: skip
def test_only_succeeded_scheduled_weekly_ticks_refresh_the_anchor(ignored):
    rows = [_row(1, MONDAY), _row(2, MONDAY + timedelta(days=3), **ignored)]
    assert newest_scheduled_success(rows)["monitoring_run_id"] == "m1"


def test_a_started_at_tie_is_broken_by_monitoring_run_id():
    assert newest_scheduled_success([_row(2, MONDAY), _row(1, MONDAY)])["monitoring_run_id"] == "m2"


@pytest.mark.parametrize("started", [datetime(2026, 10, 26, 4, 44), "2026-10-26T04:44:00Z"])
def test_a_naive_or_string_started_at_is_refused(started):
    # Design E10: the pre-refactor badge raised TypeError for a naive value and parsed strings; real inputs are tz-aware.
    with pytest.raises(ValueError, match="tz-aware"):
        newest_scheduled_success([_row(1, started)])


def test_no_qualifying_row_and_a_non_utc_anchor():
    assert newest_scheduled_success([]) is None and stale_after([], cron=WEEKLY_CRON) is None
    cet = datetime(2026, 10, 26, 5, 44, tzinfo=timezone(timedelta(hours=1)))
    stale = stale_after([_row(1, cet)], cron=WEEKLY_CRON)
    assert stale == MONDAY + timedelta(days=14) and stale.tzinfo == UTC


def test_the_moved_names_are_the_same_objects():
    assert config.WEEKLY_CRON is freshness.WEEKLY_CRON and config.period_of is freshness.period_of
    assert ledger.cadence_label is freshness.cadence_label


def test_freshness_imports_nothing_heavy():
    code = (
        "import sys, readmission_risk.monitoring.freshness\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('pandas', 'numpy', 'google')"
        " or m.startswith('readmission_risk.bundles'))\n"
        "assert not bad, bad\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


# Captured by running the PRE-refactor ledger at b46a940 on exactly these frames (design note, Implementation section).
FRAMES = {
    "empty": lambda: pd.DataFrame(),
    "burn_in_only": lambda: pd.DataFrame([_tick(1), _tick(2, started="2026-10-20T14:07:30Z")]),
    "weekly_success": lambda: pd.DataFrame([_tick(1, cadence="weekly", started="2026-10-26T04:44:00Z")]),
    "catch_up_only": lambda: pd.DataFrame([_tick(1, cadence="catch_up", trigger="laptop", github_run_url=None)]),
    "failed_newer": lambda: pd.DataFrame([
        _tick(1, cadence="weekly", started="2026-10-26T04:44:00Z"),
        _tick(2, cadence="weekly", status="failed", started="2026-10-27T05:17:00Z"),
    ]),
    "tie": lambda: pd.DataFrame([
        _tick(1, cadence="weekly", started="2026-10-26T04:44:00Z"), _tick(2, cadence="weekly", started="2026-10-26T04:44:00Z"),
    ]),
}  # fmt: skip
CAPTURED = {
    "empty": ("none yet", "lightgrey", None),
    "burn_in_only": ("2026-10-20 14:07 UTC · sim 2023-08-14 · schedule not started", "brightgreen",
                     "f38a6be93557a86ba74ddd9f55d283c69844a7442556a3b5b3cc096e8d4c8422"),
    "weekly_success": ("2026-10-26 04:44 UTC · sim 2023-08-14 · stale after 2026-11-09 04:44 UTC", "brightgreen",
                       "5cd45b11f4cadd173304c5c48b443623777d9e6d0ca4d612803b4659cf2b1f03"),
    "catch_up_only": ("2026-10-20 13:07 UTC · sim 2023-08-14 · schedule not started", "brightgreen",
                      "154f2376325e1fc55b2012cd064ef94ea80cf57fdbc629b45387b068118f5257"),
    "failed_newer": ("2026-10-27 05:17 UTC · sim 2023-08-14 · stale after 2026-11-09 04:44 UTC", "red",
                     "d90075c96d0d874f52049c60709d4f06f47e79aaf218f75152605b96e58f79a6"),
    "tie": ("2026-10-26 04:44 UTC · sim 2023-08-14 · stale after 2026-11-09 04:44 UTC", "brightgreen",
            "f024dcb08ddf00ca164970edc9e38bdfd1931d8938f0117468c06a24277bc730"),
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(FRAMES))
def test_the_ledger_and_badge_are_byte_identical_to_the_pre_refactor_code(name):
    frame = FRAMES[name]()
    message, color, ledger_sha = CAPTURED[name]
    badge = ledger.last_tick_badge(frame, cron=WEEKLY_CRON)
    assert badge == {"schemaVersion": 1, "label": "last tick (simulated clock)", "message": message, "color": color}
    if ledger_sha is not None:
        text = ledger.render_ledger(frame, RUNS, config=CONFIG)
        assert hashlib.sha256(text.encode()).hexdigest() == ledger_sha
