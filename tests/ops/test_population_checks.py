"""ops/population_checks.py and scripts/check_population.py (v2 slice 6a): checks C1-C9 on hand-built populations, gathering
through FakeGcloud, the CLI's exit codes and report, the no-Id-leak rule and the no-model-code import rule. No GCP, no JVM."""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import hashlib
import importlib.util
import json
import subprocess
import sys
import warnings
from pathlib import Path

import pandas as pd
import pytest

from readmission_risk.models.data import load_gold_table_with_metadata
from readmission_risk.ops import population_checks as pc
from readmission_risk.ops.generation_vm import gold_prefix, raw_prefix
from readmission_risk.ops.prereg import load_prereg
from tests.gold_helpers import write_gold_metadata_for_frame
from tests.models.helpers import frame, pad_rows, row
from tests.ops.conftest import REPO
from tests.ops.fake_gcloud import FakeGcloud

PREREG = load_prereg(REPO / "prereg.yaml")
PREREG_BLOB = (REPO / "prereg.yaml").read_bytes()
PRE_COMMIT = "c" * 40
PILOT, MAIN = "pilot_20k", "main_200k"
CONNECTOR = "spark-bigquery-with-dependencies_2.13-0.44.0.jar"
FULL_PROVENANCE = {
    "head_commit": "a" * 40, "zip_sha256": "d" * 64, "main_sha256": "e" * 64, "prereg_commit": PRE_COMMIT, "batch_id": "b1",
    "runtime.python_version": "3.12.9", "runtime.java_version": "21.0.8",
    "runtime.env.DATAPROC_IMAGE_VERSION": "3.0.14", f"runtime.jar.{CONNECTOR}": "/usr/lib/spark/jars",
}  # fmt: skip
DEAD = "dead-1"


def raw_summary(run: str = PILOT, **overrides) -> dict:
    gen, pop = PREREG.generation, PREREG.populations[run]
    summary = {
        "seed": pop.seed, "clinician_seed": pop.clinician_seed, "population_size": pop.population,
        "reference_date": gen["reference_date"], "end_date": gen["end_date"], "jvm_timezone": gen["jvm_timezone"],
        "state": gen["state"], "years_of_history": gen["years_of_history"], "included_files": gen["included_files"],
        "n_alive_patients": pop.population,
        "provenance": {"synthea_jar_sha256": gen["synthea_jar_sha256"], "prereg_commit": PRE_COMMIT,
                       "prereg_sha256": hashlib.sha256(PREREG_BLOB).hexdigest()},
    }  # fmt: skip
    summary.update(overrides)
    return summary


def blob_at(commit: str) -> bytes:
    if commit == PRE_COMMIT:
        return PREREG_BLOB
    raise subprocess.CalledProcessError(128, ["git", "show"])


def gold_rows(extra: list[dict] | None = None) -> list[dict]:
    """pad_rows (2 positives, train 2019 / test 2024) plus 46 negatives: a 4% positive rate."""
    rows = pad_rows()
    for i in range(46):
        year = "2019" if i % 2 else "2024"
        rows.append(row(f"n{i}", f"{year}-03-01T00:00:00Z", f"{year}-03-04T00:00:00Z"))
    return rows + (extra or [])


def write_gold(directory: Path, df: pd.DataFrame, run: str = PILOT, **overrides) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    df.to_parquet(directory / "part-00000.parquet", index=False)
    values = {
        "version": 2, "reference_date": PREREG.generation["reference_date"], "generation_summary": raw_summary(run),
        "n_planned_stays": 3, "build_provenance": FULL_PROVENANCE if run == MAIN else {},
    }  # fmt: skip
    values.update(overrides)
    write_gold_metadata_for_frame(directory, df, **values)
    return directory


def encounters(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["START", "PATIENT", "DESCRIPTION"])


def patients_for(df: pd.DataFrame, extra: dict[str, str] | None = None) -> pd.DataFrame:
    ids = {pid: "" for pid in df["patient_id"]}
    ids.update({DEAD: "2026-10-07"})
    ids.update(extra or {})
    return pd.DataFrame({"Id": list(ids), "DEATHDATE": list(ids.values())})


DEFAULT_ENCOUNTERS = [
    ("2026-10-12T10:00:00Z", "n0", "Encounter for check up"),
    ("2026-10-14T02:01:56Z", DEAD, "Death Certification"),
]


def make_inputs(tmp_path: Path, run: str = PILOT, *, df: pd.DataFrame | None = None, gold_overrides=None, **changes):
    df = frame(gold_rows()) if df is None else df
    gold_dir = write_gold(tmp_path / f"gold-{run}", df, run, **(gold_overrides or {}))
    loaded, metadata = load_gold_table_with_metadata(gold_dir)
    comparators = [
        pc.IdSource("v1_raw", "v1", frozenset({"v1-a", "v1-b", "v1-c"}), 3),
        pc.IdSource("v1_gold_full_run_2b", "v1g", frozenset({"v1-a", "v1-b"}), 4),
    ]
    if run == MAIN:
        comparators.insert(0, pc.IdSource("pilot_20k_raw", "gs://p", frozenset({"pilot-a"}), 1))
    inputs = pc.PopulationInputs(
        run=run, gold_frame=loaded, gold_metadata=metadata, gold_error=None, raw_summary=raw_summary(run),
        patients=patients_for(df), encounters=encounters(DEFAULT_ENCOUNTERS), comparators=tuple(comparators),
        v1_raw_summary={"seed": 42}, inputs={},
    )  # fmt: skip
    return dataclasses.replace(inputs, **changes)


def prereg_with(**power) -> object:
    raw = json.loads(json.dumps(PREREG.raw))
    raw["power"].update(power)
    return type(PREREG)(raw=raw, populations=PREREG.populations)


def evaluate(inputs, prereg=PREREG) -> dict[str, pc.CheckResult]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # the split's small-sample reliability warning
        results = pc.evaluate_checks(inputs, prereg, prereg_blob_at=blob_at)
    assert [r.id for r in results] == ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C7b", "C8", "C9"]
    return {r.id: r for r in results}


def statuses(results: dict[str, pc.CheckResult]) -> dict[str, str]:
    return {k: v.status for k, v in results.items()}


# --- the happy paths -----------------------------------------------------------------------------------------------------


def test_pilot_passes(tmp_path):
    results = evaluate(make_inputs(tmp_path))
    assert statuses(results) == {**dict.fromkeys(["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C7b"], "PASS"), "C8": "INFO", "C9": "INFO"}
    assert pc.exit_code(list(results.values())) == 0
    assert results["C7"].values == {"n_after_ref1": 1, "n_not_death_certification": 0, "n_not_posthumous": 0}


def test_main_passes_at_the_power_floor_and_fails_one_below(tmp_path):
    inputs = make_inputs(tmp_path, MAIN)
    n_test_positive = evaluate(inputs)["C9"].values["n_test_positive"]
    results = evaluate(inputs, prereg_with(min_test_positives=n_test_positive))
    assert set(statuses(results).values()) == {"PASS"}
    assert evaluate(inputs, prereg_with(min_test_positives=n_test_positive + 1))["C9"].status == "FAIL"


# --- C1, C2 ---------------------------------------------------------------------------------------------------------------


def test_c1_loader_refusal_fails_and_skips_gold_checks(tmp_path):
    inputs = make_inputs(tmp_path, gold_frame=None, gold_metadata=None, gold_error="Gold table in x has zero rows")
    results = evaluate(inputs)
    assert results["C1"].status == "FAIL" and "zero rows" in results["C1"].detail
    assert {k: results[k].status for k in ("C2", "C7b", "C8", "C9")} == dict.fromkeys(("C2", "C7b", "C8", "C9"), "SKIP")
    assert results["C3"].status == "PASS" and "metadata clauses not run" in results["C3"].detail
    assert results["C4"].status == "PASS" and "n_gold_patients" not in results["C4"].values


@pytest.mark.parametrize("overrides", [{"readmission_window_days": 31}, {"lookback_years": 2}])
def test_c1_label_settings_must_match_prereg(tmp_path, overrides):
    assert evaluate(make_inputs(tmp_path, gold_overrides=overrides))["C1"].status == "FAIL"


def test_c1_other_label_definition_or_reference_date_fails(tmp_path):
    inputs = make_inputs(tmp_path)
    other = dataclasses.replace(inputs.gold_metadata, label_definition="unplanned_readmission_v9")
    assert evaluate(dataclasses.replace(inputs, gold_metadata=other))["C1"].status == "FAIL"
    other = dataclasses.replace(inputs.gold_metadata, reference_date="20261011")
    assert "reference_date" in evaluate(dataclasses.replace(inputs, gold_metadata=other))["C1"].detail


def test_c2_needs_planned_stays_and_a_plausible_positive_rate(tmp_path):
    assert evaluate(make_inputs(tmp_path, gold_overrides={"n_planned_stays": 0}))["C2"].status == "FAIL"
    positives = [row(f"p{i}", "2024-05-01T00:00:00Z", "2024-05-03T00:00:00Z", 1) for i in range(10)]
    result = evaluate(make_inputs(tmp_path, df=frame(gold_rows(positives))))["C2"]
    assert result.status == "FAIL" and "positive rate" in result.detail


# --- C3 -------------------------------------------------------------------------------------------------------------------


def test_c3_unreadable_prereg_commit_fails(tmp_path):
    summary = raw_summary(provenance={**raw_summary()["provenance"], "prereg_commit": "f" * 40})
    result = evaluate(make_inputs(tmp_path, raw_summary=summary))["C3"]
    assert result.status == "FAIL" and "cannot read prereg.yaml" in result.detail


@pytest.mark.parametrize("commit", ["--output=/tmp/x", "C" * 40, "c" * 39, 7])
def test_c3_malformed_prereg_commit_fails_without_calling_git(tmp_path, commit):
    def never(c):
        pytest.fail(f"git called with {c!r}")

    summary = raw_summary(provenance={**raw_summary()["provenance"], "prereg_commit": commit})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        results = pc.evaluate_checks(make_inputs(tmp_path, raw_summary=summary), PREREG, prereg_blob_at=never)
    assert "not a 40-character lowercase hex commit" in results[2].detail


def test_c3_raw_summary_must_equal_the_gold_metadata_copy(tmp_path):
    result = evaluate(make_inputs(tmp_path, raw_summary=raw_summary(n_alive_patients=20001)))["C3"]
    assert result.status == "FAIL" and "differs from the gold metadata" in result.detail


def test_c3_prereg_mismatch_and_build_commit_mismatch(tmp_path):
    assert "seed" in evaluate(make_inputs(tmp_path, raw_summary=raw_summary(seed=18)))["C3"].detail
    inputs = make_inputs(tmp_path, gold_overrides={"build_provenance": {"prereg_commit": "9" * 40}})
    assert "build_provenance.prereg_commit" in evaluate(inputs)["C3"].detail


# --- C4, C5 ---------------------------------------------------------------------------------------------------------------


def test_c4_duplicate_raw_ids_and_gold_ids_missing_from_raw(tmp_path):
    inputs = make_inputs(tmp_path)
    duplicated = pd.concat([inputs.patients, inputs.patients.iloc[:1]], ignore_index=True)
    assert evaluate(dataclasses.replace(inputs, patients=duplicated))["C4"].values["n_duplicate_raw_ids"] == 1
    missing = inputs.patients[inputs.patients["Id"] != "n0"]
    result = evaluate(dataclasses.replace(inputs, patients=missing))["C4"]
    assert result.status == "FAIL" and result.values["n_gold_ids_missing_from_raw"] == 1


def test_c5_one_shared_id_fails(tmp_path):
    inputs = make_inputs(tmp_path, MAIN)
    shared = pc.IdSource("pilot_20k_raw", "gs://p", frozenset({"pilot-a", "n3"}), 2)
    result = evaluate(dataclasses.replace(inputs, comparators=(shared, *inputs.comparators[1:])))["C5"]
    assert result.status == "FAIL" and result.values["pilot_20k_raw"]["n_overlap"] == 1


def test_c5_v1_link(tmp_path):
    inputs = make_inputs(tmp_path)
    assert evaluate(dataclasses.replace(inputs, v1_raw_summary={"seed": 43}))["C5"].status == "FAIL"
    stray = pc.IdSource("v1_gold_full_run_2b", "v1g", frozenset({"v1-a", "v1-z"}), 2)
    result = evaluate(dataclasses.replace(inputs, comparators=(inputs.comparators[0], stray)))["C5"]
    assert result.status == "FAIL" and result.values["n_v1_gold_ids_missing_from_v1_raw"] == 1


# --- C6, C7, C7b ----------------------------------------------------------------------------------------------------------


def test_c6_unparsable_or_early_start_fails(tmp_path):
    bad = encounters([*DEFAULT_ENCOUNTERS, ("2026/10/12", "n0", "x")])
    assert evaluate(make_inputs(tmp_path, encounters=bad))["C6"].values["n_unparsable_start"] == 1
    early = encounters([("2026-10-11T23:59:59Z", "n0", "x")])
    assert evaluate(make_inputs(tmp_path, encounters=early))["C6"].status == "FAIL"


@pytest.mark.parametrize(
    ("start", "patient", "description", "deaths", "status"),
    [
        ("2026-10-13T08:00:00Z", "n0", "Encounter for check up", {}, "FAIL"),  # not a Death Certification
        ("2026-10-14T02:00:00Z", "late", "Death Certification", {"late": "2026-10-15"}, "FAIL"),  # dated before the death
        ("2026-10-13T10:00:00Z", "late", "Death Certification", {"late": "2026-10-13"}, "PASS"),  # died after reference_date
        ("2026-10-14T02:00:00Z", "ghost", "Death Certification", {}, "FAIL"),  # patient not in patients.csv
    ],
)
def test_c7_only_posthumous_death_certifications_after_the_end(tmp_path, start, patient, description, deaths, status):
    df = frame(gold_rows())
    inputs = make_inputs(tmp_path, df=df, patients=patients_for(df, deaths),
                         encounters=encounters([*DEFAULT_ENCOUNTERS, (start, patient, description)]))  # fmt: skip
    assert evaluate(inputs)["C7"].status == status


def test_c7_fails_instead_of_crashing_when_no_patient_has_a_death_date(tmp_path):
    df = frame(gold_rows())
    patients = patients_for(df).assign(DEATHDATE="")
    result = evaluate(make_inputs(tmp_path, df=df, patients=patients))["C7"]
    assert result.status == "FAIL" and result.values["n_not_posthumous"] == 1


def test_c7b_modeling_row_with_label_horizon_after_the_end_fails(tmp_path):
    late = row("late-pos", "2026-08-28T00:00:00Z", "2026-09-01T00:00:00Z", 1, label_horizon="2026-10-20T00:00:00Z")
    result = evaluate(make_inputs(tmp_path, df=frame(gold_rows([late]))))["C7b"]
    assert result.status == "FAIL" and result.values["n_modeling_label_horizon_after_ref1"] == 1


# --- C8, C9 ---------------------------------------------------------------------------------------------------------------


def _main_with(tmp_path, provenance: dict):
    return evaluate(make_inputs(tmp_path, MAIN, gold_overrides={"build_provenance": provenance}))["C8"]


def test_c8_fails_without_submitter_keys_or_runtime_keys(tmp_path):
    assert _main_with(tmp_path, {k: v for k, v in FULL_PROVENANCE.items() if k != "batch_id"}).status == "FAIL"
    assert _main_with(tmp_path, {k: v for k, v in FULL_PROVENANCE.items() if not k.startswith("runtime.")}).status == "FAIL"


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"runtime.env.DATAPROC_IMAGE_VERSION": "3.0.14-debian12"}, "PASS"),
        ({"runtime.env.DATAPROC_IMAGE_VERSION": "4.0.2", "runtime.conf.spark.dataproc.x.version": "0.44.0"}, "WARN"),
        ({"runtime.collection_errors": "java_version: RuntimeError"}, "WARN"),
        ({"runtime.java_version": None}, "WARN"),
        (
            {
                f"runtime.jar.{CONNECTOR}": None,
                "runtime.jar.gcs-connector-3.0.4.jar": "/x",
                "runtime.jar.google-cloud-bigquerystorage-3.11.0.jar": "/x",
            },
            "WARN",
        ),
    ],
)
def test_c8_warnings(tmp_path, change, expected):
    provenance = {**FULL_PROVENANCE, **change}
    result = _main_with(tmp_path, {k: v for k, v in provenance.items() if v is not None})
    assert result.status == expected, result.detail
    assert all(k.startswith("runtime.") for k in result.values)


def test_c8_and_c9_are_info_for_the_pilot(tmp_path):
    results = evaluate(make_inputs(tmp_path))
    assert results["C8"].status == "INFO" and results["C9"].status == "INFO"
    assert isinstance(results["C9"].values["n_test_positive"], int)


def test_split_failure_fails_c7b_and_c9(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("post-condition")

    monkeypatch.setattr(pc, "chronological_group_split", boom)
    results = evaluate(make_inputs(tmp_path))
    assert results["C7b"].status == "FAIL" and results["C9"].status == "FAIL"


# --- no Id leaks ----------------------------------------------------------------------------------------------------------


def test_no_patient_or_encounter_id_or_description_leaks(tmp_path, capsys):
    df = frame(gold_rows([row("LEAKCHECK-G", "2024-06-01T00:00:00Z", "2024-06-03T00:00:00Z")]))
    patients = patients_for(df)
    patients = pd.concat([patients[patients["Id"] != "LEAKCHECK-G"],
                          pd.DataFrame({"Id": ["LEAKCHECK-P", "LEAKCHECK-P"], "DEATHDATE": ["", ""]})], ignore_index=True)  # fmt: skip
    inputs = make_inputs(tmp_path, MAIN, df=df, patients=patients,
                         encounters=encounters([*DEFAULT_ENCOUNTERS, ("2026-10-14T00:00:00Z", "LEAKCHECK-E", "LEAKCHECK-DESC")]))  # fmt: skip
    inputs = dataclasses.replace(
        inputs, comparators=(pc.IdSource("pilot_20k_raw", "gs://p", frozenset({"LEAKCHECK-P"}), 1), *inputs.comparators[1:])
    )
    results = list(evaluate(inputs).values())
    assert {r.id: r.status for r in results if r.id in ("C4", "C5", "C7")} == {"C4": "FAIL", "C5": "FAIL", "C7": "FAIL"}
    for r in results:
        print(pc.format_result(r))
    report = tmp_path / "report.json"
    pc.write_report(report, run=MAIN, head_commit="a", tree_dirty=False, prereg_sha256="b", generated_at_utc="t",
                    inputs={}, results=results, error=None)  # fmt: skip
    assert "LEAKCHECK" not in report.read_text() and "LEAKCHECK" not in capsys.readouterr().out


# --- gather_inputs --------------------------------------------------------------------------------------------------------


def _csv(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode()


@pytest.fixture
def gather_env(tmp_path):
    df = frame(gold_rows())
    gold_dir = write_gold(tmp_path / "gold", df, MAIN)
    fake = FakeGcloud()
    fake.objects[f"{raw_prefix(MAIN)}/generation_summary.json"] = json.dumps(raw_summary(MAIN)).encode()
    fake.objects[f"{raw_prefix(MAIN)}/csv/patients.csv"] = _csv(patients_for(df).assign(SSN="x"))
    fake.objects[f"{raw_prefix(MAIN)}/csv/encounters.csv"] = _csv(encounters(DEFAULT_ENCOUNTERS).assign(STOP=""))
    fake.objects[f"{raw_prefix(PILOT)}/csv/patients.csv"] = b"Id,BIRTHDATE\npilot-a,2000-01-01\n"
    v1_raw = tmp_path / "v1raw"
    (v1_raw / "csv").mkdir(parents=True)
    (v1_raw / "csv" / "patients.csv").write_text("Id,BIRTHDATE\npad_tr_0x,2000-01-01\n")
    (v1_raw / "generation_summary.json").write_text(json.dumps({"seed": 42}))
    v1_df = frame([row("pad_tr_0x", "2019-01-01T00:00:00Z", "2019-01-05T00:00:00Z")])
    v1_gold = tmp_path / "v1gold"
    v1_gold.mkdir()
    v1_df.to_parquet(v1_gold / "part-00000.parquet", index=False)
    write_gold_metadata_for_frame(v1_gold, v1_df)
    kwargs = {"runner": fake, "gold_opener": lambda uri: contextlib.nullcontext(gold_dir),
              "work_dir": tmp_path / "work", "v1_raw_dir": v1_raw, "v1_gold_dir": v1_gold}  # fmt: skip
    return {"fake": fake, "kwargs": kwargs, "gold_dir": gold_dir}


def test_gather_happy_path(gather_env):
    inputs = pc.gather_inputs(MAIN, **gather_env["kwargs"])
    assert inputs.gold_error is None and len(inputs.gold_frame) == 50
    assert list(inputs.patients.columns) == ["Id", "DEATHDATE"] and inputs.patients["DEATHDATE"].isna().sum() == 0
    assert [c.name for c in inputs.comparators] == ["pilot_20k_raw", "v1_raw", "v1_gold_full_run_2b"]
    for uri in (f"{raw_prefix(MAIN)}/csv/patients.csv", f"{raw_prefix(PILOT)}/csv/patients.csv"):
        assert inputs.inputs[uri]["crc32c"].startswith("fake-crc32c-")
    assert f"{gold_prefix(MAIN)}/part-00000.parquet" in inputs.inputs
    assert f"{gold_prefix(MAIN)}/_gold_metadata.json" in inputs.inputs
    assert (gather_env["kwargs"]["work_dir"] / MAIN / "comparators" / PILOT / "patients.csv").is_file()


def test_gather_size_mismatch_is_a_gather_error_with_inputs(gather_env):
    uri = f"{raw_prefix(MAIN)}/csv/encounters.csv"
    gather_env["fake"].size_overrides[uri] = 1
    with pytest.raises(pc.GatherError, match="downloaded") as excinfo:
        pc.gather_inputs(MAIN, **gather_env["kwargs"])
    assert f"{raw_prefix(MAIN)}/csv/patients.csv" in excinfo.value.inputs


def test_gather_missing_column_is_a_gather_error(gather_env):
    gather_env["fake"].objects[f"{raw_prefix(MAIN)}/csv/encounters.csv"] = b"START,PATIENT\n2026-10-12T00:00:00Z,n0\n"
    with pytest.raises(pc.GatherError, match=r"missing column\(s\) \['DESCRIPTION'\]"):
        pc.gather_inputs(MAIN, **gather_env["kwargs"])


def test_gather_missing_object_is_a_gather_error(gather_env):
    del gather_env["fake"].objects[f"{raw_prefix(PILOT)}/csv/patients.csv"]
    with pytest.raises(pc.GatherError):
        pc.gather_inputs(MAIN, **gather_env["kwargs"])


def test_gather_loader_refusal_sets_gold_error(gather_env):
    meta = gather_env["gold_dir"] / "_gold_metadata.json"
    data = json.loads(meta.read_text())
    data["n_rows"] += 1
    data["n_inpatient_stays"] += 1
    meta.write_text(json.dumps(data))
    inputs = pc.gather_inputs(MAIN, **gather_env["kwargs"])
    assert inputs.gold_frame is None and "does not match" in inputs.gold_error


def test_gather_opener_failure_is_a_gather_error(gather_env):
    def opener(uri):
        raise FileNotFoundError("no such bucket")

    with pytest.raises(pc.GatherError, match="cannot read gold"):
        pc.gather_inputs(MAIN, **{**gather_env["kwargs"], "gold_opener": opener})


# --- the CLI --------------------------------------------------------------------------------------------------------------


def _load_cli():
    spec = importlib.util.spec_from_file_location("check_population", REPO / "scripts" / "check_population.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_git(head_blob: bytes = PREREG_BLOB, *, status_rc: int = 0):
    def run_git(argv, **kwargs):
        args = argv[3:]  # after git -C <root>
        if args == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(argv, 0, b"a" * 40 + b"\n", b"")
        if args == ["show", "HEAD:prereg.yaml"]:
            return subprocess.CompletedProcess(argv, 0, head_blob, b"")
        if args == ["status", "--porcelain"]:
            return subprocess.CompletedProcess(argv, status_rc, b"", b"")
        if args == ["show", "--end-of-options", f"{PRE_COMMIT}:prereg.yaml"]:
            return subprocess.CompletedProcess(argv, 0, PREREG_BLOB, b"")
        return subprocess.CompletedProcess(argv, 128, b"", b"fatal")

    return run_git


@pytest.fixture
def cli_env(gather_env, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "prereg.yaml").write_bytes(PREREG_BLOB)
    kw = gather_env["kwargs"]
    argv = ["--run", MAIN, "--v1-raw-dir", str(kw["v1_raw_dir"]), "--v1-gold-dir", str(kw["v1_gold_dir"]),
            "--work-dir", str(kw["work_dir"])]  # fmt: skip
    call = {"runner": kw["runner"], "gold_opener": kw["gold_opener"], "run_git": fake_git(), "repo_root": repo,
            "environ": {"CLOUDSDK_CORE_PROJECT": "synthea-readmission-risk"}}  # fmt: skip
    return {"argv": argv, "call": call, "report": kw["work_dir"] / MAIN / "population_checks.json", "repo": repo}


def test_cli_writes_the_report_on_exit_1(cli_env, capsys):
    cli = _load_cli()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        code = cli.main(cli_env["argv"], **cli_env["call"])
    report = json.loads(cli_env["report"].read_text())
    assert code == 1  # C9: the hand-built population is far below the 400-positive floor
    assert set(report) == {"run", "head_commit", "tree_dirty", "prereg_sha256", "generated_at_utc", "inputs", "checks", "error"}
    assert report["prereg_sha256"] == hashlib.sha256(PREREG_BLOB).hexdigest() and report["error"] is None
    assert [c["id"] for c in report["checks"]][-1] == "C9" and "FAIL C9 Power (M7)" in capsys.readouterr().out


def test_cli_exit_0_when_every_check_passes(cli_env, monkeypatch):
    cli = _load_cli()
    monkeypatch.setattr(cli, "evaluate_checks", lambda inputs, prereg, **kw: [pc.CheckResult("C1", "x", "PASS", "ok", {})])
    assert cli.main(cli_env["argv"], **cli_env["call"]) == 0
    assert json.loads(cli_env["report"].read_text())["checks"][0]["status"] == "PASS"


def test_cli_evaluation_error_exits_2_with_a_report(cli_env, monkeypatch):
    cli = _load_cli()

    def boom(inputs, prereg, **kw):
        raise TypeError("unexpected")

    monkeypatch.setattr(cli, "evaluate_checks", boom)
    assert cli.main(cli_env["argv"], **cli_env["call"]) == 2
    report = json.loads(cli_env["report"].read_text())
    assert report["checks"] == [] and report["error"] == "evaluation failed: TypeError: unexpected"


def test_cli_gather_error_exits_2_with_a_report(cli_env):
    cli = _load_cli()
    del cli_env["call"]["runner"].objects[f"{raw_prefix(PILOT)}/csv/patients.csv"]
    assert cli.main(cli_env["argv"], **cli_env["call"]) == 2
    report = json.loads(cli_env["report"].read_text())
    assert report["checks"] == [] and report["error"] and report["inputs"]


@pytest.mark.parametrize("case", ["project", "edited_prereg", "invalid_prereg", "git_status"])
def test_cli_preconditions_exit_2_without_a_report(cli_env, case, capsys):
    cli = _load_cli()
    call = dict(cli_env["call"])
    if case == "project":
        call["environ"] = {}
    elif case == "edited_prereg":
        (cli_env["repo"] / "prereg.yaml").write_bytes(PREREG_BLOB + b"\n# edited\n")
    elif case == "invalid_prereg":
        (cli_env["repo"] / "prereg.yaml").write_bytes(b"prereg_version: 1\n")
        call["run_git"] = fake_git(b"prereg_version: 1\n")
    else:
        call["run_git"] = fake_git(status_rc=128)
    assert cli.main(cli_env["argv"], **call) == 2
    assert not cli_env["report"].exists() and "ERROR:" in capsys.readouterr().err


# --- import rules ---------------------------------------------------------------------------------------------------------

FORBIDDEN = ("readmission_risk.scoring", "readmission_risk.models.training", "readmission_risk.models.evaluate",
             "readmission_risk.models.tracking", "mlflow", "sklearn", "xgboost")  # fmt: skip


def _forbidden(module: str, extra: tuple[str, ...] = ()) -> bool:
    return any(module == f or module.startswith(f + ".") for f in FORBIDDEN + extra)


@pytest.mark.parametrize("path", ["src/readmission_risk/ops/population_checks.py", "scripts/check_population.py"])
def test_no_direct_import_of_model_code(path):
    tree = ast.parse((REPO / path).read_text())
    imported = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    imported += [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
    assert not [m for m in imported if _forbidden(m)]


def test_importing_population_checks_loads_no_model_code():
    code = "import sys, readmission_risk.ops.population_checks; print('\\n'.join(sorted(sys.modules)))"
    loaded = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()
    assert not [m for m in loaded if _forbidden(m, ("pyspark",))]
