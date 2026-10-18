"""Shared helpers for tests/portable (v2 slice 6c): the fixture bundles' pickles, their test rows, and portable files."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from readmission_risk.bundles.manifest import PORTABLE_PARAMS_FILENAME
from readmission_risk.models.data import load_gold_table_with_metadata, prepare_features
from readmission_risk.portable import predictor
from readmission_risk.scoring.bundle_guard import load_bundle_model, validate_bundle


def pickle_and_test_rows(gold_dir: Path, bundle_uri: str):
    """(unpickled model, prepare_features of the bundle's test partition sorted by encounter_id)."""
    gold, metadata = load_gold_table_with_metadata(gold_dir)
    bundle = validate_bundle(bundle_uri, gold=gold, metadata=metadata)
    test_ids = sorted(bundle.partition_of.index[bundle.partition_of == "test"])
    X = prepare_features(gold.set_index("encounter_id", drop=False).loc[test_ids])
    return load_bundle_model(bundle), X


def portable_files(bundle_uri: str) -> dict[str, bytes]:
    """portable.json and the booster files of a schema-2 bundle (no checks: for load_portable tests)."""
    root = Path(bundle_uri)
    return {p.name: p.read_bytes() for p in root.iterdir() if p.name == PORTABLE_PARAMS_FILENAME or p.suffix == ".ubj"}


def portable_document(bundle_uri: str) -> dict:
    return json.loads((Path(bundle_uri) / PORTABLE_PARAMS_FILENAME).read_text())


def load(bundle_uri: str) -> predictor.PortableModel:
    return predictor.load_portable(portable_files(bundle_uri))


def assert_same(got: np.ndarray, expected) -> None:
    expected = np.asarray(expected, dtype=np.float64)
    assert got.shape == expected.shape
    assert np.array_equal(got, expected), f"max abs diff {np.max(np.abs(got - expected))!r}"


def with_value(X: pd.DataFrame, column: str, value, *, dtype=object) -> pd.DataFrame:
    """A copy of X with every row's `column` set to `value` (object dtype by default, as prepare_features' categoricals)."""
    out = X.copy()
    out[column] = pd.Series([value] * len(out), index=out.index, dtype=dtype)
    return out
