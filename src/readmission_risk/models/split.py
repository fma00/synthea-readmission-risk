"""Chronological, patient-disjoint train/test split with a censoring buffer and a label-window purge.
See notes/eg-new-feature/model-training-2026-09-19.md (Interfaces > split.py) for the design.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from .data import GROUP_COLUMN, LABEL_COLUMN

_CENSOR_BUFFER_WARN_FRACTION = 0.01


@dataclass(frozen=True)
class SplitResult:
    train: pd.DataFrame
    test: pd.DataFrame
    summary: dict[str, int | float | str]


def _utc_midnight(d: date) -> pd.Timestamp:
    return pd.Timestamp(d.isoformat(), tz="UTC")


def _require_tz_aware(df: pd.DataFrame, column: str) -> None:
    if not isinstance(df[column].dtype, pd.DatetimeTZDtype):
        # ValueError, not TypeError: the design specifies one exception type for every bad-input case here
        raise ValueError(  # noqa: TRY004
            f"{column} must be a tz-aware datetime column (load_gold_table returns UTC); got {df[column].dtype}"
        )


def _require_two_classes(part: pd.DataFrame, name: str, test_start_date: date) -> None:
    if len(part) == 0:
        raise ValueError(
            f"The {name} partition is empty after the split (test_start_date={test_start_date.isoformat()}); "
            "choose a test_start_date that leaves both partitions non-empty"
        )
    if part[LABEL_COLUMN].nunique() < 2:
        raise ValueError(
            f"The {name} partition contains only one class of {LABEL_COLUMN} "
            f"(test_start_date={test_start_date.isoformat()}); AUC and calibration are undefined"
        )


def chronological_group_split(
    df: pd.DataFrame,
    *,
    reference_date: date,
    test_start_date: date,
    readmission_window_days: int = 30,
    train_start_date: date | None = None,
) -> SplitResult:
    """Pure. With W = readmission_window_days, T = test_start_date at 00:00Z and
    R_end = (reference_date + 1 day) at 00:00Z:

    1. censoring buffer -- keep a row iff index_stop + W < R_end. This is slice 2's own rule for when
       a negative is observable, applied here to positives too (the only place slice 2's policy was
       asymmetric), so within the modeling set a row exists only if its outcome WINDOW was fully observed.
    2. test = kept rows with index_start >= T.
    3. pre = the other kept rows; drop those before train_start_date if given.
    4. label-window purge -- drop pre rows with index_stop + W >= T. Their outcome window (stop, stop + W]
       is INCLUSIVE at the upper bound in slice 2 (`a.START <= readmit_deadline`), so a row with
       index_stop + W == T exactly could have its label set by a readmission starting AT T -- a test-period
       event -- and must be purged too. Only rows with index_stop + W < T have every readmission START that
       can set their label before the test period begins.

       Since slice 2b two more things depend on records that are NOT covered by this purge: (a) the label
       depends on the content of a readmitting stay (whether it carries a planned procedure) and on the stop
       times of the patient's earlier stays (the continuation rule); (b) whether a row is in the gold table at
       all depends on the stop times of overlapping stays (the terminal rule). Either can be dated at or after
       T when a stay straddles it, and this split does NOT enforce that. It is a property that is measured, not
       guaranteed, on the real data, and a different population or test_start_date can break it: see the
       slice-2b design doc, Data handling and the "tightening slice 3's purge" follow-up.
    5. patient disjointness -- drop pre rows whose patient_id appears in ANY test row. The remainder
       is train.

    Raises ValueError for input/config problems (evaluated before the post-conditions, which would be
    undefined on empty frames), RuntimeError if an algorithm invariant is violated. Emits a
    UserWarning if the censoring buffer drops more than 1% of rows (a too-early reference_date is the
    likely cause: on real data the buffer removes only the handful of censored positives slice 2 kept).
    """
    if readmission_window_days < 1:
        raise ValueError(f"readmission_window_days must be >= 1; got {readmission_window_days}")
    if train_start_date is not None:
        if not train_start_date < test_start_date < reference_date:
            raise ValueError(
                "dates must satisfy train_start_date < test_start_date < reference_date; got "
                f"{train_start_date.isoformat()}, {test_start_date.isoformat()}, {reference_date.isoformat()}"
            )
    elif not test_start_date < reference_date:
        raise ValueError(
            "test_start_date must be before reference_date; got "
            f"{test_start_date.isoformat()} >= {reference_date.isoformat()}"
        )
    if len(df) == 0:
        raise ValueError("chronological_group_split received a zero-row frame")
    _require_tz_aware(df, "index_start")
    _require_tz_aware(df, "index_stop")

    window = pd.Timedelta(days=readmission_window_days)
    test_start = _utc_midnight(test_start_date)
    reference_end = _utc_midnight(reference_date + timedelta(days=1))
    n_input = len(df)

    # Step 1
    buffered = df.loc[(df["index_stop"] + window) < reference_end]
    n_dropped_censor_buffer = n_input - len(buffered)
    if n_dropped_censor_buffer / n_input > _CENSOR_BUFFER_WARN_FRACTION:
        warnings.warn(
            f"The censoring buffer dropped {n_dropped_censor_buffer} of {n_input} rows "
            f"({n_dropped_censor_buffer / n_input:.1%}); check that reference_date "
            f"({reference_date.isoformat()}) matches the Synthea/Big Join run that produced this table "
            "(a date that is too early drops too many rows).",
            UserWarning,
            stacklevel=2,
        )

    # Step 2
    is_test = buffered["index_start"] >= test_start
    test = buffered.loc[is_test]
    pre = buffered.loc[~is_test]

    # Step 3
    n_dropped_before_train_start = 0
    if train_start_date is not None:
        keep_pre = pre["index_start"] >= _utc_midnight(train_start_date)
        n_dropped_before_train_start = int((~keep_pre).sum())
        pre = pre.loc[keep_pre]

    # Step 4
    label_known = (pre["index_stop"] + window) < test_start
    n_dropped_label_window_purge = int((~label_known).sum())
    pre = pre.loc[label_known]

    # Step 5
    overlaps_test = pre[GROUP_COLUMN].isin(set(test[GROUP_COLUMN]))
    n_dropped_patient_overlap = int(overlaps_test.sum())
    train = pre.loc[~overlaps_test]

    train = train.copy()
    test = test.copy()

    _require_two_classes(train, "train", test_start_date)
    _require_two_classes(test, "test", test_start_date)

    # Post-conditions: invariants of the algorithm, not input validation.
    if set(train[GROUP_COLUMN]) & set(test[GROUP_COLUMN]):
        raise RuntimeError("split invariant violated: train and test share patient_id values")
    if set(train["encounter_id"]) & set(test["encounter_id"]):
        raise RuntimeError("split invariant violated: train and test share encounter_id values")
    if not (train["index_start"].max() < test_start <= test["index_start"].min()):
        raise RuntimeError("split invariant violated: train/test are not chronologically separated at test_start")
    if not ((train["index_stop"] + window) < test_start).all():
        raise RuntimeError("split invariant violated: a train row's label window reaches into the test period")

    n_train_positive = int(train[LABEL_COLUMN].sum())
    n_test_positive = int(test[LABEL_COLUMN].sum())
    summary: dict[str, int | float | str] = {
        "n_input": int(n_input),
        "n_dropped_censor_buffer": int(n_dropped_censor_buffer),
        "n_test": len(test),
        "n_dropped_before_train_start": int(n_dropped_before_train_start),
        "n_dropped_label_window_purge": int(n_dropped_label_window_purge),
        "n_dropped_patient_overlap": int(n_dropped_patient_overlap),
        "n_train": len(train),
        "n_train_positive": n_train_positive,
        "n_test_positive": n_test_positive,
        "train_positive_rate": float(n_train_positive / len(train)),
        "test_positive_rate": float(n_test_positive / len(test)),
        "n_train_patients": int(train[GROUP_COLUMN].nunique()),
        "n_test_patients": int(test[GROUP_COLUMN].nunique()),
        "train_index_start_min": train["index_start"].min().isoformat(),
        "train_index_start_max": train["index_start"].max().isoformat(),
        "test_index_start_min": test["index_start"].min().isoformat(),
        "test_index_start_max": test["index_start"].max().isoformat(),
        "censor_buffer_cutoff": (reference_end - window).isoformat(),
    }
    return SplitResult(train=train, test=test, summary=summary)
