"""The dashboard's pure formatting (v3 slice 3, Verification V-R)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import MappingProxyType

import pandas as pd
import pytest

from readmission_risk.dashboard import present as p
from readmission_risk.dashboard.queries import PublishedDate
from readmission_risk.warehouse.bigquery import SERVING_GOLD_FINGERPRINT
from readmission_risk.warehouse.dashboard_views import FORBIDDEN_OUTPUT_COLUMNS
from tests.dashboard.fake_world import SENTINELS, monitoring_rows, sheet_rows

DAY = date(2023, 10, 30)


@pytest.mark.parametrize(
    ("n", "rate", "expected"),
    [
        (None, None, "no history"), (None, 0.2, "no history"), (9, None, "withheld (n = 9 < 30)"),
        (9, 0.25, "withheld (n = 9 < 30)"), (40, None, "rate unavailable (n = 40)"), (1234, 0.0812, "8.1% of 1,234"),
    ],
)  # fmt: skip
def test_rate_text(n, rate, expected):
    assert p.rate_text(n, rate) == expected


def test_a_withheld_or_missing_rate_is_never_a_number():
    for text in (p.rate_text(9, 0.25), p.rate_text(40, None), p.rate_text(None, 0.3)):
        assert "%" not in text


@pytest.mark.parametrize(
    ("code", "description", "expected"),
    [("1", "Asthma", "Asthma (1)"), ("1", None, "(no description) (1)"), (None, "Asthma", "Asthma (no code)"),
     (None, None, "(no reason recorded)")],
)  # fmt: skip
def test_reason_text(code, description, expected):
    assert p.reason_text(code, description) == expected


def test_call_sheet_frame_columns_dtypes_and_values():
    rows = p.freeze_rows(sheet_rows(DAY, "r5"))
    frame = p.call_sheet_frame(list(reversed(rows)))
    assert tuple(frame.columns) == p.CALL_SHEET_DISPLAY
    assert set(frame.columns).isdisjoint(FORBIDDEN_OUTPUT_COLUMNS)
    assert list(frame["Rank"]) == [1, 2, 3, 4, 5] and str(frame["Rank"].dtype) == "int64"
    assert str(frame["Discharged (UTC)"].dtype) == "datetime64[ns, UTC]"
    assert all(str(frame[c].dtype) == "Int64" for c in ("Prior inpatient stays", "Prior emergency visits", "Active conditions"))
    assert frame["Prior emergency visits"].isna().tolist() == [False, False, False, True, False]
    assert list(frame["Top-N"]) == ["✅ call this week", "✅ call this week", "-", "-", "-"]
    assert frame.loc[1, "Reason's historical readmission rate"] == "withheld (n = 9 < 30)"
    assert frame.loc[2, "Reason's historical readmission rate"] == "no history"
    assert frame.loc[0, "Risk score (%)"] == pytest.approx(19.0)


def test_call_sheet_frame_never_shows_a_label_or_demographic_sentinel():
    frame = p.call_sheet_frame(sheet_rows(DAY, "r5"))
    cells = {str(v) for v in frame.to_numpy().ravel()}
    for sentinel in SENTINELS.values():
        assert not any(str(sentinel) in cell for cell in cells), sentinel


def test_call_sheet_frame_of_no_rows_keeps_its_dtypes():
    frame = p.call_sheet_frame([])
    assert frame.empty and tuple(frame.columns) == p.CALL_SHEET_DISPLAY
    assert str(frame["Rank"].dtype) == "int64" and str(frame["Active conditions"].dtype) == "Int64"


def test_inputs_are_not_mutated_and_frozen_rows_are_read_only():
    rows = sheet_rows(DAY, "r5")
    before = [dict(r) for r in rows]
    p.call_sheet_frame(rows)
    p.split_top_n(rows)
    assert rows == before
    frozen = p.freeze_rows(rows)
    with pytest.raises(TypeError):
        frozen[0]["rank"] = 9  # type: ignore[index]
    assert isinstance(frozen[0], MappingProxyType)


def test_split_top_n():
    top, rest = p.split_top_n(sheet_rows(DAY, "r5"))
    assert [r["rank"] for r in top] == [1, 2] and [r["rank"] for r in rest] == [3, 4, 5]
    assert p.split_top_n(sheet_rows(DAY, "r5", top=0))[0] == []
    assert p.split_top_n(sheet_rows(DAY, "r5", top=5))[1] == []


def test_sheet_warnings():
    published = PublishedDate(DAY, "r5", "b" * 64, 7, 2, 5, DAY)
    rows = sheet_rows(DAY, "r5")
    assert p.sheet_warnings(rows, published) == []
    odd = [{**r, "scoring_run_id": "rX"} if r["rank"] == 1 else r for r in rows[:4]]
    assert p.sheet_warnings(odd, published) == [
        "4 rows are shown, but the run recorded 5.",
        "1 row comes from another scoring run than the listed one.",
    ]
    flagged = [{**r, "is_top_n": r["rank"] == 1} for r in rows]
    assert p.sheet_warnings(flagged, published) == ["1 row is flagged Top-N; the run's Top-N is 2."]


def test_monitoring_points_and_table():
    points = p.monitoring_points(monitoring_rows(), bundle="b" * 64)
    assert tuple(points.columns) == p.POINT_COLUMNS
    assert list(points["sim_week"]) == ["2023-10-02", "2023-10-09", "2023-10-16", "2023-10-23", "2023-10-30"]  # m0: other bundle
    assert str(points["sim_date"].dtype) == "datetime64[ns, UTC]"
    assert list(points["plotted"]) == [True, True, False, True, True]
    table = p.monitoring_table(points)
    assert list(table.columns) == ["Simulated week", "Verdict", "O/E [95% CI]", "Observed / expected", "Positives floor", "Cadence"]
    assert table.iloc[0].tolist() == ["2023-10-30", "✅ ok", "1.020 [0.800, 1.300]", "21 / 20.6", "20", "weekly"]
    assert table.iloc[2, 2] == "-" and table.iloc[2, 3] == "-"
    assert p.monitoring_points(monitoring_rows(), bundle=None).empty


def test_monitoring_points_keep_the_newest_tick_per_date_and_a_null_bound_formats_in_place():
    rows = monitoring_rows()
    rerun = {**rows[-1], "monitoring_run_id": "m9", "started_at": datetime(2026, 10, 26, 6, tzinfo=UTC), "oe_ci_low": None,
             "expected": None}  # fmt: skip
    points = p.monitoring_points([*rows, rerun], bundle="b" * 64)
    assert len(points) == 5
    newest = p.monitoring_table(points).iloc[0].tolist()
    assert newest[2] == "1.020 [-, 1.300]" and newest[3] == "21 / -"


def test_visible_runs_s12():
    clock = date(2023, 10, 30)
    rows = [
        {"run_date": date(2023, 10, 30), "gold_fingerprint": SERVING_GOLD_FINGERPRINT, "published": True},
        {"run_date": date(2025, 6, 30), "gold_fingerprint": SERVING_GOLD_FINGERPRINT, "published": True},
        {"run_date": date(2023, 10, 2), "gold_fingerprint": "d" * 64, "published": True},
        {"run_date": None, "gold_fingerprint": None, "published": False},
    ]
    assert p.visible_scoring_runs(rows, clock=clock) == ([rows[0], rows[3]], 2)
    assert p.visible_scoring_runs(rows, clock=None) == ([rows[3]], 3)
    ticks = [{"sim_date": clock, "model_bundle_id": "b", "status": "succeeded"},
             {"sim_date": date(2024, 1, 1), "model_bundle_id": "b", "status": "succeeded"},
             {"sim_date": clock, "model_bundle_id": "c", "status": "succeeded"},
             {"sim_date": None, "model_bundle_id": None, "status": "failed"}]  # fmt: skip
    assert p.visible_monitoring_runs(ticks, clock=clock, bundle="b") == ([ticks[0], ticks[3]], 2)
    assert p.visible_monitoring_runs(ticks, clock=None, bundle=None) == ([ticks[3]], 3)


def test_failed_weekly_attempts_at_the_next_week_stay_visible():
    # The clock moves only on success (monitoring/tick.py), so a failed attempt carries sim_date = clock + 7 days and its
    # unpublished scoring run run_date = clock + 7: both publish nothing and must show (S12 as refined in pre-commit review).
    clock, nxt = date(2023, 10, 30), date(2023, 11, 6)
    tick = {"sim_date": nxt, "model_bundle_id": "b", "status": "failed"}
    run = {"run_date": nxt, "gold_fingerprint": SERVING_GOLD_FINGERPRINT, "published": False, "status": "data_tests_failed"}
    assert p.visible_monitoring_runs([tick], clock=clock, bundle="b") == ([tick], 0)
    assert p.visible_scoring_runs([run], clock=clock) == ([run], 0)
    assert p.visible_scoring_runs([{**run, "published": True}], clock=clock) == ([], 1)  # a published future run stays hidden
    # the defensive no-clock rule as refined: dated failed ticks and unpublished runs are kept even without a clock
    assert p.visible_monitoring_runs([tick], clock=None, bundle="b") == ([tick], 0)
    assert p.visible_scoring_runs([run], clock=None) == ([run], 0)


def test_runs_tables():
    scoring = p.scoring_runs_table([
        {"scoring_run_id": "a", "started_at": datetime(2026, 10, 20, 9, tzinfo=UTC), "run_date": date(2023, 10, 2), "status": "refused",
         "published": False, "history_tests_passed": None, "github_run_url": "javascript:alert(1)"},
        {"scoring_run_id": "b", "started_at": datetime(2026, 10, 21, 9, tzinfo=UTC), "run_date": None, "status": "succeeded",
         "published": True, "history_tests_passed": True, "predictions_tests_passed": False,
         "github_run_url": "https://github.com/fma00/synthea-readmission-risk/actions/runs/123"},
    ])  # fmt: skip
    assert list(scoring.columns) == ["Ran at (wall clock, UTC)", "Run date", "Status", "Trigger", "History tests",
                                     "Prediction tests", "Published", "Data-test failure", "Error type", "Run"]  # fmt: skip
    assert scoring.iloc[0].tolist() == ["2026-10-21 09:00", "-", "✅ succeeded", "-", "passed", "FAILED", "yes", "-", "-",
                                        "https://github.com/fma00/synthea-readmission-risk/actions/runs/123"]  # fmt: skip
    assert scoring.iloc[1].tolist()[2] == "⛔ refused" and scoring.iloc[1].tolist()[-1] is None
    ticks = p.monitoring_runs_table(monitoring_rows() * 10)
    assert len(ticks) == p.TICKS_SHOWN and ticks.iloc[0, 4] == "weekly"


@pytest.mark.parametrize(
    ("url", "ok"),
    [("https://github.com/fma00/synthea-readmission-risk/actions/runs/123", True),
     ("https://github.com/fma00/synthea-readmission-risk/actions/runs/123/attempts/2", True),
     ("javascript:alert(1)", False), ("https://github.com/other/repo/actions/runs/1", False),
     ("https://github.com/fma00/synthea-readmission-risk/actions/runs/1/x", False), (None, False), (123, False)],
)  # fmt: skip
def test_run_link(url, ok):
    assert (p.run_link(url) == url) if ok else p.run_link(url) is None


def test_short_texts():
    assert p.short_id("a" * 64) == "aaaaaaaaaaaa…"
    for bad in ("a" * 63, "A" * 64, "`" + "a" * 63, 7, None):
        assert p.short_id(bad) == "unknown"
    assert [p.status_word(s) for s in ("succeeded", "weird", None)] == ["succeeded", "other", "-"]
    assert p.status_text("weird") == "❔ weird" and p.verdict_text(None) == "-" and p.verdict_text("alert") == "🚨 alert"
    assert p.count_text(1, "row is", "rows are") == "1 row is" and p.count_text(0, "row is", "rows are") == "0 rows are"
    assert pd.isna(p._float(None))
