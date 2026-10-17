"""FR0.3 / M8: the portable form of the frozen v1 models reproduces their pickles exactly (max abs diff 0) on the real v1
test rows. Runs on the owner's machine (the v1 gold table and bundles are gitignored); CI skips it, listed by -rs.
Bundles are discovered by their MLflow run id, never by a hard-coded bundle id. See
notes/eg-new-feature/v2-slice6c-portable-v1-export-2026-10-17.md (Verification criteria > test_v1_parity.py)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
BUNDLES = REPO / "bundles"
V1_GOLD = REPO / "data" / "gold" / "full_run_2b"
V1_RUNS = {
    "logistic_regression": "fe9633ca42384ea5aeaba83e13d65701",
    "xgboost_calibrated": "abe9c605509b49dda4d8ba0aef511a60",
}
V1_TEST_ROWS = 1539
DRIFT_PREFIXES = ("library version mismatch", "python version mismatch")  # guard 8 and guard 14 (vi): environment drift


def _schema2_bundles(run_id: str) -> list[Path]:
    found = []
    for manifest_path in sorted(BUNDLES.glob("*/manifest.json")) if BUNDLES.is_dir() else []:
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            continue
        if manifest.get("schema_version") == 2 and (manifest.get("mlflow") or {}).get("run_id") == run_id:
            found.append(manifest_path.parent)
    return found


def _bundles_or_skip(model_name: str) -> list[Path]:
    bundles = _schema2_bundles(V1_RUNS[model_name])
    if not bundles:
        pytest.skip(f"no schema-2 bundle under bundles/ for v1 run {V1_RUNS[model_name]} ({model_name})")
    return bundles


@pytest.mark.parametrize("model_name", sorted(V1_RUNS))
def test_v1_portable_bundles_self_verify(model_name):
    from readmission_risk.portable.predictor import (
        read_portable_bundle,
        verify_reference,
    )

    for bundle in _bundles_or_skip(model_name):
        model, reference = read_portable_bundle(str(bundle))
        assert model.model_name == model_name
        assert verify_reference(model, reference) == 0.0
        assert int((reference.kinds == "test").sum()) == V1_TEST_ROWS


@pytest.mark.parametrize("model_name", sorted(V1_RUNS))
def test_v1_portable_matches_the_pickle_on_fresh_rows(model_name):
    bundles = _bundles_or_skip(model_name)
    if not V1_GOLD.is_dir():
        pytest.skip(f"the v1 gold table {V1_GOLD.relative_to(REPO)} is absent")
    from readmission_risk.models.data import (
        load_gold_table_with_metadata,
        prepare_features,
    )
    from readmission_risk.portable.predictor import read_portable_bundle
    from readmission_risk.scoring.bundle_guard import load_bundle_model, validate_bundle

    gold, metadata = load_gold_table_with_metadata(V1_GOLD)
    for bundle in bundles:
        try:
            validated = validate_bundle(str(bundle), gold=gold, metadata=metadata)
        except ValueError as exc:
            if str(exc).startswith(DRIFT_PREFIXES):
                pytest.skip(f"the pickle cannot be loaded in this environment: {exc}")
            raise
        test_ids = sorted(validated.partition_of.index[validated.partition_of == "test"])
        X = prepare_features(gold.set_index("encounter_id", drop=False).loc[test_ids])
        assert len(X) == V1_TEST_ROWS
        portable, _ = read_portable_bundle(str(bundle))
        # the same prepared frame, one predict_proba call per side (the batch-layout contract)
        got, expected = portable.predict_proba(X), np.asarray(load_bundle_model(validated).predict_proba(X))
        assert np.array_equal(got, expected), f"max abs diff {np.max(np.abs(got - expected))!r}"
