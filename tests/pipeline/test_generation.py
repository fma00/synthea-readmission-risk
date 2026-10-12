import ast
import json
import subprocess
import sys
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
    count_alive_patients,
    parse_provenance_args,
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
        "10",
        "Massachusetts",
    ]


def test_build_synthea_command_years_of_history_threaded_through(tmp_path):
    config = make_config(tmp_path, years_of_history=0)
    argv = build_synthea_command(config)
    assert "--exporter.years_of_history" in argv
    idx = argv.index("--exporter.years_of_history")
    assert argv[idx + 1] == "0"


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
        # immunizations.csv (not payers.csv, which moved INTO EXPECTED_CSV_TABLES in slice 2 --
        # see generation.py's EXPECTED_CSV_TABLES comment) stands in as a table genuinely outside
        # EXPECTED_CSV_TABLES, to keep proving row-count accounting covers tables beyond that list.
        _populate_fixture_csv_dir(
            config.output_dir,
            extra_tables={"immunizations.csv": 3},
            empty_non_required_table="careplans.csv",
        )
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        result = run_synthea_generation(config)

    assert isinstance(result, GenerationResult)
    assert result.warnings == ["careplans.csv"]
    # Complete accounting includes immunizations.csv even though it's outside EXPECTED_CSV_TABLES.
    assert result.row_counts["immunizations.csv"] == 3
    assert result.row_counts["patients.csv"] == 5

    summary_path = config.output_dir / "generation_summary.json"
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text())
    assert summary["population_size"] == 1000
    assert summary["seed"] == 42
    assert summary["row_counts"]["immunizations.csv"] == 3
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


# --- v2 slice 5: -cs, -e, JVM time zone, included files, heap, alive-population check, provenance ---
# (notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md, Interfaces section 8)

REPO = Path(__file__).resolve().parents[2]


def test_command_unchanged_without_new_options(tmp_path):
    argv = build_synthea_command(make_config(tmp_path))
    assert not any(a.startswith(("-D", "-X")) or a in ("-cs", "-e", "--exporter.csv.included_files") for a in argv)


def test_clinician_seed_and_end_date_positions(tmp_path):
    argv = build_synthea_command(make_config(tmp_path, clinician_seed=17, end_date="20260917"))
    assert argv[argv.index("-s") : argv.index("-s") + 6] == ["-s", "42", "-cs", "17", "-r", "20260916"]
    assert argv[argv.index("-r") + 2 : argv.index("-r") + 4] == ["-e", "20260917"]


def test_included_files_flag_before_state(tmp_path):
    argv = build_synthea_command(make_config(tmp_path, included_files=EXPECTED_CSV_TABLES))
    assert argv[-3:] == ["--exporter.csv.included_files", ",".join(EXPECTED_CSV_TABLES), "Massachusetts"]


def test_jvm_options_position_and_format(tmp_path):
    argv = build_synthea_command(make_config(tmp_path, jvm_timezone="UTC", java_max_heap="96g"))
    assert argv[:5] == ["java", "-Duser.timezone=UTC", "-Xmx96g", "-XX:+ExitOnOutOfMemoryError", "-jar"]


def test_exit_on_oom_only_with_heap(tmp_path):
    assert "-XX:+ExitOnOutOfMemoryError" not in build_synthea_command(make_config(tmp_path, jvm_timezone="UTC"))


@pytest.mark.parametrize(
    "overrides, match",
    [
        ({"included_files": EXPECTED_CSV_TABLES[:-1]}, "missing"),
        ({"included_files": (*EXPECTED_CSV_TABLES, "patients.csv")}, "distinct"),
        ({"included_files": (*EXPECTED_CSV_TABLES, "claims.txt")}, "distinct"),
        ({"java_max_heap": "96G"}, "java_max_heap"),
        ({"java_max_heap": "0g"}, "java_max_heap"),
        ({"jvm_timezone": "UTC; rm -rf /"}, "jvm_timezone"),
        ({"end_date": "20260916"}, "after reference_date"),
        ({"end_date": "2026-09-17"}, "end_date"),
        ({"clinician_seed": True}, "clinician_seed"),
        ({"clinician_seed": -1}, "clinician_seed"),
        ({"provenance": {"run": ""}}, "provenance"),
    ],
)
def test_config_validation(tmp_path, overrides, match):
    with pytest.raises(ValueError, match=match):
        make_config(tmp_path, **overrides)


def test_summary_has_new_keys(tmp_path):
    config = make_config(tmp_path, clinician_seed=17, end_date="20260917", jvm_timezone="UTC",
                         included_files=EXPECTED_CSV_TABLES, provenance={"run": "pilot_20k"})

    def fake_run(*args, **kwargs):
        _populate_fixture_csv_dir(config.output_dir)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_synthea_generation(config)
    summary = json.loads((config.output_dir / "generation_summary.json").read_text())
    assert summary["clinician_seed"] == 17
    assert summary["end_date"] == "20260917"
    assert summary["jvm_timezone"] == "UTC"
    assert summary["included_files"] == list(EXPECTED_CSV_TABLES)
    assert summary["years_of_history"] == 10
    assert summary["state"] == "Massachusetts"
    assert summary["java_max_heap"] is None
    assert summary["n_alive_patients"] is None  # not counted unless required
    assert summary["provenance"] == {"run": "pilot_20k"}
    assert isinstance(summary["peak_child_rss_bytes"], int)


def test_summary_new_keys_null_by_default(tmp_path):
    config = make_config(tmp_path)

    def fake_run(*args, **kwargs):
        _populate_fixture_csv_dir(config.output_dir)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_synthea_generation(config)
    summary = json.loads((config.output_dir / "generation_summary.json").read_text())
    for key in ("clinician_seed", "end_date", "jvm_timezone", "included_files", "java_max_heap", "provenance"):
        assert summary[key] is None, key


@pytest.mark.parametrize("platform, raw, expected", [("darwin", 5000, 5000), ("linux", 5000, 5000 * 1024)])
def test_peak_rss_normalised_per_platform(monkeypatch, platform, raw, expected):
    import resource

    from readmission_risk.pipeline import generation

    monkeypatch.setattr(generation.sys, "platform", platform)
    monkeypatch.setattr(
        generation.resource, "getrusage", lambda who: type("R", (), {"ru_maxrss": raw})() if who == resource.RUSAGE_CHILDREN else None
    )
    assert generation._peak_child_rss_bytes() == expected


def _write_patients(path: Path, alive: int, dead: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = ["Id,BIRTHDATE,DEATHDATE"] + [f"a{i},1980-01-01," for i in range(alive)] + [f"d{i},1980-01-01,2020-01-01" for i in range(dead)]
    path.write_text("\n".join(rows) + "\n")


@pytest.mark.parametrize("population, ok", [(3, True), (4, False)])
def test_alive_population_check(tmp_path, population, ok):
    config = make_config(tmp_path, population_size=population, require_alive_population=True)

    def fake_run(*args, **kwargs):
        _populate_fixture_csv_dir(config.output_dir)
        _write_patients(config.output_dir / "csv" / "patients.csv", alive=3, dead=2)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        if ok:
            run_synthea_generation(config)
            assert json.loads((config.output_dir / "generation_summary.json").read_text())["n_alive_patients"] == 3
        else:
            with pytest.raises(SyntheaValidationError, match="produced 3 alive patients, expected 4"):
                run_synthea_generation(config)


def test_alive_check_off_by_default(tmp_path):
    config = make_config(tmp_path, population_size=1000)

    def fake_run(*args, **kwargs):
        _populate_fixture_csv_dir(config.output_dir)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_synthea_generation(config)  # 5 placeholder patient rows, population 1000: no check, no error


def test_count_alive_patients(tmp_path):
    _write_patients(tmp_path / "patients.csv", alive=4, dead=1)
    assert count_alive_patients(tmp_path / "patients.csv") == 4


def test_parse_provenance_args():
    assert parse_provenance_args(None) == {}
    assert parse_provenance_args(["run=pilot_20k", "x=a=b"]) == {"run": "pilot_20k", "x": "a=b"}
    for bad in (["norun"], ["=v"], ["k="], ["k=1", "k=2"]):
        with pytest.raises(ValueError, match="provenance"):
            parse_provenance_args(bad)


def test_generation_code_is_stdlib_only():
    """The generation VM runs pipeline/generation.py from the Spark zip on the image's system Python (no third-party packages)."""
    files = [
        REPO / "src/readmission_risk/pipeline/generation.py",
        REPO / "src/readmission_risk/__init__.py",
        REPO / "src/readmission_risk/pipeline/__init__.py",
        REPO / "scripts/generate_synthea_data.py",
    ]
    allowed = {"readmission_risk", "readmission_risk.pipeline.generation"}
    bad = []
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            bad += [f"{path.name}: {n}" for n in names if n.split(".")[0] not in sys.stdlib_module_names and n not in allowed and n != "__future__"]
    assert bad == []
