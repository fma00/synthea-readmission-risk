"""The small, training-free surface for locating and loading a logged model (slice 4 imports this).
Imports mlflow only -- no training code. See notes/eg-new-feature/model-training-2026-09-19.md
(Interfaces > tracking.py and the slice-4 model-artifact contract).
"""

from __future__ import annotations

from pathlib import Path

import mlflow.sklearn
from mlflow.tracking import MlflowClient

import mlflow


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
