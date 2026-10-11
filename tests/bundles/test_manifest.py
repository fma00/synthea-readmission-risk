from __future__ import annotations

import math

import pytest

from readmission_risk.bundles.manifest import (
    canonical_json,
    compute_bundle_id,
    file_entry,
    manifest_bytes,
    sha256_hex,
)


def test_canonical_json_is_key_order_independent_and_compact():
    a = canonical_json({"b": 1, "a": {"y": [1, 2], "x": None}})
    b = canonical_json({"a": {"x": None, "y": [1, 2]}, "b": 1})
    assert a == b == b'{"a":{"x":null,"y":[1,2]},"b":1}'
    assert canonical_json({"s": "é"}) == b'{"s":"\\u00e9"}'  # ASCII only


def test_bundle_id_excludes_its_own_key_and_changes_with_any_other_field():
    manifest = {"schema_version": 1, "model_name": "m", "files": {"x": {"sha256": "0" * 64}}, "portable": None}
    base = compute_bundle_id(manifest)
    assert compute_bundle_id({**manifest, "bundle_id": "anything"}) == base
    for key, value in (("schema_version", 2), ("model_name", "n"), ("files", {}), ("portable", 0)):
        assert compute_bundle_id({**manifest, key: value}) != base, key


def test_bundle_id_known_answer():
    # sha256 of '{"a":[1,"x"],"b":null,"schema_version":1}', checked independently with `shasum -a 256`
    assert compute_bundle_id({"schema_version": 1, "a": [1, "x"], "b": None, "bundle_id": "ignored"}) == (
        "bdde680d65e357b3e820dafb0efe1b354734cf70b7d9f26ca5b0c71051c90a93"
    )


@pytest.mark.parametrize("value", [math.nan, math.inf, object(), b"bytes"])
def test_canonical_json_refuses_nan_and_non_json(value):
    with pytest.raises(ValueError):
        canonical_json({"x": value})


def test_manifest_bytes_refuses_a_wrong_bundle_id():
    manifest = {"schema_version": 1}
    good = {**manifest, "bundle_id": compute_bundle_id(manifest)}
    assert manifest_bytes(good) == canonical_json(good) + b"\n"
    with pytest.raises(ValueError, match="content hash"):
        manifest_bytes({**manifest, "bundle_id": "0" * 64})
    with pytest.raises(ValueError, match="content hash"):
        manifest_bytes(manifest)


def test_file_entry():
    assert file_entry("model.pkl", b"abc") == {"sha256": sha256_hex(b"abc"), "size": 3, "role": "model_cloudpickle"}
