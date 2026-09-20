"""Locating a logged model run and refusing every run/gold mismatch BEFORE the model is unpickled (a cloudpickle can
execute arbitrary code on load; the tracking store is trusted local input, as slice 3 documents). Reads MLflow, never
writes to it, and never loads a model. See notes/eg-new-feature/batch-scoring-cli-2026-09-20.md (Interfaces > runs.py).

Uniform rule for absent or malformed run metadata: anything absent or unparseable is a ValueError carrying the guard's
key, never a KeyError / MlflowException / JSON or pandas parse error (the CLI convention would turn those into
tracebacks instead of "ERROR:" and exit 1).
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

import mlflow.models
import numpy as np
import pandas as pd
import sklearn
import xgboost
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

import mlflow
from readmission_risk.models.data import FEATURE_COLUMNS, gold_fingerprint
from readmission_risk.models.tracking import mlflow_tracking_uri
from readmission_risk.models.training import MODEL_NAMES
from readmission_risk.pipeline.gold_metadata import GoldMetadata, parse_yyyymmdd

from .constants import PARTITION_TEST, PARTITION_TRAIN

_MAX_RUNS_SEARCHED = 5000  # the bound train_and_evaluate's own mixed-label warning uses


def installed_versions() -> dict[str, str]:
    """The five version tags train_and_evaluate records, with the versions installed now."""
    return {
        "sklearn_version": sklearn.__version__,
        "xgboost_version": xgboost.__version__,
        "mlflow_version": mlflow.__version__,
        "pandas_version": pd.__version__,
        "numpy_version": np.__version__,
    }


@dataclass(frozen=True, eq=False)  # eq=False: it holds a Series, so the generated __eq__/__hash__ would raise
class ModelRun:
    run_id: str
    model_name: str  # the run's model_name tag
    test_start_date: str  # the run's test_start_date param, YYYYMMDD (used by guard 12)
    gold_fingerprint: str  # the run's gold_fingerprint tag; == gold_fingerprint(gold) once guard 6 has passed
    git_commit: str | None  # tag mlflow.source.git.commit
    git_dirty: str | None  # tag git_dirty
    partition_of: pd.Series  # encounter_id -> "train" | "test" (object dtype, unique index), from split_assignment.csv


def _require_store(tracking_dir: Path) -> None:
    """Pointing MLflow's SQLite store at a missing path would create and migrate an empty database; a read-only scorer
    must not have that side effect."""
    db = Path(tracking_dir) / "mlflow.db"
    if not db.is_file():
        raise FileNotFoundError(f"No MLflow store at {db}: train models first (scripts/train_models.py) or fix --tracking-dir")


def resolve_run_id(tracking_dir: Path, experiment_name: str, model_name: str) -> str:
    """The run_id of the single FINISHED run in `experiment_name` whose tag model_name == model_name. ValueError if
    model_name is not in MODEL_NAMES; the experiment does not exist (the message lists the store's available experiment
    names) or is deleted; or the number of matching runs is 0 ('no FINISHED run') or > 1 (the message lists every run id and
    says to pass --run-id). Never 'latest wins': a result that depends on when you run it would break reproducibility."""
    if model_name not in MODEL_NAMES:
        raise ValueError(f"model name must be one of {MODEL_NAMES}; got {model_name!r}")
    _require_store(tracking_dir)
    mlflow.set_tracking_uri(mlflow_tracking_uri(tracking_dir))
    client = MlflowClient()
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment is None:
        available = sorted(e.name for e in client.search_experiments())
        raise ValueError(
            f"MLflow experiment {experiment_name!r} not found in {Path(tracking_dir) / 'mlflow.db'}; available: {available}"
        )
    if experiment.lifecycle_stage == "deleted":
        raise ValueError(f"MLflow experiment {experiment_name!r} is deleted")
    runs = client.search_runs(
        [experiment.experiment_id], filter_string=f"tags.model_name = '{model_name}'", max_results=_MAX_RUNS_SEARCHED
    )
    finished = sorted(r.info.run_id for r in runs if r.info.status == "FINISHED")
    if not finished:
        raise ValueError(f"no FINISHED run with model_name={model_name!r} in experiment {experiment_name!r}")
    if len(finished) > 1:
        raise ValueError(
            f"{len(finished)} FINISHED runs with model_name={model_name!r} in experiment {experiment_name!r} "
            f"({', '.join(finished)}); pass --run-id to choose one"
        )
    return finished[0]


def _download_artifact(client: MlflowClient, run_id: str, path: str, tmp: str, key: str) -> Path:
    try:
        return Path(client.download_artifacts(run_id, path, dst_path=tmp))
    except (MlflowException, OSError) as exc:
        raise ValueError(f"{key}: cannot read artifact {path!r} of MLflow run {run_id}: {exc}") from exc


def _require_tag(tags: dict[str, str], name: str, run_id: str) -> str:
    if name not in tags:
        raise ValueError(f"MLflow run {run_id} has no {name!r} tag")
    return tags[name]


def _read_assignment(path: Path, gold: pd.DataFrame, run_id: str) -> pd.Series:
    key = "split_assignment.csv"
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)  # keep_default_na: an id spelled "NA" stays a string
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"{key}: artifact of MLflow run {run_id} cannot be parsed: {exc}") from exc
    if list(frame.columns) != ["encounter_id", "partition"]:
        raise ValueError(f"{key}: columns must be exactly ['encounter_id', 'partition']; got {list(frame.columns)}")
    if frame["encounter_id"].duplicated().any():
        raise ValueError(f"{key}: encounter_id values are not unique")
    unknown = sorted(set(frame["partition"]) - {PARTITION_TRAIN, PARTITION_TEST})
    if unknown:
        raise ValueError(f"{key}: partition values must be 'train' or 'test'; found {unknown}")
    if not (frame["partition"] == PARTITION_TEST).any():
        raise ValueError(f"{key}: no 'test' row")
    absent = frame.loc[~frame["encounter_id"].isin(gold["encounter_id"]), "encounter_id"]
    if len(absent):
        raise ValueError(f"{key}: {len(absent)} encounter_id(s) are not in the gold table, e.g. {absent.iloc[0]!r}")
    return pd.Series(frame["partition"].to_numpy(dtype=object), index=pd.Index(frame["encounter_id"], dtype=object))


def _check_split_consistency(partition_of: pd.Series, test_start_date: str | None, gold: pd.DataFrame) -> None:
    key = "split consistency"
    try:
        t_date = parse_yyyymmdd(test_start_date, "test_start_date")
    except ValueError as exc:
        raise ValueError(f"{key}: the run's test_start_date param is absent or invalid: {exc}") from exc
    t = pd.Timestamp(t_date.isoformat(), tz="UTC")
    by_id = gold.set_index("encounter_id")
    starts = by_id["index_start"].reindex(partition_of.index)
    patients = by_id["patient_id"].reindex(partition_of.index)
    is_test = (partition_of == PARTITION_TEST).to_numpy()
    n_test_before = int(((starts < t).to_numpy() & is_test).sum())
    n_train_after = int(((starts >= t).to_numpy() & ~is_test).sum())
    n_patients_both = len(set(patients[is_test]) & set(patients[~is_test]))
    if n_test_before or n_train_after or n_patients_both:
        raise ValueError(
            f"{key}: split_assignment.csv disagrees with the run's test_start_date {test_start_date} and the gold table: "
            f"{n_test_before} test row(s) start before test_start_date, {n_train_after} train row(s) start on/after it, "
            f"{n_patients_both} patient(s) appear in both partitions"
        )


def validate_run(tracking_dir: Path, run_id: str, *, gold: pd.DataFrame, metadata: GoldMetadata) -> ModelRun:
    """Runs guards 1-13 in the documented order, raising ValueError (message contains the guard's key) on the first failure.
    Does NOT load the model. Sets the MLflow tracking URI (global, like load_logged_model). Writes nothing to the store; the
    two artifacts it needs are downloaded into a temporary directory that is deleted on return."""
    _require_store(tracking_dir)
    mlflow.set_tracking_uri(mlflow_tracking_uri(tracking_dir))
    client = MlflowClient()

    # 1-2
    try:
        run = client.get_run(run_id)
    except MlflowException as exc:
        raise ValueError(f"run not found: no MLflow run {run_id!r} in {Path(tracking_dir) / 'mlflow.db'} ({exc})") from exc
    if run.info.status != "FINISHED":
        raise ValueError(f"MLflow run {run_id} is not FINISHED (status {run.info.status})")
    if run.info.lifecycle_stage != "active":
        raise ValueError(f"MLflow run {run_id} is deleted")
    tags, params = run.data.tags, run.data.params

    # 3-4
    model_uri = _require_tag(tags, "model_uri", run_id)  # present only on runs written by train_and_evaluate
    model_name = _require_tag(tags, "model_name", run_id)
    if model_name not in MODEL_NAMES:
        raise ValueError(f"MLflow run {run_id} has model_name {model_name!r}; expected one of {MODEL_NAMES}")

    # 5
    if tags.get("gold_label_definition") is None:
        raise ValueError(
            f"MLflow run {run_id} has no 'gold_label_definition' tag: it predates slice 2b (all-cause label) and is not comparable"
        )
    if tags["gold_label_definition"] != metadata.label_definition:
        raise ValueError(
            f"MLflow run {run_id} was trained on gold_label_definition {tags['gold_label_definition']!r}, but the gold table's "
            f"label definition is {metadata.label_definition!r}"
        )

    # 6
    fingerprint = gold_fingerprint(gold)
    if tags.get("gold_fingerprint") != fingerprint:
        raise ValueError(
            f"MLflow run {run_id} has gold_fingerprint {tags.get('gold_fingerprint')!r}, but this gold table's is "
            f"{fingerprint!r}: the model was not trained on this table (or the table was edited)"
        )

    # 7 (each defect names only the offending param)
    if params.get("reference_date") != metadata.reference_date:
        raise ValueError(
            f"MLflow run {run_id} param 'reference_date' is {params.get('reference_date')!r}, but the gold table's is "
            f"{metadata.reference_date!r}"
        )
    if params.get("readmission_window_days") != str(metadata.readmission_window_days):
        raise ValueError(
            f"MLflow run {run_id} param 'readmission_window_days' is {params.get('readmission_window_days')!r}, but the gold "
            f"table's is {metadata.readmission_window_days!r}"
        )

    # 8
    mismatches = [
        f"{name}: run has {tags.get(name)!r}, installed {installed!r}"
        for name, installed in installed_versions().items()
        if tags.get(name) != installed
    ]
    if mismatches:
        raise ValueError(
            f"library version mismatch for MLflow run {run_id} (the model is a cloudpickle, valid only under the lockfile "
            f"it was trained with): {'; '.join(mismatches)}"
        )

    with tempfile.TemporaryDirectory() as tmp:
        # 9
        features_path = _download_artifact(client, run_id, "feature_columns.json", tmp, "feature_columns.json")
        try:
            features = json.loads(features_path.read_text(encoding="utf-8")).get("features")
        except (ValueError, AttributeError, OSError) as exc:  # not JSON, or not a JSON object
            raise ValueError(f"feature_columns.json of MLflow run {run_id} is not a JSON object: {exc}") from exc
        if not isinstance(features, list) or features != list(FEATURE_COLUMNS):
            raise ValueError(
                f"feature_columns.json of MLflow run {run_id} lists features {features!r}, expected {list(FEATURE_COLUMNS)} "
                "(the run was trained with a different feature contract)"
            )

        # 10
        try:
            info = mlflow.models.get_model_info(model_uri)
        except MlflowException as exc:
            raise ValueError(f"signature: cannot read the logged model {model_uri!r} of MLflow run {run_id}: {exc}") from exc
        signature = info.signature
        names = signature.inputs.input_names() if signature is not None and signature.inputs is not None else None
        if names != list(FEATURE_COLUMNS):
            raise ValueError(
                f"signature of the logged model of MLflow run {run_id} has input names {names!r}, expected {list(FEATURE_COLUMNS)}"
            )

        # 11
        assignment_path = _download_artifact(client, run_id, "split_assignment.csv", tmp, "split_assignment.csv")
        partition_of = _read_assignment(assignment_path, gold, run_id)

    # 12
    _check_split_consistency(partition_of, params.get("test_start_date"), gold)

    # 13
    if info.run_id != run_id:
        raise ValueError(
            f"model provenance: the model_uri tag of MLflow run {run_id} points at a model logged by run {info.run_id!r}"
        )

    return ModelRun(
        run_id=run_id,
        model_name=model_name,
        test_start_date=params["test_start_date"],
        gold_fingerprint=fingerprint,
        git_commit=tags.get("mlflow.source.git.commit"),
        git_dirty=tags.get("git_dirty"),
        partition_of=partition_of,
    )
