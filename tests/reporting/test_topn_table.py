from __future__ import annotations

import pandas as pd
import pytest

from readmission_risk.reporting.topn_table import format_topn_markdown
from tests.reporting.conftest import make_predictions


def test_exact_markdown_for_a_small_frame(predictions):
    expected = (
        "| Rank | Risk score | Discharged | Admission reason | Patient |\n"
        "|---:|---:|---|---|---|\n"
        "| 1 | 0.0900 | 2025-06-20 | Reason number 1 | patient- |\n"
        "| 2 | 0.0800 | 2025-06-21 | Reason number 2 | patient- |\n"
        "| 3 | 0.0700 | 2025-06-22 | Reason number 3 | patient- |\n"
        "| 4 | 0.0600 | 2025-06-23 | Reason number 4 | patient- |\n"
        "| 5 | 0.0500 | 2025-06-24 | Reason number 5 | patient- |\n"
    )
    assert format_topn_markdown(predictions, 5) == expected


def test_description_truncation_null_and_pipe_escaping():
    frame = make_predictions(5)
    frame["admission_reason_description"] = ["x" * 60, None, "a | b\nc", "ok", pd.NA]
    lines = format_topn_markdown(frame, 5).splitlines()[2:]
    assert "| " + "x" * 47 + "… |" in lines[0]  # a single over-long word is cut hard to 48 characters including the ellipsis
    assert "(none)" in lines[1] and "(none)" in lines[4]
    assert "a \\| b c" in lines[2]


def test_top_n_columns_are_ignored_and_rows_are_taken_by_rank():
    frame = make_predictions(7).iloc[::-1].reset_index(drop=True)  # shuffled order
    frame["top_n"] = 2
    frame["is_top_n"] = False
    lines = format_topn_markdown(frame, 5).splitlines()[2:]
    assert [line.split("|")[1].strip() for line in lines] == ["1", "2", "3", "4", "5"]


def test_fewer_rows_than_n_raises():
    with pytest.raises(ValueError, match="at least 5"):
        format_topn_markdown(make_predictions(4), 5)


def test_ranks_must_be_one_to_n():
    frame = make_predictions(7)
    frame["rank"] = [1, 2, 4, 5, 6, 7, 8]
    with pytest.raises(ValueError, match=r"exactly 1\.\.5"):
        format_topn_markdown(frame, 5)


def test_increasing_scores_raise():
    frame = make_predictions(7)
    frame["risk_score"] = [0.01 * i for i in range(7)]
    with pytest.raises(ValueError, match="non-increasing"):
        format_topn_markdown(frame, 5)


def test_missing_column_raises_a_value_error_not_a_key_error():
    with pytest.raises(ValueError, match="patient_id"):
        format_topn_markdown(make_predictions(7).drop(columns=["patient_id"]), 5)


def test_long_description_is_cut_at_a_word_boundary_with_an_ellipsis():
    frame = make_predictions(5)
    frame["admission_reason_description"] = ["History of coronary artery bypass grafting (situation)"] * 5
    row = format_topn_markdown(frame, 5).splitlines()[2]
    assert "| History of coronary artery bypass grafting… |" in row


def test_nan_risk_score_raises():
    frame = make_predictions(7)
    frame.loc[2, "risk_score"] = float("nan")
    with pytest.raises(ValueError, match="NaN"):
        format_topn_markdown(frame, 5)
