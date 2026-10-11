from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from typer.testing import CliRunner

from readmission_risk.bundles.champion import read_champion
from tests.scoring.helpers import SCORING_EXPERIMENT

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_bundle.py"


@pytest.fixture(scope="module")
def cli():
    spec = importlib.util.spec_from_file_location("export_bundle_cli", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lines(stdout: str) -> dict[str, str]:
    return dict(line.split(": ", 1) for line in stdout.splitlines()[:3])


def test_export_cli_prints_ids_and_champion_json(cli, scoring_env, tmp_path):
    runner, root = CliRunner(), tmp_path / "bundles"
    args = ["--tracking-dir", str(scoring_env.tracking_dir), "--bundles-dir", str(root), "--run-id", scoring_env.lr_run_id]
    first = runner.invoke(cli.app, args)
    assert first.exit_code == 0, first.stderr
    lines = _lines(first.stdout)
    assert lines["bundle_id"] == Path(scoring_env.lr_bundle_uri).name and lines["status"] == "created"
    assert "WARNING: bundle_uri is an absolute" in first.stderr  # tmp_path lies outside the CWD
    champion_text = first.stdout.split("champion.json for this bundle:\n", 1)[1]
    (tmp_path / "champion.json").write_text(champion_text)
    champion = read_champion(tmp_path / "champion.json")
    assert champion.bundle_uri == lines["bundle_uri"] == str((root / lines["bundle_id"]).resolve())
    again = runner.invoke(cli.app, args)
    assert again.exit_code == 0 and _lines(again.stdout)["status"] == "already existed"


def test_export_cli_champion_path_is_relative_inside_cwd(cli, scoring_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        cli.app,
        ["--tracking-dir", str(scoring_env.tracking_dir), "--experiment-name", SCORING_EXPERIMENT, "--model", "xgboost_calibrated"],
    )
    assert result.exit_code == 0, result.stderr
    lines = _lines(result.stdout)
    assert lines["bundle_uri"] == f"bundles/{Path(scoring_env.xgb_bundle_uri).name}" and "WARNING" not in result.stderr


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param([], id="neither"),
        pytest.param(["--run-id", "x", "--experiment-name", "e", "--model", "m"], id="both"),
        pytest.param(["--experiment-name", "e"], id="experiment-only"),
        pytest.param(["--model", "logistic_regression"], id="model-only"),
        pytest.param(["--run-id", "x", "--model", "logistic_regression"], id="run-id-and-model"),
    ],
)
def test_export_cli_option_combinations(cli, tmp_path, extra):
    result = CliRunner().invoke(cli.app, ["--tracking-dir", str(tmp_path), *extra])
    assert result.exit_code == 2


def test_export_cli_user_errors_exit_1(cli, scoring_env, tmp_path):
    runner = CliRunner()
    for args in (
        ["--tracking-dir", str(scoring_env.tracking_dir), "--bundles-dir", str(tmp_path), "--run-id", "0" * 32],
        ["--tracking-dir", str(tmp_path / "no-store"), "--run-id", "0" * 32],
        ["--tracking-dir", str(scoring_env.tracking_dir), "--experiment-name", "nope", "--model", "logistic_regression"],
    ):
        result = runner.invoke(cli.app, args)
        assert result.exit_code == 1, args
        assert result.stderr.startswith("ERROR:") and result.stdout == "", args


def test_export_cli_warns_when_the_scorer_would_refuse_the_run(cli, scoring_env, tmp_path):
    from tests.models.helpers import clone_run

    failed = clone_run(scoring_env.tracking_dir, scoring_env.lr_run_id, experiment="export-cli-failed", gold_frame=scoring_env.df, status="FAILED")
    args = ["--tracking-dir", str(scoring_env.tracking_dir), "--bundles-dir", str(tmp_path / "b")]
    result = CliRunner().invoke(cli.app, [*args, "--run-id", failed])
    assert result.exit_code == 0, result.stderr  # still exported: the status is recorded and the scorer judges it
    assert "WARNING: run status is FAILED" in result.stderr and "do not promote it" in result.stderr
    finished = CliRunner().invoke(cli.app, [*args, "--run-id", scoring_env.lr_run_id])
    assert "run status" not in finished.stderr
