"""scripts/export_portable_bundle.py (v2 slice 6c): the six output lines, the error exits, and a gs:// destination
against the fake GCS client."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from typer.testing import CliRunner

from readmission_risk.portable.export import DESIGN_NOTE
from tests.bundles.fake_gcs import FakeStorage

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_portable_bundle.py"
BUCKET = "fake-bundles"


@pytest.fixture(scope="module")
def cli():
    spec = importlib.util.spec_from_file_location("export_portable_bundle_cli", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake(monkeypatch):
    """A copy of tests/bundles/test_gcs.py's fixture (it is local to that module)."""
    store = FakeStorage()
    monkeypatch.setattr("readmission_risk.gcp.storage_client", lambda: store)
    return store


def _run(cli, *args: str):
    return CliRunner().invoke(cli.app, list(args))


def test_cli_prints_the_six_lines(cli, scoring_env, portable_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _run(cli, "--source-bundle", scoring_env.lr_bundle_uri, "--gold-dir", str(scoring_env.gold_dir), "--bundles-dir", "out")
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    bundle_id = portable_env.lr_result.bundle_id
    assert lines == [
        f"bundle_id: {bundle_id}",
        f"bundle_uri: out/{bundle_id}",
        "status: created",
        f"source_bundle_id: {Path(scoring_env.lr_bundle_uri).name}",
        f"parity: max abs diff 0.0 on {portable_env.lr_result.parity_rows} test rows and {portable_env.lr_result.probe_rows} probe rows",
        f"not promoted: deploy/champion.json is not changed ({DESIGN_NOTE})",
    ]
    again = _run(cli, "--source-bundle", scoring_env.lr_bundle_uri, "--gold-dir", str(scoring_env.gold_dir), "--bundles-dir", "out")
    assert again.exit_code == 0 and "status: already existed" in again.stdout


def test_cli_error_exit_1(cli, scoring_env, tmp_path):
    result = _run(cli, "--source-bundle", str(tmp_path / ("0" * 64)), "--gold-dir", str(scoring_env.gold_dir), "--bundles-dir", str(tmp_path))
    assert result.exit_code == 1 and result.stderr.startswith("ERROR: ")


def test_cli_refuses_gs_source(cli, scoring_env, tmp_path):
    result = _run(cli, "--source-bundle", "gs://b/" + "0" * 64, "--gold-dir", str(scoring_env.gold_dir), "--bundles-dir", str(tmp_path))
    assert result.exit_code == 1 and "the source bundle must be a local path" in result.stderr


def test_cli_gs_destination_prints_created(cli, fake, scoring_env, portable_env):
    args = ("--source-bundle", scoring_env.xgb_bundle_uri, "--gold-dir", str(scoring_env.gold_dir), "--bundles-dir", f"gs://{BUCKET}")
    result = _run(cli, *args)
    assert result.exit_code == 0, result.output
    bundle_id = portable_env.xgb_result.bundle_id
    assert f"bundle_uri: gs://{BUCKET}/{bundle_id}" in result.stdout and "status: created" in result.stdout
    assert len([k for k in fake.buckets[BUCKET] if k.startswith(f"{bundle_id}/")]) == 10
    again = _run(cli, *args)
    assert again.exit_code == 1 and "already exists" in again.stderr
