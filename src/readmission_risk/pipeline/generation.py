"""Local Synthea patient generation: invoke Synthea, validate its CSV output, and
record a durable summary of each run. See notes/eg-new-feature/local-synthea-generation-2026-09-16.md
for the design this implements.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

# Synthea CLI/config facts below were verified directly against the v4.0.0 release tag's source
# and synthea.properties, not assumed.

EXPECTED_CSV_TABLES: tuple[str, ...] = (
    "patients.csv",
    "encounters.csv",
    "conditions.csv",
    "medications.csv",
    "procedures.csv",
    "careplans.csv",
    "observations.csv",
)
# Deliberately not Synthea's full ~18-table CSV output (also includes payers.csv, claims.csv,
# providers.csv, immunizations.csv, allergies.csv, and others) -- this is the join-relevant subset
# slice 2 is expected to need, gated for pass/fail purposes. GenerationResult.row_counts (below) is
# NOT limited to this list -- it accounts for every CSV file Synthea actually writes.

REQUIRED_NONEMPTY_TABLES: tuple[str, ...] = ("patients.csv", "encounters.csv")
# These two must never be empty even at the 500-1,000-patient dry run -- a zero-row patients.csv
# after a 0-exit-code run means generation silently produced nothing, categorically different from
# a rare-condition table (e.g. careplans.csv) legitimately having zero rows at small population sizes.


@dataclass(frozen=True)
class SyntheaGenerationConfig:
    population_size: int
    seed: int
    reference_date: str
    output_dir: Path
    synthea_jar_path: Path = Path("tools/synthea/synthea-with-dependencies.jar")
    state: str = "Massachusetts"
    timeout_seconds: float | None = None


class SyntheaGenerationError(RuntimeError):
    """Raised by run_synthea_generation when the Synthea subprocess exits nonzero or times out."""

    def __init__(self, returncode: int | None, stderr_tail: str) -> None:
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        reason = "timed out" if returncode is None else f"exited {returncode}"
        super().__init__(f"Synthea {reason}. Last lines of stderr:\n{stderr_tail}")


class SyntheaValidationError(RuntimeError):
    """Raised by run_synthea_generation when Synthea exits 0 but its output fails validation."""

    def __init__(self, validation_result: ValidationResult) -> None:
        self.validation_result = validation_result
        super().__init__(
            "Synthea exited 0 but output failed validation: "
            f"missing={validation_result.missing_tables}"
        )


def build_synthea_command(config: SyntheaGenerationConfig) -> list[str]:
    """Pure function: config -> subprocess argv. No I/O, no subprocess call."""
    return [
        "java",
        "-jar",
        str(config.synthea_jar_path),
        "-p",
        str(config.population_size),
        "-s",
        str(config.seed),
        "-r",
        config.reference_date,
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
        "0",
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

    validation_result = validate_generation_output(config.output_dir)
    if not validation_result.ok:
        raise SyntheaValidationError(validation_result)

    csv_dir = config.output_dir / "csv"
    row_counts = {p.name: _count_data_rows(p) for p in sorted(csv_dir.glob("*.csv"))}

    summary = {
        "population_size": config.population_size,
        "seed": config.seed,
        "reference_date": config.reference_date,
        "duration_seconds": duration_seconds,
        "row_counts": row_counts,
        "warnings": list(validation_result.empty_tables),
    }
    (config.output_dir / "generation_summary.json").write_text(json.dumps(summary, indent=2))

    return GenerationResult(
        output_dir=config.output_dir,
        duration_seconds=duration_seconds,
        row_counts=row_counts,
        warnings=list(validation_result.empty_tables),
    )
