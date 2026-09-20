"""Scoring a label-free batch, ranking it with an explicit total order, and the opt-in observed-outcome summary.
Pure functions. See notes/eg-new-feature/batch-scoring-cli-2026-09-20.md (Interfaces > ranking.py).

Risk-score semantics: `risk_score` is the run's model's predicted probability of an unplanned 30-day readmission
(calibrated XGBoost: CalibratedClassifierCV; logistic regression: natively probabilistic). On the 2023+ window both models
under-predict on average (mean about 1.7% against 2.66% observed), so treat scores as a ranking, not as absolute risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from readmission_risk.models.data import LABEL_COLUMN, prepare_features

from .constants import PARTITION_TEST

_INT32_MAX = 2**31 - 1

PREDICTION_COLUMNS: tuple[str, ...] = (
    "as_of_date",
    "rank",
    "is_top_n",
    "patient_id",
    "encounter_id",
    "discharge_time",
    "admission_reason_code",
    "admission_reason_description",
    "risk_score",
    "model_run_id",
    "model_name",
    "top_n",
    "scoring_window_days",
    "partition",
    "label_definition",
    "gold_fingerprint",
    "gold_reference_date",
)

# The single source of truth for the pandas dtypes of the predictions frame (compared as str(frame[col].dtype)).
# as_of_date is an object column whose elements are exactly datetime.date; every column not listed as special is an
# object column of str (None allowed only in the two admission_reason columns).
_SPECIAL_DTYPES: dict[str, str] = {
    "rank": "int32",
    "is_top_n": "bool",
    "discharge_time": "datetime64[us, UTC]",
    "risk_score": "float64",
    "top_n": "int32",
    "scoring_window_days": "int32",
}
PREDICTION_DTYPES: dict[str, str] = {c: _SPECIAL_DTYPES.get(c, "object") for c in PREDICTION_COLUMNS}


def _require_label_free(frame: pd.DataFrame, caller: str) -> None:
    if LABEL_COLUMN in frame.columns:
        raise ValueError(
            f"{caller} must not be handed the label column {LABEL_COLUMN!r}: the scorer never sees the observed outcome"
        )


def _require_top_n(top_n: object) -> None:
    if type(top_n) is not int or not 1 <= top_n <= _INT32_MAX:
        raise ValueError(f"top_n must be an int in [1, {_INT32_MAX}]; got {top_n!r}")


def score_batch(model, batch: pd.DataFrame) -> np.ndarray:
    """ValueError if LABEL_COLUMN is in batch.columns (structural, not by convention). scores =
    model.predict_proba(prepare_features(batch))[:, 1]; never calls .fit. RuntimeError (a broken model or environment,
    not user input) if predict_proba does not return shape (len(batch), 2), or any score is non-finite, < 0 or > 1."""
    _require_label_free(batch, "score_batch")
    proba = np.asarray(model.predict_proba(prepare_features(batch)))
    if proba.shape != (len(batch), 2):
        raise RuntimeError(f"predict_proba returned shape {proba.shape}; expected {(len(batch), 2)}")
    scores = proba[:, 1].astype("float64")
    if not np.isfinite(scores).all() or (scores < 0).any() or (scores > 1).any():
        raise RuntimeError("predict_proba returned a score that is non-finite or outside [0, 1]")
    return scores


def build_predictions(
    batch: pd.DataFrame,
    scores: np.ndarray,
    *,
    run_date: date,
    top_n: int,
    window_days: int,
    model_run_id: str,
    model_name: str,
    label_definition: str,
    gold_fingerprint: str,
    gold_reference_date: str,
) -> pd.DataFrame:
    """batch: label-free rows (ValueError if the label column is present) with at least patient_id, encounter_id,
    index_stop, admission_reason_code and admission_reason_description; scores are aligned to the batch rows
    POSITIONALLY (ValueError on a length mismatch or an empty batch).

    ORDER: sort by risk_score DESCENDING (exact float comparison), ties by encounter_id ASCENDING (str order, i.e. code
    point order); rank = the 1-based position in that order (int32, always distinct: no shared ranks); is_top_n =
    rank <= top_n (top_n larger than the batch is not an error: every row is Top-N). Returns exactly PREDICTION_COLUMNS,
    in order, one row per batch row, in rank order, with a RangeIndex and the dtypes of PREDICTION_DTYPES. The batch's
    row order does not matter, and neither `batch` nor `scores` is mutated. discharge_time is index_stop as
    datetime64[us, UTC]: ValueError if any index_stop has a nonzero nanosecond component (the cast would truncate it
    silently). The `window_days` argument is written to the column `scoring_window_days` (not `window_days`, which would
    be confusable with the 30-day label horizon)."""
    _require_label_free(batch, "build_predictions")
    _require_top_n(top_n)
    if type(window_days) is not int or not 1 <= window_days <= _INT32_MAX:
        raise ValueError(f"window_days must be an int in [1, {_INT32_MAX}]; got {window_days!r}")
    if type(run_date) is not date:
        raise ValueError(f"run_date must be a datetime.date; got {type(run_date).__name__}")
    scores = np.asarray(scores, dtype="float64")
    if len(batch) == 0:
        raise ValueError("build_predictions received an empty batch")
    if scores.ndim != 1 or len(scores) != len(batch):
        raise ValueError(f"scores must be 1-D with one value per batch row ({len(batch)}); got shape {scores.shape}")
    stop = batch["index_stop"]
    if not isinstance(stop.dtype, pd.DatetimeTZDtype):
        # ValueError, not TypeError: the design specifies one exception type for every bad-input case (the CLI reports it)
        raise ValueError(f"index_stop must be a tz-aware datetime column; got {stop.dtype}")  # noqa: TRY004
    if (stop.dt.nanosecond != 0).any():
        raise ValueError("index_stop has a nonzero nanosecond component; converting to microseconds would truncate it")

    def _text(column: str) -> pd.Series:
        values = batch[column]
        return values.astype(object).where(values.notna(), None).reset_index(drop=True)

    n = len(batch)

    def _constant(value: str) -> pd.Series:
        return pd.Series([value] * n, dtype=object)

    frame = pd.DataFrame(
        {
            "patient_id": _text("patient_id"),
            "encounter_id": _text("encounter_id"),
            "discharge_time": stop.dt.tz_convert("UTC").dt.as_unit("us").reset_index(drop=True),
            "admission_reason_code": _text("admission_reason_code"),
            "admission_reason_description": _text("admission_reason_description"),
            "risk_score": scores,
        }
    )
    # stable sort on the two keys = a total order, since encounter_id is unique within a batch
    frame = frame.sort_values(["risk_score", "encounter_id"], ascending=[False, True], kind="stable").reset_index(drop=True)
    frame["as_of_date"] = pd.Series([run_date] * n, dtype=object)
    frame["rank"] = np.arange(1, n + 1, dtype="int32")
    frame["is_top_n"] = frame["rank"].to_numpy() <= top_n
    frame["model_run_id"] = _constant(model_run_id)
    frame["model_name"] = _constant(model_name)
    frame["top_n"] = np.full(n, top_n, dtype="int32")
    frame["scoring_window_days"] = np.full(n, window_days, dtype="int32")
    frame["partition"] = _constant(PARTITION_TEST)
    frame["label_definition"] = _constant(label_definition)
    frame["gold_fingerprint"] = _constant(gold_fingerprint)
    frame["gold_reference_date"] = _constant(gold_reference_date)
    frame = frame[list(PREDICTION_COLUMNS)]
    actual = {c: str(frame[c].dtype) for c in PREDICTION_COLUMNS}
    if actual != PREDICTION_DTYPES:  # an invariant of this function, not user input
        raise RuntimeError(f"build_predictions produced dtypes {actual}; expected {PREDICTION_DTYPES}")
    return frame


@dataclass(frozen=True)
class ObservedSummary:
    n_batch: int
    n_positive_batch: int
    batch_positive_rate: float
    n_top: int  # min(top_n, n_batch): the denominator of precision_at_n
    n_positive_top: int
    precision_at_n: float
    lift: float | None  # precision_at_n / batch_positive_rate; None when n_positive_batch == 0


def summarize_observed(predictions: pd.DataFrame, labels: pd.Series, *, top_n: int) -> ObservedSummary:
    """labels: 0/1 Series indexed by encounter_id (the gold label), joined to `predictions` BY encounter_id, never
    positionally. ValueError if the labels' index is not unique or any prediction encounter_id has no label. The Top-N is
    the rows with rank <= top_n. This is the only place the label is read, and it runs after scoring."""
    _require_top_n(top_n)
    if not labels.index.is_unique:
        raise ValueError("labels has a duplicated encounter_id in its index")
    ids = predictions["encounter_id"]
    missing = ids[~ids.isin(labels.index)]
    if len(missing):
        raise ValueError(f"{len(missing)} prediction encounter_id(s) have no label, e.g. {missing.iloc[0]!r}")
    y = ids.map(labels).astype("int64")
    n_batch = len(predictions)
    if n_batch == 0:
        raise ValueError("summarize_observed received an empty predictions frame")
    n_top = min(top_n, n_batch)
    n_positive_batch = int(y.sum())
    n_positive_top = int(y[(predictions["rank"] <= top_n).to_numpy()].sum())
    precision = n_positive_top / n_top
    rate = n_positive_batch / n_batch
    return ObservedSummary(
        n_batch=n_batch,
        n_positive_batch=n_positive_batch,
        batch_positive_rate=rate,
        n_top=n_top,
        n_positive_top=n_positive_top,
        precision_at_n=precision,
        lift=None if n_positive_batch == 0 else precision / rate,
    )
