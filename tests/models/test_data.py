from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from readmission_risk.models.data import (
    CATEGORICAL_FEATURES,
    COUNT_FEATURES,
    EXCLUDED_COLUMN_REASONS,
    EXPECTED_GOLD_COLUMNS,
    FEATURE_COLUMNS,
    LABEL_COLUMN,
    gold_fingerprint,
    load_gold_table,
    prepare_features,
)
from tests.models.helpers import make_gold_frame


def _write_parts(df: pd.DataFrame, directory, n_parts: int = 2, *, spark_like: bool = False) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    if spark_like:
        # what Spark actually emits: naive timestamps, int32 counts/label
        for col in ("index_start", "index_stop"):
            out[col] = out[col].dt.tz_convert("UTC").dt.tz_localize(None)
        for col in COUNT_FEATURES[:-1]:
            out[col] = out[col].astype("int32")
        out["organization_utilization"] = out["organization_utilization"].astype("int32")
        out[LABEL_COLUMN] = out[LABEL_COLUMN].astype("int32")
    bounds = np.linspace(0, len(out), n_parts + 1).astype(int)
    for i in range(n_parts):
        out.iloc[bounds[i] : bounds[i + 1]].to_parquet(directory / f"part-{i:05d}.parquet", index=False)
    (directory / "_SUCCESS").write_text("")


def test_every_gold_column_is_classified_exactly_once():
    gold_features = set(FEATURE_COLUMNS) - {"length_of_stay_days"}
    excluded = set(EXCLUDED_COLUMN_REASONS)
    label = {LABEL_COLUMN}
    assert gold_features | excluded | label == set(EXPECTED_GOLD_COLUMNS)
    assert not (gold_features & excluded) and not (gold_features & label) and not (excluded & label)
    assert len(FEATURE_COLUMNS) == 16
    assert len(EXPECTED_GOLD_COLUMNS) == 23


def test_expected_gold_columns_match_big_join():
    # Test-only import: importing big_join pulls in pyspark (no JVM is started). Production code in
    # readmission_risk.models deliberately does not import it.
    from readmission_risk.pipeline.big_join import GOLD_TABLE_COLUMNS

    assert tuple(GOLD_TABLE_COLUMNS) == EXPECTED_GOLD_COLUMNS


@pytest.mark.parametrize("spark_like", [False, True])
def test_load_gold_table_reads_spark_style_directory(tmp_path, spark_like):
    src = make_gold_frame(60, seed=3)
    _write_parts(src, tmp_path / "gold", spark_like=spark_like)

    loaded = load_gold_table(tmp_path / "gold")

    assert list(loaded.columns) == list(EXPECTED_GOLD_COLUMNS)
    for col in ("patient_id", "encounter_id", "admission_reason_code", "payer_ownership", "marital_status"):
        assert loaded[col].dtype == object
    for col in COUNT_FEATURES[:-1]:
        assert loaded[col].dtype == np.int64
    assert loaded["age_at_index_years"].dtype == np.float64
    assert loaded[LABEL_COLUMN].dtype == np.int8
    for col in ("index_start", "index_stop"):
        assert isinstance(loaded[col].dtype, pd.DatetimeTZDtype) and str(loaded[col].dt.tz) == "UTC"
    keys = list(zip(loaded["index_start"], loaded["encounter_id"]))
    assert keys == sorted(keys)
    expected = src.sort_values(["index_start", "encounter_id"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(
        loaded.drop(columns=["organization_utilization"]),
        expected.drop(columns=["organization_utilization"]),
        check_dtype=False,
        check_datetimelike_compat=True,
    )


def test_load_gold_table_missing_or_empty_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_gold_table(tmp_path / "does-not-exist")
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError):
        load_gold_table(tmp_path / "empty")


def _mutate(df: pd.DataFrame, how: str) -> pd.DataFrame:
    df = df.copy()
    if how == "missing_column":
        return df.drop(columns=["prior_encounter_count"])
    if how == "extra_column":
        df["surprise"] = 1
        return df
    if how == "zero_rows":
        return df.iloc[0:0]
    if how == "null_label":
        df[LABEL_COLUMN] = df[LABEL_COLUMN].astype("float64")
        df.loc[df.index[0], LABEL_COLUMN] = np.nan
        return df
    if how == "null_count_feature":
        df["prior_encounter_count"] = df["prior_encounter_count"].astype("float64")
        df.loc[df.index[0], "prior_encounter_count"] = np.nan
        return df
    if how == "label_two":
        df.loc[df.index[0], LABEL_COLUMN] = 2
        return df
    if how == "duplicate_encounter":
        df.loc[df.index[1], "encounter_id"] = df.loc[df.index[0], "encounter_id"]
        return df
    if how == "stop_before_start":
        df.loc[df.index[0], "index_stop"] = df.loc[df.index[0], "index_start"] - pd.Timedelta(days=1)
        return df
    raise AssertionError(how)


@pytest.mark.parametrize(
    "how, match",
    [
        ("missing_column", "prior_encounter_count"),
        ("extra_column", "surprise"),
        ("zero_rows", "zero rows"),
        ("null_label", LABEL_COLUMN),
        ("null_count_feature", "prior_encounter_count"),
        ("label_two", LABEL_COLUMN),
        ("duplicate_encounter", "encounter_id"),
        ("stop_before_start", "index_stop"),
    ],
)
def test_load_gold_table_rejects_bad_data(tmp_path, how, match):
    _write_parts(_mutate(make_gold_frame(40, seed=1), how), tmp_path / "gold", n_parts=1)
    with pytest.raises(ValueError, match=match):
        load_gold_table(tmp_path / "gold")


def test_load_gold_table_allows_null_organization_utilization(tmp_path):
    df = make_gold_frame(40, seed=1)
    df["organization_utilization"] = df["organization_utilization"].astype("float64")
    df.loc[df.index[0], "organization_utilization"] = np.nan
    _write_parts(df, tmp_path / "gold", n_parts=1)
    loaded = load_gold_table(tmp_path / "gold")  # excluded column: neither cast nor null-checked
    assert loaded["organization_utilization"].isna().sum() == 1


def test_prepare_features_shape_and_missing_fill():
    df = make_gold_frame(80, seed=2)
    df.loc[df.index[0], "admission_reason_code"] = None
    df.loc[df.index[1], "marital_status"] = None
    df.loc[df.index[2], "index_start"] = pd.Timestamp("2024-01-01T00:00:00Z")
    df.loc[df.index[2], "index_stop"] = pd.Timestamp("2024-01-02T12:00:00Z")  # a 36-hour stay
    before = df.copy(deep=True)

    features = prepare_features(df)

    assert list(features.columns) == list(FEATURE_COLUMNS)
    for col in CATEGORICAL_FEATURES:
        assert features[col].dtype == object
    assert features.loc[df.index[0], "admission_reason_code"] == "MISSING"
    assert features.loc[df.index[1], "marital_status"] == "MISSING"
    assert features.loc[df.index[2], "length_of_stay_days"] == 1.5
    assert not features.isna().any().any()
    pd.testing.assert_frame_equal(df, before)  # input not mutated


def test_prepare_features_reports_missing_columns():
    with pytest.raises(ValueError, match="prior_encounter_count"):
        prepare_features(make_gold_frame(20, seed=0).drop(columns=["prior_encounter_count"]))


def test_gold_fingerprint():
    df = make_gold_frame(50, seed=4)
    base = gold_fingerprint(df)

    assert len(base) == 64 and base == base.lower() and all(c in "0123456789abcdef" for c in base)
    assert gold_fingerprint(df.copy()) == base
    assert gold_fingerprint(df.sample(frac=1.0, random_state=0)) == base  # row-order invariant

    flipped = df.copy()
    flipped.loc[flipped.index[0], LABEL_COLUMN] = 1 - flipped.loc[flipped.index[0], LABEL_COLUMN]
    assert gold_fingerprint(flipped) != base

    # a FEATURE change with every id and label unchanged must also change it (an ids+labels-only recipe would not)
    changed = df.copy()
    changed.loc[changed.index[0], "prior_encounter_count"] += 1
    assert gold_fingerprint(changed) != base
