"""Readmission-risk prediction from Synthea data (see README.md).

Importing the package opts the process out of MLflow's usage telemetry (MLflow only disables it by itself under pytest
and in CI, so laptop runs of train_models.py / export_bundle.py / make_readme_assets.py would otherwise contact
config.mlflow-telemetry.io). It must happen before mlflow is first imported, and every entry point imports this package
first. setdefault: an explicit MLFLOW_DISABLE_TELEMETRY=false still wins.
"""

import os

os.environ.setdefault("MLFLOW_DISABLE_TELEMETRY", "true")
