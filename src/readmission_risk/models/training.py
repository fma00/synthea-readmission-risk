"""Orchestration: split the gold table, fit the logistic-regression baseline and the calibrated XGBoost
challenger on the training window only, score the held-out test window once, and track everything in a
local SQLite-backed MLflow store. See notes/eg-new-feature/model-training-2026-09-19.md for the design.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
import warnings
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import mlflow.models
import mlflow.sklearn
import numpy as np
import pandas as pd
import sklearn
import xgboost
from mlflow.entities import Metric
from mlflow.tracking import MlflowClient

import mlflow
from readmission_risk.pipeline.gold_metadata import (
    GOLD_METADATA_FILENAME,
    parse_yyyymmdd,
)

from . import tracking
from .data import (
    EXCLUDED_COLUMN_REASONS,
    FEATURE_COLUMNS,
    GROUP_COLUMN,
    LABEL_COLUMN,
    gold_fingerprint,
    load_gold_table_with_metadata,
    prepare_features,
)
from .evaluate import (
    BOOTSTRAP_METRICS,
    EvaluationResult,
    clustered_bootstrap,
    evaluate_probabilities,
    plot_reliability_diagram,
)
from .modeling import (
    build_calibrated_xgboost,
    build_logistic_regression,
    make_grouped_cv_splits,
    validate_cv_splits,
)
from .split import chronological_group_split

MODEL_NAMES: tuple[str, ...] = ("logistic_regression", "xgboost_calibrated")
MIN_TEST_POSITIVES_FOR_RELIABLE_METRICS = 50
_BASELINE, _CHALLENGER = MODEL_NAMES
_CALIBRATION_METHODS = ("sigmoid", "isotonic")


@dataclass(frozen=True)
class TrainingConfig:
    gold_dir: Path
    reference_date: str  # "YYYYMMDD"; MUST equal the reference_date of the Synthea run + Big Join that produced gold_dir (checked against _gold_metadata.json)
    test_start_date: str  # "YYYYMMDD"; no default: a modeling decision the caller must make
    train_start_date: str | None = None
    tracking_dir: Path = Path("mlflow")  # holds mlflow.db (SQLite) and artifacts/
    experiment_name: str = "readmission-risk"
    readmission_window_days: int = 30  # MUST equal BigJoinConfig.readmission_window_days used for gold_dir (checked against _gold_metadata.json)
    seed: int = 42
    calibration_cv_folds: int = 5
    calibration_method: str = "sigmoid"
    n_bootstrap: int = 500


@dataclass(frozen=True)
class ModelResult:
    name: str
    run_id: str
    model_uri: str  # URI returned by mlflow.sklearn.log_model (also stored as the run tag "model_uri")
    test_metrics: dict[str, float]
    confidence_intervals: dict[str, tuple[float, float]]  # {} if n_bootstrap == 0
    calibration_curve: list[dict[str, float]]


@dataclass(frozen=True)
class PairedComparison:
    point: dict[str, float]  # metric -> metric(xgboost_calibrated) - metric(logistic_regression)
    intervals: dict[str, tuple[float, float]]  # paired 95% intervals of those differences; {} if n_bootstrap == 0


@dataclass(frozen=True)
class TrainingResult:
    split_summary: dict[str, int | float | str]
    models: dict[str, ModelResult]
    comparison: PairedComparison


def _validate_config(config: TrainingConfig) -> tuple[date, date, date | None]:
    reference_date = parse_yyyymmdd(config.reference_date, "reference_date")
    test_start_date = parse_yyyymmdd(config.test_start_date, "test_start_date")
    train_start_date = (
        parse_yyyymmdd(config.train_start_date, "train_start_date") if config.train_start_date is not None else None
    )
    if train_start_date is not None and not train_start_date < test_start_date:
        raise ValueError("train_start_date must be before test_start_date")
    if not test_start_date < reference_date:
        raise ValueError("test_start_date must be before reference_date")
    if config.calibration_method not in _CALIBRATION_METHODS:
        raise ValueError(f"calibration_method must be one of {_CALIBRATION_METHODS}; got {config.calibration_method!r}")
    if config.calibration_cv_folds < 2:
        raise ValueError(f"calibration_cv_folds must be >= 2; got {config.calibration_cv_folds}")
    if config.readmission_window_days < 1:
        raise ValueError(f"readmission_window_days must be >= 1; got {config.readmission_window_days}")
    if config.n_bootstrap < 0:
        raise ValueError(f"n_bootstrap must be >= 0; got {config.n_bootstrap}")
    if not 0 <= config.seed <= 2**32 - 1:  # the range scikit-learn/XGBoost/numpy accept; rejected HERE, before any side effect
        raise ValueError(f"seed must be in [0, 2**32 - 1]; got {config.seed}")
    return reference_date, test_start_date, train_start_date


def _git_dirty_state() -> str:
    """"clean" | "dirty" | "unknown" from `git status --porcelain` run in the directory this module lives in, i.e.
    the repository the code came from (not wherever the process happens to be launched: MLflow's own commit tag
    is derived from the running script's path, and the two must describe the same repo). "unknown" if that
    directory is not inside a git repository (e.g. a non-editable install). MLflow records the git commit itself
    but not whether the working tree matched it."""
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            cwd=Path(__file__).resolve().parent,
        )
    except OSError:
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    return "dirty" if completed.stdout.strip() else "clean"


def _metric(key: str, value: float) -> Metric:
    return Metric(key=key, value=float(value), timestamp=int(time.time() * 1000), step=0)


def _warn_if_experiment_mixes_labels(experiment_id: str, experiment_name: str, label_definition: str) -> None:
    """Runs logged before slice 2b (all-cause label) carry no `gold_label_definition` tag, and the default experiment name is
    unchanged, so a default-argument run would land beside incomparable runs. Warn -- never refuse: the tag makes the mix
    detectable, and reusing an experiment can be deliberate."""
    stale = [
        run
        for run in MlflowClient().search_runs([experiment_id], max_results=5000)
        if run.data.tags.get("gold_label_definition") != label_definition
    ]
    if stale:
        warnings.warn(
            f"MLflow experiment {experiment_name!r} already holds {len(stale)} run(s) whose gold_label_definition tag is "
            f"missing or differs from {label_definition!r} (e.g. runs from before slice 2b): their metrics are NOT comparable "
            "with this run's; use a fresh --experiment-name",
            UserWarning,
            stacklevel=3,
        )


def train_and_evaluate(config: TrainingConfig) -> TrainingResult:
    # 1. validate config
    reference_date, test_start_date, train_start_date = _validate_config(config)

    # 2. load
    df, metadata = load_gold_table_with_metadata(config.gold_dir)
    # reference_date and readmission_window_days are checked for EXACT equality against the Big Join's own record of them
    # (_gold_metadata.json), which closes the too-late-reference_date gap: it used to be undetectable (there is deliberately
    # no check against max(index_stop) -- slice 2 keeps censored positives whose discharge can fall AFTER the reference date).
    # chronological_group_split's >1%-buffer-drop warning stays as a second line of defence against a too-early date.
    if metadata.reference_date != config.reference_date:
        raise ValueError(
            f"reference_date {config.reference_date} does not match the gold table's own reference_date "
            f"{metadata.reference_date} (see {Path(config.gold_dir) / GOLD_METADATA_FILENAME})"
        )
    if metadata.readmission_window_days != config.readmission_window_days:
        raise ValueError(
            f"readmission_window_days {config.readmission_window_days} does not match the gold table's own "
            f"readmission_window_days {metadata.readmission_window_days} (see {Path(config.gold_dir) / GOLD_METADATA_FILENAME})"
        )

    # 3. split + arrays (all pure, before any MLflow side effect)
    split = chronological_group_split(
        df,
        reference_date=reference_date,
        test_start_date=test_start_date,
        readmission_window_days=config.readmission_window_days,
        train_start_date=train_start_date,
    )
    X_train = prepare_features(split.train)
    y_train = split.train[LABEL_COLUMN].to_numpy()
    groups_train = split.train[GROUP_COLUMN].to_numpy()
    X_test = prepare_features(split.test)
    y_test = split.test[LABEL_COLUMN].to_numpy()
    groups_test = split.test[GROUP_COLUMN].to_numpy()
    train_prevalence = float(y_train.mean())
    if split.summary["n_test_positive"] < MIN_TEST_POSITIVES_FOR_RELIABLE_METRICS:
        warnings.warn(
            f"Only {split.summary['n_test_positive']} positive rows in the test window "
            f"(< {MIN_TEST_POSITIVES_FOR_RELIABLE_METRICS}): metrics and intervals are unreliable",
            UserWarning,
            stacklevel=2,
        )
    fingerprint = gold_fingerprint(df)  # computed once, before any side effect

    # 4. PURE preparation -- before ANY MLflow side effect, so a user-correctable failure here leaves no
    # empty experiment, tracking directory or enabled autolog behind
    cv_splits = make_grouped_cv_splits(y_train, groups_train, config.calibration_cv_folds)
    validate_cv_splits(cv_splits, groups_train)
    models = {
        _BASELINE: build_logistic_regression(config.seed),
        _CHALLENGER: build_calibrated_xgboost(config.seed, cv_splits, config.calibration_method),
    }

    # 5. MLflow setup (only after step 4 succeeded)
    tracking_dir = Path(config.tracking_dir)
    tracking_dir.mkdir(parents=True, exist_ok=True)
    abs_dir = tracking_dir.resolve()
    mlflow.set_tracking_uri(tracking.mlflow_tracking_uri(tracking_dir))  # explicit, never the default
    existing = mlflow.get_experiment_by_name(config.experiment_name)
    if existing is not None and existing.lifecycle_stage == "deleted":
        raise ValueError(
            f"MLflow experiment {config.experiment_name!r} exists in a deleted state in {abs_dir}/mlflow.db; "
            "restore it or pass a different experiment name"
        )
    if existing is None:
        mlflow.create_experiment(config.experiment_name, artifact_location=(abs_dir / "artifacts").as_uri())
    mlflow.set_experiment(config.experiment_name)
    if existing is not None:
        _warn_if_experiment_mixes_labels(existing.experiment_id, config.experiment_name, metadata.label_definition)

    records: dict[str, dict] = {}
    # autolog is process-global: leaving it on would patch every later sklearn .fit() in the process. The try
    # opens immediately BEFORE the autolog call so a partial patch followed by an exception is cleaned up too.
    try:
        mlflow.autolog(log_models=False, log_datasets=False, exclude_flavors=["spark", "pyspark.ml"])

        # 6. PHASE A -- one top-level run per model
        for name in MODEL_NAMES:
            model = models[name]
            with mlflow.start_run(run_name=name) as run:
                params: dict[str, object] = {
                    "seed": config.seed,
                    "reference_date": config.reference_date,
                    "test_start_date": config.test_start_date,
                    "train_start_date": config.train_start_date if config.train_start_date is not None else "none",
                    "readmission_window_days": config.readmission_window_days,
                    "gold_lookback_years": metadata.lookback_years,
                    "n_bootstrap": config.n_bootstrap,
                }
                params.update({f"split_{k}": v for k, v in split.summary.items()})
                if name == _CHALLENGER:
                    params["calibration_method"] = config.calibration_method
                    params["calibration_cv_folds"] = config.calibration_cv_folds
                mlflow.log_params(params)
                mlflow.set_tags(
                    {
                        "model_name": name,
                        "gold_fingerprint": fingerprint,
                        "gold_label_definition": metadata.label_definition,
                        "git_dirty": _git_dirty_state(),
                        # flipped to "complete" after Phase B; a run left "pending" means Phase B failed
                        # after this run was logged, so its metrics have no intervals
                        "bootstrap_status": "skipped" if config.n_bootstrap == 0 else "pending",
                        "sklearn_version": sklearn.__version__,
                        "xgboost_version": xgboost.__version__,
                        "mlflow_version": mlflow.__version__,
                        "pandas_version": pd.__version__,
                        "numpy_version": np.__version__,
                    }
                )

                if name == _CHALLENGER:
                    validate_cv_splits(cv_splits, groups_train)  # guards the positional coupling right before fit
                model.fit(X_train, y_train)  # autolog logs training-set params/scoring here

                y_prob = model.predict_proba(X_test)[:, 1]
                result: EvaluationResult = evaluate_probabilities(
                    y_test, y_prob, train_prevalence=train_prevalence
                )
                mlflow.log_metrics({f"test_{k}": float(v) for k, v in result.metrics.items()})

                mlflow.log_dict({"calibration_curve": result.calibration_curve}, "calibration_curve.json")
                mlflow.log_dict(dict(split.summary), "split_summary.json")
                mlflow.log_dict(asdict(metadata), "gold_metadata.json")
                mlflow.log_dict(
                    {"features": list(FEATURE_COLUMNS), "excluded": dict(EXCLUDED_COLUMN_REASONS)},
                    "feature_columns.json",
                )
                assignment = pd.DataFrame(
                    {
                        "encounter_id": [*split.train["encounter_id"], *split.test["encounter_id"]],
                        "partition": ["train"] * len(split.train) + ["test"] * len(split.test),
                    }
                )
                mlflow.log_text(assignment.to_csv(index=False), "split_assignment.csv")
                with tempfile.TemporaryDirectory() as tmp:
                    png = Path(tmp) / "reliability_diagram.png"
                    plot_reliability_diagram(result.calibration_curve, f"Reliability: {name}", png)
                    mlflow.log_artifact(str(png))

                # Explicit (not autolog-driven) so slice 4's load contract does not depend on how a given MLflow
                # version autologs a meta-estimator; cloudpickle because the default skops format rejects the
                # calibrated XGBoost model (UntrustedTypesFoundException).
                info = mlflow.sklearn.log_model(
                    model,
                    name="model",
                    signature=mlflow.models.infer_signature(X_train),
                    serialization_format=mlflow.sklearn.SERIALIZATION_FORMAT_CLOUDPICKLE,
                )
                mlflow.set_tag("model_uri", info.model_uri)

            records[name] = {
                "run_id": run.info.run_id,
                "model_uri": info.model_uri,
                "y_prob": y_prob,
                "result": result,
            }

        # 7. PHASE B -- one shared patient-clustered bootstrap over both models' test predictions
        boot = clustered_bootstrap(
            y_test,
            {name: records[name]["y_prob"] for name in MODEL_NAMES},
            groups_test,
            n_bootstrap=config.n_bootstrap,
            seed=config.seed,
        )
        client = MlflowClient()
        paired_key = f"{_CHALLENGER}_minus_{_BASELINE}"
        paired_intervals = boot.paired_differences.get(paired_key, {})
        point_diff = {
            m: float(records[_CHALLENGER]["result"].metrics[m] - records[_BASELINE]["result"].metrics[m])
            for m in BOOTSTRAP_METRICS
        }
        for name in MODEL_NAMES:
            batch: list[Metric] = []
            if config.n_bootstrap > 0:
                for m, (lo, hi) in boot.intervals[name].items():
                    batch += [_metric(f"test_{m}_ci_lower", lo), _metric(f"test_{m}_ci_upper", hi)]
                batch.append(_metric("test_bootstrap_resamples_used", boot.n_resamples_used))
            if name == _CHALLENGER:
                for m in BOOTSTRAP_METRICS:
                    batch.append(_metric(f"test_diff_vs_{_BASELINE}_{m}", point_diff[m]))
                    if m in paired_intervals:
                        lo, hi = paired_intervals[m]
                        batch += [
                            _metric(f"test_diff_vs_{_BASELINE}_{m}_ci_lower", lo),
                            _metric(f"test_diff_vs_{_BASELINE}_{m}_ci_upper", hi),
                        ]
            if batch:
                client.log_batch(records[name]["run_id"], metrics=batch)
            if config.n_bootstrap > 0:
                client.log_dict(
                    records[name]["run_id"],
                    {
                        "n_bootstrap": int(config.n_bootstrap),
                        "seed": int(config.seed),
                        "n_resamples_used": int(boot.n_resamples_used),
                        "n_resamples_skipped": int(boot.n_resamples_skipped),
                        "intervals": {m: list(v) for m, v in boot.intervals[name].items()},
                        "paired_differences": {
                            k: {m: list(v) for m, v in d.items()} for k, d in boot.paired_differences.items()
                        },
                    },
                    "bootstrap_summary.json",
                )
                client.set_tag(records[name]["run_id"], "bootstrap_status", "complete")
    finally:
        mlflow.autolog(disable=True)

    # ModelResult/TrainingResult are frozen, so they are built only after Phase B.
    model_results = {
        name: ModelResult(
            name=name,
            run_id=records[name]["run_id"],
            model_uri=records[name]["model_uri"],
            test_metrics=dict(records[name]["result"].metrics),
            confidence_intervals=dict(boot.intervals.get(name, {})),
            calibration_curve=records[name]["result"].calibration_curve,
        )
        for name in MODEL_NAMES
    }
    return TrainingResult(
        split_summary=dict(split.summary),
        models=model_results,
        comparison=PairedComparison(point=point_diff, intervals=dict(paired_intervals)),
    )
