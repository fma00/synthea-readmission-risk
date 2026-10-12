"""Local Synthea patient generation: invoke Synthea, validate its CSV output, and
record a durable summary of each run. See notes/eg-new-feature/local-synthea-generation-2026-09-16.md
for the design this implements.
"""

from __future__ import annotations

import csv
import json
import re
import resource
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# Synthea CLI/config facts below were verified against the v4.0.0 release tag's source and synthea.properties, not
# assumed. The jar scripts/setup_synthea.sh downloads from the v4.0.0 release reports build v3.4.0-18-ga07a65555 (pinned
# as synthea_build_version in prereg.yaml); the flags used here were exercised against that jar in the slice-5 pilot.

EXPECTED_CSV_TABLES: tuple[str, ...] = (
    "patients.csv",
    "encounters.csv",
    "conditions.csv",
    "medications.csv",
    "procedures.csv",
    "careplans.csv",
    "observations.csv",
    "payers.csv",
    "providers.csv",
    "organizations.csv",
)
# Deliberately not Synthea's full ~18-table CSV output (also includes claims.csv,
# immunizations.csv, allergies.csv, and others) -- this is the join-relevant subset slice 2's Big
# Join reads (see src/readmission_risk/pipeline/big_join.py's TABLES_JOINED), gated for pass/fail
# purposes. GenerationResult.row_counts (below) is NOT limited to this list -- it accounts for
# every CSV file Synthea actually writes.
# Extended from 7 to 10 entries (payers.csv, providers.csv, organizations.csv added) in slice 2 --
# see notes/eg-new-feature/pyspark-big-join-2026-09-19.md Scope item 1a: the Big Join joins all 10
# of these, and a missing/empty dimension table would otherwise go uncaught at regeneration time.

REQUIRED_NONEMPTY_TABLES: tuple[str, ...] = (
    "patients.csv",
    "encounters.csv",
    "payers.csv",
    "providers.csv",
    "organizations.csv",
)
# patients.csv/encounters.csv: must never be empty even at the 500-1,000-patient dry run -- a
# zero-row patients.csv after a 0-exit-code run means generation silently produced nothing,
# categorically different from a rare-condition table (e.g. careplans.csv) legitimately having
# zero rows at small population sizes.
# payers.csv/providers.csv/organizations.csv: added in slice 2 -- confirmed non-empty in every
# real generation run so far (payers.csv has 10 rows, providers.csv/organizations.csv each
# ~1,146 at population=10,000), and an empty one would otherwise silently produce all-null
# dimension attributes in the Big Join's gold table (see big_join.py's join_dimension_attributes).


@dataclass(frozen=True)
class SyntheaGenerationConfig:
    population_size: int
    seed: int
    reference_date: str
    output_dir: Path
    synthea_jar_path: Path = Path("tools/synthea/synthea-with-dependencies.jar")
    state: str = "Massachusetts"
    timeout_seconds: float | None = None
    years_of_history: int = 10
    # Default is 10 (a bounded choice), not Synthea's own "unlimited" sentinel of 0 -- defaulting
    # to 0 would mean any future regeneration that forgets to pass this explicitly (including at
    # the 50k-100k scale-up phase) silently reproduces the 17GB full-history blowup slice 2 exists
    # to fix. Pass years_of_history=0 explicitly to opt back into full/unbounded history.
    # v2 slice 5 (notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md, Interfaces section 8). All
    # default to "off", which reproduces the pre-slice-5 argv exactly. Measured 2026-10-12 with the project's jar: output
    # CONTENT is deterministic only with -s AND -cs AND -e fixed AND the JVM time zone pinned (row order varies with threads).
    clinician_seed: int | None = None  # Synthea -cs; without it providers/encounters differ between same-seed runs
    end_date: str | None = None  # Synthea -e (YYYYMMDD, > reference_date); without it the simulation ends at the wall clock
    jvm_timezone: str | None = None  # -Duser.timezone; Synthea's output depends on the JVM zone
    included_files: tuple[str, ...] | None = None  # --exporter.csv.included_files; must cover EXPECTED_CSV_TABLES
    java_max_heap: str | None = None  # -Xmx<value> (e.g. "96g"), plus -XX:+ExitOnOutOfMemoryError
    require_alive_population: bool = False  # refuse output with fewer alive patients than population_size
    provenance: Mapping[str, str] | None = None  # written verbatim into generation_summary.json

    def __post_init__(self) -> None:
        if self.clinician_seed is not None and (type(self.clinician_seed) is not int or self.clinician_seed < 0):
            raise ValueError(f"clinician_seed must be an int >= 0; got {self.clinician_seed!r}")
        if self.end_date is not None and _yyyymmdd(self.end_date, "end_date") <= _yyyymmdd(self.reference_date, "reference_date"):
            raise ValueError(f"end_date ({self.end_date}) must be after reference_date ({self.reference_date})")
        if self.jvm_timezone is not None and not _TIMEZONE_RE.fullmatch(self.jvm_timezone):
            raise ValueError(f"jvm_timezone must look like 'UTC' or 'Area/City'; got {self.jvm_timezone!r}")
        if self.included_files is not None:
            files = self.included_files
            if len(set(files)) != len(files) or not all(isinstance(f, str) and f.endswith(".csv") for f in files):
                raise ValueError(f"included_files must be distinct '*.csv' names; got {files!r}")
            missing = [t for t in EXPECTED_CSV_TABLES if t not in files]
            if missing:
                raise ValueError(f"included_files must include every expected table; missing {missing}")
        if self.java_max_heap is not None and not _HEAP_RE.fullmatch(self.java_max_heap):
            raise ValueError(f"java_max_heap must look like '96g' or '4096m'; got {self.java_max_heap!r}")
        if self.provenance is not None:
            _check_provenance(self.provenance)


_TIMEZONE_RE = re.compile(r"[A-Za-z_]+(/[A-Za-z_]+)*")
_HEAP_RE = re.compile(r"[1-9][0-9]*[mg]")


def _yyyymmdd(value: object, name: str) -> date:
    # stdlib-only twin of gold_metadata.parse_yyyymmdd: this module must not import the rest of the package (it runs on the
    # generation VM's system Python from the Spark zip, and tests/pipeline/test_generation.py checks it is stdlib-only)
    if not (isinstance(value, str) and value.isascii() and value.isdigit() and len(value) == 8):
        raise ValueError(f"{name} must be a YYYYMMDD string; got {value!r}")
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def _check_provenance(provenance: Mapping[str, str]) -> None:
    if not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in provenance.items()):
        raise ValueError(f"provenance must map non-empty strings to non-empty strings; got {dict(provenance)!r}")


def parse_provenance_args(values: list[str] | None) -> dict[str, str]:
    """The repeatable --provenance KEY=VALUE values of the generation and Big Join CLIs -> a dict. ValueError on a missing '=',
    an empty key or value, or a key given twice. None (flag never given) -> {}."""
    result: dict[str, str] = {}
    for item in values or []:
        key, sep, value = item.partition("=")
        if not sep or not key or not value:
            raise ValueError(f"--provenance expects KEY=VALUE with both non-empty; got {item!r}")
        if key in result:
            raise ValueError(f"--provenance key {key!r} given twice")
        result[key] = value
    return result


class SyntheaGenerationError(RuntimeError):
    """Raised by run_synthea_generation when the Synthea subprocess exits nonzero or times out."""

    def __init__(self, returncode: int | None, stderr_tail: str) -> None:
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        reason = "timed out" if returncode is None else f"exited {returncode}"
        super().__init__(f"Synthea {reason}. Last lines of stderr:\n{stderr_tail}")


class SyntheaValidationError(RuntimeError):
    """Raised by run_synthea_generation when Synthea exits 0 but its output fails validation."""

    def __init__(self, validation_result: ValidationResult, *, message: str | None = None) -> None:
        self.validation_result = validation_result
        super().__init__(
            message
            if message is not None
            else f"Synthea exited 0 but output failed validation: missing={validation_result.missing_tables}"
        )


def build_synthea_command(config: SyntheaGenerationConfig) -> list[str]:
    """Pure function: config -> subprocess argv. No I/O, no subprocess call."""
    jvm_options = []
    if config.jvm_timezone is not None:
        jvm_options.append(f"-Duser.timezone={config.jvm_timezone}")
    if config.java_max_heap is not None:
        # a worker-thread OutOfMemoryError then ends the JVM non-zero instead of leaving a silently short population
        jvm_options += [f"-Xmx{config.java_max_heap}", "-XX:+ExitOnOutOfMemoryError"]
    clinician = ["-cs", str(config.clinician_seed)] if config.clinician_seed is not None else []
    end = ["-e", config.end_date] if config.end_date is not None else []
    included = (
        ["--exporter.csv.included_files", ",".join(config.included_files)] if config.included_files is not None else []
    )
    return [
        "java",
        *jvm_options,
        "-jar",
        str(config.synthea_jar_path),
        "-p",
        str(config.population_size),
        "-s",
        str(config.seed),
        *clinician,
        "-r",
        config.reference_date,
        *end,
        "--exporter.baseDirectory",
        str(config.output_dir),
        "--exporter.csv.export",
        "true",
        "--exporter.fhir.export",
        "false",
        "--exporter.hospital.fhir.export",
        "false",
        "--exporter.practitioner.fhir.export",
        "false",
        "--exporter.years_of_history",
        str(config.years_of_history),
        *included,
        config.state,
    ]
    # generate.database_type is deliberately NOT included -- confirmed dead/deprecated in current
    # Synthea per its own Common-Configuration wiki.


def _count_data_rows(csv_path: Path) -> int:
    with csv_path.open("r", encoding="utf-8", errors="replace") as f:
        return max(sum(1 for _ in f) - 1, 0)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    missing_tables: list[str] = field(default_factory=list)
    empty_tables: list[str] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)


def validate_generation_output(
    output_dir: Path,
    expected_tables: tuple[str, ...] = EXPECTED_CSV_TABLES,
    required_nonempty: tuple[str, ...] = REQUIRED_NONEMPTY_TABLES,
) -> ValidationResult:
    """Filesystem-read-only check of output_dir/csv/<table> for each expected table."""
    csv_dir = output_dir / "csv"
    missing_tables: list[str] = []
    empty_tables: list[str] = []
    row_counts: dict[str, int] = {}

    for table in expected_tables:
        table_path = csv_dir / table
        if not table_path.is_file():
            missing_tables.append(table)
            continue
        count = _count_data_rows(table_path)
        row_counts[table] = count
        if count == 0:
            if table in required_nonempty:
                missing_tables.append(table)
            else:
                empty_tables.append(table)

    ok = len(missing_tables) == 0
    return ValidationResult(
        ok=ok, missing_tables=missing_tables, empty_tables=empty_tables, row_counts=row_counts
    )


@dataclass(frozen=True)
class GenerationResult:
    output_dir: Path
    duration_seconds: float
    row_counts: dict[str, int]
    warnings: list[str]


def count_alive_patients(patients_csv: Path) -> int:
    """Rows of patients.csv with an empty DEATHDATE. Synthea's -p counts alive patients (measured: -p 1000 wrote 1,168 rows, 1,000
    of them alive)."""
    with patients_csv.open("r", encoding="utf-8", newline="") as f:
        return sum(1 for row in csv.DictReader(f) if not row.get("DEATHDATE"))


def _peak_child_rss_bytes() -> int:
    """The largest peak resident set size among this process's finished children (the java process: it is the only one).
    ru_maxrss is KiB on Linux and bytes on macOS."""
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return int(peak) if sys.platform == "darwin" else int(peak) * 1024


def run_synthea_generation(config: SyntheaGenerationConfig) -> GenerationResult:
    if config.output_dir.exists():
        if not config.output_dir.is_dir():
            raise FileExistsError(f"output_dir exists and is not a directory: {config.output_dir}")
        if any(config.output_dir.iterdir()):
            raise FileExistsError(f"output_dir already exists and is non-empty: {config.output_dir}")

    start = time.monotonic()
    try:
        result = subprocess.run(
            build_synthea_command(config),
            capture_output=True,
            text=True,
            check=False,
            timeout=config.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        stderr_tail = "\n".join((stderr or "").splitlines()[-20:])
        raise SyntheaGenerationError(returncode=None, stderr_tail=stderr_tail) from exc
    duration_seconds = time.monotonic() - start

    if result.returncode != 0:
        stderr_tail = "\n".join((result.stderr or "").splitlines()[-20:])
        raise SyntheaGenerationError(returncode=result.returncode, stderr_tail=stderr_tail)

    peak_child_rss_bytes = _peak_child_rss_bytes()

    validation_result = validate_generation_output(config.output_dir)
    if not validation_result.ok:
        raise SyntheaValidationError(validation_result)

    csv_dir = config.output_dir / "csv"
    n_alive_patients = None
    if config.require_alive_population:
        n_alive_patients = count_alive_patients(csv_dir / "patients.csv")
        if n_alive_patients < config.population_size:
            raise SyntheaValidationError(
                validation_result,
                message=(
                    f"Synthea exited 0 but produced {n_alive_patients} alive patients, expected {config.population_size}"
                ),
            )
    row_counts = {p.name: _count_data_rows(p) for p in sorted(csv_dir.glob("*.csv"))}

    summary = {
        "population_size": config.population_size,
        "seed": config.seed,
        "reference_date": config.reference_date,
        "duration_seconds": duration_seconds,
        "row_counts": row_counts,
        "warnings": list(validation_result.empty_tables),
        # v2 slice 5: always written (null when unset), so every summary has one shape
        "clinician_seed": config.clinician_seed,
        "end_date": config.end_date,
        "jvm_timezone": config.jvm_timezone,
        "included_files": list(config.included_files) if config.included_files is not None else None,
        "years_of_history": config.years_of_history,
        "state": config.state,
        "java_max_heap": config.java_max_heap,
        "n_alive_patients": n_alive_patients,
        "peak_child_rss_bytes": peak_child_rss_bytes,
        "provenance": dict(config.provenance) if config.provenance is not None else None,
    }
    (config.output_dir / "generation_summary.json").write_text(json.dumps(summary, indent=2))

    return GenerationResult(
        output_dir=config.output_dir,
        duration_seconds=duration_seconds,
        row_counts=row_counts,
        warnings=list(validation_result.empty_tables),
    )
