# DESIGN DOC — Local Synthea Patient Generation (Slice 1 of 4)

**Date:** 2026-09-16 (revised after round 1, round 2, and round 3 goldfish design checks — this is revision 3, the skill's cap)
**Workflow:** `/eg-new-feature` (elephant/goldfish)
**Parent architecture:** `notes/prds/big-join-architecture-lock-in-2026-09-15.md` and CLAUDE.md's Architecture section
**Context:** the "local proving-phase pipeline" (generate Synthea patients locally → PySpark Big Join → calibrated LogisticRegression + XGBoost under local MLflow → Typer CLI Top-N triage scoring) was handed off as one bundled request. Split into four independently-designed/implemented slices at the user's request during scope confirmation. This doc covers slice 1 only.

**Revision note:** round 1 (16 critic gaps + 14 readiness questions), round 2 (10 critic gaps + 5 readiness questions), and round 3 (6 critic gaps + 10 readiness questions) all returned non-ready. Round 3's critic pass confirmed all of round 2's Synthea-factual fixes hold up against direct source verification — the remaining round-3 gaps are internal contract inconsistencies (not wrong external facts) plus one repeat instance of the same bug class (a third FHIR export toggle missed). See the revision log at the bottom for the full gap-by-gap resolution. Per the skill's 3-revision cap, if round 4 (reviewing this version) still doesn't converge, this stops for a user decision rather than revising again.

## Why

Kicks off implementation of the locked architecture in `notes/prds/big-join-architecture-lock-in-2026-09-15.md` — specifically the first stage of the "local proving phase" (generate 10-20k Synthea patients locally, no GCP dependency). Split out as its own slice per the user's explicit request to tackle the bundled 4-stage pipeline one piece at a time. This slice also discharges the architecture PRD's explicit sequencing requirement: the `.python-version` 3.14→3.12 downgrade "should be the very first change made, before any dependency is installed or any PySpark code is written" (PRD, Implementation hints), and this is the first code-touching pass since that PRD landed. It additionally produces the empirical answer to a named PRD risk: "No authoritative Synthea generation-time benchmark exists for 10-20k patients."

## Scope

**In:**
- `.python-version`: 3.14 → 3.12, **and** `.venv/` deleted and recreated under the 3.12 interpreter (a stale 3.14 `.venv/` left in place would silently defeat the whole point of this change).
- `pyproject.toml` (new, minimal — exact contents, since round 3 found the prose description alone left the `[build-system]`/src-discovery config ambiguous enough for two implementers to diverge):
  ```toml
  [build-system]
  requires = ["setuptools>=68"]
  build-backend = "setuptools.build_meta"

  [project]
  name = "readmission-risk"
  version = "0.1.0"
  requires-python = ">=3.12,<3.13"
  dependencies = []

  [tool.setuptools.packages.find]
  where = ["src"]

  [tool.pytest.ini_options]
  pythonpath = ["src"]
  ```
  Installed editable via `pip install -e .` — as its **own, separate pip invocation**, run without `--require-hashes` (a local editable path install structurally cannot carry a hash, so it can never be combined with a `--require-hashes` install in the same command).
- `requirements.in` (runtime — genuinely empty for this slice; everything used is stdlib) / `requirements-dev.in` (`pytest`, `ruff`, `pip-tools`), compiled to `requirements.txt` / `requirements-dev.txt` via `pip-compile --generate-hashes`, installed via a **second, separate** invocation: `pip install --require-hashes -r requirements-dev.txt`. Bootstrap exception: `pip-tools` itself must be installed unpinned first (`pip install pip-tools`) before it can compile the hash-pinned lockfiles — a one-time, documented exception to the hash-pinning rule. No `pyspark`/`mlflow`/`xgboost`/etc. yet — those wait for the slices that use them.
- A pinned, reproducible way to obtain Synthea itself: download `synthea-with-dependencies.jar` from GitHub release tag **`v4.0.0`** (confirmed real, versioned, has the jar asset, requires JDK 17+ matching this machine's installed OpenJDK 17 — not GitHub's `master-branch-latest`, which is a rolling/floating tag rebuilt from `master` on every merge and therefore not actually pinned to anything) into `tools/synthea/` (gitignored — the whole directory, no narrowing needed, see below). Verified against **`sha256:ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1`** — GitHub's own Releases API publishes a `digest` field per asset (confirmed directly via `curl https://api.github.com/repos/synthetichealth/synthea/releases/tags/v4.0.0`, and confirmed as a general GitHub platform feature, not repo-specific, by checking a second unrelated repo). **Round-4 fix, superseding round 2's approach:** round 2 introduced a trust-on-first-use scheme (compute the hash from whichever machine ran it first, commit that as the trusted value) on the stated belief that no authoritative checksum existed — round 4's critic pass found that belief was wrong: GitHub does publish one. The digest above is hardcoded directly into `setup_synthea.sh` (sourced once, now, from GitHub's API) rather than computed from a local download and trusted on faith. This is strictly stronger than TOFU (independently sourced, not self-referential) and simpler: no separate `synthea.sha256` file, no first-run-vs-subsequent-run branching, no `.gitignore` narrowing question (round 2 critic gap #1 is now moot rather than fixed), no ambiguity about the script auto-committing anything (round 3 readiness Q9 is likewise moot).
- A thin Python wrapper around invoking Synthea: population size (Synthea's **living-population target**, not the exact row count `patients.csv` will end up with — patients who die during the simulated timeline before the target is reached are generated and exported too, so `patients.csv` will have somewhat more rows than `population_size`; round 2 critic gap #4), seed, a required reference date (`-r`, pinned explicitly rather than left to "today," since Synthea documents it as a distinct, independent determinism input from `seed` — round 2 critic gap #3), output directory, and a single fixed US state (see Interfaces — no multi-state distribution in this slice). Config flags: `exporter.csv.export=true`, `exporter.fhir.export=false`, `exporter.hospital.fhir.export=false` (a second, separately-defaulting FHIR toggle round 1 missed — round 2 critic gap #7), and `exporter.years_of_history=0` (keep full patient history; the documented default of 10 years would silently truncate exactly the comorbidity/utilization history slice 2's Big Join and the eventual model need — round 2 critic gap #8). **Dropped in round 2:** `generate.database_type=none` — Synthea's own Common-Configuration wiki carries an explicit warning that this setting "no longer exists, and only functions for some legacy versions of Synthea," so the PRD's originally-cited GitHub #776 anti-pattern (DB/CCDA export left on) doesn't map to any current config lever; there is no known current equivalent to set. This slice's own timed dry run is exactly the mechanism that would catch any real slowness empirically, regardless of cause — this is noted as a finding worth reflecting back into the parent architecture PRD, not a blocker for this slice (round 2 critic gap #2 / readiness Q1).
- A timed dry run at 500-1,000 patients first (per the PRD's own risk mitigation), then the full 10,000-20,000 patient run, with wall-clock time **and per-table row counts** recorded to a durable `<output_dir>/generation_summary.json` (not just printed and lost — round 2 critic gap #6) and reported back to the user (this is the empirical finding this slice exists to produce).
- Post-generation validation: confirm the CSV tables the eventual "Big Join" will need exist under `<output_dir>/csv/`, with `patients.csv` and `encounters.csv` specifically required to be non-empty (a hard failure if either is empty — an empty `patients.csv` after a zero-exit-code run means generation silently produced nothing, which is categorically different from a rare-condition table like `careplans.csv` legitimately having zero rows at small population sizes; round 2 critic gap #5).
- Unit tests for the pure, testable parts (command construction, the non-empty-output-dir guard, validation logic) using fixtures — no live Synthea run inside CI.
- A minimal CI workflow (`ruff check .` + `pytest` on push, pinned to Python 3.12 via `actions/setup-python`), since CLAUDE.md/the PRD calls for CI "from day one" and this is day one of actual code.

**Out (explicitly deferred to later `/eg-new-feature` passes on this same architecture, or to slice 2 specifically):**
- The PySpark "Big Join" feature-engineering step itself (schemas, join logic, gold table) — slice 2.
- Model training, calibration, MLflow — slice 3.
- Typer CLI Top-N triage scoring script — slice 4.
- Dataproc Serverless / GCS / BigQuery — that's the architecture's separate "scale-up phase," entirely out of "local proving phase" scope.
- Running the full generation (or even the dry run) inside CI — it needs Java + a real network download + real wall-clock time, which doesn't belong in a fast lint/unit-test CI gate. Verified manually/locally instead.
- Finalizing the exact, complete list of Synthea CSV tables/columns the eventual join consumes, and any column-level schema validation — this slice validates a reasonable core subset's *presence*, not content; slice 2's hand-declared `StructType` schemas are where column-level correctness actually gets enforced.
- Cross-run row-order determinism from Synthea's (possibly multithreaded) generation — this slice's validation never depends on row order, and Spark's own read order isn't guaranteed either; if slice 2 needs stable ordering for join tests, it resolves that there.
- Multi-state/multi-region population distribution — this slice generates from a single fixed state (see Interfaces); broadening geographic distribution is a future decision, not a silent gap.

## Surfaces touched

- `.python-version` (edit)
- `pyproject.toml` (new)
- `requirements.in`, `requirements-dev.in`, `requirements.txt`, `requirements-dev.txt` (new)
- `src/readmission_risk/__init__.py`, `src/readmission_risk/pipeline/__init__.py`, `src/readmission_risk/pipeline/generation.py` (new — placed inside the already-locked `pipeline/` submodule from CLAUDE.md's Repo hygiene section, rather than inventing a new top-level submodule; Synthea generation is the first stage that submodule will hold, with the Big Join joining it in slice 2)
- `scripts/setup_synthea.sh` (new — downloads + TOFU-checksum-verifies the jar into `tools/synthea/`, gitignored)
- `scripts/generate_synthea_data.py` (new — thin `argparse`-based script entry point invoking the wrapper for dry-run vs. full-run)
- `tests/pipeline/test_generation.py` (new — mirrors the `src/` path per CLAUDE.md's `tests/` mirrors `src/` convention)
- `.github/workflows/ci.yml` (new)
- `.gitignore` (edit — add `tools/synthea/`, whole directory; no narrowing needed now that there's no runtime-generated hash file to keep committable — round 4 fix, see Scope)

## Interfaces

```python
# src/readmission_risk/pipeline/generation.py

@dataclass(frozen=True)
class SyntheaGenerationConfig:
    population_size: int      # Synthea's living-population TARGET, not the exact patients.csv row count (deceased patients generated en route are exported too)
    seed: int
    reference_date: str       # required, format YYYYMMDD, e.g. "20260916" — Synthea's -r flag; pinned explicitly since it's a distinct determinism input from seed, not defaulted to "today"
    output_dir: Path          # exactly the caller-supplied path, no auto-timestamping — this IS Synthea's exporter.baseDirectory
    synthea_jar_path: Path = Path("tools/synthea/synthea-with-dependencies.jar")
    state: str = "Massachusetts"   # explicit, fixed, single-state population for this slice — not relying on Synthea's undocumented no-argument default
    timeout_seconds: float | None = None   # None = no timeout (default — this slice's whole point is that the real duration is UNKNOWN going in, so a guessed hardcoded cutoff could kill a legitimately-slow-but-succeeding run). The dry-run caller MAY pass a generous safety-net value (e.g. 1800) since a dry run's plausible ceiling is known; the full run is typically left unset until the dry run informs a sane value.

class SyntheaGenerationError(RuntimeError):
    # Raised by run_synthea_generation when the Synthea subprocess exits nonzero OR times out.
    # NOT a wrapper around subprocess.CalledProcessError (subprocess.run is called with check=False,
    # so CalledProcessError is never actually raised by subprocess itself) — this is a plain custom
    # exception constructed manually from the completed (or timed-out) process's own returncode/stderr.
    def __init__(self, returncode: int | None, stderr_tail: str) -> None:
        # returncode is None specifically for the timeout case (subprocess.TimeoutExpired has no
        # returncode); always an int for a real nonzero exit.
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        reason = "timed out" if returncode is None else f"exited {returncode}"
        super().__init__(f"Synthea {reason}. Last lines of stderr:\n{stderr_tail}")

class SyntheaValidationError(RuntimeError):
    # Raised by run_synthea_generation when Synthea exits 0 but validate_generation_output() finds
    # ok=False (a required table is missing or empty) — a DIFFERENT failure class from
    # SyntheaGenerationError (that one is "the subprocess itself failed"; this one is "the subprocess
    # reported success but its output doesn't meet this slice's own bar"). Two separate top-level
    # classes, both subclassing RuntimeError directly — NOT nested (round 4 fix: the previous
    # docstring-based version of this block left an unclosed """ that made the nesting ambiguous).
    def __init__(self, validation_result: "ValidationResult") -> None:
        self.validation_result = validation_result
        super().__init__(
            f"Synthea exited 0 but output failed validation: missing={validation_result.missing_tables}"
        )

def build_synthea_command(config: SyntheaGenerationConfig) -> list[str]:
    """
    Pure function: config -> subprocess argv. No I/O, no subprocess call — unit-testable in isolation.

    Returns exactly:
    [
        "java", "-jar", str(config.synthea_jar_path),
        "-p", str(config.population_size),
        "-s", str(config.seed),
        "-r", config.reference_date,
        "--exporter.baseDirectory", str(config.output_dir),
        "--exporter.csv.export", "true",
        "--exporter.fhir.export", "false",
        "--exporter.hospital.fhir.export", "false",
        "--exporter.practitioner.fhir.export", "false",
        "--exporter.years_of_history", "0",
        config.state,
    ]

    (generate.database_type is deliberately NOT included — confirmed dead/deprecated in current
    Synthea per its own Common-Configuration wiki; see Scope for the round-2 finding. Round 3 added
    exporter.practitioner.fhir.export=false — a THIRD independently-defaulting-true FHIR toggle that
    round 2 missed alongside exporter.hospital.fhir.export; synthea.properties in the v4.0.0 tag
    confirms all three are separate, true-by-default keys.)
    """

@dataclass(frozen=True)
class GenerationResult:
    output_dir: Path
    duration_seconds: float
    row_counts: dict[str, int]   # COMPLETE accounting: one entry per CSV file actually present under output_dir/csv/*.csv (Synthea exports ~18 tables; this is not limited to EXPECTED_CSV_TABLES — round 3 fix, see Scope) — this is the durable empirical benchmark artifact, so it undercounts nothing.
    warnings: list[str]           # non-required EXPECTED_CSV_TABLES entries that came back empty (mirrors ValidationResult.empty_tables) — lets the calling script print a warning without a second filesystem scan.

def run_synthea_generation(config: SyntheaGenerationConfig) -> GenerationResult:
    """
    Impure. Sequence:
    1. If config.output_dir exists and is non-empty, raise FileExistsError before touching the subprocess
       (this check is unit-testable on its own — it never needs to invoke Java).
    2. subprocess.run(build_synthea_command(config), capture_output=True, text=True, check=False,
       timeout=config.timeout_seconds), timed via time.monotonic().
       capture_output=True + text=True is what makes result.stderr a str at all — without it there is
       nothing to build a "stderr tail" from.
       - On subprocess.TimeoutExpired: e.stderr may be bytes or None depending on when the timeout
         fired; decode/coerce to str (empty string if None) before slicing, then raise
         SyntheaGenerationError(returncode=None, stderr_tail=<sliced>).
    3. If result.returncode != 0: "stderr tail" means the LAST 20 LINES of result.stderr (str.splitlines()[-20:],
       joined back with "\n"; the whole string if it has fewer than 20 lines) — raise
       SyntheaGenerationError(result.returncode, stderr_tail).
    4. A missing `java` executable raises FileNotFoundError from subprocess.run itself — this propagates
       unwrapped (not caught/translated into SyntheaGenerationError), since it's a distinct environment
       problem, not a Synthea-level failure. (No runtime check that `java` satisfies Synthea's JDK 17+
       requirement — deliberately unhandled in this slice: a too-old Java would itself surface as a
       Synthea nonzero exit with an explanatory stderr message, which SyntheaGenerationError already
       carries. Same "no explicit guard" posture as the existing disk-space note in Failure modes.)
    5. On process success (returncode == 0): call validate_generation_output(config.output_dir).
       - If the result's ok is False: raise SyntheaValidationError(validation_result) — Synthea
         exiting 0 does NOT by itself mean this slice's success criteria are met (this is the
         round-3 fix for the round-1/round-2 doc's silent contradiction between "run_synthea_generation
         always returns a GenerationResult" and "missing/empty required tables are a hard failure").
       - If ok is True: separately glob output_dir/"csv"/*.csv for the COMPLETE row-count accounting
         (every file present, not just EXPECTED_CSV_TABLES — see GenerationResult docstring), write
         <output_dir>/generation_summary.json (population_size, seed, reference_date, duration_seconds,
         row_counts, warnings), and return
         GenerationResult(output_dir=config.output_dir, duration_seconds=..., row_counts=..., warnings=validation_result.empty_tables).
    """

EXPECTED_CSV_TABLES: tuple[str, ...] = (
    "patients.csv", "encounters.csv", "conditions.csv",
    "medications.csv", "procedures.csv", "careplans.csv", "observations.csv",
)
# Deliberately NOT Synthea's full ~18-table CSV output (also includes payers.csv, claims.csv,
# providers.csv, immunizations.csv, allergies.csv, and others) — this is the join-relevant subset
# slice 2 is expected to need, gated for pass/fail purposes; finalizing the complete list is
# explicitly out of scope (see Scope > Out). GenerationResult.row_counts (above) is NOT limited to
# this list — the empirical benchmark artifact accounts for every table Synthea actually writes,
# even though gating logic below only cares about this subset. This is the round-3 fix resolving
# both round-3 critic gap #3 (benchmark undercounting) and readiness Q2 (the two row_counts fields'
# docstrings contradicting each other) — they now have deliberately different, explicitly-stated scopes.

REQUIRED_NONEMPTY_TABLES: tuple[str, ...] = ("patients.csv", "encounters.csv")
# These two must never be empty even at the 500-1,000-patient dry run — a zero-row patients.csv after
# a 0-exit-code run means generation silently produced nothing, categorically different from a
# rare-condition table (e.g. careplans.csv) legitimately having zero rows at small population sizes.

@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    missing_tables: list[str]
    empty_tables: list[str]     # present but zero data rows, AND not in REQUIRED_NONEMPTY_TABLES — warning only
    row_counts: dict[str, int]  # ONLY for tables in EXPECTED_CSV_TABLES that are present (gating-scoped, not the complete accounting — see EXPECTED_CSV_TABLES comment above)

def validate_generation_output(
    output_dir: Path,
    expected_tables: tuple[str, ...] = EXPECTED_CSV_TABLES,
    required_nonempty: tuple[str, ...] = REQUIRED_NONEMPTY_TABLES,
) -> ValidationResult:
    """
    Pure-ish (filesystem read only, no Spark/pandas dependency). Scans output_dir / "csv" / <table>
    for each name in expected_tables (Synthea's confirmed CSV export subdirectory — NOT output_dir
    itself). Synthea's CSV exporter escapes embedded commas/newlines within fields (confirmed against
    its own CSVExporter source), so naive newline-based row counting is safe — no CSV-parsing library
    needed here.
    - Missing file -> added to missing_tables, ok=False.
    - Present: count data rows exactly (sum(1 for _ in f) - 1, floored at 0; reading the full file
      is an acceptable cost at the 10-20k-patient scale this slice targets).
    - Zero data rows AND table is in required_nonempty -> added to missing_tables, ok=False (treated
      as equivalent to a missing file — a silently-empty patients.csv is a hard failure).
    - Zero data rows AND table is NOT in required_nonempty -> added to empty_tables, ok unaffected
      (legitimate at small population sizes for rare-condition tables).
    - Column-name/header-content validation is explicitly out of scope (see Scope > Out) — only
      presence and row count are checked.
    """
```

## UX flow (pipeline, not UI)

**`scripts/setup_synthea.sh` (bash, no Python):**
```sh
JAR_URL="https://github.com/synthetichealth/synthea/releases/download/v4.0.0/synthea-with-dependencies.jar"
JAR_PATH="tools/synthea/synthea-with-dependencies.jar"
# Sourced directly from GitHub's Releases API digest field for this exact asset
# (curl https://api.github.com/repos/synthetichealth/synthea/releases/tags/v4.0.0) — an authoritative,
# independently-computed checksum, not a value trusted from whichever machine runs this script first.
EXPECTED_SHA256="ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1"

mkdir -p tools/synthea

if [ ! -f "$JAR_PATH" ]; then
    curl -fL "$JAR_URL" -o "$JAR_PATH"   # -f: fail (nonzero exit) on HTTP error; -L: follow redirects
fi

COMPUTED_HASH=$(shasum -a 256 "$JAR_PATH" | cut -d' ' -f1)

if [ "$COMPUTED_HASH" != "$EXPECTED_SHA256" ]; then
    rm -f "$JAR_PATH"   # don't leave a corrupt/substituted jar on disk after a failed verification
    echo "ERROR: jar hash mismatch. Expected $EXPECTED_SHA256, got $COMPUTED_HASH" >&2
    exit 1
fi
```
Re-running this script when the jar is already present locally and its hash matches skips the download entirely (the `[ ! -f "$JAR_PATH" ]` check) and just re-verifies. No separate hash file, no first-run-vs-later-run branching, nothing for this script to ever commit to git — `EXPECTED_SHA256` is a source-code constant.

**`scripts/generate_synthea_data.py`** uses stdlib `argparse` with these flags:
- `--population INT` (required)
- `--seed INT` (required)
- `--output PATH` (required)
- `--reference-date YYYYMMDD` (optional, default `"20260916"` — this design doc's own date, a fixed, documented default rather than "today")
- `--jar-path PATH` (optional, default `tools/synthea/synthea-with-dependencies.jar`, matching `SyntheaGenerationConfig`'s field default)
- `--state STR` (optional, default `"Massachusetts"`, matching the config default)
- `--timeout-seconds FLOAT` (optional, default `None`/unset, matching the config default)

Exit codes: `0` on a clean pass, including a pass with non-required-table warnings (warnings are printed to stderr but do not fail the run); `1` if `run_synthea_generation` raises `FileExistsError`, `SyntheaGenerationError`, or `SyntheaValidationError` (script catches all three, prints the error, exits 1) — there's no fourth outcome. The script itself is a thin `argparse` wrapper around `run_synthea_generation` with no independent logic of its own, and is deliberately NOT unit-tested (all real logic lives in `generation.py`, which is fully unit tested per Verification criteria) — it's exercised only by the manual/integration run.

1. One-time setup: `scripts/setup_synthea.sh` (above) downloads the `v4.0.0` Synthea release jar into `tools/synthea/`, verifies it against GitHub's hardcoded published digest.
2. Dry run: `python scripts/generate_synthea_data.py --population 1000 --seed 42 --output data/raw/dry_run/` (jar-path/state/reference-date/timeout all take their defaults) → records wall-clock time and the complete per-table row-count accounting, written to `data/raw/dry_run/generation_summary.json`.
3. Human checkpoint: user/agent reviews the dry-run timing (and the fact that `patients.csv`'s row count will be slightly above 1000, per the living-vs-exported-population distinction in Scope) before committing to the full run — this is the PRD's named risk mitigation, extrapolate from the dry run rather than guessing.
4. Full run: same script, `--population <10000-20000> --seed <fixed> --output data/raw/full_run/`.
5. Validation happens automatically inside `run_synthea_generation` (per Interfaces) — a hard failure raises `SyntheaValidationError` (nonzero exit from the script), a pass-with-warnings prints the warning list but still exits 0; `generation_summary.json` is the durable record of the run either way it succeeds.

## Data handling

- No train/test split happens in this slice (pure generation) — the chronological, patient-ID-grouped split constraint (CLAUDE.md Architecture) applies to future model-training slices, not here. Noting it's not violated, not that it's relevant yet.
- Reproducibility: `seed` AND `reference_date` are both explicit, required config fields (neither left to a Synthea default) — Synthea documents `-r` as a distinct, independent determinism input from `-s`, and ages/history in generated records are computed relative to it, so pinning only the seed would leave a silent second source of run-to-run variation. Dry run and full run are treated as **independent generations** for timing-extrapolation purposes only, not as nested subsets of each other (Synthea's population-count parameter is not documented as guaranteeing that patient N of a 1,000-run equals patient N of a 20,000-run with the same seed — this design doesn't assume that guarantee, and doesn't need it to). Cross-run row-order determinism is likewise not assumed or relied upon (see Scope > Out).
- `population_size` is Synthea's living-population *target*, not `patients.csv`'s exact row count — patients who die during the simulated timeline before the target is reached are generated and exported too. This is stated explicitly here (and in the UX flow's human checkpoint) so nobody mistakes a `patients.csv` row count slightly above `population_size` for a bug.
- No PII: Synthea output is fully synthetic by construction; the project's separate README data-realism caveat (architecture, already locked) covers this at the doc level, not per-slice.
- Supply-chain hygiene: the Synthea jar is a binary pulled from the network — pinned to release tag `v4.0.0` (immutable once published) plus verification against GitHub's own authoritative published SHA-256 digest for that exact asset (hardcoded in `setup_synthea.sh`), not a bare unpinned download, not GitHub's floating "latest" tag, and not a weaker trust-on-first-use scheme (round 4 fix — an earlier revision assumed no authoritative checksum existed; it does).

## Failure modes

- Java missing entirely → `FileNotFoundError` propagates unwrapped from `subprocess.run` (see Interfaces) — this slice does not attempt to auto-install or pre-check Java.
- Java present but the wrong major version → no explicit runtime check (deliberate, same posture as the disk-space note below) — a JDK <17 would itself cause Synthea to exit nonzero with an explanatory message, surfaced via `SyntheaGenerationError`'s stderr tail; this machine already has Java 17 confirmed.
- Java present but Synthea exits nonzero (bad config, runtime error, etc.) → `SyntheaGenerationError` raised with returncode + stderr tail.
- Synthea process exceeds `config.timeout_seconds` (if set) → `subprocess.TimeoutExpired` caught and re-raised as `SyntheaGenerationError(returncode=None, stderr_tail=...)` — distinguishable from a real nonzero exit by `returncode is None`.
- Synthea exits 0 but its output fails validation (a `REQUIRED_NONEMPTY_TABLES` entry is missing or empty) → `SyntheaValidationError(validation_result)` raised — a distinct exception class from `SyntheaGenerationError`, since this is "the subprocess reported success but didn't meet this slice's bar," not "the subprocess itself failed" (round 3 fix — round 1/2 left `run_synthea_generation`'s own pseudocode silently contradicting this same claim made in this Failure modes section).
- Jar download fails or SHA-256 mismatch against GitHub's published digest (hardcoded in the script) → `setup_synthea.sh` exits non-zero with a clear message and deletes the mismatched/corrupt jar before exiting (doesn't leave a suspect binary on disk for a later run to accidentally reuse).
- `output_dir` already exists and is non-empty → `run_synthea_generation` raises `FileExistsError` before invoking Java at all (avoids silently mixing an interrupted prior run's partial CSVs with a fresh one); no `--force` flag in this slice (YAGNI — add later if it becomes a real workflow friction).
- Present-but-empty `patients.csv` or `encounters.csv` (the `REQUIRED_NONEMPTY_TABLES`) → hard fail via `SyntheaValidationError` (see above) — a silently-empty required table after a zero-exit-code run is treated as equivalent to a missing file, not a warning.
- Present-but-empty any other CSV table → warning only (`GenerationResult.warnings`, mirroring `ValidationResult.empty_tables`), not a hard failure, since some rare-condition Synthea modules can legitimately produce zero rows at small population sizes (especially the 500-1,000 dry run).
- Disk space: no explicit guard; validation would surface a suspiciously-small/missing file as a symptom if it occurred, but this slice doesn't pre-flight disk space.

## Verification criteria

- `tests/pipeline/test_generation.py::test_build_synthea_command_*` — given a `SyntheaGenerationConfig`, assert the **exact** argv list shown in Interfaces: `-p`/`-s`/`-r` values, `--exporter.csv.export true`, `--exporter.fhir.export false`, `--exporter.hospital.fhir.export false`, `--exporter.practitioner.fhir.export false`, `--exporter.years_of_history 0`, and the trailing `state` positional arg — explicitly including a check that `--generate.database_type` does NOT appear anywhere in the output (this is the test that would have caught the round-1 CSV-export bug and every round-2/3 dead-flag or missed-flag regression).
- `tests/pipeline/test_generation.py::test_run_synthea_generation_rejects_nonempty_output_dir` — pre-create a non-empty `tmp_path`, assert `FileExistsError` is raised without needing to mock or invoke `subprocess` at all (the guard runs before the subprocess call).
- `tests/pipeline/test_generation.py::test_synthea_generation_error_attributes` / `test_synthea_validation_error_attributes` — construct each exception directly (`SyntheaGenerationError(returncode=1, stderr_tail="boom")`, `SyntheaValidationError(some_validation_result)`), assert their public attributes are readable and the message string is informative.
- `tests/pipeline/test_generation.py::test_run_synthea_generation_*` (new in round 3 — closes the "no coverage of the function's own orchestration logic" gap) — using `unittest.mock.patch("subprocess.run")` to avoid invoking real Java:
  - nonzero `returncode` → asserts `SyntheaGenerationError` is raised with the correctly-sliced last-20-lines stderr tail (test with a mocked stderr of >20 lines AND a separate case of <20 lines, to actually exercise the slicing boundary).
  - `subprocess.TimeoutExpired` side effect → asserts `SyntheaGenerationError(returncode=None, ...)` is raised.
  - success (`returncode=0`) with a fixture `csv/` directory pre-populated under a `tmp_path` output_dir → asserts `generation_summary.json` is written with the expected keys/values, and the returned `GenerationResult.row_counts` reflects ALL csv files in the fixture, not just `EXPECTED_CSV_TABLES` (this is the test that would have caught the round-3 "benchmark undercounting" gap).
  - success but `validate_generation_output` would return `ok=False` (fixture missing `patients.csv`) → asserts `SyntheaValidationError` is raised, not a silently-returned `GenerationResult`.
- `tests/pipeline/test_generation.py::test_validate_generation_output_*` — against fixture directory trees under a `csv/` subfolder: all-present-and-nonempty → `ok=True`, `empty_tables=[]`, `row_counts` matching the fixture's actual data-row counts for the `EXPECTED_CSV_TABLES` subset; missing file → `ok=False` with correct `missing_tables`; empty non-required table (e.g. `careplans.csv`) → `ok=True` with it listed in `empty_tables`; **empty `patients.csv` → `ok=False` with it listed in `missing_tables`, not `empty_tables`**.
- `ruff check .` + `pytest` pass in the new CI workflow (Python 3.12 pinned).
- Manual/integration verification (not CI): actually run `setup_synthea.sh`, then the dry run, then the full run, on this machine (Java 17 already confirmed present); report the observed wall-clock time for both, read back `generation_summary.json` for the recorded row counts rather than eyeballing, and confirm `patients.csv`'s row count is at or slightly above the requested `population_size` (per the living-vs-exported-population distinction), not exactly equal to it.

## Out-of-scope follow-ups

- PySpark Big Join feature-engineering (slice 2, next `/eg-new-feature` pass)
- Model training / calibration / MLflow (slice 3)
- Typer CLI Top-N scoring (slice 4)
- Deciding the full canonical table/column list the join actually consumes, and column-level schema validation
- Automating the full generation run itself inside CI
- Multi-state/multi-region population distribution
- Cross-run row-order determinism guarantees

## Revision log

**Round 1 → round 2:** Round 1 Critic returned 16 gaps (`design needs revision`); Round 1 Readiness returned 14 open questions (`implementation not ready`). Both were caused primarily by the original doc asserting Synthea CLI/config/export behavior without verifying it against Synthea's actual documentation. Resolutions: (1) fixed via direct verification — `exporter.csv.export` default-false and the `<baseDirectory>/csv/` subfolder confirmed against Synthea's GitHub wiki; `v4.0.0` confirmed via the GitHub releases API; (2) fixed via concrete specification — exact argv, exception type, `argparse`, the (then-)"empty" boundary, `pyproject.toml`/`pythonpath`; (3) fixed via honest scope walk-back — TOFU checksum instead of a nonexistent authoritative one, header-presence-only validation instead of content-matching; (4) rebutted — cross-run row-order determinism, since nothing in this slice depends on it.

**Round 2 → round 3:** Round 2 Critic (re-verifying round 1's fixes directly against Synthea's wiki rather than trusting the round-1 revision log) found 10 new gaps; Round 2 Readiness found 5 open questions. Both rounds independently caught the same core issues, which is why they're merged here:
1. **`.gitignore` self-contradiction** — `tools/synthea/` as a whole-directory ignore would have silently prevented `synthea.sha256` from ever being committed, defeating the TOFU guarantee. Fixed: narrowed to `tools/synthea/*.jar`.
2. **`generate.database_type=none` is dead** — Synthea's own wiki carries an explicit deprecation warning on this exact key. Fixed: dropped from the argv entirely; noted as a finding worth reflecting back into the parent architecture PRD, since the PRD's cited anti-pattern no longer maps to any current config lever.
3. **`reference_date` (`-r`) unaddressed** — a distinct, independent determinism input from `seed` per Synthea's own docs. Fixed: added as a required config field.
4. **`population_size` ≠ `patients.csv` row count** — deceased patients generated en route are exported too. Fixed: documented explicitly in Scope, Data handling, and the UX flow's human checkpoint.
5. **Uniform severity for any empty table, including `patients.csv`** — a silently-empty `patients.csv` after a 0-exit-code run is a hard failure, not the same as an empty rare-condition table. Fixed: added `REQUIRED_NONEMPTY_TABLES`, folded into `missing_tables`/`ok=False` when empty.
6. **No row counts captured** — the empirical finding this slice exists to produce was only ever "eyeballed," not recorded. Fixed: `ValidationResult`/`GenerationResult` now carry `row_counts`, written to a durable `<output_dir>/generation_summary.json`.
7. **`exporter.hospital.fhir.export` (separate `true`-default flag) left enabled** — Fixed: added to the argv alongside `exporter.fhir.export=false`.
8. **`exporter.years_of_history` defaults to 10, truncating history slice 2 will want** — Fixed: added `exporter.years_of_history=0` (confirmed via Synthea's wiki: "keep all history in the patient record").
9. **`pip install --require-hashes` + `pip install -e .` can't combine in one invocation** — Fixed: Scope now explicitly states these as two separate pip commands.
10. **`pyproject.toml`'s `[project]` table underspecified** — Fixed: `requires-python`, `version`, `dependencies = []` now stated.
Readiness's overlapping questions (exact `SyntheaGenerationError` constructor/attributes, `subprocess` capture mode and "stderr tail" definition, and `generate_synthea_data.py`'s CLI flags for jar-path/state) are resolved by the same edits above (see Interfaces and UX flow).

**Round 3 → round 4 (this revision, the 3rd — the skill's cap):** Round 3 Critic confirmed all round-2 Synthea-factual fixes hold up against direct source verification (a meaningful signal — the class of error that sank rounds 1-2 is gone), but found 6 new gaps; Round 3 Readiness found 10 open questions, mostly internal-contract inconsistencies rather than wrong external facts:
1. **A third FHIR toggle (`exporter.practitioner.fhir.export`, also true-by-default) still left enabled** — the exact bug class round 2 thought it had fixed, repeated. Fixed: added to the argv.
2. **`run_synthea_generation`'s own orchestration logic had zero test coverage** (stderr-tail slicing, JSON-summary writing, the raise-on-nonzero-exit path) — only exercised by the untested manual run. Fixed: added mocked-`subprocess.run` tests covering all of these paths, including the slicing boundary.
3. **`EXPECTED_CSV_TABLES` (7 tables) silently understated the benchmark** this slice exists to produce, versus Synthea's actual ~18-table CSV output. Fixed: split the contract — `GenerationResult.row_counts` is now a complete accounting of every CSV file actually present (full glob), while `ValidationResult.row_counts` stays scoped to the 7 gating-relevant tables; this also resolves round-3 readiness Q2 (the two fields' docstrings previously contradicted each other).
4. **No subprocess timeout** — a hung Java process was indistinguishable from "generation is just slow," which matters given this slice's whole point is establishing a trustworthy timing number. Fixed: added optional `timeout_seconds` (default `None`, since a premature hardcoded cutoff could kill a legitimately-slow-but-succeeding run — exactly the number this slice doesn't know yet).
5. **`pyproject.toml`'s `[build-system]`/src-discovery config was prose, not exact** — two implementers could reasonably diverge and one could fail `pip install -e .`. Fixed: exact TOML given.
6. **Doc header still said "round 1" on what was already round 3** — fixed (cosmetic).
7. **(Readiness) `run_synthea_generation`'s pseudocode never branched on `validate_generation_output`'s `ok` field**, directly contradicting this same doc's Failure modes section, which promised a hard fail on a missing/empty required table. Fixed: added `SyntheaValidationError`, raised specifically on this path, distinct from `SyntheaGenerationError` (subprocess-level failure) since these are different failure classes.
8. **(Readiness) No pseudocode for `setup_synthea.sh` at all** (download tool/URL, re-run/skip-download semantics, what happens to a corrupt jar on hash mismatch) despite the rest of the doc being exact. Fixed: full bash pseudocode added to UX flow, with explicit TOFU semantics (trust established once, enforced on every subsequent run) and cleanup-on-mismatch behavior.
9. **(Readiness) "Commits the checksum to git" was ambiguous about whether the setup script auto-commits** — which would conflict with CLAUDE.md's standing no-auto-commit rule. Fixed: explicitly stated the script only writes the file; committing it is a normal manual/reviewed step like any other new file this slice creates.
10. **(Readiness) No exit-code convention for `generate_synthea_data.py`, and unclear whether it's unit-tested.** Fixed: exit codes specified (0 for pass-with-or-without-warnings, 1 for any of the three exception types); explicitly stated as deliberately not unit-tested (thin wrapper, all logic lives in and is tested via `generation.py`).

No gap was dropped or silently ignored across any round. Per the skill's protocol, if the next review round still doesn't converge, this stops for a user decision (clarify scope / drop a requirement / override and proceed / abandon) rather than attempting a 4th revision.
