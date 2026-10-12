"""ops/gcloud.py: the runner seam that keeps tests away from GCP, and the "no match" vs "real error" distinction."""

from __future__ import annotations

import ast
import subprocess
import sys

import pytest

from readmission_risk.ops import gcloud
from tests.ops.conftest import REPO


def _result(code: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


def test_default_runner_refused_in_tests():
    with pytest.raises(RuntimeError, match="must not run gcloud"):
        gcloud.resolve_runner(None)(["gcloud", "storage", "ls", "gs://x"])


def test_gcloud_module_is_stdlib_plus_gcp_only():
    tree = ast.parse((REPO / "src/readmission_risk/ops/gcloud.py").read_text())
    imported = [n.name for node in ast.walk(tree) if isinstance(node, ast.Import) for n in node.names]
    imported += [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level == 0]
    assert [m for m in imported if m.split(".")[0] not in sys.stdlib_module_names and m != "__future__"] == ["readmission_risk.gcp"]
    assert (REPO / "src/readmission_risk/ops/__init__.py").read_text() == ""


def test_no_bound_default_runner():
    """A `run=default_runner` default or `from ... import default_runner` would bypass the test guard's monkeypatch."""
    offenders = []
    for path in [*(REPO / "src/readmission_risk/ops").glob("*.py"), *(REPO / "scripts").glob("*.py")]:
        text = path.read_text()
        if "= default_runner" in text or "import default_runner" in text or "default_runner," in text:
            offenders.append(path.name)
    assert offenders == []


def test_gcloud_env_disables_composite_uploads():
    assert gcloud.GCLOUD_ENV == {
        "CLOUDSDK_CORE_PROJECT": "synthea-readmission-risk",
        "CLOUDSDK_STORAGE_PARALLEL_COMPOSITE_UPLOAD_ENABLED": "False",
    }


def test_list_objects_no_match_is_empty():
    assert gcloud.list_objects(lambda argv: _result(1, stderr="ERROR: One or more URLs matched no objects."), "gs://b/p") == []
    assert gcloud.list_objects(lambda argv: _result(0, "gs://b/p/a\ngs://b/p/b\n"), "gs://b/p/") == ["gs://b/p/a", "gs://b/p/b"]


def test_list_objects_other_error_raises():
    with pytest.raises(gcloud.GcloudError, match="exited 1"):
        gcloud.list_objects(lambda argv: _result(1, stderr="ERROR: 403 Forbidden"), "gs://b/p")


def test_object_exists_semantics():
    assert gcloud.object_exists(lambda argv: _result(0, "gs://b/x"), "gs://b/x") is True
    assert gcloud.object_exists(lambda argv: _result(1, stderr="matched no objects"), "gs://b/x") is False
    with pytest.raises(gcloud.GcloudError):
        gcloud.object_exists(lambda argv: _result(2, stderr="boom"), "gs://b/x")


def test_list_labelled_rejects_other_kinds():
    with pytest.raises(ValueError, match="kind"):
        gcloud.list_labelled(lambda argv: _result(0, "[]"), "buckets")
