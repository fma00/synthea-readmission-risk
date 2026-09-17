import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from readmission_risk.pipeline.generation import (
    EXPECTED_CSV_TABLES,
    REQUIRED_NONEMPTY_TABLES,
    GenerationResult,
    SyntheaGenerationConfig,
    SyntheaGenerationError,
    SyntheaValidationError,
    ValidationResult,
    build_synthea_command,
    run_synthea_generation,
    validate_generation_output,
)


def make_config(tmp_path: Path, **overrides) -> SyntheaGenerationConfig:
    defaults = {
        "population_size": 1000,
        "seed": 42,
        "reference_date": "20260916",
        "output_dir": tmp_path / "run",
        "synthea_jar_path": Path("tools/synthea/synthea-with-dependencies.jar"),
        "state": "Massachusetts",
    }
    defaults.update(overrides)
    return SyntheaGenerationConfig(**defaults)


def write_csv(path: Path, rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["header"] + [f"row{i}" for i in range(rows)]
    path.write_text("\n".join(lines) + "\n")


# --- build_synthea_command ---


def test_build_synthea_command_exact_argv(tmp_path):
    config = make_config(tmp_path)
    argv = build_synthea_command(config)
    assert argv == [
        "java",
        "-jar",
        "tools/synthea/synthea-with-dependencies.jar",
        "-p",
        "1000",
        "-s",
        "42",
        "-r",
        "20260916",
        "--exporter.baseDirectory",
        str(tmp_path / "run"),
        "--exporter.csv.export",
        "true",
        "--exporter.fhir.export",
        "false",
        "--exporter.hospital.fhir.export",
        "false",
        "--exporter.practitioner.fhir.export",
        "false",
        "--exporter.years_of_history",
        "0",
        "Massachusetts",
    ]


def test_build_synthea_command_excludes_dead_database_type_flag(tmp_path):
    argv = build_synthea_command(make_config(tmp_path))
    assert "--generate.database_type" not in argv


# --- validate_generation_output ---


def test_validate_generation_output_all_present_and_nonempty(tmp_path):
    csv_dir = tmp_path / "csv"
    for table in EXPECTED_CSV_TABLES:
        write_csv(csv_dir / table, rows=5)

    result = validate_generation_output(tmp_path)

    assert result.ok is True
    assert result.missing_tables == []
    assert result.empty_tables == []
    assert result.row_counts == {table: 5 for table in EXPECTED_CSV_TABLES}


def test_validate_generation_output_missing_file(tmp_path):
    csv_dir = tmp_path / "csv"
    for table in EXPECTED_CSV_TABLES:
        if table == "careplans.csv":
            continue
        write_csv(csv_dir / table, rows=5)

    result = validate_generation_output(tmp_path)

    assert result.ok is False
    assert "careplans.csv" in result.missing_tables


def test_validate_generation_output_empty_non_required_table_is_a_warning(tmp_path):
    csv_dir = tmp_path / "csv"
    for table in EXPECTED_CSV_TABLES:
        rows = 0 if table == "careplans.csv" else 5
        write_csv(csv_dir / table, rows=rows)

    result = validate_generation_output(tmp_path)

    assert result.ok is True
    assert result.empty_tables == ["careplans.csv"]
    assert "careplans.csv" not in result.missing_tables


def test_validate_generation_output_empty_patients_csv_is_a_hard_failure(tmp_path):
    csv_dir = tmp_path / "csv"
    for table in EXPECTED_CSV_TABLES:
        rows = 0 if table == "patients.csv" else 5
        write_csv(csv_dir / table, rows=rows)

    result = validate_generation_output(tmp_path)

    assert result.ok is False
    assert "patients.csv" in result.missing_tables
    assert "patients.csv" not in result.empty_tables


def test_required_nonempty_tables_are_a_subset_of_expected():
    assert set(REQUIRED_NONEMPTY_TABLES) <= set(EXPECTED_CSV_TABLES)


# --- exception attribute contracts ---


def test_synthea_generation_error_attributes():
    exc = SyntheaGenerationError(returncode=1, stderr_tail="boom")
    assert exc.returncode == 1
    assert exc.stderr_tail == "boom"
    assert "1" in str(exc)
    assert "boom" in str(exc)


def test_synthea_generation_error_timeout_has_none_returncode():
    exc = SyntheaGenerationError(returncode=None, stderr_tail="hung")
    assert exc.returncode is None
    assert "timed out" in str(exc)


def test_synthea_validation_error_attributes():
    validation_result = ValidationResult(ok=False, missing_tables=["patients.csv"])
    exc = SyntheaValidationError(validation_result)
    assert exc.validation_result is validation_result
    assert "patients.csv" in str(exc)


# --- run_synthea_generation orchestration ---


def test_run_synthea_generation_rejects_nonempty_output_dir(tmp_path):
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    (output_dir / "leftover.txt").write_text("stale data from a prior interrupted run")

    with patch("subprocess.run") as mock_run, pytest.raises(FileExistsError):
        run_synthea_generation(make_config(tmp_path, output_dir=output_dir))
    mock_run.assert_not_called()


def test_run_synthea_generation_rejects_output_dir_that_is_a_file(tmp_path):
    output_dir = tmp_path / "run"
    output_dir.write_text("oops, this is a file not a directory")

    with patch("subprocess.run") as mock_run, pytest.raises(FileExistsError):
        run_synthea_generation(make_config(tmp_path, output_dir=output_dir))
    mock_run.assert_not_called()


def test_run_synthea_generation_raises_on_nonzero_exit(tmp_path):
    config = make_config(tmp_path)
    stderr = "\n".join(f"line{i}" for i in range(30))
    fake_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)

    with patch("subprocess.run", return_value=fake_result), pytest.raises(SyntheaGenerationError) as excinfo:
        run_synthea_generation(config)

    assert excinfo.value.returncode == 1
    assert excinfo.value.stderr_tail == "\n".join(f"line{i}" for i in range(10, 30))


def test_run_synthea_generation_raises_on_short_stderr(tmp_path):
    config = make_config(tmp_path)
    fake_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="line0\nline1")

    with patch("subprocess.run", return_value=fake_result), pytest.raises(SyntheaGenerationError) as excinfo:
        run_synthea_generation(config)

    assert excinfo.value.stderr_tail == "line0\nline1"


def test_run_synthea_generation_raises_on_timeout(tmp_path):
    config = make_config(tmp_path, timeout_seconds=1.0)
    timeout_exc = subprocess.TimeoutExpired(cmd=["java"], timeout=1.0, output="", stderr="stuck")

    with patch("subprocess.run", side_effect=timeout_exc), pytest.raises(SyntheaGenerationError) as excinfo:
        run_synthea_generation(config)

    assert excinfo.value.returncode is None


def _populate_fixture_csv_dir(
    output_dir: Path,
    extra_tables: dict[str, int] | None = None,
    empty_non_required_table: str | None = None,
) -> None:
    csv_dir = output_dir / "csv"
    for table in EXPECTED_CSV_TABLES:
        rows = 0 if table == empty_non_required_table else 5
        write_csv(csv_dir / table, rows=rows)
    for table, rows in (extra_tables or {}).items():
        write_csv(csv_dir / table, rows=rows)


def test_run_synthea_generation_success_writes_summary_and_complete_row_counts(tmp_path):
    config = make_config(tmp_path)

    def fake_run(*args, **kwargs):
        # Simulate Synthea actually writing its output as a side effect of the subprocess call --
        # output_dir must still be empty when run_synthea_generation's guard clause checks it.
        _populate_fixture_csv_dir(
            config.output_dir, extra_tables={"payers.csv": 3}, empty_non_required_table="careplans.csv"
        )
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        result = run_synthea_generation(config)

    assert isinstance(result, GenerationResult)
    assert result.warnings == ["careplans.csv"]
    # Complete accounting includes payers.csv even though it's outside EXPECTED_CSV_TABLES.
    assert result.row_counts["payers.csv"] == 3
    assert result.row_counts["patients.csv"] == 5

    summary_path = config.output_dir / "generation_summary.json"
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text())
    assert summary["population_size"] == 1000
    assert summary["seed"] == 42
    assert summary["row_counts"]["payers.csv"] == 3
    assert summary["warnings"] == ["careplans.csv"]


def test_run_synthea_generation_raises_validation_error_on_empty_required_table(tmp_path):
    config = make_config(tmp_path)

    def fake_run(*args, **kwargs):
        csv_dir = config.output_dir / "csv"
        for table in EXPECTED_CSV_TABLES:
            rows = 0 if table == "patients.csv" else 5
            write_csv(csv_dir / table, rows=rows)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run), pytest.raises(SyntheaValidationError) as excinfo:
        run_synthea_generation(config)

    assert "patients.csv" in excinfo.value.validation_result.missing_tables
    assert not (config.output_dir / "generation_summary.json").exists()
