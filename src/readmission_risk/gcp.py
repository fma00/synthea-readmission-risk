"""Credentials and lazily built Google Cloud clients, shared by the GCS bundle store, the gold download and the BigQuery
writer. No `google` import at module level: CI's pipeline-dataproc-runtime job imports tests/conftest.py (which patches
this module) under requirements-dataproc-3.0.txt, which has no google-* packages. Consumers look the clients up at call
time (`gcp.bigquery_client()`), never bind them, so the test guard's monkeypatch reaches every call. See
notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md (Interfaces > gcp.py).
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from google.auth.credentials import Credentials
    from google.cloud import bigquery, storage

PROJECT_ID = "synthea-readmission-risk"
LOCATION = "us-central1"
_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)
_SA_EMAIL_RE = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z0-9-]+\.iam\.gserviceaccount\.com")

_impersonate: str | None = None
_clients: dict[str, object] = {}


def configure(*, impersonate_service_account: str | None) -> None:
    """Process-wide; called once by a CLI before any client is built. Resets cached clients. Never called: no
    impersonation."""
    global _impersonate
    if impersonate_service_account is not None and not _SA_EMAIL_RE.fullmatch(impersonate_service_account):
        raise ValueError(
            f"--impersonate-service-account must be a service-account email; got {impersonate_service_account!r}"
        )
    _impersonate = impersonate_service_account
    _clients.clear()


def credentials() -> Credentials:
    """ADC, optionally impersonating the configured service account. Only a user's own credentials get a quota project
    (this project, where the operator is Owner): an external_account (CI) or service-account credential is used
    unchanged, because x-goog-user-project would need serviceusage.services.use, which the job accounts lack."""
    import google.auth
    from google.auth import impersonated_credentials
    from google.auth.exceptions import DefaultCredentialsError
    from google.oauth2 import credentials as user_credentials

    try:
        source, _ = google.auth.default(scopes=list(_SCOPES))
    except DefaultCredentialsError as exc:
        raise FileNotFoundError(
            "no Application Default Credentials: run `gcloud auth application-default login`, or score offline with "
            f"--bundle bundles/<id> and a local --gold-dir ({exc})"
        ) from exc
    is_user = isinstance(source, user_credentials.Credentials)
    if _impersonate is None:
        return source.with_quota_project(PROJECT_ID) if is_user else source
    if is_user:
        # mirrors CLOUDSDK_BILLING_QUOTA_PROJECT=LEGACY: the ADC file's quota project is deliberately another project
        source = source.with_quota_project(None)
    return impersonated_credentials.Credentials(
        source_credentials=source,
        target_principal=_impersonate,
        target_scopes=list(_SCOPES),
        lifetime=3600,
    )


def storage_client() -> storage.Client:
    if "storage" not in _clients:
        # google-cloud-storage 3.16 queues a background get_bucket for its tracing metadata on the first touch of every
        # bucket, even with its OpenTelemetry tracing off (the default here), so the lookup is never used; the job roles
        # lack storage.buckets.get, so it is a refused request each time. This is the library's own switch.
        os.environ.setdefault("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "true")
        from google.cloud import storage

        _clients["storage"] = storage.Client(project=PROJECT_ID, credentials=credentials())
    return _clients["storage"]  # type: ignore[return-value]


def bigquery_client() -> bigquery.Client:
    if "bigquery" not in _clients:
        from google.cloud import bigquery

        _clients["bigquery"] = bigquery.Client(project=PROJECT_ID, credentials=credentials(), location=LOCATION)
    return _clients["bigquery"]  # type: ignore[return-value]
