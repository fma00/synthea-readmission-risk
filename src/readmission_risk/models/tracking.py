"""The small, training-free surface for locating and loading a logged model (the bundle exporter and the README
reporting import it; since v2 slice 2 the scorer does not). Imports mlflow only -- no training code. See notes/eg-new-feature/model-training-2026-09-19.md
(Interfaces > tracking.py and the slice-4 model-artifact contract).
"""

from __future__ import annotations

from pathlib import Path

import mlflow
import mlflow.sklearn
from mlflow.tracking import MlflowClient

from .constants import MODEL_NAMES
from .versions import current_python_version

__all__ = ["current_python_version", "load_logged_model", "mlflow_tracking_uri", "resolve_run_id"]

_MAX_RUNS_SEARCHED = 5000  # the bound train_and_evaluate's own mixed-label warning uses


def mlflow_tracking_uri(tracking_dir: Path) -> str:
    """f"sqlite:///{tracking_dir.resolve()}/mlflow.db" (an absolute path after "sqlite:///" yields four
    slashes, the correct absolute-path form). The SINGLE definition of the tracking URI: train_and_evaluate
    uses it to set the URI, and any consumer must use it to read."""
    return f"sqlite:///{Path(tracking_dir).resolve()}/mlflow.db"


def load_logged_model(tracking_dir: Path, run_id: str):
    """Sets the tracking URI, reads the run's `model_uri` tag, and returns the fitted scikit-learn
    estimator, ready for .predict_proba(prepare_features(...)). The tag holds an MLflow-3 LoggedModel URI
    (models:/m-<id>) that resolves ONLY against the tracking store that produced it, so it cannot be loaded
    in a fresh process without first pointing MLflow at that store -- which is what this does."""
    mlflow.set_tracking_uri(mlflow_tracking_uri(tracking_dir))
    tags = MlflowClient().get_run(run_id).data.tags
    if "model_uri" not in tags:
        raise ValueError(f"MLflow run {run_id} has no 'model_uri' tag (was it produced by train_and_evaluate?)")
    return mlflow.sklearn.load_model(tags["model_uri"])


def _require_store(tracking_dir: Path) -> None:
    """Pointing MLflow's SQLite store at a missing path would create and migrate an empty database; a read-only reader
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
