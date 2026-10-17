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


# --------------------------------------------------------------------------------------------- schema 2 (v2 slice 6c)
from readmission_risk.bundles.manifest import (
    TOP_LEVEL_KEYS,
    check_portable_block,
    check_schema2_files_table,
    expected_file_names,
    file_role,
)

_S1 = {"model.pkl", "split_assignment.csv"}
_S2_BASE = {*_S1, "portable.json", "portable_reference.csv"}


def _schema2(boosters: list[str] | None = None, **portable_overrides) -> dict:
    boosters = boosters or []
    names = [*_S2_BASE, *boosters]
    manifest = dict.fromkeys(TOP_LEVEL_KEYS)
    manifest.update(
        schema_version=2,
        files={n: file_entry(n, n.encode()) for n in names},
        portable={
            "format_version": 1,
            "params_file": "portable.json",
            "reference_file": "portable_reference.csv",
            "booster_files": boosters,
            "parity": {"rows": 3, "probe_rows": 0, "max_abs_diff": 0.0},
            "writer_versions": dict.fromkeys(("numpy", "scipy", "xgboost", "python", "platform", "blas"), "x"),
            **portable_overrides,
        },
    )
    return manifest


def test_file_role_known_names_and_boosters():
    assert [file_role(n) for n in ("model.pkl", "split_assignment.csv", "portable.json", "portable_reference.csv",
                                   "portable_fold_0.ubj", "portable_fold_12.ubj")] == [
        "model_cloudpickle", "split_assignment", "portable_params", "portable_reference", "portable_booster", "portable_booster",
    ]  # fmt: skip


@pytest.mark.parametrize("name", ["notes.txt", "portable_fold_01.ubj", "portable_fold_-1.ubj", "portable_fold_0.UBJ"])
def test_file_role_refuses_unknown_names(name):
    with pytest.raises(ValueError, match="unknown bundle file name"):
        file_role(name)


def test_file_entry_schema1_unchanged():
    assert file_entry("model.pkl", b"x") == {"sha256": sha256_hex(b"x"), "size": 1, "role": "model_cloudpickle"}


def test_expected_file_names_per_schema():
    assert expected_file_names({"schema_version": 1, "portable": None}) == _S1
    assert expected_file_names(_schema2()) == _S2_BASE
    assert expected_file_names(_schema2(["portable_fold_0.ubj", "portable_fold_1.ubj"])) == {
        *_S2_BASE, "portable_fold_0.ubj", "portable_fold_1.ubj",
    }  # fmt: skip


@pytest.mark.parametrize(
    "manifest",
    [
        {"schema_version": 3},
        {"schema_version": True},
        {"schema_version": 1, "portable": {"x": 1}},
        {**_schema2(), "portable": None},
        {**_schema2(), "extra": 1},
        _schema2(booster_files="portable_fold_0.ubj"),
    ],
    ids=["schema-3", "schema-true", "schema-1-with-portable", "schema-2-null-portable", "schema-2-extra-key", "boosters-not-a-list"],
)
def test_expected_file_names_refuses_bad_schema_or_portable(manifest):
    with pytest.raises(ValueError):
        expected_file_names(manifest)


def _drop(key):
    def edit(manifest):
        del manifest["portable"][key]

    return edit


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (_drop("parity"), "exactly the keys"),
        (lambda m: m["portable"].update(extra=1), "exactly the keys"),
        (lambda m: m["portable"].update(format_version=2), "format_version"),
        (lambda m: m["portable"].update(format_version=True), "format_version"),
        (lambda m: m["portable"].update(params_file="p.json"), "params_file"),
        (lambda m: m["portable"].update(reference_file="r.csv"), "reference_file"),
        (lambda m: m["portable"].update(booster_files=["portable_fold_1.ubj"]), "in order"),
        (lambda m: m["portable"].update(booster_files=["portable_fold_1.ubj", "portable_fold_0.ubj"]), "in order"),
        (lambda m: m["portable"].update(booster_files=[]), "disagree with the files table"),
        (lambda m: m["portable"]["parity"].update(rows=0), "parity.rows"),
        (lambda m: m["portable"]["parity"].update(rows=True), "parity.rows"),
        (lambda m: m["portable"]["parity"].update(max_abs_diff=1e-17), "max_abs_diff"),
        (lambda m: m["portable"]["parity"].update(max_abs_diff=False), "max_abs_diff"),
        (lambda m: m["portable"]["writer_versions"].update(numpy=2), "writer_versions"),
        (lambda m: m["files"]["portable.json"].pop("role"), "disagree|files table"),
        (lambda m: m.update(files=[]), "files table"),
    ],
)
def test_check_portable_block(edit, message):
    manifest = _schema2(["portable_fold_0.ubj", "portable_fold_1.ubj"])
    check_portable_block(manifest)  # the valid baseline
    edit(manifest)
    with pytest.raises(ValueError, match=message):
        check_portable_block(manifest)


def test_check_schema2_files_table():
    table = {n: {} for n in (*_S2_BASE, "portable_fold_0.ubj")}
    check_schema2_files_table(table)
    for broken in ({k: v for k, v in table.items() if k != "portable.json"},
                   {k: v for k, v in table.items() if k != "portable_reference.csv"},
                   {**table, "portable_fold_01.ubj": {}}, {**table, "notes.txt": {}}, []):  # fmt: skip
        with pytest.raises(ValueError):
            check_schema2_files_table(broken)
