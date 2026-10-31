"""The spark-rowhash-v1 canonicalizer (pure pandas; the Spark side and the Spark/Python agreement are in test_big_join.py).
Design: notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md, Interfaces section 1."""

import datetime as dt
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from readmission_risk.pipeline.fingerprint import (
    GOLD_COLUMN_KINDS,
    canonical_row_bytes,
    combine,
    partial_sums,
    rowhash_fingerprint,
)


def gold_frame(n: int = 3) -> pd.DataFrame:
    """A small gold-shaped frame with the loader's dtypes (tz-aware UTC timestamps, object strings)."""
    rows = []
    for i in range(n):
        rows.append(
            {
                "patient_id": f"p{i}",
                "encounter_id": f"e{i}",
                "index_start": pd.Timestamp("2023-01-01T08:00:00.123456Z") + pd.Timedelta(days=i),
                "index_stop": pd.Timestamp("2023-01-03T09:30:00Z") + pd.Timedelta(days=i),
                "is_readmitted": i % 2,
                "label_horizon": pd.Timestamp("2023-02-02T09:30:00Z") + pd.Timedelta(days=i),
                "admission_reason_code": None if i == 0 else str(1000 + i),
                "admission_reason_description": None if i == 0 else f"reason é{i}",
                "payer_ownership": "PRIVATE",
                "provider_specialty": "GENERAL PRACTICE",
                "organization_utilization": float("nan") if i == 1 else float(10 + i),
                "prior_encounter_count": i,
                "prior_inpatient_count": 0,
                "prior_emergency_count": 1,
                "active_condition_count": 2,
                "active_medication_count": 3,
                "procedure_count_window": 4,
                "active_careplan_count": 5,
                "observation_count_window": 6,
                "age_at_index_years": 41.23477070499658 + i,
                "gender": "F",
                "race": "white",
                "ethnicity": "nonhispanic",
                "marital_status": None if i == 2 else "M",
            }
        )
    return pd.DataFrame(rows)


def one_cell(column, value, kind_frame=None):
    df = gold_frame(1) if kind_frame is None else kind_frame
    df = df.astype({column: object})
    df.at[0, column] = value
    return canonical_row_bytes(df)[0].decode("utf-8")


def token(column, value):
    row = one_cell(column, value)
    return next(cell for cell in row.split("\x1f") if cell.startswith(f"{column}=")).split("=", 1)[1]


def test_token_per_kind():
    assert token("gender", "F") == "S1:F"
    assert token("gender", "é") == "S1:é"  # code points, not bytes
    assert token("prior_encounter_count", 7) == "I7"
    assert token("age_at_index_years", 41.5) == "D" + (41.5).hex()
    assert token("index_start", pd.Timestamp("2023-01-01T08:00:00.000001Z")) == "T2023-01-01T08:00:00.000001Z"


@pytest.mark.parametrize("value", [None, pd.NA, pd.NaT, float("nan"), np.float64("nan"), np.datetime64("NaT")])
def test_null_tokens(value):
    for column in ("gender", "prior_encounter_count", "age_at_index_years", "index_start"):
        assert token(column, value) == "N"


def test_int_column_accepts_numpy_and_integer_valued_float():
    assert token("organization_utilization", np.int32(5)) == "I5"
    assert token("organization_utilization", 5.0) == "I5"
    assert token("organization_utilization", np.float64(5.0)) == "I5"


def test_int_column_rejects_fraction():
    with pytest.raises(ValueError, match="organization_utilization"):
        token("organization_utilization", 5.5)


@pytest.mark.parametrize("value", [True, np.bool_(False)])
@pytest.mark.parametrize("column", ["prior_encounter_count", "age_at_index_years"])
def test_bool_and_numpy_bool_rejected(column, value):
    with pytest.raises(ValueError, match=column):
        token(column, value)


def test_string_column_rejects_non_str():
    with pytest.raises(ValueError, match="gender"):
        token("gender", 5)


@pytest.mark.parametrize("value", ["2023-01-01T00:00:00Z", 1672531200000000000, 1.5])
def test_timestamp_rejects_str_and_int(value):
    with pytest.raises(ValueError, match="index_start"):
        token("index_start", value)


def test_timestamp_naive_equals_aware_utc():
    aware = token("index_start", pd.Timestamp("2023-01-01T08:00:00Z"))
    assert token("index_start", pd.Timestamp("2023-01-01T08:00:00")) == aware
    assert token("index_start", dt.datetime(2023, 1, 1, 8)) == aware
    assert token("index_start", np.datetime64("2023-01-01T08:00:00")) == aware


def test_timestamp_non_utc_aware_converted():
    assert token("index_start", pd.Timestamp("2023-01-01T10:00:00+02:00")) == "T2023-01-01T08:00:00.000000Z"


def test_sub_microsecond_rejected():
    with pytest.raises(ValueError, match="sub-microsecond"):
        token("index_start", pd.Timestamp("2023-01-01T08:00:00.000000001Z"))


def test_column_order_irrelevant():
    df = gold_frame()
    assert rowhash_fingerprint(df[list(reversed(df.columns))]) == rowhash_fingerprint(df)


def test_row_order_irrelevant():
    df = gold_frame(5)
    assert rowhash_fingerprint(df.iloc[::-1].reset_index(drop=True)) == rowhash_fingerprint(df)


def test_duplicate_row_changes_fingerprint():
    df = gold_frame()
    assert rowhash_fingerprint(pd.concat([df, df.iloc[[0]]], ignore_index=True)) != rowhash_fingerprint(df)


def test_single_cell_change_changes_fingerprint():
    df = gold_frame()
    edited = df.copy()
    edited.loc[0, "gender"], edited.loc[1, "gender"] = "M", "F"  # one row changed (row 1 was already F)
    assert rowhash_fingerprint(edited) != rowhash_fingerprint(df)


def test_wrong_column_set_rejected():
    df = gold_frame()
    with pytest.raises(ValueError, match=r"missing \['race'\].*extra \['surprise'\]"):
        rowhash_fingerprint(df.drop(columns="race").assign(surprise=1))


def test_input_not_mutated():
    df = gold_frame()
    before = df.copy()
    rowhash_fingerprint(df)
    pd.testing.assert_frame_equal(df, before)


def test_known_answer_vector():
    """Pins the definition: if this changes, the change is a NEW definition name, never an edit of spark-rowhash-v1."""
    assert rowhash_fingerprint(gold_frame()) == "01cb2ea942bed19ac87c5b1f426dd5b3b2240b86129d9b786031025f15cc3634"


def test_zero_rows_defined():
    empty = gold_frame().iloc[0:0]
    assert partial_sums(empty) == (0, 0, 0)
    assert rowhash_fingerprint(empty) == combine(0, 0, 0)


def test_gold_column_kinds_names_match_both_column_lists():
    from readmission_risk.models.data import EXPECTED_GOLD_COLUMNS
    from readmission_risk.pipeline.big_join import GOLD_TABLE_COLUMNS

    assert set(GOLD_COLUMN_KINDS) == set(EXPECTED_GOLD_COLUMNS) == set(GOLD_TABLE_COLUMNS)
    assert len(GOLD_COLUMN_KINDS) == 24


def test_gold_helpers_import_no_mlflow():
    """tests/gold_helpers.py must import in CI's pipeline-dataproc-runtime job, which has no mlflow or sklearn."""
    code = "import sys, tests.gold_helpers; assert 'mlflow' not in sys.modules and 'sklearn' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


def test_canonical_token_is_the_canonicalizers_own_token():
    """v3 slice 1's column diagnostic compares cells with it; an alias, never a second canonical form."""
    from readmission_risk.pipeline import fingerprint

    assert fingerprint.canonical_token is fingerprint._token
