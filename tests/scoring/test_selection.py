from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from readmission_risk.scoring.selection import (
    observation_horizon,
    select_held_out_window,
    window_bounds,
)
from tests.scoring.helpers import assert_count_phrase, ts


def _gold(rows):
    """rows: (encounter_id, index_start, index_stop) triples."""
    return pd.DataFrame(
        {
            "encounter_id": [r[0] for r in rows],
            "index_start": ts(*[r[1] for r in rows]),
            "index_stop": ts(*[r[2] for r in rows]),
        }
    )


def test_window_bounds_and_row_boundaries():
    start, end = window_bounds(date(2026, 3, 10), 3)
    assert (start, end) == (pd.Timestamp("2026-03-08T00:00Z"), pd.Timestamp("2026-03-11T00:00Z"))
    # e1 and e4 sit just OUTSIDE the window and are deliberately not held-out: out-of-window rows must never be inspected.
    # e6 is listed first and shares e2's index_stop, to pin the second sort key.
    gold = _gold(
        [
            ("e6", "2026-03-06T00:00:00Z", "2026-03-08T00:00:00Z"),
            ("e1", "2026-03-05T00:00:00Z", "2026-03-07T23:59:59Z"),
            ("e2", "2026-03-06T00:00:00Z", "2026-03-08T00:00:00Z"),
            ("e3", "2026-03-10T08:00:00Z", "2026-03-10T23:59:59.999999Z"),
            ("e4", "2026-03-09T00:00:00Z", "2026-03-11T00:00:00Z"),
            ("e5", "2026-03-01T00:00:00Z", "2026-03-09T12:00:00Z"),
        ]
    )
    partition_of = pd.Series({"e1": "train", "e2": "test", "e3": "test", "e5": "test", "e6": "test"})  # e4: in neither
    result = select_held_out_window(gold, partition_of, run_date=date(2026, 3, 10), window_days=3)
    assert list(result["encounter_id"]) == ["e2", "e6", "e5", "e3"]
    assert list(result.columns) == list(gold.columns)  # every input column is returned


@pytest.mark.parametrize("bad", [0, -1, True, 2.0])
def test_window_bounds_rejects_bad_window_days(bad):
    with pytest.raises(ValueError, match="window_days"):
        window_bounds(date(2026, 3, 10), bad)


@pytest.mark.parametrize(
    "run_date, window_days",
    [(date(1, 1, 1), 30), (date(2026, 3, 10), 10**9), (date(9999, 12, 31), 1)],
)
def test_window_bounds_overflow_is_a_value_error(run_date, window_days):
    with pytest.raises(ValueError, match="supported date range"):
        window_bounds(run_date, window_days)


def test_select_refuses_tz_naive_index_stop_and_missing_columns():
    gold = _gold([("t1", "2026-03-09T00:00:00Z", "2026-03-10T00:00:00Z")])
    naive = gold.assign(index_stop=gold["index_stop"].dt.tz_localize(None))
    partition_of = pd.Series({"t1": "test"})
    with pytest.raises(ValueError, match="tz-aware"):
        select_held_out_window(naive, partition_of, run_date=date(2026, 3, 10), window_days=1)
    with pytest.raises(ValueError, match="encounter_id"):
        select_held_out_window(gold.drop(columns=["encounter_id"]), partition_of, run_date=date(2026, 3, 10), window_days=1)
    with pytest.raises(ValueError, match="index_stop"):
        select_held_out_window(gold.drop(columns=["index_stop"]), partition_of, run_date=date(2026, 3, 10), window_days=1)


_THREE = [
    ("t1", "2026-03-09T00:00:00Z", "2026-03-10T00:00:00Z"),
    ("t2", "2026-03-09T00:00:00Z", "2026-03-10T01:00:00Z"),
    ("t3", "2026-03-09T00:00:00Z", "2026-03-10T02:00:00Z"),
]


def test_completeness_guard_refuses_unscored_in_window_rows():
    # t2 is a training row, t3 is in neither partition; a silent filter that returned [t1] would be the wrong behaviour
    with pytest.raises(ValueError) as info:
        select_held_out_window(_gold(_THREE), pd.Series({"t1": "test", "t2": "train"}), run_date=date(2026, 3, 10), window_days=1)
    assert_count_phrase(str(info.value), 1, "in-sample")
    assert_count_phrase(str(info.value), 1, "discharge(s) excluded by the split")


def test_completeness_guard_counts_an_all_train_window_as_in_sample():
    partition_of = pd.Series({"t1": "train", "t2": "train", "t3": "train"})
    with pytest.raises(ValueError) as info:
        select_held_out_window(_gold(_THREE), partition_of, run_date=date(2026, 3, 10), window_days=1)
    assert_count_phrase(str(info.value), 3, "in-sample")
    assert_count_phrase(str(info.value), 0, "discharge(s) excluded")
    assert "no held-out discharges" not in str(info.value)  # the in-sample error wins over the empty-batch error


def test_empty_window_refused():
    with pytest.raises(ValueError, match="^no held-out discharges"):
        select_held_out_window(_gold(_THREE), pd.Series({"t1": "test"}), run_date=date(2026, 4, 10), window_days=1)


def test_partition_of_with_unknown_value_or_duplicate_index_refused():
    gold = _gold(_THREE)
    # even for an out-of-window row: the pure function does not rely on guard 11 having run
    with pytest.raises(ValueError, match="validation"):
        select_held_out_window(gold, pd.Series({"t1": "test", "zz": "validation"}), run_date=date(2026, 3, 10), window_days=1)
    with pytest.raises(ValueError, match="duplicated"):
        select_held_out_window(
            gold, pd.Series(["test", "test"], index=["t1", "t1"]), run_date=date(2026, 3, 10), window_days=1
        )


def test_observation_horizon():
    # reference end minus the label window: the real table's reference 2026-09-16 with a 30-day window
    assert observation_horizon(date(2026, 9, 16), 30) == pd.Timestamp("2026-08-18T00:00Z")
    assert observation_horizon(date(2026, 3, 10), 1) == pd.Timestamp("2026-03-10T00:00Z")


def test_window_past_the_observation_horizon_is_refused():
    gold = _gold(_THREE)  # all three discharged on 2026-03-10
    partition_of = pd.Series({"t1": "test", "t2": "test", "t3": "test"})
    horizon = pd.Timestamp("2026-03-11T00:00Z")
    # a window ending exactly at the horizon is fine (kills a >= comparison) ...
    ok = select_held_out_window(gold, partition_of, run_date=date(2026, 3, 10), window_days=1, observed_until=horizon)
    assert list(ok["encounter_id"]) == ["t1", "t2", "t3"]
    # ... one day later is refused, naming the latest valid run date (kills a check that is not applied at all), even
    # though every discharge that DOES exist in the window is a held-out row
    with pytest.raises(ValueError, match="on or before 2026-03-10"):
        select_held_out_window(gold, partition_of, run_date=date(2026, 3, 11), window_days=1, observed_until=horizon)
    # the pure function is unchanged when no horizon is given
    select_held_out_window(gold, partition_of, run_date=date(2026, 3, 11), window_days=2)

