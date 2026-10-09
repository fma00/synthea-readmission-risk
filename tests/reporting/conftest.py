"""Shared fixtures for the reporting tests: synthetic curves, a throwaway MLflow store and a predictions frame."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import mlflow
import pandas as pd
import pytest
from mlflow.tracking import MlflowClient

from readmission_risk.models.tracking import mlflow_tracking_uri
from readmission_risk.reporting.assets import RunFacts

EXPERIMENT = "reporting-test"
LR_RUN = "a" * 32
XGB_RUN = "b" * 32


def make_curve(bins: list[tuple[float, int, int]]) -> list[dict[str, float]]:
    """bins = [(mean_predicted, n_positive, n), ...] -> evaluate_probabilities-style records."""
    return [{"mean_predicted": m, "observed_fraction": k / n, "n": n} for m, k, n in bins]


LOW_CURVE = make_curve([(0.01, 0, 20), (0.02, 1, 20), (0.03, 1, 20), (0.05, 2, 20), (0.10, 4, 20)])  # 100 rows, 8 positives
HIGH_CURVE = make_curve([(0.10, 0, 20), (0.12, 1, 20), (0.14, 1, 20), (0.16, 2, 20), (0.20, 4, 20)])  # mean prediction > 8%


@pytest.fixture(autouse=True)
def restore_tracking_uri():
    previous = mlflow.get_tracking_uri()
    yield
    mlflow.set_tracking_uri(previous)


@pytest.fixture
def curves():
    return {"logistic_regression": LOW_CURVE, "xgboost_calibrated": LOW_CURVE}


@pytest.fixture
def facts():
    return {
        "logistic_regression": RunFacts("logistic_regression", LR_RUN, 100, 8),
        "xgboost_calibrated": RunFacts("xgboost_calibrated", XGB_RUN, 100, 8),
    }


def make_predictions(n_rows: int = 7, **overrides) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "as_of_date": [dt.date(2025, 6, 30)] * n_rows,
            "rank": list(range(1, n_rows + 1)),
            "patient_id": [f"patient-{i:04d}-uuid" for i in range(1, n_rows + 1)],
            "discharge_time": pd.to_datetime([f"2025-06-{20 + i:02d}T10:00:00Z" for i in range(n_rows)]),
            "admission_reason_description": [f"Reason number {i}" for i in range(1, n_rows + 1)],
            "risk_score": [0.09 - 0.01 * i for i in range(n_rows)],
            "model_run_id": [LR_RUN] * n_rows,
            "model_name": ["logistic_regression"] * n_rows,
            "scoring_window_days": [30] * n_rows,
            "partition": ["test"] * n_rows,
        }
    )
    for column, value in overrides.items():
        frame[column] = value
    return frame


@pytest.fixture
def predictions():
    return make_predictions()


def build_store(tmp_path: Path, *, curves_by_model: dict, rows: int = 100, positives: int = 8, test_ids=None) -> Path:
    """A throwaway store: tmp_path/mlflow.db plus artifacts under tmp_path/artifacts, one FINISHED run per model."""
    mlflow.set_tracking_uri(mlflow_tracking_uri(tmp_path))
    client = MlflowClient()
    experiment_id = client.create_experiment(EXPERIMENT, artifact_location=str(tmp_path / "artifacts"))
    test_ids = test_ids or {}
    for name, curve in curves_by_model.items():
        ids = test_ids.get(name, ["e1", "e2", "NA"])
        csv = "encounter_id,partition\n" + "\n".join(f"{i},test" for i in ids) + "\nt1,train\n"
        with mlflow.start_run(experiment_id=experiment_id):
            mlflow.set_tag("model_name", name)
            mlflow.log_metrics({"test_n_rows": float(rows), "test_n_positive": float(positives)})
            mlflow.log_dict({"calibration_curve": curve}, "calibration_curve.json")
            mlflow.log_text(csv, "split_assignment.csv")
    return tmp_path
