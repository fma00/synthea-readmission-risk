"""The gold table from GCS (owner decision D2): a gs:// prefix is downloaded into a temporary directory and read by the
UNCHANGED load_gold_table_with_metadata, so guard 6's pandas-csv-sha256-v1 fingerprint is computed exactly as for a local
copy (L2 checks the objects are byte-identical). A local path passes through untouched. See
notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md (Interfaces > warehouse/gold_source.py).
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from readmission_risk import gcp
from readmission_risk.bundles.gcs import parse_gs_uri

MAX_OBJECTS = 1000
MAX_TOTAL_BYTES = 512 * 1024**2
_IGNORED_RE = re.compile(r"_SUCCESS|\..*\.crc")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def _download_failures() -> tuple[type[BaseException], ...]:
    from google.api_core.exceptions import GoogleAPIError
    from google.auth.exceptions import GoogleAuthError

    return (GoogleAPIError, GoogleAuthError)


def list_gold_objects(storage, gold_uri: str) -> list[tuple[str, object]]:
    """The validated objects of a gs:// gold prefix, as (name relative to the prefix, blob) pairs: a bounded listing
    (MAX_OBJECTS + 1, one page), `_SUCCESS`, `.crc` files and folder placeholders skipped, only Parquet parts and
    _gold_metadata.json allowed (no subdirectories), at most MAX_TOTAL_BYTES, and at least one Parquet part. ValueError or
    FileNotFoundError for a refusal (a missing bucket is FileNotFoundError); every other Google API or auth error is
    re-raised UNWRAPPED, so the caller classifies it (the gold publish, v3 slice 1; materialized_gold_dir wraps it)."""
    from google.api_core.exceptions import NotFound

    bucket_name, prefix = parse_gs_uri(gold_uri)
    bucket = storage.bucket(bucket_name)
    try:
        # bounded: a wrong --gold-dir (a bucket root, a large tree) is refused after one page, not after listing it all
        blobs = list(bucket.list_blobs(prefix=f"{prefix}/", max_results=MAX_OBJECTS + 1))
    except NotFound:
        raise FileNotFoundError(f"No Parquet files found in gold directory: {gold_uri} (no such bucket)") from None

    if len(blobs) > MAX_OBJECTS:
        raise ValueError(f"more than {MAX_OBJECTS} objects under {gold_uri}; refusing to download them")
    wanted = []
    for blob in blobs:
        name = blob.name[len(prefix) + 1 :]
        if name == "" or _IGNORED_RE.fullmatch(name):
            continue
        if "/" in name or not (name.endswith(".parquet") or name == "_gold_metadata.json"):
            raise ValueError(f"unexpected object {name!r} under {gold_uri} (a gold prefix holds only Parquet parts and _gold_metadata.json)")
        wanted.append((name, blob))
    total = sum(blob.size or 0 for _, blob in wanted)
    if total > MAX_TOTAL_BYTES:
        raise ValueError(f"{total} bytes under {gold_uri}; refusing more than {MAX_TOTAL_BYTES}")
    if not any(name.endswith(".parquet") for name, _ in wanted):
        raise FileNotFoundError(f"No Parquet files found in gold directory: {gold_uri}")
    return wanted


@contextmanager
def materialized_gold_dir(gold_uri: str) -> Iterator[Path]:
    if not gold_uri.startswith("gs://"):
        match = _SCHEME_RE.match(gold_uri)
        if match:
            raise ValueError(f"unsupported gold URI scheme {match.group(0)!r}: {gold_uri}")
        yield Path(gold_uri)
        return

    try:
        wanted = list_gold_objects(gcp.storage_client(), gold_uri)
    except _download_failures() as exc:
        raise ValueError(f"cannot read gold at {gold_uri}: {exc}") from exc

    with tempfile.TemporaryDirectory(prefix="gold-") as tmp:
        for name, blob in wanted:
            try:
                blob.download_to_filename(str(Path(tmp) / name))
            except _download_failures() as exc:
                raise ValueError(f"cannot read gold at {gold_uri}: {exc}") from exc
        yield Path(tmp)
