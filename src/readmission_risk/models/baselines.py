"""A trivial no-model reference for judging what the models add: the per-admission-reason readmission rate.
On the pre-slice-2b (all-cause label) gold table this lookup alone reached test ROC-AUC 0.933; on the slice-2b (unplanned-label) gold
table it reaches 0.873 (see docs/results.md).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_MISSING_KEY = "MISSING"


def reason_code_lookup(
    train_codes: pd.Series, y_train: np.ndarray, test_codes: pd.Series, *, smoothing: float = 10.0
) -> np.ndarray:
    """Probability for each test row = the TRAIN positive rate of its admission reason code, with additive
    smoothing toward the train prevalence (`smoothing` pseudo-observations). A null reason is its own key
    ("MISSING" -- it is strongly informative in the gold table); a code never seen in training gets the train
    prevalence. Uses no test labels."""
    y = np.asarray(y_train)
    prevalence = float(y.mean())
    train_keys = pd.Series(train_codes).fillna(_MISSING_KEY).to_numpy()
    stats = pd.DataFrame({"code": train_keys, "y": y}).groupby("code")["y"].agg(["sum", "count"])
    rate = (stats["sum"] + smoothing * prevalence) / (stats["count"] + smoothing)
    probabilities = pd.Series(test_codes).fillna(_MISSING_KEY).map(rate).fillna(prevalence)
    return probabilities.to_numpy(dtype=float)
