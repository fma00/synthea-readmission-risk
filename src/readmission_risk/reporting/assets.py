"""Builds the README review assets (reliability figure x light/dark, sample Top-N table) from finished MLflow runs and a
predictions partition. See notes/eg-new-feature/readme-reliability-plot-and-sample-table-2026-10-09.md
(Interfaces > assets.py) for the checks and their reasons.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import mlflow
import pandas as pd
from mlflow.tracking import MlflowClient

from readmission_risk.models.tracking import resolve_run_id

from .reliability import (
    DEFAULT_POPULATION_LABEL,
    bin_points,
    build_reliability_figure,
    title_for,
)
from .style import MODEL_DISPLAY_NAMES, MODEL_NAMES, THEMES, house_style, save_figure
from .topn_table import format_topn_markdown

_PREVIEW_DPI = 200
_ASSIGNMENT_COLUMNS = ["encounter_id", "partition"]


@dataclass(frozen=True)
class RunFacts:
    model_name: str
    run_id: str
    test_n_rows: int
    test_n_positive: int


@dataclass(frozen=True)
class AssetConfig:
    tracking_dir: Path
    experiment_name: str
    predictions_path: Path
    out_dir: Path
    top_n_rows: int = 5
    # Names the test window in the figure title and the captions: the v1 default, or e.g. "200k population" (v2 slice 7).
    population_label: str = DEFAULT_POPULATION_LABEL


def _integral_metric(metrics: dict[str, float], key: str, run_id: str) -> int:
    if key not in metrics:
        raise ValueError(f"MLflow run {run_id} has no metric {key!r} (was it produced by train_and_evaluate?)")
    value = metrics[key]
    if value != int(value):
        raise ValueError(f"MLflow run {run_id}: metric {key} = {value} is not an integer count")
    return int(value)


def load_curves(
    tracking_dir: Path, experiment_name: str
) -> tuple[dict[str, list[dict[str, float]]], dict[str, RunFacts]]:
    """Resolves each model's single FINISHED run, downloads its calibration curve, and reconciles the curve with the run:
    the bin counts must sum to the run's test rows and positives, and both runs must have the SAME set of test encounters
    (equal sums alone would not prove the same rows). resolve_run_id sets the global MLflow tracking URI, so the previous
    URI is restored on exit."""
    previous_uri = mlflow.get_tracking_uri()
    curves: dict[str, list[dict[str, float]]] = {}
    facts: dict[str, RunFacts] = {}
    test_ids: dict[str, set[str]] = {}
    try:
        for name in MODEL_NAMES:
            run_id = resolve_run_id(tracking_dir, experiment_name, name)
            client = MlflowClient()
            metrics = client.get_run(run_id).data.metrics
            n_rows = _integral_metric(metrics, "test_n_rows", run_id)
            n_positive = _integral_metric(metrics, "test_n_positive", run_id)
            with tempfile.TemporaryDirectory() as tmp:
                curve_path = Path(client.download_artifacts(run_id, "calibration_curve.json", tmp))
                assignment_path = Path(client.download_artifacts(run_id, "split_assignment.csv", tmp))
                wrapper = json.loads(curve_path.read_text(encoding="utf-8"))
                assignment = pd.read_csv(assignment_path, dtype=str, keep_default_na=False)
            if not isinstance(wrapper, dict) or "calibration_curve" not in wrapper:
                raise ValueError(f"MLflow run {run_id}: calibration_curve.json lacks the 'calibration_curve' key")
            curve = wrapper["calibration_curve"]
            points = bin_points(curve)
            if sum(p.n for p in points) != n_rows or sum(p.n_positive for p in points) != n_positive:
                raise ValueError(
                    f"MLflow run {run_id}: the calibration curve covers {sum(p.n for p in points)} rows / "
                    f"{sum(p.n_positive for p in points)} positives but the run's test window has {n_rows} / {n_positive}"
                )
            if list(assignment.columns) != _ASSIGNMENT_COLUMNS:
                raise ValueError(f"MLflow run {run_id}: split_assignment.csv columns are {list(assignment.columns)}")
            curves[name] = curve
            facts[name] = RunFacts(name, run_id, n_rows, n_positive)
            test_ids[name] = set(assignment.loc[assignment["partition"] == "test", "encounter_id"])
    finally:
        mlflow.set_tracking_uri(previous_uri)
    first, second = MODEL_NAMES
    if test_ids[first] != test_ids[second]:
        raise ValueError(f"the {first} and {second} runs do not share the same test encounters; they cannot be plotted together")
    return curves, facts


def _single_value(predictions: pd.DataFrame, column: str) -> object:
    values = predictions[column]
    if values.isna().any() or values.nunique() != 1:
        raise ValueError(f"predictions column {column!r} must hold exactly one distinct non-null value")
    return values.iloc[0]


def _check_predictions(predictions: pd.DataFrame, facts: dict[str, RunFacts]) -> tuple[str, object, int]:
    required = ["model_name", "model_run_id", "as_of_date", "partition", "scoring_window_days"]
    missing = [c for c in required if c not in predictions.columns]
    if missing:
        raise ValueError(f"predictions lack required columns: {missing}")
    model_name = str(_single_value(predictions, "model_name"))
    run_id = str(_single_value(predictions, "model_run_id"))
    as_of_date = _single_value(predictions, "as_of_date")
    window_days = int(_single_value(predictions, "scoring_window_days"))
    if model_name not in MODEL_NAMES:
        raise ValueError(f"predictions model_name {model_name!r} is not one of {MODEL_NAMES}")
    if run_id != facts[model_name].run_id:
        raise ValueError(
            f"predictions come from run {run_id} but the plotted {model_name} run is {facts[model_name].run_id}: "
            "a stale predictions file would contradict the figure"
        )
    if not (predictions["partition"] == "test").all():
        raise ValueError("predictions contain rows outside the held-out 'test' partition")
    return model_name, as_of_date, window_days


def _mean_prediction(curve: list[dict[str, float]]) -> float:
    points = bin_points(curve)
    return sum(p.n * p.mean_predicted for p in points) / sum(p.n for p in points)


def _captions(
    config: AssetConfig,
    predictions: pd.DataFrame,
    curves: dict[str, list[dict[str, float]]],
    facts: dict[str, RunFacts],
    model_name: str,
    as_of_date: object,
    window_days: int,
) -> str:
    n = config.top_n_rows
    first = (
        f"Sample Top-{n}: ranks 1-{n} of {len(predictions)} discharges scored in the {window_days}-day window ending "
        f"{as_of_date:%Y-%m-%d}, {MODEL_DISPLAY_NAMES[model_name]}. Demo over held-out rows ({config.population_label}), not new-data scoring."
    )
    reference = facts[MODEL_NAMES[0]]
    prevalence = reference.test_n_positive / reference.test_n_rows
    second = (
        f"Risk scores are rankings, not absolute risks: the test-window prevalence is {prevalence:.2%} "
        f"({reference.test_n_positive} of {reference.test_n_rows:,})"
    )
    # The clause is included only when the data support it, so a regeneration on another population cannot print a false claim.
    if all(_mean_prediction(curve) < prevalence for curve in curves.values()):
        second += " and the models under-predict on average."
    else:
        second += "."
    return f"{first}\n{second}\n\n"


def _description(config: AssetConfig, curves: dict[str, list[dict[str, float]]], facts: dict[str, RunFacts]) -> str:
    run_ids = ", ".join(f"{name}={facts[name].run_id}" for name in MODEL_NAMES)
    reference = facts[MODEL_NAMES[0]]
    lengths = [len(curves[name]) for name in MODEL_NAMES]
    if len(set(lengths)) == 1:
        bins_text = f"{lengths[0]} quantile bins"
    else:
        bins_text = f"{lengths[0]} and {lengths[1]} quantile bins ({MODEL_NAMES[0]}, {MODEL_NAMES[1]})"
    return (
        f"Reliability diagram. Source: MLflow experiment {config.experiment_name}, runs {run_ids}; "
        f"{reference.test_n_rows} test rows, {reference.test_n_positive} positives; {bins_text}; 95% Wilson intervals with rows "
        f"treated as independent (indicative only); {_quality(config.population_label)}"
    )


def _quality(population_label: str) -> str:
    if population_label == DEFAULT_POPULATION_LABEL:
        return "development-set quality, not a virgin holdout."
    return f"{population_label}, not a virgin holdout."


def build_assets(
    config: AssetConfig,
    *,
    curve_loader: Callable = load_curves,
    predictions_loader: Callable = pd.read_parquet,
) -> list[Path]:
    """Writes reliability_{light,dark}.svg and sample_topn.md into config.out_dir and the PNG previews into
    out_dir/preview/. Everything is validated first; files are rendered into out_dir/.tmp-<random>/ and then moved one by one
    (atomic per file, not as a set). Returns the final paths."""
    curves, facts = curve_loader(config.tracking_dir, config.experiment_name)
    predictions = predictions_loader(config.predictions_path)
    model_name, as_of_date, window_days = _check_predictions(predictions, facts)
    table = format_topn_markdown(predictions, config.top_n_rows)
    sample = _captions(config, predictions, curves, facts, model_name, as_of_date, window_days) + table
    description = _description(config, curves, facts)

    out_dir = Path(config.out_dir)
    (out_dir / "preview").mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=out_dir, prefix=".tmp-"))  # unique name: no PID collision after a killed run
    try:
        for theme in THEMES.values():
            with house_style(theme):
                fig = build_reliability_figure(curves, theme, title=title_for(config.population_label))
                fig.canvas.draw()
                save_figure(
                    fig,
                    tmp_dir / f"reliability_{theme.name}.svg",
                    metadata={"Title": title_for(config.population_label), "Description": description, "Date": None},
                )
                save_figure(
                    fig,
                    tmp_dir / f"reliability_{theme.name}.png",
                    dpi=_PREVIEW_DPI,
                    facecolor=theme.preview_background,
                )
        (tmp_dir / "sample_topn.md").write_text(sample, encoding="utf-8")
        written = []
        for tmp_file in sorted(tmp_dir.iterdir()):
            destination = (out_dir / "preview" if tmp_file.suffix == ".png" else out_dir) / tmp_file.name
            os.replace(tmp_file, destination)
            written.append(destination)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return written
