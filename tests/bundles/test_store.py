from __future__ import annotations

import os
import stat

import pytest

from readmission_risk.bundles import store
from readmission_risk.bundles.manifest import (
    compute_bundle_id,
    file_entry,
    manifest_bytes,
)
from readmission_risk.bundles.store import (
    bundle_uri_leaf,
    check_bundle_uri,
    list_bundle_entries,
    local_bundle_path,
    read_bundle_file,
    write_bundle,
)

FILES = {"model.pkl": b"model-bytes", "split_assignment.csv": b"encounter_id,partition\ne1,test\n"}


def _manifest(files=FILES, **extra) -> dict:
    manifest = {"schema_version": 1, "files": {n: file_entry(n, d) for n, d in files.items()}, **extra}
    return {**manifest, "bundle_id": compute_bundle_id(manifest)}


def _mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def test_write_bundle_layout_permissions_and_uri(tmp_path):
    manifest = _manifest()
    root = tmp_path / "bundles"
    uri, created = write_bundle(FILES, manifest, root)
    target = root / manifest["bundle_id"]
    assert created is True and uri == str(target)
    assert sorted(os.listdir(target)) == ["manifest.json", "model.pkl", "split_assignment.csv"]
    assert (target / "manifest.json").read_bytes() == manifest_bytes(manifest)
    assert (target / "model.pkl").read_bytes() == FILES["model.pkl"]
    assert _mode(target) == 0o555 and all(_mode(target / n) == 0o444 for n in os.listdir(target))
    assert [p.name for p in root.iterdir()] == [manifest["bundle_id"]]  # no .tmp-* left behind


def test_rewrite_identical_is_a_noop(tmp_path):
    manifest = _manifest()
    uri, _ = write_bundle(FILES, manifest, tmp_path)
    before = {n: os.stat(os.path.join(uri, n)).st_mtime_ns for n in os.listdir(uri)}
    assert write_bundle(FILES, manifest, tmp_path) == (uri, False)
    assert {n: os.stat(os.path.join(uri, n)).st_mtime_ns for n in os.listdir(uri)} == before


def test_existing_id_with_different_content_is_refused(tmp_path):
    manifest = _manifest()
    uri, _ = write_bundle(FILES, manifest, tmp_path)
    os.chmod(uri, 0o700)
    model = os.path.join(uri, "model.pkl")
    os.chmod(model, 0o644)
    with open(model, "ab") as handle:
        handle.write(b"x")
    with pytest.raises(ValueError, match="never overwritten"):
        write_bundle(FILES, manifest, tmp_path)


def test_existing_empty_directory_is_refused(tmp_path):
    manifest = _manifest()
    (tmp_path / manifest["bundle_id"]).mkdir()
    with pytest.raises(ValueError, match="never overwritten"):
        write_bundle(FILES, manifest, tmp_path)
    assert os.listdir(tmp_path / manifest["bundle_id"]) == []


@pytest.mark.parametrize(
    "files, manifest_fn",
    [
        ({"model.pkl": b"x"}, lambda: _manifest({"model.pkl": b"x"})),  # wrong file set
        (FILES, lambda: _manifest({**FILES, "model.pkl": b"other"})),  # sha mismatch
        (FILES, lambda: {**_manifest(), "bundle_id": "0" * 64}),  # bundle_id not the content hash
    ],
)
def test_validation_happens_before_touching_disk(tmp_path, files, manifest_fn):
    root = tmp_path / "bundles"
    with pytest.raises(ValueError):
        write_bundle(files, manifest_fn(), root)
    assert not root.exists()


def test_failed_write_leaves_no_temp_dir_and_no_bundle(tmp_path, monkeypatch):
    def boom(src, dst):
        raise OSError("disk on fire")

    monkeypatch.setattr(store.os, "rename", boom)
    with pytest.raises(OSError, match="disk on fire"):
        write_bundle(FILES, _manifest(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def _racing_rename(monkeypatch, other_files, other_manifest, root):
    real_rename = os.rename

    def race(src, dst):
        monkeypatch.setattr(store.os, "rename", real_rename)
        write_bundle(other_files, other_manifest, root)  # another writer wins first
        raise OSError("target exists")

    monkeypatch.setattr(store.os, "rename", race)


def test_rename_race_with_identical_bundle_is_a_noop(tmp_path, monkeypatch):
    manifest = _manifest()
    _racing_rename(monkeypatch, FILES, manifest, tmp_path)
    uri, created = write_bundle(FILES, manifest, tmp_path)
    assert created is False and uri == str(tmp_path / manifest["bundle_id"])
    assert [p.name for p in tmp_path.iterdir()] == [manifest["bundle_id"]]


def test_rename_race_with_different_content_is_refused(tmp_path, monkeypatch):
    manifest = _manifest()
    real_rename = os.rename

    def race(src, dst):
        os.mkdir(dst)  # an unexpected entry appears at the target
        monkeypatch.setattr(store.os, "rename", real_rename)
        raise OSError("target exists")

    monkeypatch.setattr(store.os, "rename", race)
    with pytest.raises(ValueError, match="never overwritten"):
        write_bundle(FILES, manifest, tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == [manifest["bundle_id"]]


def test_local_bundle_path_refuses_gs():
    # a gs:// URI is not a local path; list_bundle_entries dispatches it to bundles.gcs (tests/bundles/test_gcs.py)
    with pytest.raises(ValueError, match="not a local bundle path"):
        local_bundle_path("gs://bucket/x")


@pytest.mark.parametrize("uri, message", [("s3://b/x", "unsupported bundle URI scheme"), ("", "empty")])
def test_other_schemes_are_refused(uri, message):
    with pytest.raises(ValueError, match=message):
        local_bundle_path(uri)
    with pytest.raises(ValueError, match=message):
        list_bundle_entries(uri)


@pytest.mark.parametrize(
    "uri, message",
    [
        ("", "empty bundle URI"),
        ("s3://b/x", "unsupported bundle URI scheme"),
        ("gs://b/x", "invalid bucket name"),
        ("gs://bucket", "no object prefix"),
        ("gs://bucket/a//b", "segment"),
        ("gs://bucket/../x", "segment"),
    ],
)
def test_check_bundle_uri_refuses(uri, message):
    with pytest.raises(ValueError, match=message):
        check_bundle_uri(uri)


@pytest.mark.parametrize("uri", ["gs://bucket/abc", "gs://bucket/p/abc/", "bundles/abc", "/tmp/x/abc"])
def test_check_bundle_uri_accepts(uri):
    check_bundle_uri(uri)  # no exception, and no client call (the autouse no-GCP guard would raise)


def test_bundle_uri_leaf(tmp_path):
    assert bundle_uri_leaf("gs://bucket/p/q/abc") == "abc"
    assert bundle_uri_leaf("gs://bucket/abc/") == "abc"
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    assert bundle_uri_leaf(str(tmp_path / "link")) == "real"  # local: the resolved directory name, as before


def test_read_refuses_symlink_directory_and_path_traversal(tmp_path):
    uri, _ = write_bundle(FILES, _manifest(), tmp_path / "bundles")
    os.chmod(uri, 0o700)
    os.symlink(tmp_path / "outside", os.path.join(uri, "link"))
    os.mkdir(os.path.join(uri, "sub"))
    for name in ("link", "sub"):
        with pytest.raises(ValueError, match="not a regular file"):
            read_bundle_file(uri, name)
    for name in ("..", ".", "", "a/b", "a\\b"):
        with pytest.raises(ValueError, match="invalid bundle file name"):
            read_bundle_file(uri, name)
    with pytest.raises(FileNotFoundError):
        read_bundle_file(uri, "absent.txt")
    assert read_bundle_file(uri, "model.pkl") == FILES["model.pkl"]
    assert list_bundle_entries(uri) == ["link", "manifest.json", "model.pkl", "split_assignment.csv", "sub"]


def test_missing_bundle_and_non_directory(tmp_path):
    with pytest.raises(FileNotFoundError, match="bundle not found: no bundle at"):
        list_bundle_entries(str(tmp_path / "absent"))
    file = tmp_path / "file"
    file.write_text("x")
    with pytest.raises(ValueError, match="is not a directory"):
        list_bundle_entries(str(file))
