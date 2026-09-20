"""Selecting the held-out discharges a run date scores. Pure functions; the completeness guard in
select_held_out_window is the whole in-sample policy. See notes/eg-new-feature/batch-scoring-cli-2026-09-20.md.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .constants import PARTITION_TEST, PARTITION_TRAIN


def _utc_midnight(d: date) -> pd.Timestamp:
    return pd.Timestamp(d.isoformat(), tz="UTC")


def window_bounds(run_date: date, window_days: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(start, end): start = (run_date - (window_days - 1) days) at 00:00:00 UTC, INCLUSIVE; end = (run_date + 1 day)
    at 00:00:00 UTC, EXCLUSIVE. A row is in the window iff start <= index_stop < end. Calendar days are UTC days (gold
    timestamps are UTC). ValueError unless type(window_days) is int and >= 1, or if the window falls outside the
    supported date range (the date arithmetic raises OverflowError for e.g. run_date 0001-01-01, window_days 10**9 or
    run_date 9999-12-31; that is re-raised as ValueError so the CLI reports it as a user error)."""
    if type(window_days) is not int or window_days < 1:
        raise ValueError(f"window_days must be an int >= 1; got {window_days!r}")
    try:
        start_date = run_date - timedelta(days=window_days - 1)
        end_date = run_date + timedelta(days=1)
    except OverflowError:
        raise ValueError(
            f"window is outside the supported date range (run_date {run_date.isoformat()}, window_days {window_days})"
        ) from None
    return _utc_midnight(start_date), _utc_midnight(end_date)


def observation_horizon(reference_date: date, label_window_days: int) -> pd.Timestamp:
    """The instant before which every discharge's outcome window was fully observed: (reference_date + 1 day) at 00:00Z
    minus the label window. Slice 2 keeps a non-readmitted index row only if index_stop + label window < that reference end
    (positives are kept regardless), so a discharge at or after this instant is present in the gold table only if it was
    readmitted. A batch that reaches past it is missing most of its discharges by construction, and no comparison against
    the rows that DO exist can notice that."""
    return _utc_midnight(reference_date + timedelta(days=1)) - pd.Timedelta(days=label_window_days)


def select_held_out_window(
    gold: pd.DataFrame,
    partition_of: pd.Series,
    *,
    run_date: date,
    window_days: int,
    observed_until: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """gold: the frame load_gold_table_with_metadata returns; only `encounter_id` and a tz-aware `index_stop` are required
    (ValueError otherwise) and every input column is returned. partition_of: Series indexed by encounter_id with values
    "train" | "test" (the model run's split_assignment.csv); a gold encounter_id missing from its index is "in neither
    partition" (dropped by the split).

    observed_until (pipeline.py always passes observation_horizon(...)): ValueError if the window's end is after it, i.e. the
    window reaches into the region where non-readmitted discharges were never kept, so the batch would be silently truncated;
    a window ending exactly at it is fine.

    Steps: (0) ValueError if partition_of has a duplicated index or a value outside {"train","test"}; (1) in_window =
    start <= index_stop < end; (2) among in_window rows count n_train ("train") and n_dropped (not in partition_of);
    (3) if n_train + n_dropped > 0 raise ValueError (a window that touches training rows, split-purged rows or the
    censoring-buffer row is refused rather than silently truncated); (4) if no in_window row is "test" raise ValueError
    starting "no held-out discharges"; (5) return the in-window "test" rows sorted by (index_stop, encounter_id), as a
    copy with a fresh RangeIndex. Rows OUTSIDE the window are never inspected: their partition is irrelevant."""
    start, end = window_bounds(run_date, window_days)
    if observed_until is not None and end > observed_until:
        latest = (observed_until - pd.Timedelta(days=1)).date()  # observed_until is a UTC midnight
        raise ValueError(
            f"window [{start.isoformat()}, {end.isoformat()}) reaches past {observed_until.isoformat()}, after which the "
            "gold table keeps only readmitted discharges (the others' outcome windows were still open at the reference "
            f"date), so the batch would be silently truncated; run_date must be on or before {latest.isoformat()}"
        )
    missing = [c for c in ("encounter_id", "index_stop") if c not in gold.columns]
    if missing:
        raise ValueError(f"gold frame is missing required column(s): {missing}")
    if not isinstance(gold["index_stop"].dtype, pd.DatetimeTZDtype):
        # ValueError, not TypeError: the design specifies one exception type for every bad-input case (the CLI reports it)
        raise ValueError(  # noqa: TRY004
            f"index_stop must be a tz-aware datetime column (load_gold_table returns UTC); got {gold['index_stop'].dtype}"
        )
    if not partition_of.index.is_unique:
        raise ValueError("partition_of has a duplicated encounter_id in its index")
    unknown = sorted(set(partition_of.unique()) - {PARTITION_TRAIN, PARTITION_TEST})
    if unknown:
        raise ValueError(f"partition_of has value(s) outside {{'{PARTITION_TRAIN}', '{PARTITION_TEST}'}}: {unknown}")

    window = gold.loc[(gold["index_stop"] >= start) & (gold["index_stop"] < end)]
    part = window["encounter_id"].map(partition_of)  # NaN where the encounter is in neither partition
    n_train = int((part == PARTITION_TRAIN).sum())
    n_dropped = int(part.isna().sum())
    if n_train or n_dropped:
        raise ValueError(
            f"window [{start.isoformat()}, {end.isoformat()}) contains {n_train} in-sample (training-partition) "
            f"discharge(s) and {n_dropped} discharge(s) excluded by the split (neither partition); a held-out Top-N "
            "list needs every discharge in the window to be a held-out row"
        )
    batch = window.loc[part == PARTITION_TEST]
    if len(batch) == 0:
        raise ValueError(f"no held-out discharges in window [{start.isoformat()}, {end.isoformat()})")
    return batch.sort_values(["index_stop", "encounter_id"], kind="stable").reset_index(drop=True)
