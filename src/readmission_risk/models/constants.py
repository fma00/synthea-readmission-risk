"""Model facts shared by training, the bundle exporter and the scorer. Imports nothing, so the scorer can read them
without importing training.py (which imports mlflow). See
notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md.
"""

from __future__ import annotations

MODEL_NAMES: tuple[str, ...] = ("logistic_regression", "xgboost_calibrated")
# MLflow run tag holding the Spark-computed spark-rowhash-v1 gold fingerprint; training sets it only for version-2 gold metadata,
# and the bundle exporter records definition spark-rowhash-v1 for runs that carry it (v2 slice 5).
ROWHASH_TAG = "gold_fingerprint_spark_rowhash_v1"
