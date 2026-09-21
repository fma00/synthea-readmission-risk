from __future__ import annotations

import warnings

import pandas as pd
import pytest

from readmission_risk.models.split import chronological_group_split
from tests.models.helpers import (
    REFERENCE_DATE,
    TEST_START,
    frame,
    make_gold_frame,
    pad_rows,
    row,
)


def _split(df, **kwargs):
    return chronological_group_split(df, reference_date=REFERENCE_DATE, test_start_date=TEST_START, **kwargs)


def _ids(part: pd.DataFrame) -> set[str]:
    return set(part["encounter_id"])


def test_censor_buffer_boundary_symmetric():
    rows = [
        row("keep_pos", "2026-08-10T00:00:00Z", "2026-08-17T23:59:59Z", 1),
        row("keep_neg", "2026-08-10T00:00:00Z", "2026-08-17T23:59:59Z", 0),
        row("drop_pos", "2026-08-10T00:00:00Z", "2026-08-18T00:00:00Z", 1),
        row("drop_neg", "2026-08-10T00:00:00Z", "2026-08-18T00:00:00Z", 0),
    ] + pad_rows() + [row(f"f{i}", "2024-02-01T00:00:00Z", "2024-02-05T00:00:00Z", i % 2) for i in range(96)]
    with pytest.warns(UserWarning, match="reference_date"):  # 2 of 104 rows > 1%
        result = _split(frame(rows))

    test_patients = set(result.test["patient_id"])
    assert {"keep_pos", "keep_neg"} <= test_patients
    assert not ({"drop_pos", "drop_neg"} & test_patients)
    assert result.summary["n_dropped_censor_buffer"] == 2
    assert result.summary["censor_buffer_cutoff"] == "2026-08-18T00:00:00+00:00"


def _filler(n_rows_total: int, extra: list[dict]) -> pd.DataFrame:
    fill = n_rows_total - len(pad_rows()) - len(extra)
    rows = pad_rows() + extra + [row(f"f{i}", "2024-02-01T00:00:00Z", "2024-02-05T00:00:00Z", i % 2) for i in range(fill)]
    return frame(rows)


def test_warns_when_censor_buffer_drops_more_than_one_percent():
    buffer_rows = [row(f"b{i}", "2026-08-10T00:00:00Z", "2026-08-18T00:00:00Z", 1) for i in range(2)]
    with pytest.warns(UserWarning, match="reference_date"):
        _split(_filler(100, buffer_rows))


def test_no_warning_at_or_below_one_percent():
    buffer_rows = [row("b0", "2026-08-10T00:00:00Z", "2026-08-18T00:00:00Z", 1)]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = _split(_filler(100, buffer_rows))
    assert result.summary["n_dropped_censor_buffer"] == 1


def test_test_partition_is_on_or_after_test_start():
    rows = [
        *pad_rows(),
        row("on_boundary", "2023-01-01T00:00:00Z", "2023-01-03T00:00:00Z", 0),
        row("just_before", "2022-12-31T23:59:59Z", "2023-01-03T00:00:00Z", 0),
    ]
    result = _split(frame(rows))
    assert "on_boundary" in set(result.test["patient_id"])
    assert "just_before" not in set(result.test["patient_id"])
    # the second-earlier row is pre-cutoff but its label window reaches into the test period -> purged
    assert result.summary["n_dropped_label_window_purge"] == 1


def test_label_window_purge_boundary():
    # Slice 2's readmission window is (stop, stop + W] -- INCLUSIVE at the upper bound -- so a row with
    # stop + W == T exactly could be labeled by a readmission starting exactly at T (a test-period event).
    rows = [
        *pad_rows(),
        row("just_inside", "2022-11-28T00:00:00Z", "2022-12-01T23:59:59Z", 0),  # stop + 30d == T - 1s -> kept
        row("exact", "2022-11-28T00:00:00Z", "2022-12-02T00:00:00Z", 0),  # stop + 30d == T exactly -> purged
        row("late", "2022-11-28T00:00:00Z", "2022-12-02T00:00:01Z", 0),  # +1s -> purged
    ]
    result = _split(frame(rows))
    assert "just_inside" in set(result.train["patient_id"])
    assert not ({"exact", "late"} & set(result.train["patient_id"]))
    assert result.summary["n_dropped_label_window_purge"] == 2


@pytest.mark.filterwarnings("ignore:The censoring buffer dropped:UserWarning")
@pytest.mark.parametrize("window, purged, buffered", [(14, False, False), (30, True, True)])
def test_readmission_window_days_reaches_both_the_purge_and_the_buffer(window, purged, buffered):
    """A hard-coded 30 in either step would pass every default-window test. The "pre" row ends 20 days before the
    test start, so its label window clears the cutoff at W=14 (kept) but not at W=30 (purged); the "late" row ends
    22 days before the reference date, so it survives the censoring buffer at W=14 but not at W=30."""
    rows = [
        *pad_rows(),
        row("pre", "2022-12-05T00:00:00Z", "2022-12-12T00:00:00Z", 0),  # 20 days before T = 2023-01-01
        row("late", "2026-08-20T00:00:00Z", "2026-08-25T00:00:00Z", 0),  # 22 days before the reference date
    ]
    result = _split(frame(rows), readmission_window_days=window)
    assert ("pre" not in set(result.train["patient_id"])) is purged
    assert ("late" not in set(result.test["patient_id"])) is buffered
    assert result.summary["censor_buffer_cutoff"] == (
        pd.Timestamp("2026-09-17", tz="UTC") - pd.Timedelta(days=window)
    ).isoformat()


def test_patient_disjoint_drops_overlapping_patients_from_train():
    rows = [
        *pad_rows(),
        row("both", "2020-06-01T00:00:00Z", "2020-06-05T00:00:00Z", 0),
        row("both", "2024-06-01T00:00:00Z", "2024-06-05T00:00:00Z", 1),
    ]
    result = _split(frame(rows))
    assert "both" in set(result.test["patient_id"])
    assert "both" not in set(result.train["patient_id"])
    assert result.summary["n_dropped_patient_overlap"] == 1


def test_split_postconditions_hold_property():
    for seed in range(5):
        result = _split(make_gold_frame(400, seed=seed))
        assert not (set(result.train["patient_id"]) & set(result.test["patient_id"]))
        assert not (_ids(result.train) & _ids(result.test))
        test_start = pd.Timestamp("2023-01-01", tz="UTC")
        assert result.train["index_start"].max() < test_start <= result.test["index_start"].min()


def _every_drop_reason_frame() -> pd.DataFrame:
    return frame(
        pad_rows()
        + [
            row("buf", "2026-08-10T00:00:00Z", "2026-08-18T00:00:00Z", 1),  # censoring buffer
            row("early", "2018-06-01T00:00:00Z", "2018-06-05T00:00:00Z", 0),  # before train_start_date
            row("purge", "2022-11-28T00:00:00Z", "2022-12-02T00:00:01Z", 0),  # label-window purge
            row("both", "2020-06-01T00:00:00Z", "2020-06-05T00:00:00Z", 0),  # patient overlap (train side)
            row("both", "2024-06-01T00:00:00Z", "2024-06-05T00:00:00Z", 1),
        ]
        + [row(f"f{i}", "2024-02-01T00:00:00Z", "2024-02-05T00:00:00Z", i % 2) for i in range(95)]
    )


@pytest.mark.filterwarnings("ignore:The censoring buffer dropped:UserWarning")
def test_summary_accounting_identity():
    result = _split(_every_drop_reason_frame(), train_start_date=pd.Timestamp("2019-01-01").date())
    s = result.summary
    for key in (
        "n_dropped_censor_buffer",
        "n_dropped_before_train_start",
        "n_dropped_label_window_purge",
        "n_dropped_patient_overlap",
    ):
        assert s[key] >= 1, key
    assert s["n_input"] == (
        s["n_dropped_censor_buffer"]
        + s["n_test"]
        + s["n_dropped_before_train_start"]
        + s["n_dropped_label_window_purge"]
        + s["n_dropped_patient_overlap"]
        + s["n_train"]
    )


@pytest.mark.filterwarnings("ignore:The censoring buffer dropped:UserWarning")
def test_train_start_date_only_filters_train():
    df = _every_drop_reason_frame()
    without = _split(df)
    with_start = _split(df, train_start_date=pd.Timestamp("2019-01-01").date())
    assert with_start.summary["n_dropped_before_train_start"] == 1
    assert without.summary["n_dropped_before_train_start"] == 0
    assert _ids(with_start.test) == _ids(without.test)
    assert "early" in set(without.train["patient_id"])
    assert "early" not in set(with_start.train["patient_id"])


def _no_test_rows() -> pd.DataFrame:
    return frame([r for r in pad_rows() if r["patient_id"].startswith("pad_tr")])


def _no_train_rows() -> pd.DataFrame:
    return frame([r for r in pad_rows() if r["patient_id"].startswith("pad_te")])


def _single_class(frame_rows: list[dict], keep_label_for_prefix: str) -> pd.DataFrame:
    return frame([r for r in frame_rows if not (r["patient_id"].startswith(keep_label_for_prefix) and r["is_readmitted"] == 1)])


@pytest.mark.parametrize(
    "case",
    [
        "naive_timezone",
        "train_start_not_before_test_start",
        "test_start_not_before_reference",
        "zero_window",
        "empty_test",
        "empty_train",
        "single_class_train",
        "single_class_test",
        "zero_rows",
    ],
)
def test_split_rejects_bad_inputs(case):
    df = frame(pad_rows())
    kwargs: dict = {}
    if case == "naive_timezone":
        df["index_start"] = df["index_start"].dt.tz_localize(None)
    elif case == "train_start_not_before_test_start":
        kwargs["train_start_date"] = TEST_START
    elif case == "test_start_not_before_reference":
        with pytest.raises(ValueError, match="test_start_date"):
            chronological_group_split(df, reference_date=TEST_START, test_start_date=TEST_START)
        return
    elif case == "zero_window":
        kwargs["readmission_window_days"] = 0
    elif case == "empty_test":
        df = _no_test_rows()
    elif case == "empty_train":
        df = _no_train_rows()
    elif case == "single_class_train":
        df = _single_class(pad_rows(), "pad_tr")
    elif case == "single_class_test":
        df = _single_class(pad_rows(), "pad_te")
    elif case == "zero_rows":
        df = df.iloc[0:0]
    with pytest.raises(ValueError):  # ValueError specifically, never the RuntimeError of the post-conditions
        _split(df, **kwargs)
