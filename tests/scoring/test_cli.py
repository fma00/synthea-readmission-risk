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
    make_result,
    write_champion,
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
        "--bundle", env.lr_bundle_uri,
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
        "--gold-dir", "g", "--run-date", "20260630", "--output-dir", "o", "--bundle", "bundles/b",
        "--top-n", "3", "--window-days", "45", "--show-observed-outcomes",
    ]  # fmt: skip
    assert runner.invoke(cli.app, distinct).exit_code == 0
    config = seen[-1]
    assert config.gold_dir == Path("g") and config.run_date == "20260630" and config.output_dir == Path("o")
    assert config.bundle_uri == "bundles/b" and config.champion_path is None
    assert config.top_n == 3 and config.window_days == 45  # a top_n/window_days swap fails here
    assert config.show_observed_outcomes is True

    assert runner.invoke(cli.app, ["--gold-dir", "g", "--run-date", "20260630", "--champion", "c.json"]).exit_code == 0
    assert seen[-1].champion_path == Path("c.json") and seen[-1].bundle_uri is None

    assert runner.invoke(cli.app, ["--gold-dir", "g", "--run-date", "20260630"]).exit_code == 0
    default = seen[-1]  # every default (kills a wrong CLI default; --bundle alone must never trip the exclusion)
    assert default.output_dir == Path("data/predictions") and default.bundle_uri is None and default.champion_path is None
    assert default.top_n == 10 and default.window_days is None and default.show_observed_outcomes is False


def test_cli_end_to_end_prints_report_and_writes_partition(cli, scoring_env, tmp_path):
    out = tmp_path / "out"
    result = CliRunner().invoke(cli.app, _real_args(scoring_env, out))
    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith(DEMO_NOTICE)
    bundle_id = Path(scoring_env.lr_bundle_uri).name
    assert f"model: logistic_regression bundle={bundle_id[:12]} mlflow_run_id={scoring_env.lr_run_id}" in result.stdout
    assert re.search(r"Top 3 of \d+ scored discharges:", result.stdout)
    assert (out / "run_date=20260630" / "predictions.parquet").is_file()


def test_cli_reports_user_errors_with_exit_1(cli, scoring_env, tmp_path, monkeypatch):
    runner, env = CliRunner(), scoring_env
    out = tmp_path / "out"
    monkeypatch.chdir(tmp_path)  # no deploy/champion.json here
    absent_champion = write_champion(tmp_path / "absent-bundle-champion.json", str(tmp_path / "bundles" / ("0" * 64)), "0" * 64)
    no_bundle_args = ["--gold-dir", str(env.gold_dir), "--run-date", DEFAULT_RUN_DATE, "--window-days", str(DEFAULT_WINDOW_DAYS), "--output-dir", str(out)]
    cases = {
        "missing gold dir": ["--gold-dir", str(tmp_path / "nope"), "--run-date", DEFAULT_RUN_DATE, "--output-dir", str(out)],
        "in-sample window": _real_args(env, out, "--run-date", "20221130", "--window-days", "30"),
        "--bundle with --champion": _real_args(env, out, "--champion", "c.json"),  # exit 1, not Typer's usage exit 2
        "no champion file and no --bundle": no_bundle_args,
        "champion points at an absent bundle": [*no_bundle_args, "--champion", str(absent_champion)],
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
    def squash(text: str) -> str:
        return re.sub(r"\s+", "", text)

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
