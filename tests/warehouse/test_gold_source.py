"""The gold table from GCS (D2): downloaded byte-identically into a temp dir and read by the unchanged loader, so the
fingerprint guard holds."""

from __future__ import annotations

import pytest
from google.api_core import exceptions as api
from google.auth.exceptions import RefreshError

from readmission_risk.models.data import gold_fingerprint, load_gold_table_with_metadata
from readmission_risk.warehouse import gold_source
from readmission_risk.warehouse.gold_source import materialized_gold_dir
from tests.bundles.fake_gcs import FakeStorage

BUCKET = "fake-gold"


@pytest.fixture
def fake(monkeypatch):
    store = FakeStorage()
    monkeypatch.setattr("readmission_risk.gcp.storage_client", lambda: store)
    return store


def _upload_gold(store: FakeStorage, gold_dir, prefix="full_run_2b") -> str:
    for path in sorted(gold_dir.iterdir()):
        store.put(BUCKET, f"{prefix}/{path.name}", path.read_bytes())
    return f"gs://{BUCKET}/{prefix}"


def test_local_passthrough(tmp_path):
    with materialized_gold_dir(str(tmp_path)) as gold_dir:
        assert gold_dir == tmp_path
    with pytest.raises(ValueError, match="unsupported gold URI scheme"), materialized_gold_dir("s3://x/y"):
        pass


def test_downloads_only_parquet_and_metadata_and_fingerprint_matches(fake, scoring_env):
    uri = _upload_gold(fake, scoring_env.gold_dir)
    fake.put(BUCKET, "full_run_2b/_SUCCESS", b"")
    fake.put(BUCKET, "full_run_2b/.part-00000.snappy.parquet.crc", b"crc")
    fake.put(BUCKET, "full_run_2b/", b"")  # a console folder placeholder
    local_gold, local_meta = load_gold_table_with_metadata(scoring_env.gold_dir)
    with materialized_gold_dir(uri) as gold_dir:
        names = sorted(p.name for p in gold_dir.iterdir())
        remote_gold, remote_meta = load_gold_table_with_metadata(gold_dir)
        kept = gold_dir
    assert all(n.endswith(".parquet") or n == "_gold_metadata.json" for n in names)
    assert gold_fingerprint(remote_gold) == gold_fingerprint(local_gold)
    assert remote_meta == local_meta
    assert not kept.exists()  # the temp dir is removed on exit


def test_temp_dir_removed_on_error(fake, scoring_env):
    uri = _upload_gold(fake, scoring_env.gold_dir)
    with pytest.raises(RuntimeError, match="boom"), materialized_gold_dir(uri) as gold_dir:
        kept = gold_dir
        raise RuntimeError("boom")
    assert not kept.exists()


@pytest.mark.parametrize(
    "extra, message",
    [("full_run_2b/notes.txt", "unexpected object 'notes.txt'"), ("full_run_2b/sub/part.parquet", "unexpected object 'sub/part.parquet'")],
)
def test_refuses_unexpected_objects(fake, scoring_env, extra, message):
    uri = _upload_gold(fake, scoring_env.gold_dir)
    fake.put(BUCKET, extra, b"x")
    with pytest.raises(ValueError, match=message), materialized_gold_dir(uri):
        pass


def test_refuses_oversize_and_too_many(fake, scoring_env, monkeypatch):
    uri = _upload_gold(fake, scoring_env.gold_dir)
    monkeypatch.setattr(gold_source, "MAX_TOTAL_BYTES", 10)
    with pytest.raises(ValueError, match="refusing more than 10"), materialized_gold_dir(uri):
        pass
    monkeypatch.setattr(gold_source, "MAX_TOTAL_BYTES", 512 * 1024**2)
    monkeypatch.setattr(gold_source, "MAX_OBJECTS", 1)
    with pytest.raises(ValueError, match="more than 1 objects"), materialized_gold_dir(uri):
        pass
    # the listing itself is bounded: one page of MAX_OBJECTS + 1, never the whole prefix
    assert [c[3] for c in fake.calls if c[0] == "list"][-1] == 2


def test_maps_missing_and_forbidden(fake, scoring_env):
    with pytest.raises(FileNotFoundError, match="no such bucket"), materialized_gold_dir("gs://no-such-bucket/x"):
        pass
    fake.put(BUCKET, "other/x.parquet", b"x")
    with pytest.raises(FileNotFoundError, match="No Parquet files"), materialized_gold_dir(f"gs://{BUCKET}/empty"):
        pass
    fake.forbidden = True
    with pytest.raises(ValueError, match="cannot read gold"), materialized_gold_dir(f"gs://{BUCKET}/other"):
        pass
    fake.forbidden, fake.auth_error = False, RefreshError("token refresh failed")
    with pytest.raises(ValueError, match="cannot read gold"), materialized_gold_dir(f"gs://{BUCKET}/other"):
        pass


def test_a_forbidden_download_is_a_value_error(fake, scoring_env, monkeypatch):
    uri = _upload_gold(fake, scoring_env.gold_dir)

    def deny(self, filename):
        raise api.Forbidden("403 storage.objects.get denied")

    monkeypatch.setattr("tests.bundles.fake_gcs.FakeBlob.download_to_filename", deny)
    with pytest.raises(ValueError, match="cannot read gold"), materialized_gold_dir(uri):
        pass


def test_list_gold_objects_returns_parts_and_metadata_and_raises_api_errors_unwrapped(fake, scoring_env):
    """v3 slice 1: the gold publish classifies Google errors itself, so list_gold_objects re-raises them unwrapped (only
    materialized_gold_dir keeps turning them into ValueError, tested above)."""
    uri = _upload_gold(fake, scoring_env.gold_dir)
    fake.put(BUCKET, "full_run_2b/_SUCCESS", b"")
    names = [name for name, _ in gold_source.list_gold_objects(fake, uri)]
    assert "_gold_metadata.json" in names and all(n.endswith(".parquet") or n == "_gold_metadata.json" for n in names)
    fake.forbidden = True
    with pytest.raises(api.Forbidden):
        gold_source.list_gold_objects(fake, uri)
    fake.forbidden, fake.auth_error = False, RefreshError("token refresh failed")
    with pytest.raises(RefreshError):
        gold_source.list_gold_objects(fake, uri)
