"""pipeline/storage.py: LocalStorage (pathlib) and HadoopStorage (Spark's Hadoop FileSystem), the latter exercised on file://
URIs because tests never reach GCS. Design: notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md
(Interfaces section 3)."""

from pathlib import Path

import pytest

from readmission_risk.pipeline.storage import HadoopStorage, LocalStorage, storage_for


@pytest.fixture(params=["local", "hadoop"])
def store(request, spark, tmp_path):
    """(storage, base location) pairs: a plain path for LocalStorage, a file:// URI for HadoopStorage."""
    if request.param == "local":
        return LocalStorage(), str(tmp_path)
    return HadoopStorage(spark), tmp_path.as_uri()


def test_storage_for_routes_local_path_and_plain_str(tmp_path):
    for location in (tmp_path, str(tmp_path), "relative/dir"):
        storage, text = storage_for(location, None)
        assert isinstance(storage, LocalStorage)
        assert text == str(location)


def test_storage_for_routes_gs(spark):
    storage, text = storage_for("gs://bucket/gold/v2/run/", spark)
    assert isinstance(storage, HadoopStorage)
    assert text == "gs://bucket/gold/v2/run"


@pytest.mark.parametrize("location", ["s3://bucket/x", "file:///tmp/x", "https://example.invalid/x"])
def test_storage_for_rejects_other_schemes(location):
    with pytest.raises(ValueError, match="unsupported location scheme"):
        storage_for(location, None)


def test_gs_requires_spark():
    with pytest.raises(ValueError, match="SparkSession is required"):
        storage_for("gs://bucket/x", None)


def test_gs_path_object_rejected():
    with pytest.raises(ValueError, match="must be passed as str"):
        storage_for(Path("gs://bucket/x"), None)


def test_hadoop_storage_rejects_connect_session():
    class NoJvm:
        pass

    with pytest.raises(RuntimeError, match="classic SparkSession"):
        HadoopStorage(NoJvm())


def test_exists_and_is_file(store, tmp_path):
    storage, base = store
    (tmp_path / "a.csv").write_text("h\n1\n")
    (tmp_path / "d").mkdir()
    assert storage.exists(storage.join(base, "a.csv"))
    assert storage.is_file(storage.join(base, "a.csv"))
    assert storage.exists(storage.join(base, "d"))
    assert not storage.is_file(storage.join(base, "d"))
    assert not storage.exists(storage.join(base, "missing.csv"))
    assert not storage.is_file(storage.join(base, "missing.csv"))


def test_has_data_row(store, tmp_path):
    storage, base = store
    (tmp_path / "header_only.csv").write_text("a,b\n")
    (tmp_path / "one_row.csv").write_text("a,b\n1,2\n")
    (tmp_path / "empty.csv").write_text("")
    assert not storage.has_data_row(storage.join(base, "header_only.csv"))
    assert storage.has_data_row(storage.join(base, "one_row.csv"))
    assert not storage.has_data_row(storage.join(base, "empty.csv"))
    with pytest.raises(FileNotFoundError):
        storage.has_data_row(storage.join(base, "missing.csv"))


def test_read_text_utf8_round_trip(store, tmp_path):
    storage, base = store
    text = '{\n  "name": "Zoë é ✓"\n}\n'
    (tmp_path / "m.json").write_text(text, encoding="utf-8")
    assert storage.read_text(storage.join(base, "m.json")) == text
    with pytest.raises(FileNotFoundError):
        storage.read_text(storage.join(base, "missing.json"))


def test_write_text_new_refuses_existing(store, tmp_path):
    storage, base = store
    uri = storage.join(base, "out.json")
    storage.write_text_new(uri, "Zoë\n")
    assert (tmp_path / "out.json").read_text(encoding="utf-8") == "Zoë\n"
    with pytest.raises(FileExistsError):
        storage.write_text_new(uri, "again\n")
    assert (tmp_path / "out.json").read_text(encoding="utf-8") == "Zoë\n"


def test_local_write_leaves_no_crc_or_tmp(tmp_path):
    LocalStorage().write_text_new(str(tmp_path / "out.json"), "x\n")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.json"]


def test_hadoop_errors_become_os_error(spark):
    storage = HadoopStorage(spark)
    with pytest.raises(OSError, match="nosuchscheme://bucket/x"):
        storage.exists("nosuchscheme://bucket/x")
