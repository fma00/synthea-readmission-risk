"""Cohorts on the simulated clock (v2 slice 7): bounds, coverage, the outcome-independent membership rule."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from readmission_risk.monitoring.cohort import (
    CohortSpec,
    cohort_spec,
    evaluate_cohort,
    ranges,
    read_window,
    required_ticks,
)
from readmission_risk.monitoring.config import parse_monitoring_config
from readmission_risk.monitoring.rule import AlertRule

CONFIG = parse_monitoring_config(json.loads((Path(__file__).resolve().parents[2] / "deploy" / "monitoring.json").read_text()))
RULE = AlertRule(13, 20, (0.80, 1.25))
SPEC = CohortSpec(30, 91)
S = date(2023, 8, 14)  # the first sim date with a full cohort
REQUIRED = required_ticks(S, SPEC, CONFIG)
ALL_FOUND = set(REQUIRED.all)


def _ts(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="UTC")


def _frames(rows):
    """rows: (encounter_id, discharge_time, risk_score, is_readmitted, label_horizon)."""
    utc = "datetime64[us, UTC]"
    predictions = pd.DataFrame(
        {
            "encounter_id": pd.Series([r[0] for r in rows], dtype=object),
            "discharge_time": pd.Series([_ts(r[1]) for r in rows], dtype=utc),
            "risk_score": pd.Series([r[2] for r in rows], dtype="float64"),
        }
    )
    labels = pd.DataFrame(
        {
            "encounter_id": pd.Series([r[0] for r in rows], dtype=object),
            "is_readmitted": pd.Series([r[3] for r in rows], dtype="int8"),
            "label_horizon": pd.Series([_ts(r[4]) for r in rows], dtype=utc),
        }
    )
    return predictions, labels


def test_required_ticks_and_read_window():
    assert [(S - d).days // 7 for d in REQUIRED.matured] == list(range(17, 3, -1))
    assert [(S - d).days // 7 for d in REQUIRED.immature] == [4, 3, 2, 1, 0]
    assert len(REQUIRED.all) == 18 and REQUIRED.all[0] == CONFIG.first_sim_date
    assert read_window(S, SPEC, CONFIG) == (S - timedelta(days=119), S)
    early = required_ticks(CONFIG.first_sim_date, SPEC, CONFIG)
    assert len(early.all) == 18 and min(early.all) < CONFIG.first_sim_date  # missing dates are included


def test_cohort_spec_refuses_other_sizes():
    class Meta:
        readmission_window_days = 30

    assert cohort_spec(RULE, Meta) == SPEC
    Meta.readmission_window_days = 28
    with pytest.raises(ValueError, match="design change"):
        cohort_spec(RULE, Meta)
    with pytest.raises(ValueError, match="design change"):
        cohort_spec(AlertRule(12, 20, (0.8, 1.25)), type("M", (), {"readmission_window_days": 30}))


def test_bounds_are_half_open():
    (m_lo, m_hi), (i_lo, i_hi) = ranges(S, SPEC)
    assert m_lo == _ts("2023-04-16") and m_hi == i_lo == _ts("2023-07-16") and i_hi == _ts("2023-08-15")
    rows = [
        ("a", "2023-04-16T00:00:00", 0.1, 1, "2023-05-16"),  # first matured instant: in
        ("b", "2023-04-15T23:59:59", 0.2, 1, "2023-05-15"),  # before: out
        ("c", "2023-07-15T23:59:59", 0.3, 0, "2023-08-14"),  # last matured instant: in
        ("d", "2023-07-16T00:00:00", 0.4, 0, "2023-08-15"),  # immature
        ("e", "2023-08-14T23:59:59", 0.5, 0, "2023-09-13"),  # last immature instant
        ("f", "2023-08-15T00:00:00", 0.6, 0, "2023-09-14"),  # after the sim date: ignored
    ]
    evaluation = evaluate_cohort(*_frames(rows), sim_date=S, rule=RULE, spec=SPEC, found_ticks=ALL_FOUND, required=REQUIRED)
    assert (evaluation.n_matured, evaluation.observed) == (2, 1)
    assert evaluation.expected == pytest.approx(0.4)
    assert evaluation.n_immature == 2 and evaluation.expected_immature == pytest.approx(0.9)
    assert evaluation.verdict == "insufficient_data"  # 1 positive < floor 20
    assert evaluation.coverage_required == evaluation.coverage_found == 18
    assert (evaluation.cohort_start, evaluation.cohort_end) == (date(2023, 4, 16), date(2023, 7, 16))


def test_a_late_label_positive_stays_in_the_cohort():
    # the bias regression test: membership never depends on the outcome; a positive whose label_horizon is after the
    # simulated date is kept and counted, not excluded
    rows = [("p", "2023-07-10T12:00:00", 0.3, 1, "2023-09-30"), ("n", "2023-07-10T13:00:00", 0.1, 0, "2023-08-09")]
    evaluation = evaluate_cohort(*_frames(rows), sim_date=S, rule=RULE, spec=SPEC, found_ticks=ALL_FOUND, required=REQUIRED)
    assert evaluation.n_matured == 2 and evaluation.observed == 1 and evaluation.n_label_after_sim == 1


def test_full_cohort_alert_and_ok():
    rows = [(f"e{i:03d}", "2023-06-01T10:00:00", 0.1, int(i < 30), "2023-07-01") for i in range(200)]  # O 30, E 20
    evaluation = evaluate_cohort(*_frames(rows), sim_date=S, rule=RULE, spec=SPEC, found_ticks=ALL_FOUND, required=REQUIRED)
    assert evaluation.verdict == "alert" and evaluation.ratio == pytest.approx(1.5) and evaluation.ci_low > 1
    assert 0 <= evaluation.auc <= 1
    rows = [(f"e{i:03d}", "2023-06-01T10:00:00", 0.1, int(i < 21), "2023-07-01") for i in range(200)]  # O 21, E 20
    assert evaluate_cohort(*_frames(rows), sim_date=S, rule=RULE, spec=SPEC, found_ticks=ALL_FOUND, required=REQUIRED).verdict == "ok"


def test_incomplete_coverage_never_alerts_and_the_empty_case_has_null_ratio():
    rows = [(f"e{i:03d}", "2023-06-01T10:00:00", 0.1, int(i < 60), "2023-07-01") for i in range(200)]  # would alert
    missing_immature = ALL_FOUND - {S - timedelta(days=7)}
    evaluation = evaluate_cohort(*_frames(rows), sim_date=S, rule=RULE, spec=SPEC, found_ticks=missing_immature, required=REQUIRED)
    assert evaluation.verdict == "incomplete_cohort" and evaluation.coverage_found == 17
    assert evaluation.ratio == pytest.approx(3.0) and evaluation.n_immature is None and evaluation.expected_immature is None
    first = CONFIG.first_sim_date  # catch-up tick 1: no matured tick can exist
    req = required_ticks(first, SPEC, CONFIG)
    empty = evaluate_cohort(*_frames([]), sim_date=first, rule=RULE, spec=SPEC, found_ticks={first}, required=req)
    assert (empty.verdict, empty.n_matured, empty.observed, empty.expected) == ("incomplete_cohort", 0, 0, 0.0)
    assert empty.ratio is None and empty.ci_low is None and empty.auc is None


def test_full_but_empty_cohort_is_insufficient_data():
    evaluation = evaluate_cohort(*_frames([]), sim_date=S, rule=RULE, spec=SPEC, found_ticks=ALL_FOUND, required=REQUIRED)
    assert evaluation.verdict == "insufficient_data" and evaluation.ratio is None


def test_bad_inputs_are_refused_and_inputs_not_mutated():
    rows = [("a", "2023-06-01T10:00:00", 0.1, 0, "2023-07-01"), ("a", "2023-06-02T10:00:00", 0.2, 0, "2023-07-02")]
    with pytest.raises(ValueError, match="more than one row"):
        evaluate_cohort(*_frames(rows), sim_date=S, rule=RULE, spec=SPEC, found_ticks=ALL_FOUND, required=REQUIRED)
    predictions, labels = _frames([("a", "2023-06-01T10:00:00", 0.1, 0, "2023-07-01")])
    with pytest.raises(ValueError, match="no gold label"):
        evaluate_cohort(predictions, labels.iloc[0:0], sim_date=S, rule=RULE, spec=SPEC, found_ticks=ALL_FOUND, required=REQUIRED)
    before = (predictions.copy(), labels.copy())
    first = evaluate_cohort(predictions, labels, sim_date=S, rule=RULE, spec=SPEC, found_ticks=ALL_FOUND, required=REQUIRED)
    second = evaluate_cohort(predictions, labels, sim_date=S, rule=RULE, spec=SPEC, found_ticks=ALL_FOUND, required=REQUIRED)
    assert first == second
    pd.testing.assert_frame_equal(predictions, before[0])
    pd.testing.assert_frame_equal(labels, before[1])


def test_row_order_does_not_change_the_sums():
    rows = [(f"e{i:03d}", "2023-06-01T10:00:00", 0.1 + i * 1e-7, int(i % 7 == 0), "2023-07-01") for i in range(300)]
    predictions, labels = _frames(rows)
    a = evaluate_cohort(predictions, labels, sim_date=S, rule=RULE, spec=SPEC, found_ticks=ALL_FOUND, required=REQUIRED)
    shuffled = predictions.sample(frac=1, random_state=0)
    b = evaluate_cohort(shuffled, labels, sim_date=S, rule=RULE, spec=SPEC, found_ticks=ALL_FOUND, required=REQUIRED)
    assert a == b
