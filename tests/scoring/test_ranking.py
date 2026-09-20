from __future__ import annotations

import copy
from datetime import date

import numpy as np
import pandas as pd
import pytest

from readmission_risk.models.data import LABEL_COLUMN
from readmission_risk.scoring.ranking import (
    PREDICTION_COLUMNS,
    PREDICTION_DTYPES,
    build_predictions,
    score_batch,
    summarize_observed,
)
from tests.models.helpers import make_gold_frame
from tests.scoring.helpers import PREDICTION_KWARGS, make_batch, make_predictions

_IDS = ["e", "c", "a", "b", "d"]
_SCORES = [0.2, 0.5, 0.5, 0.5, 0.1]


def test_ties_break_by_encounter_id_ascending_with_distinct_ranks():
    out = make_predictions()  # input order e, c, a, b, d; top_n=3
    assert list(out["encounter_id"]) == ["a", "b", "c", "e", "d"]
    assert list(out["rank"]) == [1, 2, 3, 4, 5]  # distinct: no competition ranking ([1, 1, 1, 4, 5])
    assert list(out["is_top_n"]) == [True, True, True, False, False]
    assert list(out["risk_score"]) == [0.5, 0.5, 0.5, 0.2, 0.1]  # descending score (ascending would start at 0.1)


def test_ranking_independent_of_input_order():
    expected = make_predictions()
    permutation = [3, 0, 4, 1, 2]
    shuffled_ids = [_IDS[i] for i in permutation]
    shuffled_scores = [_SCORES[i] for i in permutation]
    # every row keeps its own id-to-score pairing; only the input order changes (times/reasons differ per position, so
    # compare the order-determined columns)
    shuffled = make_predictions(ids=shuffled_ids, scores=shuffled_scores)
    for column in ("encounter_id", "rank", "is_top_n", "risk_score"):
        pd.testing.assert_series_equal(shuffled[column], expected[column])


@pytest.mark.parametrize("top_n, expected_true", [(5, 5), (7, 5), (1, 1)])
def test_top_n_larger_than_batch_marks_every_row(top_n, expected_true):
    out = make_predictions(top_n=top_n)
    assert int(out["is_top_n"].sum()) == expected_true
    assert (out["top_n"] == top_n).all()  # the requested N is recorded even when it exceeds the batch


@pytest.mark.parametrize("bad", [0, -1, True, 2.5, 2**31])
def test_bad_top_n_refused(bad):
    with pytest.raises(ValueError, match="top_n"):
        make_predictions(top_n=bad)


def test_build_predictions_columns_dtypes_and_constants():
    batch = make_batch(_IDS, descriptions=["d0", None, "d2", "d3", "d4"], reasons=["R0", None, "R2", "R3", "R4"])
    out = build_predictions(batch, _SCORES, **PREDICTION_KWARGS)
    assert list(out.columns) == list(PREDICTION_COLUMNS)
    assert {c: str(out[c].dtype) for c in out.columns} == PREDICTION_DTYPES
    assert PREDICTION_DTYPES["discharge_time"] == "datetime64[us, UTC]"
    assert (out["as_of_date"] == date(2026, 6, 30)).all() and all(type(v) is date for v in out["as_of_date"])
    assert (out["model_run_id"] == "run-abc").all() and (out["model_name"] == "logistic_regression").all()
    assert (out["partition"] == "test").all()
    assert (out["top_n"] == 3).all() and (out["scoring_window_days"] == 180).all()
    assert (out["label_definition"] == "unplanned_readmission_v1").all()
    assert (out["gold_fingerprint"] == "f" * 64).all() and (out["gold_reference_date"] == "20260916").all()
    assert LABEL_COLUMN not in out.columns
    # the null reason (input row 1 = encounter 'c') stays None, never the string "None" or NaN
    row_c = out.loc[out["encounter_id"] == "c"].iloc[0]
    assert row_c["admission_reason_description"] is None and row_c["admission_reason_code"] is None
    # discharge_time is microsecond-resolution UTC even when the input is nanosecond-resolution
    assert batch["index_stop"].dtype == "datetime64[ns, UTC]"
    assert out["discharge_time"].dtype == "datetime64[us, UTC]"


def test_nanosecond_index_stop_is_refused_not_truncated():
    stops = pd.DatetimeIndex(pd.to_datetime(["2026-03-01T12:00:00Z"] * 5, utc=True)) + pd.Timedelta(nanoseconds=1)
    with pytest.raises(ValueError, match="nanosecond"):
        build_predictions(make_batch(_IDS, stops=stops), _SCORES, **PREDICTION_KWARGS)


def test_scorer_refuses_a_frame_carrying_the_label():
    gold = make_gold_frame(20, seed=0)  # still has is_readmitted
    with pytest.raises(ValueError, match=LABEL_COLUMN):
        score_batch(_StubModel(np.full(len(gold), 0.1)), gold)
    with pytest.raises(ValueError, match=LABEL_COLUMN):
        build_predictions(gold.assign(index_stop=gold["index_stop"]), np.full(len(gold), 0.1), **PREDICTION_KWARGS)
    # build_predictions also refuses a scores array of the wrong length (longer and shorter) and an empty batch
    batch = make_batch(_IDS)
    for wrong in ([0.1] * 4, [0.1] * 6):
        with pytest.raises(ValueError, match="scores"):
            build_predictions(batch, wrong, **PREDICTION_KWARGS)
    with pytest.raises(ValueError, match="empty"):
        build_predictions(batch.iloc[:0], [], **PREDICTION_KWARGS)


class _StubModel:
    """predict_proba returns fixed positive-class probabilities; fit must never be called."""

    def __init__(self, positive, shape=None):
        self._positive = np.asarray(positive, dtype="float64")
        self._shape = shape

    def fit(self, *args, **kwargs):
        raise AssertionError("scoring must never fit")

    def predict_proba(self, X):
        assert len(X) == len(self._positive)
        if self._shape == "one_column":
            return self._positive.reshape(-1, 1)
        return np.column_stack([1.0 - self._positive, self._positive])


def _label_free(n=12):
    return make_gold_frame(n, seed=0).drop(columns=[LABEL_COLUMN])


def test_score_batch_never_fits_and_returns_the_positive_column():
    batch = _label_free()
    positive = np.linspace(0.01, 0.9, len(batch))
    np.testing.assert_array_equal(score_batch(_StubModel(positive), batch), positive)


@pytest.mark.parametrize("bad", ["shape", "nan", "above_one", "negative"])
def test_score_batch_rejects_broken_model_output(bad):
    batch = _label_free()
    positive = np.full(len(batch), 0.1)
    if bad == "nan":
        positive[3] = np.nan
    elif bad == "above_one":
        positive[3] = 1.2
    elif bad == "negative":
        positive[3] = -0.1
    model = _StubModel(positive, shape="one_column" if bad == "shape" else None)
    with pytest.raises(RuntimeError):
        score_batch(model, batch)


def test_pure_functions_do_not_mutate_their_inputs():
    batch = make_batch(_IDS)
    scores = np.array(_SCORES)
    batch_before, scores_before = copy.deepcopy(batch), scores.copy()
    predictions = build_predictions(batch, scores, **PREDICTION_KWARGS)
    pd.testing.assert_frame_equal(batch, batch_before)
    np.testing.assert_array_equal(scores, scores_before)

    labels = pd.Series({"d": 1, "e": 0, "a": 1, "b": 0, "c": 0})
    predictions_before, labels_before = copy.deepcopy(predictions), labels.copy()
    summarize_observed(predictions, labels, top_n=3)
    pd.testing.assert_frame_equal(predictions, predictions_before)
    pd.testing.assert_series_equal(labels, labels_before)

    gold = make_gold_frame(20, seed=0).drop(columns=[LABEL_COLUMN])
    gold_before = copy.deepcopy(gold)
    score_batch(_StubModel(np.full(len(gold), 0.1)), gold)
    pd.testing.assert_frame_equal(gold, gold_before)


# Observed-outcome summary: ranks a..e as in the tie fixture (a, b, c, e, d), top_n=3.
_LABELS = pd.Series({"d": 1, "e": 0, "a": 1, "b": 0, "c": 0})  # deliberately NOT in rank order


def test_summarize_observed_hand_computed():
    summary = summarize_observed(make_predictions(), _LABELS, top_n=3)
    assert (summary.n_batch, summary.n_positive_batch, summary.n_top, summary.n_positive_top) == (5, 2, 3, 1)
    assert summary.precision_at_n == pytest.approx(1 / 3)
    assert summary.batch_positive_rate == pytest.approx(0.4)
    assert summary.lift == pytest.approx((1 / 3) / 0.4)  # 0.8333...


def test_summarize_observed_top_n_exceeds_batch_and_zero_positives():
    predictions = make_predictions()
    summary = summarize_observed(predictions, _LABELS, top_n=10)
    assert (summary.n_top, summary.n_positive_top) == (5, 2)
    assert summary.precision_at_n == pytest.approx(0.4)  # a denominator of N would give 0.2
    assert summary.lift == pytest.approx(1.0)

    none_positive = summarize_observed(predictions, pd.Series({k: 0 for k in "abcde"}), top_n=3)
    assert none_positive.lift is None and none_positive.n_positive_top == 0

    with pytest.raises(ValueError, match="no label"):
        summarize_observed(predictions, pd.Series({"a": 1}), top_n=3)
    with pytest.raises(ValueError, match="duplicated"):
        summarize_observed(predictions, pd.Series([0, 1], index=["a", "a"]), top_n=3)
