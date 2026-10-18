"""Pre-training checks C1-C9 on a generated v2 population (v2 slice 6a): read-only, counts only, never loads or fits a model.

`gather_inputs` does all I/O (gcloud through ops/gcloud.py's runner, the gold through warehouse.gold_source, local v1 files);
`evaluate_checks` is pure. The no-peeking rule: a result's `detail` and `values` hold counts, sizes, checksums, versions, dates
and configuration values only, never a patient Id, encounter Id or description text from the data, and no per-subgroup
figures (tests/ops/test_population_checks.py seeds failures with marker Ids and checks none leaks). Design:
notes/eg-new-feature/v2-slice6a-main-200k-and-checks-2026-10-14.md (Interfaces > S3).
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import json
import re
import shutil
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow as pa
import pyarrow.csv as pacsv

from readmission_risk.models.data import load_gold_table_with_metadata
from readmission_risk.models.split import chronological_group_split
from readmission_risk.ops.dataproc_batch import (
    RUNTIME_VERSION,
    generation_summary_problems,
)
from readmission_risk.ops.gcloud import GcloudError, Runner, run_json, run_ok
from readmission_risk.ops.generation_vm import (
    MAIN_RUN,
    gold_prefix,
    raw_prefix,
    sha256_file,
)
from readmission_risk.ops.prereg import Prereg
from readmission_risk.pipeline.gold_metadata import (
    FINGERPRINT_DEFINITION_ROWHASH_V1,
    GoldMetadata,
    parse_yyyymmdd,
)
from readmission_risk.pipeline.runtime_env import RUNTIME_KEY_PREFIX
from readmission_risk.warehouse.gold_source import materialized_gold_dir

RUNS = ("pilot_20k", MAIN_RUN)
PILOT_RUN = "pilot_20k"
V1_SEED = 42
EXPECTED_GOLD_COLUMN_COUNT = 24
POSITIVE_RATE_RANGE = (0.005, 0.05)
MAX_PAYER_NULL_FRACTION = 0.10
DEATH_CERTIFICATION = "Death Certification"
REQUIRED_BUILD_PROVENANCE = ("head_commit", "zip_sha256", "main_sha256", "prereg_commit", "batch_id")
RAW_CSVS = ("patients.csv", "encounters.csv")
PATIENT_COLUMNS = ("Id", "DEATHDATE")
ENCOUNTER_COLUMNS = ("START", "PATIENT", "DESCRIPTION")
_START_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_DATE_FORMAT = "%Y-%m-%d"
COMMIT_RE = re.compile(r"[0-9a-f]{40}")  # a prereg_commit read from GCS is passed to git only in this form
_SUB_VERSION_RE = re.compile(rf"(?<![0-9.]){re.escape(RUNTIME_VERSION)}\.[0-9]+")

Status = Literal["PASS", "FAIL", "WARN", "INFO", "SKIP"]


@dataclass(frozen=True)
class CheckResult:
    id: str
    name: str
    status: Status
    detail: str
    values: dict


@dataclass(frozen=True)
class IdSource:
    name: str  # "pilot_20k_raw" | "v1_raw" | "v1_gold_full_run_2b"
    location: str
    ids: frozenset[str]
    n_rows: int


@dataclass(frozen=True)
class PopulationInputs:
    run: str
    gold_frame: pd.DataFrame | None
    gold_metadata: GoldMetadata | None
    gold_error: str | None
    raw_summary: dict
    patients: pd.DataFrame
    encounters: pd.DataFrame
    comparators: tuple[IdSource, ...]
    v1_raw_summary: dict | None
    inputs: dict[str, dict]


class GatherError(RuntimeError):
    def __init__(self, message: str, inputs: dict) -> None:
        super().__init__(message)
        self.inputs = dict(inputs)


# --- gathering (all I/O) ----------------------------------------------------------------------------------------------


def _record_local(inputs: dict, path: Path) -> None:
    inputs[str(path)] = {"bytes": path.stat().st_size, "sha256": sha256_file(path), "crc32c": None}


def _read_columns(path: Path, columns: tuple[str, ...], inputs: dict) -> pd.DataFrame:
    with path.open(newline="", encoding="utf-8") as f:
        header = next(csv.reader(f), [])
    missing = [c for c in columns if c not in header]
    if missing:
        raise GatherError(f"{path}: missing column(s) {missing}", inputs)
    table = pacsv.read_csv(
        path,
        convert_options=pacsv.ConvertOptions(
            include_columns=list(columns),
            column_types={c: pa.string() for c in columns},
            strings_can_be_null=False,
        ),
    )
    return table.to_pandas()


def _download(runner: Runner | None, uri: str, dest: Path, inputs: dict) -> Path:
    described = run_json(runner, ["gcloud", "storage", "objects", "describe", uri])
    if not isinstance(described, dict) or "size" not in described:
        raise GatherError(f"{uri}: unexpected describe output", inputs)
    run_ok(runner, ["gcloud", "storage", "cp", uri, str(dest)])  # gcloud verifies the crc32c of a download itself
    size = dest.stat().st_size
    if size != int(described["size"]):
        raise GatherError(f"{uri}: downloaded {size} bytes, the object has {described['size']}", inputs)
    inputs[uri] = {"bytes": size, "sha256": sha256_file(dest), "crc32c": described.get("crc32c_hash")}
    return dest


def _fresh_dir(path: Path) -> Path:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def _ids(name: str, location: str, values: pd.Series) -> IdSource:
    return IdSource(name=name, location=location, ids=frozenset(values.astype(str)), n_rows=len(values))


def gather_inputs(
    run: str,
    *,
    runner: Runner | None,
    gold_opener: Callable[[str], AbstractContextManager[Path]] = materialized_gold_dir,
    work_dir: Path,
    v1_raw_dir: Path,
    v1_gold_dir: Path,
) -> PopulationInputs:
    inputs: dict[str, dict] = {}
    if run not in RUNS:
        raise GatherError(f"unknown run {run!r} (known: {list(RUNS)})", inputs)
    try:
        return _gather(run, runner, gold_opener, work_dir, v1_raw_dir, v1_gold_dir, inputs)
    except GatherError:
        raise
    except (GcloudError, OSError, ValueError, KeyError) as exc:
        raise GatherError(f"{type(exc).__name__}: {exc}", inputs) from exc


def _gather(run, runner, gold_opener, work_dir, v1_raw_dir, v1_gold_dir, inputs) -> PopulationInputs:
    summary_uri = f"{raw_prefix(run)}/generation_summary.json"
    summary_text = run_ok(runner, ["gcloud", "storage", "cat", summary_uri])
    inputs[summary_uri] = {
        "bytes": len(summary_text.encode()),
        "sha256": hashlib.sha256(summary_text.encode()).hexdigest(),
        "crc32c": None,
    }
    try:
        raw_summary = json.loads(summary_text)
    except json.JSONDecodeError as exc:
        raise GatherError(f"{summary_uri} is not valid JSON: {exc}", inputs) from exc
    if not isinstance(raw_summary, dict):
        raise GatherError(f"{summary_uri} is not a JSON object", inputs)

    subset = _fresh_dir(Path(work_dir) / run / "raw_subset")
    local = {name: _download(runner, f"{raw_prefix(run)}/csv/{name}", subset / name, inputs) for name in RAW_CSVS}
    patients = _read_columns(local["patients.csv"], PATIENT_COLUMNS, inputs)
    encounters = _read_columns(local["encounters.csv"], ENCOUNTER_COLUMNS, inputs)

    comparators: list[IdSource] = []
    if run == MAIN_RUN:
        pilot_dir = _fresh_dir(Path(work_dir) / run / "comparators" / PILOT_RUN)
        uri = f"{raw_prefix(PILOT_RUN)}/csv/patients.csv"
        pilot = _read_columns(_download(runner, uri, pilot_dir / "patients.csv", inputs), ("Id",), inputs)
        comparators.append(_ids(f"{PILOT_RUN}_raw", uri, pilot["Id"]))

    v1_patients, v1_summary_path = Path(v1_raw_dir) / "csv" / "patients.csv", Path(v1_raw_dir) / "generation_summary.json"
    for path in (v1_patients, v1_summary_path):
        if not path.is_file():
            raise GatherError(f"v1 raw file not found: {path}", inputs)
        _record_local(inputs, path)
    v1_raw_summary = json.loads(v1_summary_path.read_text(encoding="utf-8"))
    comparators.append(_ids("v1_raw", str(v1_patients), _read_columns(v1_patients, ("Id",), inputs)["Id"]))
    v1_gold_dir = Path(v1_gold_dir)
    if not v1_gold_dir.is_dir():
        raise GatherError(f"v1 gold directory not found: {v1_gold_dir}", inputs)
    for path in sorted(p for p in v1_gold_dir.iterdir() if p.is_file()):
        _record_local(inputs, path)
    try:
        v1_gold, _ = load_gold_table_with_metadata(v1_gold_dir)
    except (ValueError, FileNotFoundError) as exc:
        raise GatherError(f"v1 gold {v1_gold_dir} refused: {exc}", inputs) from exc
    comparators.append(_ids("v1_gold_full_run_2b", str(v1_gold_dir), v1_gold["patient_id"]))

    frame = metadata = gold_error = None
    stack = contextlib.ExitStack()
    try:
        gold_dir = stack.enter_context(gold_opener(gold_prefix(run)))
    except Exception as exc:  # any failure before the gold is on disk (auth, network, missing prefix) is a gather failure
        raise GatherError(f"cannot read gold at {gold_prefix(run)}: {type(exc).__name__}: {exc}", inputs) from exc
    with stack:
        for path in sorted(p for p in Path(gold_dir).iterdir() if p.is_file()):
            inputs[f"{gold_prefix(run)}/{path.name}"] = {"bytes": path.stat().st_size, "sha256": sha256_file(path), "crc32c": None}
        try:
            frame, metadata = load_gold_table_with_metadata(Path(gold_dir))
        except (ValueError, FileNotFoundError) as exc:
            gold_error = str(exc)

    return PopulationInputs(
        run=run,
        gold_frame=frame,
        gold_metadata=metadata,
        gold_error=gold_error,
        raw_summary=raw_summary,
        patients=patients,
        encounters=encounters,
        comparators=tuple(comparators),
        v1_raw_summary=v1_raw_summary if isinstance(v1_raw_summary, dict) else None,
        inputs=inputs,
    )


# --- evaluation (pure) ------------------------------------------------------------------------------------------------


def _result(check_id: str, name: str, problems: list[str], values: dict, ok_detail: str = "ok") -> CheckResult:
    return CheckResult(check_id, name, "FAIL" if problems else "PASS", "; ".join(problems) or ok_detail, values)


def _skip(check_id: str, name: str) -> CheckResult:
    return CheckResult(check_id, name, "SKIP", "gold unavailable (see C1)", {})


def _check_gold_settings(inputs: PopulationInputs, prereg: Prereg) -> CheckResult:
    name = "Gold loads, fingerprint, label settings"
    if inputs.gold_error is not None or inputs.gold_metadata is None:
        return CheckResult("C1", name, "FAIL", f"gold refused by the loader: {inputs.gold_error}", {})
    meta, label = inputs.gold_metadata, prereg.label
    expected = {
        "metadata_version": 2,
        "gold_fingerprint_definition": FINGERPRINT_DEFINITION_ROWHASH_V1,
        "label_definition": label["definition"],
        "planned_procedure_codes": tuple(label["planned_procedure_codes"]),
        "readmission_window_days": label["readmission_window_days"],
        "lookback_years": label["lookback_years"],
        "reference_date": prereg.generation["reference_date"],
    }
    problems = [
        f"{field}: gold has {getattr(meta, field)!r}, expected {want!r}"
        for field, want in expected.items()
        if getattr(meta, field) != want
    ]
    values = {
        "metadata_version": meta.metadata_version,
        "gold_fingerprint": meta.gold_fingerprint,
        "spark_version": meta.spark_version,
        "n_rows": meta.n_rows,
        "n_positive": meta.n_positive,
    }
    return _result("C1", name, problems, values)


def _check_structure(inputs: PopulationInputs) -> CheckResult:
    name = "Structure"
    frame, meta = inputs.gold_frame, inputs.gold_metadata
    if frame is None or meta is None:
        return _skip("C2", name)
    rate = meta.n_positive / meta.n_rows if meta.n_rows else 0.0
    all_null = [c for c in frame.columns if frame[c].isna().all()]
    payer_null = float(frame["payer_ownership"].isna().mean()) if "payer_ownership" in frame.columns else 1.0
    problems = []
    if meta.n_planned_stays <= 0:
        problems.append(f"n_planned_stays is {meta.n_planned_stays}")
    if not POSITIVE_RATE_RANGE[0] <= rate <= POSITIVE_RATE_RANGE[1]:
        problems.append(f"positive rate {rate:.4%} outside [{POSITIVE_RATE_RANGE[0]:.1%}, {POSITIVE_RATE_RANGE[1]:.1%}]")
    if len(frame.columns) != EXPECTED_GOLD_COLUMN_COUNT:
        problems.append(f"{len(frame.columns)} columns, expected {EXPECTED_GOLD_COLUMN_COUNT}")
    if all_null:
        problems.append(f"all-null column(s) {all_null}")
    if payer_null > MAX_PAYER_NULL_FRACTION:
        problems.append(f"payer_ownership null fraction {payer_null:.4f} > {MAX_PAYER_NULL_FRACTION}")
    values = {
        "n_rows": meta.n_rows,
        "n_positive": meta.n_positive,
        "positive_rate": round(rate, 6),
        "n_planned_stays": meta.n_planned_stays,
        "n_columns": len(frame.columns),
        "payer_ownership_null_fraction": round(payer_null, 6),
    }
    return _result("C2", name, problems, values)


def _check_generation(inputs: PopulationInputs, prereg: Prereg, prereg_blob_at: Callable[[str], bytes]) -> CheckResult:
    summary = inputs.raw_summary
    problems = list(generation_summary_problems(summary, prereg, inputs.run))
    provenance = summary.get("provenance") or {}
    commit = provenance.get("prereg_commit")
    if not commit:
        problems.append("generation_summary.provenance.prereg_commit is missing")
    elif not (isinstance(commit, str) and COMMIT_RE.fullmatch(commit)):
        problems.append("generation_summary.provenance.prereg_commit is not a 40-character lowercase hex commit")
    else:
        try:
            blob_sha = hashlib.sha256(prereg_blob_at(commit)).hexdigest()
        except Exception as exc:  # noqa: BLE001 -- any failure to read the committed prereg is this clause's FAIL
            problems.append(f"cannot read prereg.yaml at {commit}: {type(exc).__name__}")
        else:
            if blob_sha != provenance.get("prereg_sha256"):
                problems.append(f"sha256 of prereg.yaml at {commit} is {blob_sha}, the raw run recorded {provenance.get('prereg_sha256')!r}")
    meta = inputs.gold_metadata
    ok_detail = "ok"
    if meta is None:
        ok_detail = "gold unavailable: metadata clauses not run"
    else:
        if summary != meta.generation_summary:
            problems.append("the raw generation_summary.json differs from the gold metadata's generation_summary")
        built_from = (meta.build_provenance or {}).get("prereg_commit")
        if built_from is not None and built_from != commit:
            problems.append(f"build_provenance.prereg_commit {built_from} differs from the generation's {commit}")
    values = {
        "prereg_commit": commit,
        "population_size": summary.get("population_size"),
        "n_alive_patients": summary.get("n_alive_patients"),
    }
    return _result("C3", "Generation provenance", problems, values, ok_detail)


def _check_patient_ids(inputs: PopulationInputs) -> CheckResult:
    raw = inputs.patients["Id"]
    n_duplicate = int(len(raw) - raw.nunique())
    problems = [f"{n_duplicate} duplicated raw patient Id(s)"] if n_duplicate else []
    values: dict = {"n_raw_patients": len(raw), "n_duplicate_raw_ids": n_duplicate}
    if inputs.gold_frame is not None:
        gold_ids = set(inputs.gold_frame["patient_id"].astype(str))
        missing = len(gold_ids - set(raw))
        values.update(n_gold_patients=len(gold_ids), n_gold_ids_missing_from_raw=missing)
        if missing:
            problems.append(f"{missing} gold patient Id(s) not in the raw patients.csv")
    return _result("C4", "Patient Ids", problems, values)


def _check_disjointness(inputs: PopulationInputs) -> CheckResult:
    run_ids = set(inputs.patients["Id"])
    problems: list[str] = []
    values: dict = {"n_run_ids": len(run_ids)}
    by_name = {source.name: source for source in inputs.comparators}
    for source in inputs.comparators:
        overlap = len(run_ids & source.ids)
        values[source.name] = {"n_rows": source.n_rows, "n_ids": len(source.ids), "n_overlap": overlap}
        if overlap:
            problems.append(f"{overlap} patient Id(s) shared with {source.name}")
    v1_raw, v1_gold = by_name.get("v1_raw"), by_name.get("v1_gold_full_run_2b")
    seed = (inputs.v1_raw_summary or {}).get("seed")
    if seed != V1_SEED or v1_raw is None or v1_gold is None:
        problems.append(f"not the v1 raw behind full_run_2b: v1 generation_summary seed is {seed!r}, expected {V1_SEED}")
    else:
        if len(v1_raw.ids) != v1_raw.n_rows:
            problems.append(f"not the v1 raw behind full_run_2b: {v1_raw.n_rows - len(v1_raw.ids)} duplicated v1 raw Id(s)")
        outside = len(v1_gold.ids - v1_raw.ids)
        values["n_v1_gold_ids_missing_from_v1_raw"] = outside
        if outside:
            problems.append(f"not the v1 raw behind full_run_2b: {outside} full_run_2b patient Id(s) missing from the v1 raw")
    return _result("C5", "Disjointness", problems, values)


def _starts(inputs: PopulationInputs) -> pd.Series:
    return pd.to_datetime(inputs.encounters["START"], format=_START_FORMAT, utc=True, errors="coerce")


def _check_latest_start(inputs: PopulationInputs, ref: pd.Timestamp, starts: pd.Series) -> CheckResult:
    n_unparsable = int(starts.isna().sum())
    latest = starts.max()
    problems = []
    if n_unparsable:
        problems.append(f"{n_unparsable} encounter START value(s) do not parse as {_START_FORMAT}")
    if pd.isna(latest) or latest < ref:
        problems.append(f"latest encounter START {latest} is before the reference date {ref.date()}")
    values = {"n_encounters": len(starts), "n_unparsable_start": n_unparsable,
              "max_start": None if pd.isna(latest) else latest.isoformat()}  # fmt: skip
    return _result("C6", "A7 (i) latest START", problems, values)


def _check_posthumous(inputs: PopulationInputs, ref1: pd.Timestamp, starts: pd.Series) -> CheckResult:
    late = starts >= ref1
    after = inputs.encounters.loc[late]
    after_starts = starts.loc[late]
    deaths = pd.to_datetime(inputs.patients["DEATHDATE"], format=_DATE_FORMAT, utc=True, errors="coerce")
    death_by_id = deaths.set_axis(inputs.patients["Id"].to_numpy())  # not via numpy: an all-NaT array would lose its UTC zone
    death_by_id = death_by_id[~death_by_id.index.duplicated(keep="first")]
    death = after["PATIENT"].map(death_by_id)
    not_dc = int((after["DESCRIPTION"] != DEATH_CERTIFICATION).sum())
    posthumous = death.notna() & (death <= after_starts.dt.floor("D"))
    not_posthumous = int((~posthumous).sum())
    problems = []
    if not_dc:
        problems.append(f"{not_dc} encounter(s) starting on or after {ref1.date()} are not a {DEATH_CERTIFICATION}")
    if not_posthumous:
        problems.append(f"{not_posthumous} encounter(s) starting on or after {ref1.date()} are not posthumous")
    values = {"n_after_ref1": len(after), "n_not_death_certification": not_dc, "n_not_posthumous": not_posthumous}
    return _result("C7", "A7 (ii) posthumous Death Certifications only", problems, values)


def _check_label_horizon(inputs: PopulationInputs, ref1: pd.Timestamp, split, split_error: str | None) -> CheckResult:
    name = "A7 (iii) modeling rows within the observation window"
    if inputs.gold_frame is None:
        return _skip("C7b", name)
    if split_error is not None:
        return CheckResult("C7b", name, "FAIL", f"the pre-registered split raised: {split_error}", {})
    modeling = pd.concat([split.train, split.test], ignore_index=True)
    n_after = int((modeling["label_horizon"] >= ref1).sum())
    values = {
        "n_modeling_rows": len(modeling),
        "n_modeling_label_horizon_after_ref1": n_after,
        "n_gold_label_horizon_after_ref1": int((inputs.gold_frame["label_horizon"] >= ref1).sum()),
        "n_modeling_index_stop_after_ref1": int((modeling["index_stop"] >= ref1).sum()),
    }
    problems = [f"{n_after} modeling row(s) have label_horizon on or after {ref1.date()}"] if n_after else []
    return _result("C7b", name, problems, values)


def _check_runtime(inputs: PopulationInputs) -> CheckResult:
    name = "Runtime facts (FR14)"
    meta = inputs.gold_metadata
    if meta is None:
        return _skip("C8", name)
    provenance = meta.build_provenance or {}
    runtime = {k: v for k, v in sorted(provenance.items()) if k.startswith(RUNTIME_KEY_PREFIX)}
    if inputs.run != MAIN_RUN:
        return CheckResult("C8", name, "INFO", "not recorded (built before 6a)" if not runtime else "recorded", runtime)
    missing = [k for k in REQUIRED_BUILD_PROVENANCE if k not in provenance]
    if missing or not runtime:
        detail = f"build_provenance lacks {missing}" if missing else f"build_provenance has no {RUNTIME_KEY_PREFIX}* key"
        return CheckResult("C8", name, "FAIL", f"{detail}: the gold was not built by this slice's submitter", runtime)
    warnings = [f"{RUNTIME_KEY_PREFIX}{k} missing" for k in ("python_version", "java_version") if f"{RUNTIME_KEY_PREFIX}{k}" not in runtime]
    if f"{RUNTIME_KEY_PREFIX}collection_errors" in runtime:
        warnings.append(f"collection errors: {runtime[f'{RUNTIME_KEY_PREFIX}collection_errors']}")
    jar_prefix = f"{RUNTIME_KEY_PREFIX}jar."
    jars = [k.removeprefix(jar_prefix) for k in runtime if k.startswith(jar_prefix)]
    if not any(j.startswith("spark-") and "bigquery" in j for j in jars):
        warnings.append("no BigQuery connector jar found (FR14 connector version not recorded)")
    version_values = [v for k, v in runtime.items() if k.startswith((f"{RUNTIME_KEY_PREFIX}env.", f"{RUNTIME_KEY_PREFIX}conf."))]
    if not any(_SUB_VERSION_RE.search(v) for v in version_values):
        warnings.append(f"no {RUNTIME_VERSION}.<n> runtime sub-version found (FR14 sub-version not recorded)")
    if warnings:
        return CheckResult("C8", name, "WARN", "; ".join(warnings), runtime)
    return CheckResult("C8", name, "PASS", "ok", runtime)


def _check_power(inputs: PopulationInputs, prereg: Prereg, split, split_error: str | None) -> CheckResult:
    name = "Power (M7)"
    if inputs.gold_frame is None:
        return _skip("C9", name)
    if split_error is not None:
        return CheckResult("C9", name, "FAIL", f"the pre-registered split raised: {split_error}", {})
    counts = {k: v for k, v in split.summary.items() if isinstance(v, int) and not isinstance(v, bool)}
    n_test_positive = int(split.summary["n_test_positive"])
    floor = prereg.power["min_test_positives"]
    if inputs.run != MAIN_RUN:
        return CheckResult("C9", name, "INFO", f"n_test_positive {n_test_positive} (floor {floor} applies to {MAIN_RUN})", counts)
    problems = [] if n_test_positive >= floor else [f"n_test_positive {n_test_positive} < min_test_positives {floor}"]
    return _result("C9", name, problems, counts, f"n_test_positive {n_test_positive} >= {floor}")


def evaluate_checks(inputs: PopulationInputs, prereg: Prereg, *, prereg_blob_at: Callable[[str], bytes]) -> list[CheckResult]:
    ref_date = parse_yyyymmdd(prereg.generation["reference_date"], "reference_date")
    ref = pd.Timestamp(ref_date, tz="UTC")
    ref1 = ref + timedelta(days=1)
    split = split_error = None
    if inputs.gold_frame is not None:
        train_start = prereg.split["train_start_date"]
        try:
            split = chronological_group_split(
                inputs.gold_frame,
                reference_date=ref_date,
                test_start_date=parse_yyyymmdd(prereg.split["test_start_date_resolved"], "test_start_date_resolved"),
                readmission_window_days=prereg.label["readmission_window_days"],
                train_start_date=None if train_start is None else parse_yyyymmdd(train_start, "train_start_date"),
            )
        except (RuntimeError, ValueError) as exc:
            split_error = str(exc)
    starts = _starts(inputs)
    return [
        _check_gold_settings(inputs, prereg),
        _check_structure(inputs),
        _check_generation(inputs, prereg, prereg_blob_at),
        _check_patient_ids(inputs),
        _check_disjointness(inputs),
        _check_latest_start(inputs, ref, starts),
        _check_posthumous(inputs, ref1, starts),
        _check_label_horizon(inputs, ref1, split, split_error),
        _check_runtime(inputs),
        _check_power(inputs, prereg, split, split_error),
    ]


def exit_code(results: list[CheckResult]) -> int:
    return 1 if any(r.status == "FAIL" for r in results) else 0


def format_result(result: CheckResult) -> str:
    return f"{result.status} {result.id} {result.name}: {result.detail}"


def write_report(
    path: Path,
    *,
    run: str,
    head_commit: str,
    tree_dirty: bool,
    prereg_sha256: str,
    generated_at_utc: str,
    inputs: dict,
    results: list[CheckResult],
    error: str | None,
) -> None:
    report = {
        "run": run,
        "head_commit": head_commit,
        "tree_dirty": tree_dirty,
        "prereg_sha256": prereg_sha256,
        "generated_at_utc": generated_at_utc,
        "inputs": inputs,
        "checks": [asdict(r) for r in results],
        "error": error,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
