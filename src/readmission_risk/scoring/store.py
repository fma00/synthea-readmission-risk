"""The prediction partition on local disk: <output-dir>/run_date=YYYYMMDD/predictions.parquet, one file per run date,
replaced as a unit. The one module that knows the storage layout, so the BigQuery direct-write swap replaces
write_partition only. See notes/eg-new-feature/batch-scoring-cli-2026-09-20.md (Interfaces > store.py).
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .constants import DEMO_NOTICE
from .ranking import PREDICTION_COLUMNS, PREDICTION_DTYPES

PARTITION_FILENAME = "predictions.parquet"
_TMP_PREFIX = ".predictions."  # dot-prefixed: pyarrow's dataset discovery ignores it, a plain name does not
_TMP_SUFFIX = ".tmp"
SCHEMA_VERSION = "1"
_NULLABLE_COLUMNS = ("admission_reason_code", "admission_reason_description")

_ARROW_TYPES: dict[str, pa.DataType] = {
    "as_of_date": pa.date32(),
    "rank": pa.int32(),
    "is_top_n": pa.bool_(),
    "discharge_time": pa.timestamp("us", tz="UTC"),
    "risk_score": pa.float64(),
    "top_n": pa.int32(),
    "scoring_window_days": pa.int32(),
}
PREDICTION_SCHEMA: pa.Schema = pa.schema(
    [
        pa.field(c, _ARROW_TYPES.get(c, pa.string()), nullable=c in _NULLABLE_COLUMNS)
        for c in PREDICTION_COLUMNS
    ]
).with_metadata(
    {
        "readmission_risk.demo_notice": DEMO_NOTICE,
        "readmission_risk.schema_version": SCHEMA_VERSION,
    }
)


def partition_dir(output_dir: Path, run_date: date) -> Path:
    return Path(output_dir) / f"run_date={run_date:%Y%m%d}"


def _validate_predictions(predictions: pd.DataFrame, run_date: date) -> None:
    """Every check that can fail is made BEFORE the filesystem is touched."""
    if list(predictions.columns) != list(PREDICTION_COLUMNS):
        raise ValueError(
            f"predictions must have exactly the columns {list(PREDICTION_COLUMNS)} in order; got {list(predictions.columns)}"
        )
    if len(predictions) == 0:
        raise ValueError("predictions is empty")
    # pa.Table.from_pandas does NOT catch these (verified): it silently accepts a tz-naive datetime64[us] column for a
    # timestamp[us, tz=UTC] field, a column in another timezone, and a float64 rank.
    for column in PREDICTION_COLUMNS:
        actual = str(predictions[column].dtype)
        if actual != PREDICTION_DTYPES[column]:
            raise ValueError(f"column {column!r} has dtype {actual}; expected {PREDICTION_DTYPES[column]}")
    not_dates = [v for v in predictions["as_of_date"] if type(v) is not date]
    if not_dates:
        raise ValueError(f"column 'as_of_date' must hold datetime.date objects; found {type(not_dates[0]).__name__}")
    if not (predictions["rank"].to_numpy() == range(1, len(predictions) + 1)).all():
        raise ValueError("column 'rank' must be 1..n in row order")
    wrong_date = [v for v in predictions["as_of_date"] if v != run_date]
    if wrong_date:
        raise ValueError(f"as_of_date value {wrong_date[0]} differs from the partition's run_date {run_date}")
    for column in PREDICTION_COLUMNS:
        if column not in _NULLABLE_COLUMNS:
            n_null = int(predictions[column].isna().sum())
            if n_null:
                raise ValueError(f"column {column!r} has {n_null} null value(s) but is non-nullable")


def write_partition(predictions: pd.DataFrame, output_dir: Path, run_date: date) -> Path:
    """Validates, then replaces <output_dir>/run_date=YYYYMMDD/predictions.parquet as a unit: the file is written to a
    uniquely named dot-prefixed temp file in the same directory and os.replace()d onto the final name (atomic), and the
    temp file is removed in a `finally`. Overlapping runs for one run date each replace with a complete file; the last wins. The
    final file gets the process umask's permissions. A hard crash (kill -9, power loss) between the write and the `finally`
    leaves a `.predictions.<hex>.tmp` file that is never reused; readers ignore it (dot-prefixed) and it is deliberately NOT
    swept at the start of a write, because a sweep could delete another live writer's temp file. Re-running a run date therefore overwrites the whole partition, whatever model/N/window
    produced it. Raises ValueError (naming the column) if `predictions` is not exactly the PREDICTION_COLUMNS frame that
    ranking.build_predictions returns (columns, dtypes, non-nullability, rank == 1..n, as_of_date == run_date); nothing is
    created on disk in that case. Returns the final path."""
    _validate_predictions(predictions, run_date)
    try:
        table = pa.Table.from_pandas(predictions, schema=PREDICTION_SCHEMA, preserve_index=False)
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError) as exc:
        # ArrowTypeError is a TypeError and would otherwise escape the CLI's "ERROR:" path
        raise ValueError(f"predictions cannot be converted to PREDICTION_SCHEMA: {exc}") from exc
    directory = partition_dir(output_dir, run_date)
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / PARTITION_FILENAME
    # A UNIQUE temp name in the same directory: with a fixed one, two overlapping runs for the same run date (a cron run and
    # a manual one, an orchestrator retry) would truncate and write the same inode, so the loser could corrupt the file the
    # winner had just renamed into place. Now each writer owns its file, every os.replace moves a complete file, and the
    # last replace wins.
    # (Named with uuid4 and created by pq.write_table, NOT tempfile.mkstemp: mkstemp creates the file 0600 and the rename
    # would carry that mode onto the partition, hiding it from a dashboard or scheduler running as another user.)
    tmp = directory / f"{_TMP_PREFIX}{uuid.uuid4().hex}{_TMP_SUFFIX}"
    try:
        pq.write_table(table, tmp, compression="snappy")
        os.replace(tmp, final)
    finally:
        tmp.unlink(missing_ok=True)
    return final


def read_partition(output_dir: Path, run_date: date) -> pd.DataFrame:
    """The partition's single file via pd.read_parquet (the file itself, not the directory). FileNotFoundError if absent."""
    path = partition_dir(output_dir, run_date) / PARTITION_FILENAME
    if not path.is_file():
        raise FileNotFoundError(f"No prediction partition at {path}")
    return pd.read_parquet(path)
