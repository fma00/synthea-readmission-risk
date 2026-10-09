from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from typer.testing import CliRunner

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "make_readme_assets.py"


@pytest.fixture(scope="module")
def cli():
    spec = importlib.util.spec_from_file_location("make_readme_assets_cli", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_missing_store_exits_one_with_an_error_line(cli, tmp_path):
    result = CliRunner().invoke(
        cli.app,
        ["--experiment-name", "x", "--predictions", str(tmp_path / "none.parquet"), "--tracking-dir", str(tmp_path / "no-store")],
    )
    assert result.exit_code == 1
    assert "ERROR:" in result.stderr  # containment: importing mlflow can print an INFO line to stderr too


def test_missing_required_options_exit_two(cli):
    assert CliRunner().invoke(cli.app, []).exit_code == 2
