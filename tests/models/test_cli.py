from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from mlflow.tracking import MlflowClient

from readmission_risk.models.tracking import mlflow_tracking_uri
from tests.models.helpers import (
    REFERENCE_DATE_STR,
    TEST_START_STR,
    make_gold_frame,
    write_test_gold_metadata,
)

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "train_models.py"


@pytest.fixture(scope="module")
def cli():
    spec = importlib.util.spec_from_file_location("train_models_cli", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_gold(directory: Path, **metadata_overrides) -> Path:
    directory.mkdir(parents=True)
    df = make_gold_frame(300, seed=1)
    half = len(df) // 2
    df.iloc[:half].to_parquet(directory / "part-0.parquet", index=False)
    df.iloc[half:].to_parquet(directory / "part-1.parquet", index=False)
    write_test_gold_metadata(directory, df, **metadata_overrides)
    return directory


def _base_args(gold: Path, tracking: Path) -> list[str]:
    return [
        "--gold-dir", str(gold),
        "--reference-date", REFERENCE_DATE_STR,
        "--test-start-date", TEST_START_STR,
        "--tracking-dir", str(tracking),
        "--n-bootstrap", "0",
    ]  # fmt: skip


def test_cli_maps_flags_to_config_and_prints_report(cli, tmp_path, capsys):
    # the CLI runs with --readmission-window-days 14, so the gold table's metadata must say 14 too
    gold, tracking = _write_gold(tmp_path / "gold", readmission_window_days=14), tmp_path / "t"
    extra = [
        "--train-start-date", "20200101",
        "--seed", "7",
        "--readmission-window-days", "14",
        "--calibration-method", "isotonic",
        "--calibration-cv-folds", "3",
        "--experiment-name", "cli-test",
    ]  # fmt: skip
    code = cli.main([*_base_args(gold, tracking), *extra])
    assert code == 0

    out = capsys.readouterr().out
    assert "Split summary:" in out and "logistic_regression:" in out and "(bootstrap skipped: --n-bootstrap 0)" in out
    client = MlflowClient(tracking_uri=mlflow_tracking_uri(tracking))
    runs = client.search_runs([client.get_experiment_by_name("cli-test").experiment_id])
    assert len(runs) == 2
    for run in runs:  # a wrong mapping (e.g. train_start_date=args.test_start_date) would fail these
        assert run.data.params["train_start_date"] == "20200101"
        assert run.data.params["test_start_date"] == TEST_START_STR
        assert run.data.params["reference_date"] == REFERENCE_DATE_STR
        assert run.data.params["seed"] == "7"
        # the leakage-critical flag: it sets the censoring buffer and the label-window purge, so it must reach them
        assert run.data.params["readmission_window_days"] == "14"
        assert run.data.params["split_censor_buffer_cutoff"] == "2026-09-03T00:00:00+00:00"  # 2026-09-17 minus 14 days
    xgb = next(r for r in runs if r.data.tags["model_name"] == "xgboost_calibrated")
    assert xgb.data.params["calibration_method"] == "isotonic"
    assert xgb.data.params["calibration_cv_folds"] == "3"


def test_cli_reports_user_errors_with_exit_1(cli, tmp_path, capsys):
    gold = _write_gold(tmp_path / "gold")
    assert cli.main(["--gold-dir", str(tmp_path / "nope"), "--reference-date", REFERENCE_DATE_STR,
                     "--test-start-date", TEST_START_STR, "--tracking-dir", str(tmp_path / "t1")]) == 1  # fmt: skip
    assert "ERROR:" in capsys.readouterr().err
    bad_date = _base_args(gold, tmp_path / "t2")
    bad_date[bad_date.index("--reference-date") + 1] = "2026-09-16"
    assert cli.main(bad_date) == 1
    assert "reference_date" in capsys.readouterr().err
    assert not (tmp_path / "t2").exists()  # rejected before any side effect


def test_cli_verbose_prints_traceback(cli, tmp_path, capsys):
    argv = ["--gold-dir", str(tmp_path / "nope"), "--reference-date", REFERENCE_DATE_STR,
            "--test-start-date", TEST_START_STR, "--tracking-dir", str(tmp_path / "t"), "--verbose"]  # fmt: skip
    assert cli.main(argv) == 1
    assert "Traceback" in capsys.readouterr().err


def test_cli_refuses_a_gold_table_without_metadata(cli, tmp_path, capsys):
    gold = _write_gold(tmp_path / "gold")
    (gold / "_gold_metadata.json").unlink()
    assert cli.main(_base_args(gold, tmp_path / "t")) == 1
    err = capsys.readouterr().err
    assert "ERROR:" in err and "rebuild" in err
    assert not (tmp_path / "t").exists()
