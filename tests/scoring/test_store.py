from __future__ import annotations

import hashlib
import os
import stat
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from readmission_risk.scoring import store
from readmission_risk.scoring.constants import DEMO_NOTICE
from readmission_risk.scoring.ranking import PREDICTION_COLUMNS
from readmission_risk.scoring.store import (
    PARTITION_FILENAME,
    PREDICTION_SCHEMA,
    partition_dir,
    read_partition,
    write_partition,
)
from tests.scoring.helpers import make_batch, make_predictions

RUN_DATE = date(2026, 6, 30)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _predictions_with_null_reason() -> pd.DataFrame:
    batch = make_batch(["e", "c", "a", "b", "d"], descriptions=["d0", None, "d2", "d3", "d4"], reasons=["R0", None, "R2", "R3", "R4"])
    from readmission_risk.scoring.ranking import build_predictions
    from tests.scoring.helpers import PREDICTION_KWARGS

    return build_predictions(batch, [0.2, 0.5, 0.5, 0.5, 0.1], **PREDICTION_KWARGS)


def test_partition_round_trip_and_layout(tmp_path):
    predictions = _predictions_with_null_reason()
    path = write_partition(predictions, tmp_path, RUN_DATE)
    assert path == tmp_path / "run_date=20260630" / PARTITION_FILENAME
    assert [p.name for p in path.parent.iterdir()] == [PARTITION_FILENAME]  # exactly one file at rest
    pd.testing.assert_frame_equal(read_partition(tmp_path, RUN_DATE), predictions)  # includes the null reason

    assert PREDICTION_SCHEMA.names == list(PREDICTION_COLUMNS)
    file_schema = pq.read_schema(path)
    # remove_metadata: from_pandas adds a 'pandas' metadata key, which is allowed
    assert file_schema.remove_metadata().equals(PREDICTION_SCHEMA.remove_metadata())
    assert file_schema.metadata[b"readmission_risk.demo_notice"].decode() == DEMO_NOTICE
    assert file_schema.metadata[b"readmission_risk.schema_version"] == b"1"


def test_read_partition_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_partition(tmp_path, RUN_DATE)


def test_parent_directory_reads_as_hive_partitions(tmp_path):
    first = write_partition(make_predictions(run_date=date(2026, 6, 30)), tmp_path, date(2026, 6, 30))
    write_partition(make_predictions(run_date=date(2026, 7, 1)), tmp_path, date(2026, 7, 1))
    (first.parent / ".predictions.0123456789abcdef.tmp").write_bytes(b"PAR1partial")  # a crash leftover must not break directory reads
    parent = pd.read_parquet(tmp_path)
    assert len(parent) == 10
    # run_date is a categorical partition column with int32 categories; as_of_date (in-file) must agree with it row by row
    assert parent["run_date"].astype(str).tolist() == parent["as_of_date"].map(lambda d: d.strftime("%Y%m%d")).tolist()


def test_rewrite_replaces_the_whole_partition_and_is_byte_stable(tmp_path):
    write_partition(make_predictions(top_n=3, model_run_id="run-one"), tmp_path, RUN_DATE)
    path = write_partition(make_predictions(ids=("x", "y", "z"), scores=(0.3, 0.2, 0.1), top_n=1, model_run_id="run-two"), tmp_path, RUN_DATE)
    back = read_partition(tmp_path, RUN_DATE)
    assert len(back) == 3 and set(back["model_run_id"]) == {"run-two"} and set(back["top_n"]) == {1}
    assert not {"a", "b", "c", "d", "e"} & set(back["encounter_id"])  # nothing of the first frame survives

    first_hash = _sha(path)
    write_partition(make_predictions(ids=("x", "y", "z"), scores=(0.3, 0.2, 0.1), top_n=1, model_run_id="run-two"), tmp_path, RUN_DATE)
    assert _sha(path) == first_hash  # the same frame written twice gives a byte-identical file


def _mutations(predictions: pd.DataFrame):
    yield "wrong columns", predictions.drop(columns=["gold_fingerprint"]), "columns"
    yield "empty frame", predictions.iloc[:0], "empty"
    yield "ranks not 1..n", predictions.assign(rank=predictions["rank"] + 1).astype({"rank": "int32"}), "rank"
    yield "as_of_date differs", predictions.assign(as_of_date=date(2026, 7, 1)), "as_of_date"
    yield "null patient_id", predictions.assign(patient_id=[None, *predictions["patient_id"][1:]]), "patient_id"
    yield "naive discharge_time", predictions.assign(discharge_time=predictions["discharge_time"].dt.tz_localize(None)), "discharge_time"
    yield "other-tz discharge_time", predictions.assign(discharge_time=predictions["discharge_time"].dt.tz_convert("Europe/Zurich")), "discharge_time"
    yield "float rank", predictions.assign(rank=predictions["rank"].astype("float64")), "rank"
    yield "string risk_score", predictions.assign(risk_score=predictions["risk_score"].astype(str)), "risk_score"
    # object column (so the dtype check passes) whose elements are datetime.datetime, a subclass of date that must be refused
    datetimes = pd.Series([datetime(2026, 6, 30, tzinfo=UTC)] * len(predictions), dtype=object)
    yield "datetime as_of_date", predictions.assign(as_of_date=datetimes), "datetime.date"


def test_write_partition_validates_before_touching_disk(tmp_path):
    predictions = make_predictions()
    fresh = tmp_path / "never-created"
    for label, bad, match in _mutations(predictions):
        with pytest.raises(ValueError, match=match):
            write_partition(bad, fresh, RUN_DATE)
        assert not fresh.exists(), label  # every check precedes mkdir

    good = write_partition(predictions, tmp_path / "existing", RUN_DATE)
    before = _sha(good)
    for label, bad, match in _mutations(predictions):
        with pytest.raises(ValueError, match=match):
            write_partition(bad, tmp_path / "existing", RUN_DATE)
        assert _sha(good) == before, label
        assert [p.name for p in good.parent.iterdir()] == [PARTITION_FILENAME], label  # no temp file left behind


def test_failed_write_leaves_previous_partition_intact(tmp_path, monkeypatch):
    path = write_partition(make_predictions(model_run_id="run-one"), tmp_path, RUN_DATE)
    before = _sha(path)

    seen = []

    def failing_write_table(table, where, **kwargs):
        seen.append(Path(where))
        Path(where).write_bytes(b"PAR1 partial bytes")  # so the temp file really exists when the failure happens
        raise OSError("disk full")

    monkeypatch.setattr(store.pq, "write_table", failing_write_table)
    with pytest.raises(OSError, match="disk full"):
        write_partition(make_predictions(model_run_id="run-two"), tmp_path, RUN_DATE)
    assert _sha(path) == before
    assert [p.name for p in path.parent.iterdir()] == [PARTITION_FILENAME]  # the dot-prefixed tmp file was removed
    assert partition_dir(tmp_path, RUN_DATE) == path.parent
    # the temp file is dot-prefixed (pyarrow's dataset discovery ignores those; a plain .tmp breaks pd.read_parquet(<dir>))
    # and lives beside the final file, so os.replace is an atomic same-directory rename
    assert len(seen) == 1 and seen[0].name.startswith(".") and seen[0].parent == path.parent


def test_overlapping_runs_for_one_run_date_each_replace_with_a_complete_file(tmp_path, monkeypatch):
    """An inner run for the same run date completes between the outer run's write and its rename (a cron run overlapping a
    manual one, an orchestrator retry). With one fixed temp name the inner run would overwrite the outer's file and the outer's
    os.replace would then fail; with a unique temp name per writer both are complete and the last replace wins."""
    outer = make_predictions(model_run_id="outer")
    inner = make_predictions(ids=("x", "y", "z"), scores=(0.3, 0.2, 0.1), top_n=1, model_run_id="inner")
    real_replace = store.os.replace
    state = {"raced": False}

    def racing_replace(src, dst):
        if not state["raced"]:
            state["raced"] = True
            write_partition(inner, tmp_path, RUN_DATE)  # the other run finishes first, on the same partition
        real_replace(src, dst)

    monkeypatch.setattr(store.os, "replace", racing_replace)
    path = write_partition(outer, tmp_path, RUN_DATE)  # must not raise
    pd.testing.assert_frame_equal(read_partition(tmp_path, RUN_DATE), outer)  # the last replace wins, byte-complete
    assert [p.name for p in path.parent.iterdir()] == [PARTITION_FILENAME]


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_partition_file_gets_umask_permissions_not_owner_only(tmp_path):
    """A temp file made by tempfile.mkstemp is 0600 and the rename would carry that onto the partition, hiding it from a
    dashboard or scheduler running as another user."""
    previous = os.umask(0o022)
    try:
        path = write_partition(make_predictions(), tmp_path, RUN_DATE)
    finally:
        os.umask(previous)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644

