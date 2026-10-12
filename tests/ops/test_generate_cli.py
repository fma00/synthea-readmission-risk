"""scripts/generate_synthea_data.py and scripts/train_models.py argument handling added in slice 5."""

from __future__ import annotations

import dataclasses
import importlib.util

import pytest

from readmission_risk.models.training import TrainingConfig
from tests.ops.conftest import REPO


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"script_{name}", REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = ["--population", "10", "--seed", "1", "--output", "/tmp/x"]


@pytest.mark.parametrize("bad", ["norun", "=v", "k=", None])
def test_generate_cli_provenance_error_exits_2(bad, capsys):
    cli = _load("generate_synthea_data")
    extra = ["--provenance", "k=1", "--provenance", "k=2"] if bad is None else ["--provenance", bad]
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_args([*BASE, *extra])
    assert excinfo.value.code == 2
    assert "provenance" in capsys.readouterr().err


def test_generate_cli_new_flags():
    cli = _load("generate_synthea_data")
    args = cli.parse_args([*BASE, "--clinician-seed", "17", "--end-date", "20261013", "--jvm-timezone", "UTC",
                           "--included-files", "default", "--java-max-heap", "96g", "--require-alive-population",
                           "--provenance", "run=pilot_20k"])  # fmt: skip
    config = cli._config(args)
    assert (config.clinician_seed, config.end_date, config.jvm_timezone, config.java_max_heap) == (17, "20261013", "UTC", "96g")
    assert config.require_alive_population and config.provenance == {"run": "pilot_20k"}
    assert len(config.included_files) == 10


def test_generate_cli_bad_config_is_exit_1(capsys):
    cli = _load("generate_synthea_data")
    assert cli.main([*BASE, "--java-max-heap", "lots"]) == 1
    assert "java_max_heap" in capsys.readouterr().err


def test_cli_defaults_equal_config():
    """prereg.yaml's training values are checked against TrainingConfig; the CLI's argparse defaults must equal it."""
    cli = _load("train_models")
    args = cli.parse_args(["--gold-dir", "x", "--reference-date", "20261012", "--test-start-date", "20230101"])
    defaults = {f.name: f.default for f in dataclasses.fields(TrainingConfig)}
    for name in ("seed", "calibration_cv_folds", "calibration_method", "n_bootstrap", "readmission_window_days"):
        assert getattr(args, name) == defaults[name], name
