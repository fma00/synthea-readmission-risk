"""readmission_risk.gcp (v2 slice 4): which credentials get a quota project, impersonation, and that the module imports
no google package at import time. Marked real_gcp_module: the autouse guard leaves gcp's functions alone here, and each
test fakes google.auth.default itself, so nothing can reach the network."""

from __future__ import annotations

import subprocess
import sys

import google.auth
import pytest
from google.auth import credentials as ga_credentials
from google.auth import impersonated_credentials
from google.auth.exceptions import DefaultCredentialsError
from google.oauth2 import credentials as user_credentials

from readmission_risk import gcp

pytestmark = pytest.mark.real_gcp_module


class _ExternalAccount(ga_credentials.CredentialsWithQuotaProject):
    """Stands in for the external_account credential google-github-actions/auth writes in CI."""

    def __init__(self):
        super().__init__()
        self.quota_calls = []

    def refresh(self, request):  # pragma: no cover - never refreshed in these tests
        raise AssertionError("no network")

    def with_quota_project(self, quota_project_id):
        self.quota_calls.append(quota_project_id)
        return self


@pytest.fixture(autouse=True)
def _reset():
    gcp.configure(impersonate_service_account=None)
    yield
    gcp.configure(impersonate_service_account=None)


def _adc(monkeypatch, credential):
    monkeypatch.setattr(google.auth, "default", lambda scopes=None: (credential, "some-other-project"))


def test_quota_project_only_for_user_credentials(monkeypatch):
    user = user_credentials.Credentials(token="t", quota_project_id="some-other-project")  # noqa: S106
    _adc(monkeypatch, user)
    assert gcp.credentials().quota_project_id == gcp.PROJECT_ID

    external = _ExternalAccount()
    _adc(monkeypatch, external)
    assert gcp.credentials() is external and external.quota_calls == []  # used unchanged (CI)


def test_impersonation_clears_a_user_sources_quota_project(monkeypatch):
    user = user_credentials.Credentials(token="t", quota_project_id="some-other-project")  # noqa: S106
    _adc(monkeypatch, user)
    gcp.configure(impersonate_service_account="scorer@synthea-readmission-risk.iam.gserviceaccount.com")
    creds = gcp.credentials()
    assert isinstance(creds, impersonated_credentials.Credentials)
    assert creds.service_account_email == "scorer@synthea-readmission-risk.iam.gserviceaccount.com"
    assert creds._source_credentials.quota_project_id is None

    external = _ExternalAccount()
    _adc(monkeypatch, external)
    source = gcp.credentials()._source_credentials  # the library keeps its own copy of the source
    assert isinstance(source, _ExternalAccount) and external.quota_calls == [] and source.quota_calls == []


@pytest.mark.parametrize("email", ["scorer", "user@example.com", "UPPER@x.iam.gserviceaccount.com", ""])
def test_configure_validates_email(email):
    with pytest.raises(ValueError, match="service-account email"):
        gcp.configure(impersonate_service_account=email)


def test_configure_resets_cached_clients():
    gcp._clients["bigquery"] = object()
    gcp.configure(impersonate_service_account=None)
    assert gcp._clients == {}


def test_missing_adc_is_file_not_found(monkeypatch):
    def none(scopes=None):
        raise DefaultCredentialsError("nothing")

    monkeypatch.setattr(google.auth, "default", none)
    with pytest.raises(FileNotFoundError, match="application-default login"):
        gcp.credentials()


def test_gcp_imports_without_google():
    code = (
        "import sys\n"
        "sys.modules['google'] = None\n"  # any `import google...` now raises
        "import readmission_risk.gcp, readmission_risk.bundles.store, readmission_risk.bundles.gcs\n"
        "import readmission_risk.warehouse.run, readmission_risk.warehouse.seeded_check\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def test_storage_client_switches_off_the_unused_bucket_metadata_lookup(monkeypatch):
    from google.cloud import storage

    built = []
    monkeypatch.delenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", raising=False)
    monkeypatch.setattr(storage, "Client", lambda **kwargs: built.append(kwargs) or object())
    _adc(monkeypatch, _ExternalAccount())
    gcp.storage_client()
    assert built and __import__("os").environ["DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA"] == "true"

    gcp.configure(impersonate_service_account=None)  # drops the cached client
    monkeypatch.setenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "false")  # an explicit choice wins
    gcp.storage_client()
    assert __import__("os").environ["DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA"] == "false"


_CI_MARKERS = ("PYTEST_CURRENT_TEST", "GITHUB_ACTIONS", "CI", "CIRCLECI", "GITLAB_CI", "JENKINS_URL", "TRAVIS",
               "BUILDKITE", "TF_BUILD", "CODEBUILD_BUILD_ID", "BITBUCKET_BUILD_NUMBER", "DO_NOT_TRACK")  # fmt: skip


def _run_without_ci_markers(code: str, **extra_env) -> subprocess.CompletedProcess:
    """A fresh interpreter that MLflow does NOT treat as a test or CI run, with every outbound socket connection refused
    (so even a broken opt-out could not reach the network from this test)."""
    import os

    env = {k: v for k, v in os.environ.items() if k not in _CI_MARKERS and k != "MLFLOW_DISABLE_TELEMETRY"}
    env.update(extra_env)
    guard = (
        "import socket\n"
        "def _refuse(*a, **k):\n"
        "    raise OSError('network disabled in this test')\n"
        "socket.socket.connect = _refuse\n"
        "socket.create_connection = _refuse\n"
    )
    return subprocess.run([sys.executable, "-c", guard + code], capture_output=True, text=True, check=False, env=env)


def test_importing_the_package_opts_out_of_mlflow_telemetry():
    code = (
        "import os, readmission_risk.models.tracking\n"
        "from mlflow.telemetry.client import get_telemetry_client\n"
        "assert os.environ['MLFLOW_DISABLE_TELEMETRY'] == 'true'\n"
        "assert get_telemetry_client() is None, 'mlflow created a telemetry client'\n"
    )
    done = _run_without_ci_markers(code)
    assert done.returncode == 0, done.stderr


def test_an_explicit_mlflow_telemetry_choice_wins():
    done = _run_without_ci_markers(
        "import os, readmission_risk\nassert os.environ['MLFLOW_DISABLE_TELEMETRY'] == 'false'\n",
        MLFLOW_DISABLE_TELEMETRY="false",
    )
    assert done.returncode == 0, done.stderr
