"""Model facts shared by training, the bundle exporter and the scorer. Imports nothing, so the scorer can read them
without importing training.py (which imports mlflow). See
notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md.
"""

from __future__ import annotations

MODEL_NAMES: tuple[str, ...] = ("logistic_regression", "xgboost_calibrated")
