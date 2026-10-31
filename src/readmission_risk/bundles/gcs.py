"""Bundles in GCS: gs://<bucket>/[<prefix>/]<bundle_id>/{manifest.json, model.pkl, split_assignment.csv}. A reader (the
scorer: storage.objects.list + get) and a create-only writer (bundle-writer: storage.objectCreator, every upload with
if_generation_match=0, so an existing object is never replaced -- PRD FR7). Reached only through bundles.store, which
imports this module lazily; google.* is imported inside the functions. See
notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md (Interfaces > bundles/gcs.py).
"""

from __future__ import annotations

import re

from readmission_risk import gcp

from .manifest import MANIFEST_FILENAME

_BUCKET_RE = re.compile(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]")


def parse_gs_uri(uri: str) -> tuple[str, str]:
    """'gs://bucket/a/b' -> ('bucket', 'a/b'); one trailing '/' is stripped. ValueError for anything else."""
    if not uri.startswith("gs://"):
        raise ValueError(f"not a gs:// URI: {uri}")
    rest = uri[len("gs://") :]
    if rest.endswith("/"):
        rest = rest[:-1]
    bucket, _, prefix = rest.partition("/")
    if not _BUCKET_RE.fullmatch(bucket):
        raise ValueError(f"invalid bucket name {bucket!r} in {uri}")
    if not prefix:
        raise ValueError(f"no object prefix in {uri}")
    if any(segment in ("", ".", "..") for segment in prefix.split("/")):
        raise ValueError(f"empty, '.' or '..' path segment in {uri}")
    return bucket, prefix


def _read_error(exc: BaseException, uri: str, what: str) -> OSError:
    """Maps a GCS read failure: NotFound -> FileNotFoundError; Forbidden -> PermissionError; any other API, auth or
    transport error -> OSError (bundle_guard turns OSError into its keyed "cannot read bundle" ValueError)."""
    from google.api_core import exceptions as api

    if isinstance(exc, api.NotFound):
        if what == "listing":  # the bucket itself is missing
            return FileNotFoundError(f"bundle not found: no bundle at {uri}")
        return FileNotFoundError(f"bundle at {uri} has no {what}")
    if isinstance(exc, api.Forbidden):
        return PermissionError(f"cannot read {what} of bundle at {uri}: {exc}")
    return OSError(f"cannot read {what} of bundle at {uri}: {exc}")


def _read_failures() -> tuple[type[BaseException], ...]:
    """The exception classes a GCS read can fail with (API, auth, transport), imported lazily."""
    from google.api_core.exceptions import GoogleAPIError
    from google.auth.exceptions import GoogleAuthError

    return (GoogleAPIError, GoogleAuthError, OSError)


def list_entries(uri: str) -> list[str]:
    """Sorted, de-duplicated names directly under the bundle prefix. A deeper object 'x/y' contributes 'x' (so the
    exact-entry check still refuses a subdirectory); a zero-byte console 'folder' placeholder is skipped."""
    bucket_name, prefix = parse_gs_uri(uri)
    bucket = gcp.storage_client().bucket(bucket_name)  # never get_bucket(): the job roles lack storage.buckets.get
    try:
        names = [blob.name for blob in bucket.list_blobs(prefix=f"{prefix}/")]
    except _read_failures() as exc:
        raise _read_error(exc, uri, "listing") from exc
    entries = {name[len(prefix) + 1 :].split("/", 1)[0] for name in names}
    entries.discard("")
    if not entries:
        raise FileNotFoundError(f"bundle not found: no bundle at {uri}")
    return sorted(entries)


def read_file(uri: str, name: str, *, client=None) -> bytes:
    """One object's bytes. `client` (optional) is a storage client to use instead of gcp.storage_client() (the gold
    publish injects its own, v3 slice 1)."""
    if name in ("", ".", "..") or "/" in name or "\\" in name:
        raise ValueError(f"invalid bundle file name {name!r}")
    bucket_name, prefix = parse_gs_uri(uri)
    blob = (client if client is not None else gcp.storage_client()).bucket(bucket_name).blob(f"{prefix}/{name}")
    try:
        return blob.download_as_bytes()
    except _read_failures() as exc:
        raise _read_error(exc, uri, name) from exc


def write_files(expected: dict[str, bytes], bundles_root: str, bundle_id: str) -> tuple[str, bool]:
    """Uploads every file with if_generation_match=0, manifest.json LAST, retry=None: with the precondition, a retried
    upload whose first attempt had in fact succeeded would 412 against its own object, so a transient error fails the
    export instead (re-running then reports "already exists" for what landed). The writer cannot read, so an existing
    object is reported, never compared. Returns (bundle_uri, True)."""
    from google.api_core.exceptions import PreconditionFailed

    uri = f"{bundles_root.rstrip('/')}/{bundle_id}"
    bucket_name, prefix = parse_gs_uri(uri)
    bucket = gcp.storage_client().bucket(bucket_name)
    order = [name for name in sorted(expected) if name != MANIFEST_FILENAME] + [MANIFEST_FILENAME]
    for name in order:
        blob = bucket.blob(f"{prefix}/{name}")
        try:
            blob.upload_from_string(
                expected[name], content_type="application/octet-stream", if_generation_match=0, retry=None
            )
        except PreconditionFailed:
            raise ValueError(
                f"bundle {bundle_id} already exists (at least {name}) at {uri}; bundles are never overwritten, and this "
                "identity cannot read to compare (verify with a reader: docs/setup.md > Bundles in GCS)"
            ) from None
    return uri, True
