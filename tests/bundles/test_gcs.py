"""The GCS bundle store (v2 slice 4) against an in-memory fake: the reader, the create-only writer, and a real bundle
validated from gs:// exactly like its local twin. See notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md."""

from __future__ import annotations

from pathlib import Path

import pytest
from google.api_core import exceptions as api
from google.auth.exceptions import RefreshError

from readmission_risk.bundles import gcs
from readmission_risk.bundles.manifest import MANIFEST_FILENAME, sha256_hex
from readmission_risk.bundles.store import (
    list_bundle_entries,
    read_bundle_file,
    write_bundle,
)
from readmission_risk.models.data import load_gold_table_with_metadata
from readmission_risk.scoring.bundle_guard import validate_bundle
from tests.bundles.fake_gcs import FakeStorage

BUCKET = "fake-bundles"


@pytest.fixture
def fake(monkeypatch):
    store = FakeStorage()
    monkeypatch.setattr("readmission_risk.gcp.storage_client", lambda: store)
    return store


def _upload_local_bundle(store: FakeStorage, local_uri: str, prefix: str = "") -> str:
    bundle_id = Path(local_uri).name
    for path in sorted(Path(local_uri).iterdir()):
        store.put(BUCKET, f"{prefix}{bundle_id}/{path.name}", path.read_bytes())
    return f"gs://{BUCKET}/{prefix}{bundle_id}"


@pytest.mark.parametrize(
    "uri, expected",
    [
        ("gs://bucket/a", ("bucket", "a")),
        ("gs://bucket/a/b/", ("bucket", "a/b")),
        ("gs://my.bucket-1/x", ("my.bucket-1", "x")),
    ],
)
def test_parse_gs_uri_accepts(uri, expected):
    assert gcs.parse_gs_uri(uri) == expected


@pytest.mark.parametrize(
    "uri", ["gs://bucket", "gs://bucket/", "gs://b/x", "gs://Bucket/x", "gs://bucket/a//b", "gs://bucket/./x", "s3://bucket/x"]
)
def test_parse_gs_uri_refuses(uri):
    with pytest.raises(ValueError):
        gcs.parse_gs_uri(uri)


def test_list_entries_relative_names_subdirectory_and_placeholder(fake):
    fake.put(BUCKET, "p/abc/", b"")  # a console "folder" placeholder
    fake.put(BUCKET, "p/abc/manifest.json", b"{}")
    fake.put(BUCKET, "p/abc/sub/x", b"1")
    fake.put(BUCKET, "p/abcdef/other", b"not this bundle")
    assert list_bundle_entries(f"gs://{BUCKET}/p/abc") == ["manifest.json", "sub"]


def test_list_entries_empty_is_not_found(fake):
    fake.put(BUCKET, "other/x", b"1")
    with pytest.raises(FileNotFoundError, match="bundle not found"):
        list_bundle_entries(f"gs://{BUCKET}/x")
    with pytest.raises(FileNotFoundError, match="bundle not found"):
        list_bundle_entries("gs://no-such-bucket/x")  # the bucket itself is missing


def test_read_file_maps_not_found_forbidden_and_auth_errors(fake):
    fake.put(BUCKET, "abc/manifest.json", b"{}")
    assert read_bundle_file(f"gs://{BUCKET}/abc", "manifest.json") == b"{}"
    with pytest.raises(FileNotFoundError, match=r"has no model\.pkl"):
        read_bundle_file(f"gs://{BUCKET}/abc", "model.pkl")
    fake.forbidden = True
    with pytest.raises(PermissionError, match=r"cannot read manifest\.json"):
        read_bundle_file(f"gs://{BUCKET}/abc", "manifest.json")
    fake.forbidden, fake.auth_error = False, RefreshError("token refresh failed")
    with pytest.raises(OSError, match="token refresh failed"):
        read_bundle_file(f"gs://{BUCKET}/abc", "manifest.json")
    with pytest.raises(ValueError, match="invalid bundle file name"):
        read_bundle_file(f"gs://{BUCKET}/abc", "../x")


def _bundle_files(local_uri: str) -> tuple[dict, dict]:
    import json

    files = {p.name: p.read_bytes() for p in Path(local_uri).iterdir() if p.name != MANIFEST_FILENAME}
    manifest = json.loads((Path(local_uri) / MANIFEST_FILENAME).read_text())
    return files, manifest


def test_write_files_manifest_last_if_generation_match_zero_retry_none(fake, scoring_env):
    files, manifest = _bundle_files(scoring_env.lr_bundle_uri)
    uri, created = write_bundle(files, manifest, f"gs://{BUCKET}")  # a bare bucket root
    bundle_id = manifest["bundle_id"]
    assert (uri, created) == (f"gs://{BUCKET}/{bundle_id}", True)
    uploads = [c for c in fake.calls if c[0] == "upload"]
    assert [c[2].rsplit("/", 1)[1] for c in uploads][-1] == MANIFEST_FILENAME
    assert sorted(c[2].rsplit("/", 1)[1] for c in uploads) == sorted([*files, MANIFEST_FILENAME])
    assert all(c[3] == {"if_generation_match": 0, "retry": None} for c in uploads)
    for path in Path(scoring_env.lr_bundle_uri).iterdir():
        assert fake.buckets[BUCKET][f"{bundle_id}/{path.name}"].data == path.read_bytes()


def test_write_files_under_a_prefix(fake, scoring_env):
    files, manifest = _bundle_files(scoring_env.lr_bundle_uri)
    uri, _ = write_bundle(files, manifest, f"gs://{BUCKET}/l3-probe/")
    assert uri == f"gs://{BUCKET}/l3-probe/{manifest['bundle_id']}"
    assert f"l3-probe/{manifest['bundle_id']}/{MANIFEST_FILENAME}" in fake.buckets[BUCKET]


def test_write_files_existing_object_is_already_exists_and_nothing_replaced(fake, scoring_env):
    files, manifest = _bundle_files(scoring_env.lr_bundle_uri)
    bundle_id = manifest["bundle_id"]
    fake.put(BUCKET, f"{bundle_id}/model.pkl", b"someone else's bytes")
    with pytest.raises(ValueError, match=r"already exists \(at least model.pkl\).*never overwritten"):
        write_bundle(files, manifest, f"gs://{BUCKET}")
    assert fake.buckets[BUCKET][f"{bundle_id}/model.pkl"].data == b"someone else's bytes"
    assert fake.buckets[BUCKET][f"{bundle_id}/model.pkl"].generation == 1
    assert f"{bundle_id}/{MANIFEST_FILENAME}" not in fake.buckets[BUCKET]  # the manifest is never written after a refusal


def test_write_files_never_reads(fake, scoring_env):
    files, manifest = _bundle_files(scoring_env.lr_bundle_uri)
    write_bundle(files, manifest, f"gs://{BUCKET}")
    assert {c[0] for c in fake.calls} == {"bucket", "upload"}  # no list, download, get_bucket, reload or exists


def test_write_files_forbidden_propagates(fake, scoring_env):
    files, manifest = _bundle_files(scoring_env.lr_bundle_uri)
    fake.forbidden = True
    with pytest.raises(api.Forbidden):
        write_bundle(files, manifest, f"gs://{BUCKET}")


@pytest.fixture(scope="module")
def loaded_gold(scoring_env):
    return load_gold_table_with_metadata(scoring_env.gold_dir)


def test_validate_bundle_reads_a_gcs_bundle_like_a_local_one(fake, scoring_env, loaded_gold):
    gold, metadata = loaded_gold
    local = validate_bundle(scoring_env.lr_bundle_uri, gold=gold, metadata=metadata)
    uri = _upload_local_bundle(fake, scoring_env.lr_bundle_uri, prefix="p/")
    pin = sha256_hex((Path(scoring_env.lr_bundle_uri) / MANIFEST_FILENAME).read_bytes())
    remote = validate_bundle(uri, gold=gold, metadata=metadata, expected_manifest_sha256=pin)
    assert remote.bundle_id == local.bundle_id and remote.bundle_uri == uri
    assert remote.model_bytes == local.model_bytes
    assert remote.partition_of.equals(local.partition_of)
    assert (remote.champion_sha256, local.champion_sha256) == (pin, None)


def _tamper(fake: FakeStorage, uri: str, how: str) -> str:
    _, prefix = gcs.parse_gs_uri(uri)
    objects = fake.buckets[BUCKET]
    if how == "no-manifest":
        del objects[f"{prefix}/{MANIFEST_FILENAME}"]
    elif how == "extra-object":
        fake.put(BUCKET, f"{prefix}/extra.txt", b"x")
    elif how == "subdirectory":
        fake.put(BUCKET, f"{prefix}/sub/x", b"x")
    elif how == "bad-sha":
        objects[f"{prefix}/model.pkl"].data += b"tampered"
    elif how == "wrong-prefix":
        for name in [n for n in objects if n.startswith(f"{prefix}/")]:
            fake.put(BUCKET, name.replace(prefix, "p/not-its-id", 1), objects[name].data)
        return f"gs://{BUCKET}/p/not-its-id"
    return uri


@pytest.mark.parametrize(
    "how, key",
    [
        ("no-manifest", "manifest: "),
        ("extra-object", "files: "),
        ("subdirectory", "files: "),
        ("bad-sha", "files: "),
        ("wrong-prefix", "bundle_id: "),
    ],
)
def test_gcs_integrity_cases(fake, scoring_env, loaded_gold, tmp_path, how, key):
    gold, metadata = loaded_gold
    uri = _tamper(fake, _upload_local_bundle(fake, scoring_env.lr_bundle_uri, prefix="p/"), how)
    with pytest.raises(ValueError) as remote:
        validate_bundle(uri, gold=gold, metadata=metadata)
    assert str(remote.value).startswith(key), str(remote.value)
