from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from readmission_risk.scoring.constants import DEMO_NOTICE
from readmission_risk.scoring.pipeline import ScoringConfig
from readmission_risk.scoring.ranking import PREDICTION_COLUMNS
from tests.scoring.helpers import (
    DEFAULT_RUN_DATE,
    DEFAULT_WINDOW_DAYS,
    SCORING_EXPERIMENT,
    make_result,
)

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "score_discharges.py"


@pytest.fixture(scope="module")
def cli():
    spec = importlib.util.spec_from_file_location("score_discharges_cli", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _real_args(env, out: Path, *extra: str) -> list[str]:
    return [
        "--gold-dir", str(env.gold_dir),
        "--tracking-dir", str(env.tracking_dir),
        "--experiment-name", SCORING_EXPERIMENT,
        "--run-date", DEFAULT_RUN_DATE,
        "--window-days", str(DEFAULT_WINDOW_DAYS),
        "--top-n", "3",
        "--output-dir", str(out),
        *extra,
    ]  # fmt: skip


def _record_config(cli, monkeypatch) -> list[ScoringConfig]:
    """Replaces the script's score_run_date with a fake that records the ScoringConfig and returns a minimal REAL result,
    so the script's own format_report call and printing run unmodified."""
    seen: list[ScoringConfig] = []

    def fake(config):
        seen.append(config)
        return make_result()

    monkeypatch.setattr(cli, "score_run_date", fake)
    return seen


def test_cli_maps_every_flag_to_config_and_defaults(cli, monkeypatch):
    seen = _record_config(cli, monkeypatch)
    runner = CliRunner()
    distinct = [
        "--gold-dir", "g", "--run-date", "20260630", "--output-dir", "o", "--tracking-dir", "t", "--experiment-name", "x",
        "--model", "xgboost_calibrated", "--top-n", "3", "--window-days", "45", "--show-observed-outcomes",
    ]  # fmt: skip
    assert runner.invoke(cli.app, distinct).exit_code == 0
    config = seen[-1]
    assert config.gold_dir == Path("g") and config.run_date == "20260630" and config.output_dir == Path("o")
    assert config.tracking_dir == Path("t") and config.experiment_name == "x"
    assert config.model_name == "xgboost_calibrated" and config.run_id is None
    assert config.top_n == 3 and config.window_days == 45  # a top_n/window_days swap fails here
    assert config.show_observed_outcomes is True

    assert runner.invoke(cli.app, ["--gold-dir", "g", "--run-date", "20260630", "--run-id", "abc"]).exit_code == 0
    assert seen[-1].run_id == "abc" and seen[-1].model_name is None

    assert runner.invoke(cli.app, ["--gold-dir", "g", "--run-date", "20260630"]).exit_code == 0
    default = seen[-1]  # every default (kills a wrong CLI default)
    assert default.output_dir == Path("data/predictions") and default.tracking_dir == Path("mlflow")
    assert default.experiment_name == "readmission-risk" and default.top_n == 10 and default.window_days is None
    assert default.model_name is None and default.run_id is None and default.show_observed_outcomes is False


def test_cli_end_to_end_prints_report_and_writes_partition(cli, scoring_env, tmp_path):
    out = tmp_path / "out"
    result = CliRunner().invoke(cli.app, _real_args(scoring_env, out))
    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith(DEMO_NOTICE)
    assert f"model: logistic_regression run_id={scoring_env.lr_run_id}" in result.stdout
    assert re.search(r"Top 3 of \d+ scored discharges:", result.stdout)
    assert (out / "run_date=20260630" / "predictions.parquet").is_file()


def test_cli_reports_user_errors_with_exit_1(cli, scoring_env, tmp_path):
    runner, env = CliRunner(), scoring_env
    out = tmp_path / "out"
    cases = {
        "missing gold dir": ["--gold-dir", str(tmp_path / "nope"), "--run-date", DEFAULT_RUN_DATE, "--output-dir", str(out)],
        "in-sample window": _real_args(env, out, "--run-date", "20221130", "--window-days", "30"),
        "--model with --run-id": _real_args(env, out, "--model", "xgboost_calibrated", "--run-id", env.xgb_run_id),
        "unknown model": _real_args(env, out, "--model", "random_forest"),  # exit 1, not Typer's usage exit 2
    }
    for label, args in cases.items():
        result = runner.invoke(cli.app, args)
        assert result.exit_code == 1, label
        assert result.stderr.startswith("ERROR:"), label
        assert result.stdout == "", label  # nothing on stdout for a user error
        assert not out.exists(), label

    verbose = runner.invoke(cli.app, [*cases["in-sample window"], "--verbose"])
    assert verbose.exit_code == 1 and "ERROR:" in verbose.stderr and "Traceback" in verbose.stderr
    assert "Traceback" not in runner.invoke(cli.app, cases["in-sample window"]).stderr

    assert runner.invoke(cli.app, ["--gold-dir", "g"]).exit_code == 2  # a missing required option is a usage error


def test_cli_help_shows_the_notice(cli):
    result = CliRunner().invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    squash = lambda text: re.sub(r"\s+", "", text)
    # ALL whitespace is removed: a whitespace-normalised comparison fails because Click wraps "development-set" at the hyphen
    assert squash(DEMO_NOTICE) in squash(result.stdout)
    assert "--no-verbose" not in result.stdout and "--no-show-observed-outcomes" not in result.stdout


def test_cli_observed_flag(cli, scoring_env, tmp_path):
    runner = CliRunner()
    off = runner.invoke(cli.app, _real_args(scoring_env, tmp_path / "off"))
    on = runner.invoke(cli.app, _real_args(scoring_env, tmp_path / "on", "--show-observed-outcomes"))
    assert off.exit_code == 0 and on.exit_code == 0
    assert "Observed outcomes" not in off.stdout and "Observed outcomes" in on.stdout
    for out in ("off", "on"):  # either way the file carries no label or observed column
        frame = pd.read_parquet(tmp_path / out / "run_date=20260630" / "predictions.parquet")
        assert list(frame.columns) == list(PREDICTION_COLUMNS)
