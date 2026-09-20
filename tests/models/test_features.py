from __future__ import annotations

import numpy as np
import pandas as pd

from readmission_risk.models.data import COUNT_FEATURES, prepare_features
from readmission_risk.models.features import (
    build_linear_preprocessor,
    build_tree_preprocessor,
)
from readmission_risk.models.split import chronological_group_split
from tests.models.helpers import REFERENCE_DATE, TEST_START, make_gold_frame


def _train_test_features(n: int = 600):
    split = chronological_group_split(
        make_gold_frame(n, seed=0), reference_date=REFERENCE_DATE, test_start_date=TEST_START
    )
    return prepare_features(split.train), prepare_features(split.test)


def test_preprocessors_fit_on_train_only():
    X_train, X_test = _train_test_features()
    pre = build_linear_preprocessor()
    pre.fit(X_train)

    counts = list(COUNT_FEATURES)
    expected_counts_mean = np.log1p(X_train[counts]).mean().to_numpy()
    all_rows_mean = np.log1p(pd.concat([X_train, X_test])[counts]).mean().to_numpy()
    scaler = pre.named_transformers_["counts"].named_steps["scale"]
    np.testing.assert_allclose(scaler.mean_, expected_counts_mean)
    assert not np.allclose(scaler.mean_, all_rows_mean)  # train and test differ, so this really tests "train only"
    np.testing.assert_allclose(pre.named_transformers_["age"].mean_, [X_train["age_at_index_years"].mean()])

    before = scaler.mean_.copy()
    pre.transform(X_test)
    np.testing.assert_array_equal(scaler.mean_, before)  # transform never changes fitted state


def test_linear_preprocessor_feature_names():
    X_train, _ = _train_test_features()
    pre = build_linear_preprocessor().fit(X_train)
    names = pre.get_feature_names_out()
    assert len(names) == pre.transform(X_train).shape[1]
    assert "prior_encounter_count" in names and "age_at_index_years" in names  # unprefixed
    assert any(n.startswith("admission_reason_code_") for n in names)
    assert not any(n.startswith(("counts__", "age__", "cats__")) for n in names)


def _tiny_frame(race_values: list[str], ethnicity_values: list[str]) -> pd.DataFrame:
    X_train, _ = _train_test_features()
    X = X_train.iloc[: len(race_values)].copy()
    X["race"] = pd.Series(race_values, index=X.index, dtype=object)
    X["ethnicity"] = pd.Series(ethnicity_values, index=X.index, dtype=object)
    return X


def _block(pre, name_prefix: str, X_row: pd.DataFrame) -> np.ndarray:
    names = list(pre.get_feature_names_out())
    cols = [i for i, n in enumerate(names) if n.startswith(name_prefix)]
    return pre.transform(X_row)[0, cols]


def test_unseen_category_lands_in_infrequent_bucket_when_one_exists():
    # race: 'common' has 60 rows, 'rare_a'/'rare_b' have 5 each (< 20 -> pooled into the infrequent bucket)
    race = ["common"] * 60 + ["rare_a"] * 5 + ["rare_b"] * 5
    X = _tiny_frame(race, ["nonhispanic"] * 70)
    pre = build_linear_preprocessor().fit(X)

    probe_rare = X.iloc[[60]].copy()  # a rare training category
    probe_unseen = X.iloc[[60]].copy()
    probe_unseen["race"] = "brand_new"
    rare_block = _block(pre, "race_", probe_rare)
    unseen_block = _block(pre, "race_", probe_unseen)
    np.testing.assert_array_equal(rare_block, unseen_block)
    assert unseen_block.sum() == 1  # exactly the infrequent-bucket column is set


def test_unseen_category_is_all_zero_when_no_infrequent_bucket():
    # ethnicity: both categories have >= 20 rows -> no infrequent bucket exists -> unseen becomes all-zero
    ethnicity = ["nonhispanic"] * 40 + ["hispanic"] * 30
    X = _tiny_frame(["common"] * 70, ethnicity)
    pre = build_linear_preprocessor().fit(X)
    probe = X.iloc[[0]].copy()
    probe["ethnicity"] = "zzz"
    block = _block(pre, "ethnicity_", probe)  # must not raise
    assert block.sum() == 0


def test_tree_preprocessor_passes_numeric_columns_through_unscaled():
    X_train, _ = _train_test_features()
    pre = build_tree_preprocessor().fit(X_train)
    out = pre.transform(X_train)
    numeric_names = [*COUNT_FEATURES, "age_at_index_years"]
    names = list(pre.get_feature_names_out())
    for col in numeric_names:
        np.testing.assert_allclose(out[:, names.index(col)], X_train[col].to_numpy())
