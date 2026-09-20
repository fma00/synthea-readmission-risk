#!/usr/bin/env python
"""Thin CLI wrapper around readmission_risk.models.training.train_and_evaluate.

All real logic lives in the readmission_risk.models package, which is fully unit tested; the manual real-data run
is described in notes/eg-new-feature/model-training-2026-09-19.md.

Exit code 0 on success. Exit 1, printing "ERROR: <message>" to stderr, on FileNotFoundError or ValueError --
the two user-correctable failure types (bad path, bad data, bad flag). A RuntimeError (a violated split
invariant, i.e. an algorithm bug) and MLflow/OS errors (unwritable store, corrupt DB) are deliberately NOT
caught: they propagate as an ordinary traceback because they indicate a bug or a broken environment rather
than something the caller can fix by changing an argument. (A ValueError raised deep inside scikit-learn/pandas for a
genuine bug is also reported as ERROR; pass --verbose to see its traceback.)

The CLI's flag-to-config mapping is covered by tests/models/test_cli.py; the training logic by the tests it calls.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from readmission_risk.models.evaluate import BOOTSTRAP_METRICS
from readmission_risk.models.training import (
    MODEL_NAMES,
    TrainingConfig,
    train_and_evaluate,
)

_REPORTED_METRICS = (
    "roc_auc",
    "pr_auc",
    "brier_score",
    "brier_skill_score",
    "expected_calibration_error",
    "calibration_gap",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train the logistic-regression baseline and the calibrated XGBoost challenger on the gold table."
    )
    parser.add_argument("--gold-dir", type=Path, required=True)
    parser.add_argument(
        "--reference-date",
        required=True,
        help="YYYYMMDD; MUST equal the reference date used for the Synthea run and Big Join that produced the gold table",
    )
    parser.add_argument("--test-start-date", required=True, help="YYYYMMDD; first day of the chronological test window")
    parser.add_argument("--train-start-date", default=None, help="YYYYMMDD; optional lower bound for training rows")
    parser.add_argument("--tracking-dir", type=Path, default=Path("mlflow"))
    parser.add_argument("--experiment-name", default="readmission-risk")
    parser.add_argument("--readmission-window-days", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--calibration-method", choices=["sigmoid", "isotonic"], default="sigmoid")
    parser.add_argument("--calibration-cv-folds", type=int, default=5)
    parser.add_argument("--n-bootstrap", type=int, default=500)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="also print the full traceback for the FileNotFoundError/ValueError failures reported as ERROR",
    )
    return parser.parse_args(argv)


def _fmt(value: float) -> str:
    return f"{value:.4f}"


def _print_report(result, n_bootstrap: int) -> None:
    print("Split summary:")
    for key, value in result.split_summary.items():
        print(f"  {key}: {value}")

    print("\nTest-window metrics (95% patient-clustered bootstrap intervals in brackets):")
    for name in MODEL_NAMES:
        model = result.models[name]
        parts = []
        for metric in _REPORTED_METRICS:
            text = f"{metric}={_fmt(model.test_metrics[metric])}"
            if metric in model.confidence_intervals:
                lo, hi = model.confidence_intervals[metric]
                text += f" [{_fmt(lo)}, {_fmt(hi)}]"
            parts.append(text)
        print(f"  {name}: " + " ".join(parts))
        print(f"    run_id={model.run_id} model_uri={model.model_uri}")

    baseline, challenger = MODEL_NAMES
    print(f"\nPaired ({challenger} - {baseline}):")
    if n_bootstrap == 0:
        print("  (bootstrap skipped: --n-bootstrap 0)")
    for metric in BOOTSTRAP_METRICS:
        point = result.comparison.point[metric]
        if metric in result.comparison.intervals:
            lo, hi = result.comparison.intervals[metric]
            print(f"  {metric}: {_fmt(point)} [{_fmt(lo)}, {_fmt(hi)}]")
        else:
            print(f"  {metric}: {_fmt(point)}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = TrainingConfig(
        gold_dir=args.gold_dir,
        reference_date=args.reference_date,
        test_start_date=args.test_start_date,
        train_start_date=args.train_start_date,
        tracking_dir=args.tracking_dir,
        experiment_name=args.experiment_name,
        readmission_window_days=args.readmission_window_days,
        seed=args.seed,
        calibration_cv_folds=args.calibration_cv_folds,
        calibration_method=args.calibration_method,
        n_bootstrap=args.n_bootstrap,
    )
    try:
        result = train_and_evaluate(config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1

    _print_report(result, args.n_bootstrap)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
