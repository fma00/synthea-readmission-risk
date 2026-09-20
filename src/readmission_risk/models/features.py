"""Unfitted preprocessors for the two models. Both are meant to be the "preprocess" step of a Pipeline,
so every fitted statistic (scaler means, one-hot category sets) is learned on training rows only.
See notes/eg-new-feature/model-training-2026-09-19.md (Interfaces > features.py).
"""

from __future__ import annotations

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

from .data import CATEGORICAL_FEATURES, COUNT_FEATURES, NUMERIC_OTHER_FEATURES

_MIN_CATEGORY_FREQUENCY = 20


def _one_hot_encoder() -> OneHotEncoder:
    # Categories with fewer than 20 rows in the fitted data are pooled into an 'infrequent' bucket, and
    # categories never seen at fit time map to that same bucket -- but ONLY for a column that has at least
    # one infrequent category at fit time. For a column with none, "infrequent_if_exist" behaves like
    # "ignore": an unseen category becomes an all-zero block for that column.
    return OneHotEncoder(
        handle_unknown="infrequent_if_exist",
        min_frequency=_MIN_CATEGORY_FREQUENCY,
        sparse_output=False,
    )


def build_linear_preprocessor() -> ColumnTransformer:
    """For LogisticRegression. Transformer names ("counts"/"age"/"cats") are part of the interface:
    tests reach in by name. verbose_feature_names_out=False so get_feature_names_out() returns
    unprefixed names (prior_encounter_count, admission_reason_code_<code>, ...)."""
    return ColumnTransformer(
        [
            (
                "counts",
                Pipeline(
                    [
                        # heavy right skew (e.g. prior_encounter_count max 217); one-to-one names keep
                        # get_feature_names_out() working
                        ("log1p", FunctionTransformer(np.log1p, feature_names_out="one-to-one")),
                        ("scale", StandardScaler()),
                    ]
                ),
                list(COUNT_FEATURES),
            ),
            ("age", StandardScaler(), list(NUMERIC_OTHER_FEATURES)),
            ("cats", _one_hot_encoder(), list(CATEGORICAL_FEATURES)),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )


def build_tree_preprocessor() -> ColumnTransformer:
    """For XGBoost: numeric columns pass through unscaled; categoricals use the same encoder."""
    return ColumnTransformer(
        [
            ("num", "passthrough", [*COUNT_FEATURES, *NUMERIC_OTHER_FEATURES]),
            ("cats", _one_hot_encoder(), list(CATEGORICAL_FEATURES)),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )
