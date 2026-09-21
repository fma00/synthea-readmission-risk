"""Shared test helpers for the scoring slice (plain module, not conftest.py, which holds fixtures only).
See notes/eg-new-feature/batch-scoring-cli-2026-09-20.md (Verification criteria > Test infrastructure).
"""

from __future__ import annotations

import re
from datetime import date

import pandas as pd

from readmission_risk.scoring.ranking import build_predictions


def ts(*values: str) -> pd.DatetimeIndex:
    """UTC timestamps from ISO-8601 strings of mixed precision (a plain pd.to_datetime rejects mixed precision)."""
    return pd.to_datetime(list(values), utc=True, format="ISO8601")


def assert_count_phrase(message: str, n: int, phrase: str) -> None:
    """Asserts `message` contains f"{n} {phrase}" and that the count is not merely the tail of a longer number
    (\"3 in-sample\" must not be satisfied by \"13 in-sample\")."""
    assert re.search(rf"(?<![0-9]){n} {re.escape(phrase)}", message), f"{n} {phrase!r} not found in: {message}"


def make_batch(ids, *, stops=None, reasons=None, descriptions=None) -> pd.DataFrame:
    """A label-free batch with the columns build_predictions needs. Row i has patient p-<id>, a distinct discharge time
    (one day apart, from 2026-03-01), reason code R<i> and description 'reason <i>' unless overridden."""
    n = len(ids)
    stops = stops if stops is not None else ts(*[f"2026-03-{i + 1:02d}T12:00:00Z" for i in range(n)])
    return pd.DataFrame(
        {
            "patient_id": [f"p-{i}" for i in ids],
            "encounter_id": list(ids),
            "index_stop": stops,
            "admission_reason_code": reasons if reasons is not None else [f"R{i}" for i in range(n)],
            "admission_reason_description": descriptions if descriptions is not None else [f"reason {i}" for i in range(n)],
        }
    )


PREDICTION_KWARGS = {
    "run_date": date(2026, 6, 30),
    "top_n": 3,
    "window_days": 180,
    "model_run_id": "run-abc",
    "model_name": "logistic_regression",
    "label_definition": "unplanned_readmission_v1",
    "gold_fingerprint": "f" * 64,
    "gold_reference_date": "20260916",
}


def make_predictions(ids=("e", "c", "a", "b", "d"), scores=(0.2, 0.5, 0.5, 0.5, 0.1), **overrides) -> pd.DataFrame:
    """build_predictions on the tie fixture (input order e, c, a, b, d) with PREDICTION_KWARGS overridden by `overrides`."""
    kwargs = {**PREDICTION_KWARGS, **overrides}
    return build_predictions(make_batch(list(ids)), list(scores), **kwargs)


# ---------------------------------------------------------------------------------------------------------------
# Real-store fixtures: one trained store per test session, plus cloned (tampered) runs.
# ---------------------------------------------------------------------------------------------------------------
import json
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import mlflow
import mlflow.sklearn
import numpy as np
from mlflow.models import infer_signature
from mlflow.tracking import MlflowClient

from readmission_risk.models.data import (
    FEATURE_COLUMNS,
    prepare_features,
)
from readmission_risk.models.tracking import (
    load_logged_model,
    mlflow_tracking_uri,
)
from readmission_risk.models.training import (
    TrainingConfig,
    train_and_evaluate,
)
from tests.models.helpers import (
    REFERENCE_DATE_STR,
    TEST_START_STR,
    make_gold_frame,
    write_test_gold_metadata,
)

SCORING_EXPERIMENT = "scoring-test"
DEFAULT_RUN_DATE = "20260630"
DEFAULT_WINDOW_DAYS = 180


def write_gold_dir(directory: Path, df: pd.DataFrame, *, n_parts: int = 2, shuffle_seed: int | None = None, **metadata_overrides) -> Path:
    """The gold frame as `n_parts` Parquet part files (rows optionally shuffled first) plus a valid _gold_metadata.json."""
    directory.mkdir(parents=True)
    frame = df if shuffle_seed is None else df.sample(frac=1, random_state=shuffle_seed)
    bounds = np.linspace(0, len(frame), n_parts + 1).astype(int)
    for i in range(n_parts):
        frame.iloc[bounds[i] : bounds[i + 1]].to_parquet(directory / f"part-{i}.parquet", index=False)
    write_test_gold_metadata(directory, df, **metadata_overrides)
    return directory


@dataclass(frozen=True, eq=False)
class ScoringEnv:
    gold_dir: Path
    tracking_dir: Path
    experiment_name: str
    lr_run_id: str
    xgb_run_id: str
    df: pd.DataFrame  # the fixture gold frame (with the label)
    n_train: int  # from the LR run's params split_n_train / split_n_test, never hard-coded
    n_test: int

    def client(self) -> MlflowClient:
        return MlflowClient(tracking_uri=mlflow_tracking_uri(self.tracking_dir))

    def assignment_csv(self, run_id: str | None = None) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.client().download_artifacts(run_id or self.lr_run_id, "split_assignment.csv", tmp)
            return Path(path).read_text()

    def assignment(self, run_id: str | None = None) -> pd.Series:
        frame = pd.read_csv(tempfile_from_text(self.assignment_csv(run_id)), dtype=str, keep_default_na=False)
        return pd.Series(frame["partition"].to_numpy(dtype=object), index=pd.Index(frame["encounter_id"], dtype=object))

    def expected_batch(self, run_date: str, window_days: int, run_id: str | None = None) -> pd.DataFrame:
        """The independent boolean-mask batch: in-window AND in the run's test partition, straight from the fixture frame."""
        day = pd.Timestamp(f"{run_date[:4]}-{run_date[4:6]}-{run_date[6:]}", tz="UTC")
        end, start = day + pd.Timedelta(days=1), day - pd.Timedelta(days=window_days - 1)
        in_window = (self.df["index_stop"] >= start) & (self.df["index_stop"] < end)
        is_test = self.df["encounter_id"].map(self.assignment(run_id)) == "test"
        return self.df.loc[in_window & is_test].reset_index(drop=True)


def tempfile_from_text(text: str):
    import io

    return io.StringIO(text)


def build_scoring_env(root: Path) -> ScoringEnv:
    """Trains both models once on make_gold_frame(300, seed=1) (652 rows; split 165 train / 310 test / 177 in neither)."""
    original_uri = mlflow.get_tracking_uri()
    try:
        df = make_gold_frame(300, seed=1)
        gold_dir = write_gold_dir(root / "gold", df)
        tracking_dir = root / "tracking"
        result = train_and_evaluate(
            TrainingConfig(
                gold_dir=gold_dir,
                reference_date=REFERENCE_DATE_STR,
                test_start_date=TEST_START_STR,
                tracking_dir=tracking_dir,
                experiment_name=SCORING_EXPERIMENT,
                n_bootstrap=0,
            )
        )
    finally:
        mlflow.set_tracking_uri(original_uri)
    lr, xgb = result.models["logistic_regression"], result.models["xgboost_calibrated"]
    return ScoringEnv(
        gold_dir=gold_dir,
        tracking_dir=tracking_dir,
        experiment_name=SCORING_EXPERIMENT,
        lr_run_id=lr.run_id,
        xgb_run_id=xgb.run_id,
        df=df,
        n_train=int(result.split_summary["n_train"]),
        n_test=int(result.split_summary["n_test"]),
    )


def clone_run(
    tracking_dir: Path,
    source_run_id: str,
    *,
    experiment: str,
    gold_frame: pd.DataFrame,
    tags: dict[str, str] | None = None,
    params: dict[str, str | None] | None = None,
    features_json: dict | str | None = None,
    split_assignment_csv: str | None = None,
    signature_columns: Sequence[str] | None = None,
    no_signature: bool = False,
    status: str = "FINISHED",
    drop_tags: Sequence[str] = (),
    drop_artifacts: Sequence[str] = (),
) -> str:
    """A tampered copy of a trained run, in `experiment` of the same store (created with an explicit artifact location under
    the temp tracking dir: a SQLite store otherwise defaults to ./mlruns under the CWD). Copies the source's params and
    non-mlflow.* tags, re-logs the same fitted model under its own run, sets its OWN model_uri tag, and only THEN applies the
    caller's `tags` / `params` overrides (so a caller-supplied model_uri wins) and `drop_tags`. Every override is tested
    with `is not None`, never truthiness. MLflow params cannot be edited after the fact, which is why tampering uses clones;
    `params={"x": None}` means "do not copy x"."""
    tracking_dir = Path(tracking_dir)
    original_uri = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(mlflow_tracking_uri(tracking_dir))
    try:
        client = MlflowClient()
        source = client.get_run(source_run_id)
        existing = client.get_experiment_by_name(experiment)
        experiment_id = (
            existing.experiment_id
            if existing is not None
            else client.create_experiment(experiment, artifact_location=(tracking_dir.resolve() / "artifacts").as_uri())
        )
        model = load_logged_model(tracking_dir, source_run_id)
        with tempfile.TemporaryDirectory() as tmp:
            source_features = Path(client.download_artifacts(source_run_id, "feature_columns.json", tmp)).read_text()
            source_assignment = Path(client.download_artifacts(source_run_id, "split_assignment.csv", tmp)).read_text()

        columns = list(FEATURE_COLUMNS) if signature_columns is None else list(signature_columns)
        signature = None if no_signature else infer_signature(prepare_features(gold_frame)[columns])

        new_params = dict(source.data.params)
        for key, value in (params or {}).items():
            if value is None:
                new_params.pop(key, None)
            else:
                new_params[key] = value
        run = mlflow.start_run(experiment_id=experiment_id)
        try:
            mlflow.log_params(new_params)
            mlflow.set_tags({k: v for k, v in source.data.tags.items() if not k.startswith("mlflow.") and k != "model_uri"})
            if "feature_columns.json" not in drop_artifacts:
                text = source_features if features_json is None else (features_json if isinstance(features_json, str) else json.dumps(features_json))
                mlflow.log_text(text, "feature_columns.json")
            if "split_assignment.csv" not in drop_artifacts:
                mlflow.log_text(source_assignment if split_assignment_csv is None else split_assignment_csv, "split_assignment.csv")
            info = mlflow.sklearn.log_model(
                model, name="model", signature=signature, serialization_format=mlflow.sklearn.SERIALIZATION_FORMAT_CLOUDPICKLE
            )
            mlflow.set_tag("model_uri", info.model_uri)  # the clone's own tag ...
            for key, value in (tags or {}).items():  # ... which a caller-supplied tag then overrides
                mlflow.set_tag(key, value)
            for key in drop_tags:
                client.delete_tag(run.info.run_id, key)
        finally:
            mlflow.end_run(status=status)
        return run.info.run_id
    finally:
        mlflow.set_tracking_uri(original_uri)


def forbid_model_loading(monkeypatch) -> None:
    """Makes every route that would deserialize a logged model raise AssertionError. Apply it only around a call that must
    never unpickle (the clone construction in a GuardCase's `build` legitimately loads a model)."""
    import cloudpickle
    import mlflow.pyfunc

    def refuse(*args, **kwargs):
        raise AssertionError("a model was deserialized before every guard had passed")

    for owner, name in ((mlflow.sklearn, "load_model"), (mlflow.pyfunc, "load_model"), (cloudpickle, "load"), (cloudpickle, "loads")):
        monkeypatch.setattr(owner, name, refuse)


# ---------------------------------------------------------------------------------------------------------------
# GUARD_CASES: one per defect, each breaking exactly one guard of runs.validate_run (38 in total).
# ---------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class GuardCase:
    case_id: str
    guard_key: str
    build: Callable[[ScoringEnv, str], tuple[str, tuple[str, ...]]]  # (env, experiment) -> (run_id_to_pass, extra_substrings)
    forbidden: tuple[str, ...] = ()


def _clone(env: ScoringEnv, experiment: str, **kwargs) -> str:
    return clone_run(env.tracking_dir, env.lr_run_id, experiment=experiment, gold_frame=env.df, **kwargs)


def _simple(**kwargs):
    return lambda env, experiment: (_clone(env, experiment, **kwargs), ())


def _deleted(env: ScoringEnv, experiment: str):
    run_id = _clone(env, experiment)
    env.client().delete_run(run_id)
    return run_id, ()


def split_violation_sentence(env: ScoringEnv, assignment: pd.DataFrame) -> tuple[tuple[int, int, int], str]:
    """The three sub-rule counts of guard 12 for `assignment`, recomputed with plain pandas independently of the
    implementation, and the message sentence they must appear in."""
    t = pd.Timestamp(TEST_START_STR[:4] + "-" + TEST_START_STR[4:6] + "-" + TEST_START_STR[6:], tz="UTC")
    merged = assignment.merge(env.df[["encounter_id", "patient_id", "index_start"]], on="encounter_id")
    test, train = merged[merged["partition"] == "test"], merged[merged["partition"] == "train"]
    triple = (
        int((test["index_start"] < t).sum()),
        int((train["index_start"] >= t).sum()),
        len(set(test["patient_id"]) & set(train["patient_id"])),
    )
    sentence = (
        f"{triple[0]} test row(s) start before test_start_date, {triple[1]} train row(s) start on/after it, "
        f"{triple[2]} patient(s) appear in both partitions"
    )
    return triple, sentence


def _relabel_case(kind: str):
    def build(env: ScoringEnv, experiment: str):
        frame = pd.read_csv(tempfile_from_text(env.assignment_csv()), dtype=str, keep_default_na=False)
        by_id = env.df.set_index("encounter_id")
        frame["patient_id"] = frame["encounter_id"].map(by_id["patient_id"])
        if kind == "train_to_test":  # the FIRST train row (by encounter_id) whose patient has >= 2 train rows
            train = frame[frame["partition"] == "train"].sort_values("encounter_id")
            counts = train.groupby("patient_id")["encounter_id"].transform("size")
            target = train[counts >= 2].iloc[0]["encounter_id"]
            frame.loc[frame["encounter_id"] == target, "partition"] = "test"
        elif kind == "test_to_train":  # the first test row whose patient has >= 2 test rows
            test = frame[frame["partition"] == "test"].sort_values("encounter_id")
            counts = test.groupby("patient_id")["encounter_id"].transform("size")
            target = test[counts >= 2].iloc[0]["encounter_id"]
            frame.loc[frame["encounter_id"] == target, "partition"] = "train"
        else:  # a gold row in neither partition, of a patient who has a test row, starting before T, added as train
            test_patients = set(frame.loc[frame["partition"] == "test", "patient_id"])
            t = pd.Timestamp("2023-01-01", tz="UTC")
            dropped = env.df[~env.df["encounter_id"].isin(frame["encounter_id"])]
            dropped = dropped[dropped["patient_id"].isin(test_patients) & (dropped["index_start"] < t)].sort_values("encounter_id")
            assert len(dropped), "fixture precondition: a dropped row of a test patient starting before T must exist"
            frame = pd.concat([frame, pd.DataFrame({"encounter_id": [dropped.iloc[0]["encounter_id"]], "partition": ["train"], "patient_id": [dropped.iloc[0]["patient_id"]]})])
        modified = frame[["encounter_id", "partition"]]
        triple, sentence = split_violation_sentence(env, modified)
        expected_pattern = {"train_to_test": triple[0] == 1 and triple[1] == 0, "test_to_train": triple[1] == 1 and triple[0] == 0, "dropped_to_train": triple[:2] == (0, 0) and triple[2] >= 1}[kind]
        assert expected_pattern, f"fixture precondition: unexpected violation triple {triple} for {kind}"
        return _clone(env, experiment, split_assignment_csv=modified.to_csv(index=False)), (sentence,)

    return build


def _csv_case(transform: Callable[[str], str]):
    return lambda env, experiment: (_clone(env, experiment, split_assignment_csv=transform(env.assignment_csv())), ())


def _bogus(env: ScoringEnv, experiment: str):
    return "0" * 32, ()


def _source_model_uri(env: ScoringEnv, experiment: str):
    uri = env.client().get_run(env.lr_run_id).data.tags["model_uri"]
    return _clone(env, experiment, tags={"model_uri": uri}), ()


_REV = list(reversed(FEATURE_COLUMNS))
GUARD_CASES: list[GuardCase] = [
    GuardCase("1-bogus-run-id", "run not found", _bogus),
    GuardCase("2-status-failed", "not FINISHED", _simple(status="FAILED")),
    GuardCase("2-soft-deleted", "deleted", _deleted),
    GuardCase("3-no-model-uri", "model_uri", _simple(drop_tags=("model_uri",))),
    GuardCase("4-unknown-model-name", "model_name", _simple(tags={"model_name": "random_forest"})),
    GuardCase("4-no-model-name", "model_name", _simple(drop_tags=("model_name",))),
    GuardCase("5-other-label-definition", "gold_label_definition", _simple(tags={"gold_label_definition": "all_cause_v0"})),
    GuardCase("5-no-label-definition", "gold_label_definition", _simple(drop_tags=("gold_label_definition",))),
    GuardCase("6-wrong-fingerprint", "gold_fingerprint", _simple(tags={"gold_fingerprint": "0" * 64})),
    GuardCase("6-no-fingerprint", "gold_fingerprint", _simple(drop_tags=("gold_fingerprint",))),
    GuardCase("7-reference-date-mismatch", "reference_date", _simple(params={"reference_date": "20260917"}), ("readmission_window_days",)),
    GuardCase("7-window-mismatch", "readmission_window_days", _simple(params={"readmission_window_days": "14"}), ("reference_date",)),
    GuardCase("7-no-reference-date", "reference_date", _simple(params={"reference_date": None}), ("readmission_window_days",)),
    GuardCase("7-no-window", "readmission_window_days", _simple(params={"readmission_window_days": None}), ("reference_date",)),
    GuardCase("8-old-sklearn", "library version", _simple(tags={"sklearn_version": "0.0.0"})),
    GuardCase("8-no-numpy-tag", "library version", _simple(drop_tags=("numpy_version",))),
    GuardCase("9-features-reversed", "feature_columns.json", _simple(features_json={"features": _REV})),
    GuardCase("9-features-extra", "feature_columns.json", _simple(features_json={"features": [*FEATURE_COLUMNS, "extra"]})),
    GuardCase("9-no-features-key", "feature_columns.json", _simple(features_json='{"nofeatures": 1}')),
    GuardCase("9-not-json", "feature_columns.json", _simple(features_json="not json")),
    GuardCase("9-features-not-a-list", "feature_columns.json", _simple(features_json='{"features": "abc"}')),
    GuardCase("9-no-artifact", "feature_columns.json", _simple(drop_artifacts=("feature_columns.json",))),
    GuardCase("10-signature-reversed", "signature", _simple(signature_columns=_REV)),
    GuardCase("10-no-signature", "signature", _simple(no_signature=True)),
    GuardCase("10-bad-model-uri", "signature", _simple(tags={"model_uri": "models:/m-doesnotexist"})),
    GuardCase("11-wrong-header", "split_assignment.csv", _csv_case(lambda t: t.replace("encounter_id,partition", "id,partition", 1))),
    GuardCase("11-duplicate-id", "split_assignment.csv", _csv_case(lambda t: t + t.splitlines()[1] + "\n")),
    GuardCase("11-unknown-partition", "split_assignment.csv", _csv_case(lambda t: t.replace(",test", ",validation", 1))),
    GuardCase("11-no-test-row", "split_assignment.csv", _csv_case(lambda t: t.replace(",test", ",train"))),
    GuardCase("11-id-not-in-gold", "split_assignment.csv", _csv_case(lambda t: t + "not-in-gold,test\n")),
    GuardCase("11-empty-file", "split_assignment.csv", _csv_case(lambda t: "")),
    GuardCase("11-no-artifact", "split_assignment.csv", _simple(drop_artifacts=("split_assignment.csv",))),
    GuardCase("12-train-relabelled-test", "split consistency", _relabel_case("train_to_test")),
    GuardCase("12-test-relabelled-train", "split consistency", _relabel_case("test_to_train")),
    GuardCase("12-dropped-added-as-train", "split consistency", _relabel_case("dropped_to_train")),
    GuardCase("12-no-test-start-date", "split consistency", _simple(params={"test_start_date": None})),
    GuardCase("12-bad-test-start-date", "split consistency", _simple(params={"test_start_date": "not-a-date"})),
    GuardCase("13-model-of-another-run", "model provenance", _source_model_uri, ("signature",)),
]


# ---------------------------------------------------------------------------------------------------------------
# A hand-built ScoringResult (report and CLI tests)
# ---------------------------------------------------------------------------------------------------------------
from readmission_risk.scoring.pipeline import ScoringResult
from readmission_risk.scoring.runs import ModelRun

_IDS = [f"enc-000{i}" for i in range(1, 6)]
_SCORES = [0.4, 0.2, 0.5, 0.1, 0.3]  # rank order: enc-0003, enc-0001, enc-0005, enc-0002, enc-0004
_DESCRIPTIONS = [None, "other", "d" * 48 + "TAILTAIL", "other", "e" * 48]  # rank 2 null; rank 1 is 56 chars; rank 3 is 48


def make_result(*, git_commit="0123456789abcdef0123456789abcdef01234567", git_dirty="clean", observed=None) -> ScoringResult:
    batch = make_batch(_IDS, descriptions=_DESCRIPTIONS)
    predictions = build_predictions(batch, _SCORES, **{**PREDICTION_KWARGS, "top_n": 3, "window_days": 3, "run_date": date(2026, 3, 10)})
    run = ModelRun(
        run_id="run-abc",
        model_name="logistic_regression",
        test_start_date="20230101",
        gold_fingerprint="f" * 64,
        git_commit=git_commit,
        git_dirty=git_dirty,
        partition_of=pd.Series(dtype=object),
    )
    return ScoringResult(
        predictions=predictions,
        partition_path=Path("out/run_date=20260310/predictions.parquet"),
        run=run,
        window_start=pd.Timestamp("2026-03-08T00:00Z"),
        window_end=pd.Timestamp("2026-03-11T00:00Z"),
        window_days=3,
        observed=observed,
    )
