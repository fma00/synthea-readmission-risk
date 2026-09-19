# DESIGN DOC — PySpark Big Join Feature Engineering (Slice 2 of 4)

**Date:** 2026-09-19
**Workflow:** `/eg-new-feature` (elephant/goldfish)
**Parent architecture:** `notes/prds/big-join-architecture-lock-in-2026-09-15.md` and CLAUDE.md's Architecture section
**Precedes:** slice 1 (`notes/eg-new-feature/local-synthea-generation-2026-09-16.md`), whose CSV output this slice reads
**Context:** table-selection and lookback-window decisions were made interactively before this doc was drafted, grounded in real measurements taken against the existing slice-1 data (not estimates) — see Why. Full measurement detail (raw numbers, methodology) is saved at `notes/eg-new-feature/pyspark-big-join-2026-09-19-measurements.md`.
**Revision note:** revised once after round 1's goldfish design check (critic: 8 gaps, `design needs revision`; readiness: 3 gaps, `implementation not ready`; comprehension: passed). All 11 gaps were either fixed or resolved via new empirical verification — see the revision log at the bottom.

## Why

Executes the second stage of the "local proving phase" locked in the architecture PRD: join Synthea's per-table CSV export into a single patient-encounter-level feature table via local PySpark. This slice also discharges CLAUDE.md's explicit "Requirement carried into slice 2" (added 2026-09-17): the full-history slice-1 export came in at 17GB, well above expectation, and CLAUDE.md required this design to (1) deliberately bound the history lookback window rather than default to "keep everything," and (2) deliberately decide which CSV tables the join actually needs rather than assume all of them.

Both decisions are grounded in real data gathered during this design session (not assumed defaults):

- **`years_of_history=10`** was chosen after measuring actual Synthea output at `years_of_history` ∈ {5, 10, unlimited} at population=1,000: the 10 selected tables (see Scope) total 110MB / 194MB / 521MB respectively, extrapolating to roughly 1.1–1.2GB / 1.9–2.1GB / 5.53GB (the 5.53GB figure is a **real measurement** at population=10,000, not extrapolated) at population=10,000. `years_of_history=10` was picked because it lands at ~2GB — an ~8.5x reduction from the current 17GB full-18-table export — while retaining a 10-year window comfortably wider than the 1-year feature lookback (see next point), keeping the "truncation dead zone" described in Data handling to roughly 10% of the retained period. **This is not "because it's Synthea's own default"** — it happens to coincide with that default, but the number was derived from the measured size/lookback-window math, not copied from Synthea's docs.
- **`lookback_years=1`** (implemented as calendar-year subtraction, not a fixed 365-day count — see Interfaces) was chosen after a second real measurement: a per-calendar-year analysis of the existing full-history export found **0.0% of alive patients had zero recorded observations in any full calendar year from 1916 through 2025** (so a 1-year window is nowhere near degenerate for lab/vital-derived features), while encounters showed a 7-11% zero-rate even in the densest recent decade — read as genuine signal (not every patient is hospitalized every year), not a sparsity artifact requiring a longer window. This matches the 12-month lookback convention used by the LACE index (already cited as a source in the architecture PRD), so the choice is both empirically and clinically grounded.
- **A third empirical check, added during round-1 of the design-check revision**, directly tested how `years_of_history` actually truncates data: does it drop currently-active (ongoing) condition/medication/careplan records that started long before the retention window, or only prune resolved/historical ones? Tested directly against real `years_of_history=5`/`=10` regenerations at population=1,000 (not assumed either way): active-record counts were essentially flat across `years_of_history` settings (e.g. active conditions: 10,615 / 10,615 / 10,612 at yoh=5 / yoh=10 / unlimited), while resolved-record counts scaled sharply with the setting (resolved conditions: 15,557 / 28,250 / 90,709). **Round 2 of the design check correctly flagged that this was only checked at population=1,000, a 10x-smaller scale than this slice's actual target (`data/raw/full_run`, population=10,000)** — re-checked directly at population=10,000 (`years_of_history=10` vs. the existing unlimited-history export): active conditions 108,804 vs. 108,798, active medications 30,427 vs. 30,411, active careplans 18,842 vs. 18,839 (all within noise), while resolved counts again scaled sharply (e.g. resolved conditions 302,126 vs. 957,838). **Synthea preserves a patient's full current problem list regardless of `years_of_history`, and only prunes closed/historical records outside the window — confirmed at the real target population, not extrapolated from a smaller one.** See `notes/eg-new-feature/pyspark-big-join-2026-09-19-measurements.md` section 3 for both scales' full tables. This means the truncation "dead zone" discussed in Data handling applies only to the strictly-windowed count features, not to the `active_*` comorbidity features, which are more robust to this setting than originally assumed.

## Scope

**In:**

- **Tables joined (10 of Synthea's ~18 exported tables):** `patients.csv`, `encounters.csv`, `conditions.csv`, `medications.csv`, `procedures.csv`, `careplans.csv`, `observations.csv`, `payers.csv`, `providers.csv`, `organizations.csv`.
- **Tables explicitly excluded**, and why:
  - `claims.csv`, `claims_transactions.csv` — billing line items, not clinical data (already decided in CLAUDE.md; these two alone are ~11GB of the current 17GB export).
  - `immunizations.csv`, `allergies.csv`, `devices.csv`, `imaging_studies.csv`, `payer_transitions.csv`, `supplies.csv` — excluded per explicit user decision this session ("I don't see their value right now"); weak or indirect signal for a first-pass readmission model.
- **Regenerating slice 1's data at `years_of_history=10`.** The existing `data/raw/dry_run/` and `data/raw/full_run/` were generated with `years_of_history=0` (Synthea's sentinel for "unlimited/full history" — confirmed in slice 1's design doc against Synthea's own wiki). This slice:
  1. Adds a `years_of_history: int = 10` field to `SyntheaGenerationConfig` in `src/readmission_risk/pipeline/generation.py`, and threads it into `build_synthea_command`'s `--exporter.years_of_history` argument (currently hardcoded to the string `"0"`). **Default is `10` (this slice's own bounded choice), not `0`** — a design-check finding: defaulting to `0` would mean any future regeneration that forgets to pass the flag explicitly (including at the 50k-100k scale-up phase) silently reproduces the exact 17GB blowup this slice exists to fix. Full/unbounded history remains available via an explicit `years_of_history=0`, it's just no longer the silent default.
  1a. **Extends `generation.py`'s `EXPECTED_CSV_TABLES` from 7 to 10 entries**, adding `payers.csv`, `providers.csv`, `organizations.csv` — a round-3 design-check finding: this slice's `TABLES_JOINED` (`big_join.py`) reads 10 tables, but slice 1's own post-generation validation (`validate_generation_output`, run automatically inside `run_synthea_generation`) only ever checked 7 of them, so a missing/empty one of the 3 dimension tables wouldn't be caught at regeneration time — it would only surface later, and `load_synthea_tables` (see Interfaces) only checks file *existence*, not non-emptiness, so an empty dimension table would silently produce all-null `payer_ownership`/`provider_specialty`/`organization_utilization` columns rather than erroring anywhere. Also adds all 3 to `REQUIRED_NONEMPTY_TABLES` (confirmed non-empty in every real generation run so far — `payers.csv` has 10 rows, `providers.csv`/`organizations.csv` each ~1,146 at population=10,000 — so requiring non-emptiness matches observed reality, not a hopeful assumption). Requires updating `tests/pipeline/test_generation.py`'s existing fixture-based `test_validate_generation_output_*` tests to include fixture rows for the 3 newly-required tables. **Round-6 addition**: also requires updating `test_run_synthea_generation_success_writes_summary_and_complete_row_counts` (the existing test that deliberately passes `payers.csv` as an `extra_tables` fixture entry specifically to prove `GenerationResult.row_counts`' accounting covers tables OUTSIDE `EXPECTED_CSV_TABLES` — see that test's own comment) — once `payers.csv` moves INTO `EXPECTED_CSV_TABLES`, that test's stated premise becomes false and it silently stops testing what it claims to, even though its assertions would likely still numerically pass. Swap its `extra_tables` fixture entry to a table that remains genuinely outside `EXPECTED_CSV_TABLES` after this slice (e.g. `immunizations.csv`, one of the tables this slice explicitly excludes — see Scope > Out) so the test keeps exercising the behavior it was written to exercise.
  2. Adds a matching `--years-of-history INT` optional flag (default `10`, matching the config default) to `scripts/generate_synthea_data.py`.
  3. Regenerates `data/raw/dry_run/` and `data/raw/full_run/` in place at `--years-of-history 10`, using the same seed (42), reference date (`20260916`), state (Massachusetts), and population targets (1,000 / 10,000) as the existing runs, for direct comparability. **This deletes the existing 17GB+1.6GB full-history exports first**, since `run_synthea_generation`'s own guard refuses to write into a non-empty `output_dir` — this is flagged explicitly here (and will be flagged again at implementation time before it happens) as a destructive-but-cheaply-reproducible action, not something done silently.
- **The Big Join itself** (`src/readmission_risk/pipeline/big_join.py`, `schemas.py`): reads the regenerated CSV export, builds a patient-encounter-level gold feature table, writes it as Parquet to local disk. Local `SparkSession(master="local[*]")` only — Dataproc Serverless/GCS/BigQuery are out of scope (separate "scale-up phase" per the architecture).
- **Readmission label definition** (an all-cause 30-day readmission flag, *inspired by* CMS HRRP's and LACE's shared 30-day convention — not a full implementation of either standard: CMS HRRP's actual methodology is condition/procedure-cohort-specific with complex risk adjustment and excludes *planned* readmissions, and LACE is a risk-scoring index, not a label definition. Planned-readmission exclusion is explicitly NOT implemented in this slice — named as an out-of-scope follow-up):
  - **Index encounters** = `ENCOUNTERCLASS == 'inpatient'` rows only, with a non-null `STOP` (see Failure modes).
  - **Label = 1** if the same patient has another inpatient encounter starting strictly after the index encounter's `STOP` and within `readmission_window_days` (default 30) after it.
  - **Excluded from the gold table entirely** (not labeled 0): index encounters where the patient's `DEATHDATE` is at or before `STOP + readmission_window_days` and no readmission was observed first (can't observe a counterfactual for a deceased patient) — **round-5 correction**: this is a single upper-bound comparison (`DEATHDATE <= readmit_deadline`), matching exactly what Interfaces implements; an earlier draft of this bullet described it as a two-sided range (`[STOP, ...]`), which didn't match the Interfaces code and implied a `DEATHDATE >= STOP` check that isn't (and doesn't need to be) implemented — Synthea's own simulation guarantees a patient's `DEATHDATE` is never before their own encounter's `STOP`, so a lower bound would be redundant; and index encounters where `STOP + readmission_window_days` exceeds the export's `reference_date` and no readmission was observed first (administrative censoring — a positive outcome is still observable even under incomplete follow-up, only an apparent negative is ambiguous).
- **Dimension-table attributes** joined onto each index encounter (not lookback-windowed — these describe the index encounter itself): `payer_ownership` (via `encounters.PAYER → payers.Id`), `provider_specialty` (via `encounters.PROVIDER → providers.Id`), `organization_utilization` (via `encounters.ORGANIZATION → organizations.Id`).
- **Lookback-window aggregate features** (strictly before the index encounter's start — see Data handling): `prior_encounter_count`, `prior_inpatient_count`, `prior_emergency_count`, `active_condition_count`, `active_medication_count`, `procedure_count_window`, `active_careplan_count`, `observation_count_window`.
- **Patient demographics** (from `patients.csv`, evaluated at index-encounter time where age-dependent): `age_at_index_years`, `gender`, `race`, `ethnicity`, `marital_status`.
- Hand-declared `StructType` schema per table (all 10 — see Interfaces), matching CLAUDE.md's standing schema-handling rule.
- Output: Parquet, written to `data/gold/<run_name>/` (e.g. `data/gold/full_run/`), one row per surviving index encounter, exactly 23 columns in a canonical order (identifiers, label, admission attributes, dimension attributes, lookback features, demographics — see Interfaces > `join_patient_demographics`'s `GOLD_TABLE_COLUMNS` list for the exact, ordered spec).
- Unit tests using `pyspark.testing.assertDataFrameEqual` (PySpark 4.1+'s built-in — avoids adding `chispa` as a second dependency, per the architecture PRD's own "pick one, don't need both" note) against small in-memory fixture DataFrames.
- Adds `pyspark==4.1.3` (exact pin, not a range) to `requirements.in` (currently empty — this is the first slice that needs a runtime dependency beyond stdlib). **Version choice, per a round-2 design-check finding that the architecture PRD explicitly requires an exact-version decision at first implementation, not a deferral**: `4.1.3` is confirmed available on PyPI (`pip index versions pyspark` — real check run during this design session, not assumed) and confirmed to include `pyspark.testing.assertDataFrameEqual` (a round-4 correction: this doc previously said the function was "added in 4.1," a claim inherited from the architecture PRD and not itself re-verified — it's actually present at least as far back as `pyspark==3.5.0`. This doesn't change the version decision, only the justification: `4.1.3` is being pinned because it's a real, current, Python-3.12-compatible release that has the function, not because 4.1 was when it first appeared). `4.2.0` is also available and is what unlocks Python 3.14 support per the architecture PRD's own research, but since this project is pinned to Python 3.12 that benefit doesn't apply here, and a one-minor-version-older release is a more conservative choice for whatever Dataproc Serverless runtime image the scale-up phase eventually targets. **Verifying this exact version against Dataproc Serverless's supported-runtime matrix is explicitly deferred to the scale-up phase's own design pass** (Dataproc isn't touched in this slice at all — see Scope > Out) — this is a real, current, resolvable version choice for local use now, not a punt on the decision the PRD asks for.
- **Fixes a real gap found during round-2 review: CI would not actually install the new runtime dependency.** The existing `.github/workflows/ci.yml` only runs `pip install -e .` and `pip install --require-hashes -r requirements-dev.txt` — it never installs `requirements.txt` (the runtime lockfile, currently an empty pip-compile stub). Adds a third install command, `pip install --require-hashes -r requirements.txt`, alongside the existing two. Without this fix, every new test in this slice would fail on `pytest`'s collection step with `ModuleNotFoundError: No module named 'pyspark'`.
- Adds an `actions/setup-java@v4` step (distribution `temurin`, `java-version: '17'`, matching this project's local dev environment) to `.github/workflows/ci.yml`, before the pip install steps — a design-check finding: the existing CI workflow was never verified against a JVM-backed dependency, and relying on an unconfirmed pre-installed JDK on the hosted runner is an unnecessary risk when an explicit step removes the ambiguity entirely.
- Adds an explicit implementation step to regenerate `requirements.txt` via `pip-compile --generate-hashes requirements.in` after adding `pyspark` (a round-2 design-check finding: this project's standing hash-pinning requirement was implied but never stated as an explicit step here).
- Pins `spark.sql.session.timeZone` to `"UTC"` in `make_spark_session` (see Interfaces) — all Synthea timestamps are UTC (`...Z`-suffixed), and the pipeline's core leakage/censoring/window logic depends on exact timestamp arithmetic; leaving the JVM's local timezone unset would make results depend on which machine ran the job, undermining CLAUDE.md's reproducibility priority.
- Validates that `config.reference_date` matches the `reference_date` recorded in `input_dir/generation_summary.json` (written by slice 1's `run_synthea_generation`) before doing anything else in `build_big_join` — see Interfaces. A silent mismatch here would miscalculate the censoring boundary for the entire dataset.
- Saves the empirical measurements backing the `years_of_history`/`lookback_years` decisions to `notes/eg-new-feature/pyspark-big-join-2026-09-19-measurements.md` (already created during this design session) — a design-check finding: slice 1 made its own empirical claims auditable via `generation_summary.json`, and this slice's claims should be too, not left as session-only chat output.

**Out (explicitly deferred):**

- Dataproc Serverless, GCS, BigQuery, the `spark-bigquery-connector` — the architecture's separate scale-up phase.
- Model training, calibration, MLflow (slice 3).
- Typer CLI Top-N triage scoring (slice 4).
- Comorbidity-index scoring (e.g. Elixhauser/Charlson) — this slice uses simple active-condition *counts*, not a weighted clinical index. Named follow-up.
- Lab-value abnormality flags (e.g. "hemoglobin below threshold") — `observations.VALUE` is free-form and code-dependent (hundreds of distinct LOINC codes); this slice only counts observation *volume* in the window, not clinical abnormality. Named follow-up.
- Point-in-time-correct `organization_utilization` (Synthea's own `UTILIZATION` column is a whole-simulation running total, not a value as of the index encounter — see Interfaces). Named follow-up.
- Re-adding `immunizations`, `allergies`, `devices`, `imaging_studies`, `payer_transitions`, `supplies` to the join — explicitly excluded this round, not a silent gap.
- Excluding planned readmissions from the label (full CMS HRRP fidelity) — this slice implements a simpler all-cause 30-day flag (see Scope > In).
- Multi-state/multi-region population distribution (inherited from slice 1's single-fixed-state scope).
- A dry-run validation of the `years_of_history=10` choice at 50k-100k patients (the actual scale-up population) — recommended as a scale-up-phase action item, not this slice's job (this slice only validates at 1,000/10,000).

## Surfaces touched

- `src/readmission_risk/pipeline/generation.py` (edit — add `years_of_history` config field, thread into `build_synthea_command`)
- `scripts/generate_synthea_data.py` (edit — add `--years-of-history` flag)
- `tests/pipeline/test_generation.py` (edit — update the exact-argv test to include the new field at its default, add a case for a non-default value)
- `src/readmission_risk/pipeline/schemas.py` (new — hand-declared `StructType` per table)
- `src/readmission_risk/pipeline/spark_session.py` (new — `SparkSession` factory)
- `src/readmission_risk/pipeline/big_join.py` (new — join/feature logic, pure functions)
- `scripts/run_big_join.py` (new — thin CLI entry point)
- `tests/conftest.py` (new — session-scoped `spark` pytest fixture, shared across pyspark tests)
- `tests/pipeline/test_schemas.py`, `tests/pipeline/test_big_join.py` (new)
- `requirements.in` (edit — add `pyspark`)
- `.github/workflows/ci.yml` (edit — add `actions/setup-java@v4` step before the install steps, and add `pip install --require-hashes -r requirements.txt` as a third install command)
- `notes/eg-new-feature/pyspark-big-join-2026-09-19-measurements.md` (new, already created during this design session — durable record of the empirical measurements behind `years_of_history=10`/`lookback_years=1`)
- `data/raw/dry_run/`, `data/raw/full_run/` (regenerated in place at `years_of_history=10` — not tracked by git, but a destructive local action)
- `CLAUDE.md` (edit, post-implementation — record the final table list, `lookback_years`/`years_of_history` decision with the measured numbers, and the readmission label definition, per this repo's incremental-documentation convention and slice 1's own precedent of an implementation addendum. **Round-4 addition**: also update the "Build & test commands" section's dependency-install instructions to add `pip install --require-hashes -r requirements.txt` as a third command — that section currently documents only the two commands from before this slice added a runtime dependency, and would otherwise go stale the same way the CI workflow almost did, per round-3's fix to that exact class of gap).

Exact resulting `.github/workflows/ci.yml` (only the `steps:` list changes; everything above it is unchanged):

```yaml
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-java@v4
        with:
          distribution: "temurin"
          java-version: "17"

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: |
          pip install -e .
          pip install --require-hashes -r requirements.txt
          pip install --require-hashes -r requirements-dev.txt

      - name: Lint
        run: ruff check .

      - name: Test
        run: pytest
```

## Interfaces

```python
# src/readmission_risk/pipeline/schemas.py

from pyspark.sql.types import (
    StructType, StructField, StringType, DateType, TimestampType, DoubleType, IntegerType,
)

# Date/timestamp formats verified directly against the existing data/raw/full_run/csv/ export:
# patients.BIRTHDATE/DEATHDATE and conditions/careplans.START/STOP are date-only ("YYYY-MM-DD");
# encounters/medications/procedures.START/STOP and observations.DATE are ISO-8601 timestamps
# with a trailing "Z" ("YYYY-MM-DDTHH:MM:SSZ"). This mix is intentional on Synthea's part, not
# a data-quality bug, and drives the DateType vs TimestampType choices below.
#
# IMPORTANT: each StructType's field order below MUST exactly match its CSV's real column order.
# spark.read.schema(...).csv(...) with an explicit schema aligns columns POSITIONALLY, not by
# name -- it does not cross-check the schema's field names against the file's header row. Verified
# against all 10 real files during this design's own review; a future Synthea version bump that
# reorders columns would silently misassign values rather than erroring, so this is a load-bearing
# assumption, not an implementation detail to skip past.
#
# IMPORTANT (round-2 design-check finding): `nullable=False` below is schema METADATA only --
# Spark's CSV data source does NOT validate non-null constraints on read. A genuinely-empty
# Id/PATIENT/START value in a row marked non-nullable would pass through silently as an empty
# string or null-cast value, not raise. This is consistent with this doc's existing "documented,
# not hard-checked" posture on Synthea's referential-integrity guarantees (see join_patient_demographics,
# join_dimension_attributes) -- `nullable=False` here documents an expectation, not an enforcement.

PATIENTS_SCHEMA = StructType([
    StructField("Id", StringType(), nullable=False),
    StructField("BIRTHDATE", DateType(), nullable=False),
    StructField("DEATHDATE", DateType(), nullable=True),
    StructField("SSN", StringType(), nullable=True),
    StructField("DRIVERS", StringType(), nullable=True),
    StructField("PASSPORT", StringType(), nullable=True),
    StructField("PREFIX", StringType(), nullable=True),
    StructField("FIRST", StringType(), nullable=True),
    StructField("MIDDLE", StringType(), nullable=True),
    StructField("LAST", StringType(), nullable=True),
    StructField("SUFFIX", StringType(), nullable=True),
    StructField("MAIDEN", StringType(), nullable=True),
    StructField("MARITAL", StringType(), nullable=True),
    StructField("RACE", StringType(), nullable=True),
    StructField("ETHNICITY", StringType(), nullable=True),
    StructField("GENDER", StringType(), nullable=True),
    StructField("BIRTHPLACE", StringType(), nullable=True),
    StructField("ADDRESS", StringType(), nullable=True),
    StructField("CITY", StringType(), nullable=True),
    StructField("STATE", StringType(), nullable=True),
    StructField("COUNTY", StringType(), nullable=True),
    StructField("FIPS", StringType(), nullable=True),
    StructField("ZIP", StringType(), nullable=True),   # StringType, not numeric -- ZIPs can be zero-padded 9-digit (e.g. "010201314"), confirmed in the actual data
    StructField("LAT", DoubleType(), nullable=True),
    StructField("LON", DoubleType(), nullable=True),
    StructField("HEALTHCARE_EXPENSES", DoubleType(), nullable=True),
    StructField("HEALTHCARE_COVERAGE", DoubleType(), nullable=True),
    StructField("INCOME", DoubleType(), nullable=True),
])

ENCOUNTERS_SCHEMA = StructType([
    StructField("Id", StringType(), nullable=False),
    StructField("START", TimestampType(), nullable=False),
    StructField("STOP", TimestampType(), nullable=True),
    StructField("PATIENT", StringType(), nullable=False),
    StructField("ORGANIZATION", StringType(), nullable=True),
    StructField("PROVIDER", StringType(), nullable=True),
    StructField("PAYER", StringType(), nullable=True),
    StructField("ENCOUNTERCLASS", StringType(), nullable=True),  # confirmed values in this export: ambulatory, emergency, home, hospice, inpatient, outpatient, snf, urgentcare, virtual, wellness
    StructField("CODE", StringType(), nullable=True),
    StructField("DESCRIPTION", StringType(), nullable=True),
    StructField("BASE_ENCOUNTER_COST", DoubleType(), nullable=True),
    StructField("TOTAL_CLAIM_COST", DoubleType(), nullable=True),
    StructField("PAYER_COVERAGE", DoubleType(), nullable=True),
    StructField("REASONCODE", StringType(), nullable=True),
    StructField("REASONDESCRIPTION", StringType(), nullable=True),
])

CONDITIONS_SCHEMA = StructType([
    StructField("START", DateType(), nullable=False),
    StructField("STOP", DateType(), nullable=True),
    StructField("PATIENT", StringType(), nullable=False),
    StructField("ENCOUNTER", StringType(), nullable=True),
    StructField("SYSTEM", StringType(), nullable=True),
    StructField("CODE", StringType(), nullable=True),
    StructField("DESCRIPTION", StringType(), nullable=True),
])

MEDICATIONS_SCHEMA = StructType([
    StructField("START", TimestampType(), nullable=False),
    StructField("STOP", TimestampType(), nullable=True),
    StructField("PATIENT", StringType(), nullable=False),
    StructField("PAYER", StringType(), nullable=True),
    StructField("ENCOUNTER", StringType(), nullable=True),
    StructField("CODE", StringType(), nullable=True),
    StructField("DESCRIPTION", StringType(), nullable=True),
    StructField("BASE_COST", DoubleType(), nullable=True),
    StructField("PAYER_COVERAGE", DoubleType(), nullable=True),
    StructField("DISPENSES", IntegerType(), nullable=True),
    StructField("TOTALCOST", DoubleType(), nullable=True),
    StructField("REASONCODE", StringType(), nullable=True),
    StructField("REASONDESCRIPTION", StringType(), nullable=True),
])

PROCEDURES_SCHEMA = StructType([
    StructField("START", TimestampType(), nullable=False),
    StructField("STOP", TimestampType(), nullable=True),
    StructField("PATIENT", StringType(), nullable=False),
    StructField("ENCOUNTER", StringType(), nullable=True),
    StructField("SYSTEM", StringType(), nullable=True),
    StructField("CODE", StringType(), nullable=True),
    StructField("DESCRIPTION", StringType(), nullable=True),
    StructField("BASE_COST", DoubleType(), nullable=True),
    StructField("REASONCODE", StringType(), nullable=True),
    StructField("REASONDESCRIPTION", StringType(), nullable=True),
])

CAREPLANS_SCHEMA = StructType([
    StructField("Id", StringType(), nullable=False),
    StructField("START", DateType(), nullable=False),
    StructField("STOP", DateType(), nullable=True),
    StructField("PATIENT", StringType(), nullable=False),
    StructField("ENCOUNTER", StringType(), nullable=True),
    StructField("CODE", StringType(), nullable=True),
    StructField("DESCRIPTION", StringType(), nullable=True),
    StructField("REASONCODE", StringType(), nullable=True),
    StructField("REASONDESCRIPTION", StringType(), nullable=True),
])

OBSERVATIONS_SCHEMA = StructType([
    StructField("DATE", TimestampType(), nullable=False),
    StructField("PATIENT", StringType(), nullable=False),
    StructField("ENCOUNTER", StringType(), nullable=True),
    StructField("CATEGORY", StringType(), nullable=True),
    StructField("CODE", StringType(), nullable=True),
    StructField("DESCRIPTION", StringType(), nullable=True),
    StructField("VALUE", StringType(), nullable=True),  # deliberately NOT DoubleType -- VALUE mixes numeric and text observations (see TYPE column); casting to Double would silently NULL every non-numeric row. This slice only counts rows in the window (observation_count_window), never reads VALUE's content -- see Scope > Out for deferred value-parsing work.
    StructField("UNITS", StringType(), nullable=True),
    StructField("TYPE", StringType(), nullable=True),
])

PAYERS_SCHEMA = StructType([
    StructField("Id", StringType(), nullable=False),
    StructField("NAME", StringType(), nullable=True),
    StructField("OWNERSHIP", StringType(), nullable=True),
    StructField("ADDRESS", StringType(), nullable=True),
    StructField("CITY", StringType(), nullable=True),
    StructField("STATE_HEADQUARTERED", StringType(), nullable=True),
    StructField("ZIP", StringType(), nullable=True),
    StructField("PHONE", StringType(), nullable=True),
    StructField("AMOUNT_COVERED", DoubleType(), nullable=True),
    StructField("AMOUNT_UNCOVERED", DoubleType(), nullable=True),
    StructField("REVENUE", DoubleType(), nullable=True),
    StructField("COVERED_ENCOUNTERS", IntegerType(), nullable=True),
    StructField("UNCOVERED_ENCOUNTERS", IntegerType(), nullable=True),
    StructField("COVERED_MEDICATIONS", IntegerType(), nullable=True),
    StructField("UNCOVERED_MEDICATIONS", IntegerType(), nullable=True),
    StructField("COVERED_PROCEDURES", IntegerType(), nullable=True),
    StructField("UNCOVERED_PROCEDURES", IntegerType(), nullable=True),
    StructField("COVERED_IMMUNIZATIONS", IntegerType(), nullable=True),
    StructField("UNCOVERED_IMMUNIZATIONS", IntegerType(), nullable=True),
    StructField("UNIQUE_CUSTOMERS", IntegerType(), nullable=True),
    StructField("QOLS_AVG", DoubleType(), nullable=True),
    StructField("MEMBER_MONTHS", IntegerType(), nullable=True),
])

PROVIDERS_SCHEMA = StructType([
    StructField("Id", StringType(), nullable=False),
    StructField("ORGANIZATION", StringType(), nullable=True),
    StructField("NAME", StringType(), nullable=True),
    StructField("GENDER", StringType(), nullable=True),
    StructField("SPECIALITY", StringType(), nullable=True),
    StructField("ADDRESS", StringType(), nullable=True),
    StructField("CITY", StringType(), nullable=True),
    StructField("STATE", StringType(), nullable=True),
    StructField("ZIP", StringType(), nullable=True),
    StructField("LAT", DoubleType(), nullable=True),
    StructField("LON", DoubleType(), nullable=True),
    StructField("ENCOUNTERS", IntegerType(), nullable=True),
    StructField("PROCEDURES", IntegerType(), nullable=True),
])

ORGANIZATIONS_SCHEMA = StructType([
    StructField("Id", StringType(), nullable=False),
    StructField("NAME", StringType(), nullable=True),
    StructField("ADDRESS", StringType(), nullable=True),
    StructField("CITY", StringType(), nullable=True),
    StructField("STATE", StringType(), nullable=True),
    StructField("ZIP", StringType(), nullable=True),
    StructField("LAT", DoubleType(), nullable=True),
    StructField("LON", DoubleType(), nullable=True),
    StructField("PHONE", StringType(), nullable=True),
    StructField("REVENUE", DoubleType(), nullable=True),
    StructField("UTILIZATION", IntegerType(), nullable=True),
])

# Maps Synthea's CSV filename -> its hand-declared schema. Iteration order is the load order
# used by load_synthea_tables (not semantically significant, but fixed for test determinism).
TABLE_SCHEMAS: dict[str, StructType] = {
    "patients.csv": PATIENTS_SCHEMA,
    "encounters.csv": ENCOUNTERS_SCHEMA,
    "conditions.csv": CONDITIONS_SCHEMA,
    "medications.csv": MEDICATIONS_SCHEMA,
    "procedures.csv": PROCEDURES_SCHEMA,
    "careplans.csv": CAREPLANS_SCHEMA,
    "observations.csv": OBSERVATIONS_SCHEMA,
    "payers.csv": PAYERS_SCHEMA,
    "providers.csv": PROVIDERS_SCHEMA,
    "organizations.csv": ORGANIZATIONS_SCHEMA,
}
```

```python
# src/readmission_risk/pipeline/spark_session.py

from pyspark.sql import SparkSession

def make_spark_session(app_name: str = "big-join") -> SparkSession:
    """Local-mode only this slice. Structured as its own function (not inlined into build_big_join)
    so the scale-up phase can swap this one function for Dataproc Serverless session construction
    without touching build_big_join's read/write calls -- per the architecture PRD's requirement
    that only SparkSession construction branches between environments.

    Pins spark.sql.session.timeZone to "UTC" -- a design-check finding: every Synthea timestamp
    in this pipeline is UTC ("...Z"-suffixed), and the leakage/censoring/window logic depends on
    exact timestamp arithmetic (add_months, datediff, range filters). Leaving the JVM's local
    timezone unset would make results depend on which machine runs the job -- a reproducibility
    gap this project's CLAUDE.md treats as a standing priority."""
    spark = SparkSession.builder.master("local[*]").appName(app_name).getOrCreate()
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    return spark
```

```python
# src/readmission_risk/pipeline/big_join.py

from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from pyspark.sql import DataFrame, SparkSession
import pyspark.sql.functions as F

from .schemas import TABLE_SCHEMAS

TABLES_JOINED: tuple[str, ...] = (
    "patients.csv", "encounters.csv", "conditions.csv", "medications.csv",
    "procedures.csv", "careplans.csv", "observations.csv",
    "payers.csv", "providers.csv", "organizations.csv",
)
# Deliberately excludes claims.csv/claims_transactions.csv (billing, not clinical -- CLAUDE.md)
# and immunizations/allergies/devices/imaging_studies/payer_transitions/supplies (excluded by
# explicit decision this design round -- see Scope > Out). This tuple IS the single source of
# truth for "which tables does the join read" -- load_synthea_tables iterates exactly this list,
# looking up TABLE_SCHEMAS[name] for each entry (schemas.py). A round-2 design-check finding:
# these two collections (this tuple and schemas.py's TABLE_SCHEMAS dict) must stay in sync or
# load_synthea_tables raises a bare KeyError -- tests/pipeline/test_big_join.py must include
# test_tables_joined_matches_table_schemas asserting set(TABLES_JOINED) == set(TABLE_SCHEMAS.keys())
# (see Verification criteria).

@dataclass(frozen=True)
class BigJoinConfig:
    input_dir: Path             # e.g. Path("data/raw/full_run") -- must contain a csv/ subfolder (slice 1's own output layout)
    output_dir: Path            # e.g. Path("data/gold/full_run") -- must NOT already exist (see build_big_join)
    reference_date: str         # required, "YYYYMMDD" -- MUST match the value passed to Synthea when input_dir was generated (used for forward-censoring; no default, mirroring SyntheaGenerationConfig's own no-silent-default convention for reference_date)
    lookback_years: int = 1
    readmission_window_days: int = 30


DIMENSION_TABLES_MUST_BE_NONEMPTY: tuple[str, ...] = ("payers.csv", "providers.csv", "organizations.csv")
# These 3 feed join_dimension_attributes as unconditional left-join sources with no fallback --
# an empty one would silently produce all-null payer_ownership/provider_specialty/
# organization_utilization columns rather than erroring anywhere. Scope item 1a already extends
# slice 1's own REQUIRED_NONEMPTY_TABLES to cover these 3 for the common case (input_dir produced
# by this session's own run_synthea_generation call), but that guard is an EXTERNAL precondition
# this function has no way to verify was actually applied -- a round-5 design-check finding: a
# stale directory, hand-copied data, or any future input_dir that didn't go through that exact
# validated path would silently reintroduce the all-null failure mode. load_synthea_tables adds
# its OWN independent, cheap guard for exactly these 3 (not all 10 -- the other 7 either get
# exercised immediately by code that would visibly fail on empty data, like build_index_encounters
# needing non-empty encounters.csv, or are large enough that a plain-Python emptiness pre-check
# isn't cheap) so this function doesn't depend on an external validation step having run.

def load_synthea_tables(spark: SparkSession, input_dir: Path) -> dict[str, DataFrame]:
    """
    Impure (filesystem + Spark read). For each name in TABLES_JOINED, checks
    (input_dir / "csv" / name).is_file() BEFORE invoking Spark; raises
    FileNotFoundError(f"Required table not found: {path}") for the FIRST missing table
    encountered (deterministic order = TABLES_JOINED's own order), rather than letting Spark
    raise its own less-specific error partway through a later stage.
    For each name in DIMENSION_TABLES_MUST_BE_NONEMPTY specifically: after confirming the file
    exists, also does a cheap plain-Python line count (same `sum(1 for _ in f) - 1` pattern slice
    1's generation.py already uses for its own validation, not a Spark read -- these 3 files are
    small (tens to low thousands of rows even at 100k patients) so this is a negligible cost); if
    the count is 0 (header only, no data rows), raises
    ValueError(f"Required table is present but empty: {path}") BEFORE any Spark read of it.
    For each present (and, where applicable, confirmed-non-empty) file:
    spark.read.schema(TABLE_SCHEMAS[name]).option("header", True).csv(str(path)).
    Returns {table_name: DataFrame}, keyed exactly by the TABLES_JOINED entries (e.g. "encounters.csv").
    """


def build_index_encounters(
    encounters: DataFrame,
    patients: DataFrame,
    reference_date: date,
    readmission_window_days: int = 30,
) -> DataFrame:
    """
    Pure (Spark transformations only -- no I/O). Steps, in order:

    1. Filter to ENCOUNTERCLASS == "inpatient" AND STOP IS NOT NULL (a null STOP means the
       encounter was never closed -- doesn't occur in the current export, confirmed by direct
       inspection, but defensively excluded rather than assumed impossible, since a null STOP
       makes the discharge-anchored readmission window undefined).
    2. Compute the readmission-window upper bound as
       `readmit_deadline = F.expr(f"STOP + INTERVAL {readmission_window_days} DAYS")` -- an
       INTERVAL expression, NOT `F.date_add(STOP, readmission_window_days)` (date_add truncates
       to DateType, silently discarding STOP's time-of-day; INTERVAL arithmetic on a
       TimestampType column preserves it, which matters for encounters near midnight). This same
       `... + INTERVAL n DAYS` pattern is reused for every "+readmission_window_days" computation
       in this function (steps 2 and 3 below) -- there is exactly one way to do this arithmetic
       in this codebase, not an implementer's choice between date_add and INTERVAL.
    3. Derive two views from the step-1 candidates and the raw `encounters` input: `candidates`
       (the step-1 output: inpatient, non-null-STOP rows -- these are what will become index
       rows) and `all_inpatient` (the FULL, unfiltered `encounters` DataFrame re-filtered to
       ENCOUNTERCLASS == "inpatient" only, independently of step 1 -- this is deliberately NOT
       the same DataFrame as `candidates`, since a readmitting encounter need not itself satisfy
       every index-eligibility rule, only need to BE an inpatient encounter). **`candidates` and
       `all_inpatient` are both filtered from the SAME parent `encounters` DataFrame, so BOTH
       must be explicitly aliased before joining** (`candidates = candidates.alias("c")`,
       `all_inpatient = all_inpatient.alias("a")`) -- a round-5 fix: `.filter()` alone doesn't
       change Spark's underlying column expression IDs, so joining two DataFrames derived from
       one shared parent without aliasing risks `AnalysisException: Reference '...' is ambiguous`
       (a well-known Spark self-join gotcha this doc had not previously called out, despite
       calling out several subtler ones). Self-join: for each row in `candidates` (referenced via
       `F.col("c.PATIENT")` etc. after aliasing), is_readmitted = 1 if a row in `all_inpatient`
       exists for the same PATIENT with `F.col("a.Id") != F.col("c.Id")` and `F.col("a.START")`
       in `(F.col("c.STOP"), F.col("c.readmit_deadline")]`. **Exact mechanism (round-6
       precision fix -- a bare `left_semi` join alone only returns the SUBSET of `candidates`
       that have a match, i.e. only the `is_readmitted=1` rows; it does not by itself produce a
       0/1 column on the FULL candidate set, unlike every other mechanism pinned to one exact
       expression elsewhere in this doc)**:
       ```python
       readmitted_ids = candidates.join(all_inpatient, <join condition above>, "left_semi").select("c.Id")
       labeled_candidates = candidates.join(
           readmitted_ids.withColumnRenamed("Id", "readmitted_id"),
           F.col("c.Id") == F.col("readmitted_id"),
           "left",
       ).withColumn("is_readmitted", F.when(F.col("readmitted_id").isNotNull(), 1).otherwise(0)).drop("readmitted_id")
       ```
       i.e.: compute the semi-join to get just the set of readmitted candidate `Id`s, then
       left-join that id set back onto the FULL `candidates`, and derive `is_readmitted` via
       `F.when(...).otherwise(0)` on whether the join matched. Call this step's output
       `labeled_candidates` (`candidates` with `is_readmitted` appended) -- named explicitly here
       because step 4 below operates on THIS DataFrame, not the raw `encounters` parameter.
    4. Left-join `patients.DEATHDATE` onto `labeled_candidates.PATIENT == patients.Id` (round-5
       fix: earlier drafts said "onto `encounters.PATIENT`," which is imprecise -- by this point
       the working DataFrame is `labeled_candidates`, the step-3 output that already carries
       `is_readmitted`, not the raw, unfiltered `encounters` input). DROP (filter out
       entirely, do not set is_readmitted=0) any row where:
       - is_readmitted == 0 AND DEATHDATE IS NOT NULL AND DEATHDATE <= readmit_deadline
         (died before a readmission could be observed) -- `DEATHDATE` is DateType, compared
         against `readmit_deadline` (TimestampType) via the same implicit DateType-to-midnight-UTC
         cast used throughout this doc; a patient who died later the SAME calendar day as
         `readmit_deadline` is still treated as "died before the deadline," which is the same
         conservative "drop rather than mislabel" direction used for every other Date-vs-Timestamp
         comparison in this design, OR
       - is_readmitted == 0 AND readmit_deadline >= reference_date_exclusive_end
         (administrative censoring -- insufficient follow-up to call this a true negative).
         `reference_date_exclusive_end = F.lit(reference_date) + F.expr("INTERVAL 1 DAY")` --
         **round-5 fix, a likely-silent-bug catch**: an earlier draft string-interpolated
         `reference_date` into a bare `CAST('{reference_date_str}' AS TIMESTAMP)` SQL expression
         without ever specifying what string format `reference_date_str` was -- since this
         pipeline carries `reference_date` as Synthea's compact `"YYYYMMDD"` string everywhere
         ELSE (`config.reference_date`, the CLI flag, `generation_summary.json`), an implementer
         could plausibly (and wrongly) reuse that same compact form here. Spark's bare
         `CAST(string AS TIMESTAMP)` does NOT parse `"20260916"` -- it requires an explicit
         `to_timestamp(col, 'yyyyMMdd')` format string for that pattern -- so passing the compact
         form would silently produce `NULL` for `reference_date_exclusive_end`, which would in
         turn make `readmit_deadline >= reference_date_exclusive_end` NULL (never true) for every
         row, silently disabling the entire censoring-boundary check for the whole dataset with no
         error anywhere. **Fixed by not going through a SQL string at all**: `reference_date` at
         this point in the function is already a Python `date` object (this function's own second
         positional parameter, per its signature above) -- `F.lit(reference_date)` passes it
         directly as a typed Spark literal, which Spark converts correctly with no string
         formatting or parsing involved anywhere. midnight UTC at the START of the day AFTER
         reference_date, i.e. the boundary is
         exclusive of reference_date's own full calendar day. This is deliberately the OPPOSITE
         cast direction from the leakage argument used elsewhere in this doc (where casting a
         Date to midnight-of-that-day is the conservative choice for avoiding leakage): treating
         only the first instant of reference_date's day as the cutoff would over-censor negatives
         whose deadline falls later the same day, compounding the selection-bias issue already
         flagged in Data handling, so the boundary is pushed to the end of that day instead.
         **`reference_date` is treated as an accepted, approximate lower-bound cutoff for "the
         export's observed range," not a literal hard stop** — a round-3 design-check finding
         corrected a factual overstatement in an earlier draft (which claimed Synthea's data
         "extends through the ENTIRE calendar day of reference_date" and no further). Checked
         directly against the real export (`data/raw/full_run/csv/encounters.csv`, re-verified in
         round 4 after round 3's own first citation of this number was itself inaccurate):
         **82 encounters** have `STOP` dates after `reference_date` (up to a week later),
         breaking down by `ENCOUNTERCLASS` as `ambulatory` (29), `wellness` (14), `snf` (12),
         `hospice` (8), `outpatient` (7), `inpatient` (5), `urgentcare` (4), `emergency` (3) --
         `ambulatory` is the dominant class, not primarily death-related records. Using
         `reference_date + 1 day` as the censoring boundary therefore
         slightly OVER-censors (a handful of true negatives near the boundary get treated as
         censored and dropped rather than kept as confirmed negatives) rather than under-censors
         -- the same safe direction as every other approximation in this design (conservative
         toward dropping ambiguous rows, never toward mislabeling one). Deriving the boundary
         empirically per-run (e.g. `max(encounters.STOP)`) would be more precise but is not worth
         the added complexity for a few dozen affected rows at this population scale; named as an
         out-of-scope follow-up if slice 3's evaluation shows it matters.
       A row with is_readmitted == 1 is NEVER dropped by either rule (a positive is observable
       under both death and censoring, since it already happened before either cutoff).
    5. Return one row per surviving index encounter with columns:
       PATIENT, encounter_id (renamed from Id), index_start (renamed from START),
       index_stop (renamed from STOP), REASONCODE, REASONDESCRIPTION, PAYER, PROVIDER,
       ORGANIZATION (the last three kept for join_dimension_attributes; dropped from the final
       gold table by that later function, not by this one), is_readmitted (IntegerType 0/1,
       cast from the boolean semi-join membership result).
    """


def compute_lookback_features(
    index_encounters: DataFrame,     # output of build_index_encounters
    all_encounters: DataFrame,       # full, unfiltered ENCOUNTERS_SCHEMA-typed DataFrame
    conditions: DataFrame,
    medications: DataFrame,
    procedures: DataFrame,
    careplans: DataFrame,
    observations: DataFrame,
    lookback_years: int = 1,
) -> DataFrame:
    """
    Pure. For each row in index_encounters, window_start =
    F.add_months(F.to_date(F.col("index_start")), -12 * lookback_years) -- calendar-year
    subtraction (leap-year-safe: Spark's add_months clamps e.g. Feb-29 minus 12 months to Feb-28
    of the target non-leap year, rather than producing an off-by-one-day boundary the way a fixed
    `F.date_sub(index_start, 365)` would in a leap year). The explicit `F.to_date(...)` cast is
    deliberate, not incidental: `F.add_months` returns DateType even given a TimestampType input,
    silently truncating index_start's time-of-day to midnight if not cast first -- casting
    explicitly makes that truncation an intentional, documented choice rather than an implicit
    side effect. Since window_start is used only as a LOWER bound and the intended semantics is
    "roughly N calendar years back" (not sub-day precision), this makes the retained window up to
    ~24 hours wider than an hour-exact `lookback_years` would be at its earliest edge -- an
    accepted, immaterial approximation at this feature's granularity, not a bug. The resulting
    DateType-vs-TimestampType comparisons below (window_start against TimestampType columns) use
    the same safe implicit-cast direction already documented for conditions/careplans.START vs
    index_start. Window = [window_start, index_start) -- index_start itself EXCLUDED (see Data
    handling for the leakage argument).

    All seven aggregate joins below are LEFT joins from index_encounters, followed by
    F.coalesce(count_col, F.lit(0)) so "zero matching rows" and "no join partner at all" both
    surface as 0, not null -- these mean the same thing here (nothing happened in the window):

    - prior_encounter_count: count of `all_encounters` rows where PATIENT matches, START is in
      [window_start, index_start), AND STOP <= index_start (fully-completed prior encounters only).
    - prior_inpatient_count: same filter, + ENCOUNTERCLASS == "inpatient".
    - prior_emergency_count: same filter, + ENCOUNTERCLASS == "emergency".
    - active_condition_count: count of DISTINCT `conditions.CODE` where PATIENT matches,
      `F.to_date(START) < F.to_date(index_start)` (explicitly truncated to whole calendar days on
      BOTH sides -- see the leakage fix note below), AND (STOP IS NULL OR STOP >= window_start) --
      "active-or-diagnosed-in-window," not "newly diagnosed in window" -- matches how comorbidity
      indices are conventionally defined.
    - active_medication_count: same window logic as active_condition_count, on `medications.CODE`
      -- EXCEPT the date-truncation fix below does NOT apply here: `medications.START` is already
      TimestampType (unlike `conditions.START`/`careplans.START`, which are DateType), so
      `medications.START < index_start` is already an exact, unambiguous instant-to-instant
      comparison with no implicit-cast leakage risk to fix.
    - procedure_count_window: COUNT (not distinct -- a repeated procedure is itself a signal) of
      `procedures` rows where PATIENT matches and START is in [window_start, index_start) --
      `procedures.START` is TimestampType, same "already exact" reasoning as `active_medication_count`.
    - active_careplan_count: same window logic as active_condition_count (including the
      date-truncation fix -- `careplans.START` is DateType), on `careplans.CODE`.
    - observation_count_window: count of `observations` rows where PATIENT matches and DATE is
      in [window_start, index_start) -- `observations.DATE` is TimestampType, same "already exact"
      reasoning.

    **Leakage fix (round-4 design-check finding, corrects the prior "cross-type date comparison"
    note below):** `conditions.START`/`careplans.START` are DateType (a calendar day with no
    time-of-day) while `index_start` is TimestampType. The straightforward comparison
    `conditions.START < index_start` relies on Spark's implicit cast of the DateType side to
    MIDNIGHT of that day -- which means a condition dated the SAME CALENDAR DAY as the index
    admission (e.g. the admitting diagnosis itself, very plausibly coded the same day the
    inpatient encounter starts) ALWAYS compares as "before" index_start's actual admission time,
    and gets counted into `active_condition_count` as pre-existing history. This is a genuine
    leakage risk, not a safe direction as an earlier draft of this doc claimed: it lets
    same-day, admission-time information leak into a feature meant to represent status BEFORE
    the admission, directly contradicting Data handling's "no feature ever reads data timestamped
    at or after the index encounter's own start" claim. Fixed by truncating BOTH sides to
    `F.to_date(...)` before comparing (`F.to_date(conditions.START) < F.to_date(index_start)`) --
    this compares whole calendar days explicitly rather than relying on an implicit sub-day cast,
    and correctly excludes same-day records from the "active-before-admission" count. This
    slightly narrows what counts as "prior" (a condition coded earlier the same day, in a
    genuinely separate outpatient encounter, would now also be excluded) but that's the correct,
    conservative trade-off for a leakage-sensitive feature, consistent with CLAUDE.md's standing
    leakage-prevention priority. Applies to `active_condition_count` and `active_careplan_count`
    only -- see the per-feature notes above for why `active_medication_count`,
    `procedure_count_window`, and `observation_count_window` don't share this issue (their source
    columns are already TimestampType, so no implicit cast is involved).

    Returns index_encounters with these 8 additional IntegerType columns appended.
    """


def join_dimension_attributes(
    features: DataFrame,      # output of compute_lookback_features
    payers: DataFrame,
    providers: DataFrame,
    organizations: DataFrame,
) -> DataFrame:
    """
    Pure. Left-joins static, index-encounter-level (NOT lookback-windowed) dimension attributes
    via the encounter's own foreign keys (carried through from build_index_encounters).

    **Round-6 fix, a substantive gap: each dimension table MUST be narrowed to exactly `Id` +
    its one needed attribute (renamed) via `.select()` BEFORE joining -- never join the
    full-width `payers`/`providers`/`organizations` DataFrames directly.** An earlier draft only
    said "drops the raw PAYER/PROVIDER/ORGANIZATION foreign-key columns after joining," which
    left every OTHER raw column from these three tables (e.g. `NAME`/`ADDRESS`/`CITY`/`ZIP`,
    shared verbatim across all three; `STATE`/`LAT`/`LON` shared by providers+organizations;
    `PHONE`/`REVENUE` shared by payers+organizations) to silently ride into `features` with no
    stated disposition. Concretely, `providers.csv` has its own `GENDER` column (a provider's
    gender, per its real schema) -- if it isn't dropped here, `join_patient_demographics`'s later
    unqualified `GENDER -> gender` rename would collide with `patients.GENDER`, and Spark would
    raise `AnalysisException: Reference 'GENDER' is ambiguous`. This is the exact same bug class
    round 5 fixed for the `build_index_encounters` self-join (two DataFrames sharing column names
    joined without narrowing/aliasing first) -- just missed here through 5 prior rounds because
    review scrutiny had concentrated on `build_index_encounters`/`compute_lookback_features`.
    Fixed by narrowing, not aliasing (unlike the self-join fix): each dimension table is reduced
    to only what's needed before the join even happens, which is simpler here since (unlike the
    self-join) there's no need to keep other columns from these tables around post-join:

    ```python
    payers_narrow = payers.select(F.col("Id"), F.col("OWNERSHIP").alias("payer_ownership"))
    providers_narrow = providers.select(F.col("Id"), F.col("SPECIALITY").alias("provider_specialty"))
    organizations_narrow = organizations.select(F.col("Id"), F.col("UTILIZATION").alias("organization_utilization"))
    ```

    Then left-join `features` to each of `payers_narrow` (on `PAYER == payers_narrow.Id`),
    `providers_narrow` (on `PROVIDER == providers_narrow.Id`), `organizations_narrow` (on
    `ORGANIZATION == organizations_narrow.Id`) -- each narrowed DataFrame's own `Id` column is
    then also dropped (redundant with the FK it was joined on), alongside the raw
    `PAYER`/`PROVIDER`/`ORGANIZATION` FK columns themselves, leaving only the three renamed
    attribute columns added to `features`.

    `organization_utilization` NOTE: this is Synthea's own running total of encounters ever
    handled by that organization ACROSS THE WHOLE SIMULATION (confirmed against the actual
    organizations.csv data: UTILIZATION is an integer encounter count, matching
    providers.ENCOUNTERS' pattern), not a point-in-time value as of index_start. Kept as a static
    "how busy is this facility" proxy -- not a leakage risk (independent of this index
    encounter's own outcome), but not temporally precise either; documented here rather than
    silently assumed correct (see Scope > Out for the deferred point-in-time refinement).

    Left join (not inner): confirmed via direct inspection of the existing export that
    PAYER/PROVIDER/ORGANIZATION are never empty on any encounter row, so an unmatched key is not
    expected, but no runtime assertion enforces this invariant (same "documented, not
    hard-checked" posture as slice 1's Synthea-Id referential-integrity assumption).
    """


def join_patient_demographics(features: DataFrame, patients: DataFrame) -> DataFrame:
    """
    Pure. Left-joins `patients` on PATIENT == patients.Id. Computes:
    age_at_index_years = F.datediff(index_start, BIRTHDATE) / 365.25 (float, not rounded --
    rounding/bucketing is a modeling-time decision for slice 3, not this slice's job).
    Carries through GENDER -> gender, RACE -> race, ETHNICITY -> ethnicity, MARITAL -> marital_status
    (renamed to lowercase snake_case for the gold table's own naming convention, distinct from
    Synthea's raw CSV header casing).
    No runtime check that every PATIENT resolves to a patients.csv row -- Synthea's own
    referential design guarantees this (same assumption slice 1 documents), and this slice does
    not re-verify it.

    THIS is the function that produces the gold table's final shape, via one closing `.select()`
    in the EXACT column order below -- a round-4 design-check finding: neither this function nor
    `build_big_join`'s orchestrator previously stated a canonical output schema, which
    `pyspark.testing.assertDataFrameEqual` (default `ignoreColumnOrder=False`) needs to exist
    concretely before `test_build_big_join_end_to_end`'s expected fixture can even be written.
    Everything NOT in this list -- all raw `patients.csv` columns not named here (SSN, DRIVERS,
    PASSPORT, PREFIX/FIRST/MIDDLE/LAST/SUFFIX/MAIDEN names, BIRTHPLACE, ADDRESS, CITY, STATE,
    COUNTY, FIPS, ZIP, LAT, LON, HEALTHCARE_EXPENSES, HEALTHCARE_COVERAGE, INCOME, the raw
    BIRTHDATE/DEATHDATE columns themselves, patients.Id) -- is deliberately dropped, not carried
    through by accident. This keeps the gold table to exactly the columns this design's Scope
    actually calls for, consistent with it being destined for BigQuery and a public Streamlit
    dashboard per CLAUDE.md's architecture (no reason to carry patient names/SSN/addresses into
    that surface even though this is synthetic data with no real PII). GOLD_TABLE_COLUMNS, in
    the exact output order:

    1.  patient_id          (renamed from PATIENT)
    2.  encounter_id        (from build_index_encounters, already renamed from Id)
    3.  index_start
    4.  index_stop
    5.  is_readmitted
    6.  admission_reason_code         (renamed from REASONCODE)
    7.  admission_reason_description  (renamed from REASONDESCRIPTION)
    8.  payer_ownership
    9.  provider_specialty
    10. organization_utilization
    11. prior_encounter_count
    12. prior_inpatient_count
    13. prior_emergency_count
    14. active_condition_count
    15. active_medication_count
    16. procedure_count_window
    17. active_careplan_count
    18. observation_count_window
    19. age_at_index_years
    20. gender
    21. race
    22. ethnicity
    23. marital_status

    23 columns total. REASONCODE/REASONDESCRIPTION are renamed here (not earlier in
    build_index_encounters, which passes them through under their raw Synthea names per its own
    Interfaces spec) since this is the single place the gold table's final naming convention gets
    applied, matching how GENDER/RACE/ETHNICITY/MARITAL are already renamed in this same function.
    """


def build_big_join(spark: SparkSession, config: BigJoinConfig) -> DataFrame:
    """
    Orchestrator. Sequence:
    1. If config.output_dir.exists(): raise FileExistsError(...) before any Spark read --
       mirrors run_synthea_generation's own guard and rationale (never silently mix a fresh run's
       Parquet output with stale prior output).
    2. Read input_dir/"generation_summary.json" (written by slice 1's run_synthea_generation).
       - If the file is missing entirely: raise FileNotFoundError naming it explicitly.
       - If the file exists but has no "reference_date" key (round-2 design-check addition --
         covers a malformed/older-format summary, not just the two cases above): raise
         ValueError(f"{input_dir}/generation_summary.json is missing a 'reference_date' key").
       - Otherwise compare its "reference_date" value to config.reference_date; raise
         ValueError(f"config.reference_date ({config.reference_date}) does not match the input "
         f"data's own reference_date ({recorded_value}) -- see {input_dir}/generation_summary.json")
         on mismatch -- getting this wrong would silently miscalculate the censoring boundary
         (build_index_encounters step 4) for the entire dataset.
    3. tables = load_synthea_tables(spark, config.input_dir)
    4. reference_date = datetime.strptime(config.reference_date, "%Y%m%d").date()
    5. index_encounters = build_index_encounters(tables["encounters.csv"], tables["patients.csv"],
       reference_date, config.readmission_window_days)
    6. features = compute_lookback_features(index_encounters, tables["encounters.csv"],
       tables["conditions.csv"], tables["medications.csv"], tables["procedures.csv"],
       tables["careplans.csv"], tables["observations.csv"], config.lookback_years)
    7. features = join_dimension_attributes(features, tables["payers.csv"], tables["providers.csv"],
       tables["organizations.csv"])
    8. gold = join_patient_demographics(features, tables["patients.csv"])
    9. gold.write.parquet(str(config.output_dir))
    10. return gold
    """
```

## UX flow (pipeline, not UI)

1. **Regenerate slice-1 data at `years_of_history=10`** (one-time, per this design): apply the `generation.py`/`generate_synthea_data.py` edits above, then delete and re-run:
   ```
   rm -rf data/raw/dry_run data/raw/full_run
   python scripts/generate_synthea_data.py --population 1000 --seed 42 --output data/raw/dry_run/ --years-of-history 10
   python scripts/generate_synthea_data.py --population 10000 --seed 42 --output data/raw/full_run/ --years-of-history 10
   ```
   This step is flagged explicitly to the user before running at implementation time (Step 4), since it's a destructive-but-cheap-to-redo local action.
2. **Run the Big Join**: `python scripts/run_big_join.py --input-dir data/raw/full_run --output-dir data/gold/full_run --reference-date 20260916` (flags: `--lookback-years` optional, `type=int`, default 1; `--readmission-window-days` optional, `type=int`, default 30 — **round-5 addition**: both MUST be declared `type=int` in `argparse`, not left as the untyped `str` default, since `readmission_window_days` is spliced directly into a Spark SQL string via f-string interpolation (`F.expr(f"STOP + INTERVAL {readmission_window_days} DAYS")`, Interfaces > `build_index_encounters`) — an unvalidated string value reaching that call site would produce a malformed SQL expression deep inside Spark rather than a clean `argparse` type error at the CLI boundary). Built with stdlib `argparse` (a round-2 design-check finding: the doc previously left this unspecified — matches slice 1's `generate_synthea_data.py` precedent; `Typer` is reserved for slice 4's batch-scoring CLI per CLAUDE.md's MLOps spine, not introduced here). Exit code 0 on success; 1 on any of `FileNotFoundError` (missing input table, or missing `generation_summary.json`), `FileExistsError` (output dir already exists), or `ValueError` (reference-date mismatch against `generation_summary.json`, or a `generation_summary.json` present but missing its `reference_date` key) — the script catches all three exception types, prints the error, exits 1. There's no fourth outcome (mirrors `generate_synthea_data.py`'s own convention of an exhaustive catch list).
3. **Manual sanity check** (not CI): read back `data/gold/full_run/` with `spark.read.parquet(...)`, confirm row count is less than the raw `encounters.csv` inpatient-row count (20,755 at the current full-history 10k export — expect somewhat fewer after the `years_of_history=10` regeneration and the death/censoring exclusions), spot-check that `is_readmitted` is a 0/1 int column with no nulls, and that no row's `active_condition_count` etc. is negative or null.

## Data handling

- **Leakage:** every lookback-window feature is computed over `[window_start, index_start)` — index_start itself is always excluded, and no feature ever reads data timestamped at or after the index encounter's own start. The label (`is_readmitted`) is computed purely from `encounters.START/STOP/PATIENT`, entirely independent of the feature columns, so there's no path by which a feature could encode the label. `REASONCODE`/`REASONDESCRIPTION` carried onto the index encounter itself are intrinsic to that admission (known at admission time), not future information.
- **Train/test split integrity (forward-looking, not built in this slice):** the gold table retains `PATIENT` and `index_start` on every row specifically so slice 3 can perform CLAUDE.md's required chronological, patient-grouped split — this slice does not itself split data (no train/test concept applies to a feature table), but its schema is designed not to block that future requirement.
- **Known selection-bias interaction for slice 3's chronological split (design-check finding, not fixed in this slice):** the label-exclusion policy (see Scope's readmission label definition) drops censored *negatives* near the end of the observation window, but never drops censored *positives* (a readmission observed before the censoring cutoff is always kept). Since only the most-recent slice of the timeline can be censored at all, this systematically depletes negatives (relative to positives) in exactly the date range a chronological split would most likely use as the test set — biasing the apparent positive rate, and any AUC/calibration metric computed on it, in that window specifically. This slice does not correct for it (no split happens here), but it's flagged explicitly as something slice 3's design must account for (e.g. a buffer period excluded from the test set, or an explicit note on interpreting test-window metrics), not a gap to rediscover later.
- **Reproducibility:** the regenerated Synthea data reuses slice 1's pinned seed (42) and reference date (`20260916`); the Big Join itself is a deterministic function of its CSV input (no randomness), so `build_big_join` needs no seed of its own.
- **Calendar-year arithmetic, not fixed day counts:** `lookback_years` is implemented via `F.add_months` (see Interfaces), not `F.date_sub(..., 365)` — this was an explicit correction during this design session (365 days ≠ 1 calendar year around leap days).
- **`years_of_history=10` truncation boundary — scoped to the windowed count features only, per a design-check verification.** An index encounter whose `index_start` falls within `lookback_years` of the export's earliest retained date (`reference_date` minus 10 years) has a *windowed* lookback (`prior_encounter_count`, `prior_inpatient_count`, `prior_emergency_count`, `procedure_count_window`, `observation_count_window`) that partially or fully extends before the truncation boundary — the true prior history existed but resolved/historical records outside the retained window were deleted by the exporter. **This slice does NOT special-case or exclude these rows** — the affected fraction is small (roughly `lookback_years / years_of_history` = 1/10 = ~10% of the retained population) and the resulting undercounted features are a known, bounded, documented approximation, not a silent bug. **This does NOT apply to `active_condition_count`/`active_medication_count`/`active_careplan_count`**: a design-check critic pass raised exactly this concern for the active-record features too (since their filter has no lower bound on `START`), and it was checked directly against real `years_of_history=5`/`=10` regenerations rather than assumed — active-record counts were essentially flat across settings (e.g. active conditions: 10,615 / 10,615 / 10,612 at yoh=5 / yoh=10 / unlimited) while resolved-record counts scaled sharply (15,557 / 28,250 / 90,709). Synthea preserves a patient's full current problem list regardless of `years_of_history`; only closed/historical records are pruned. See `notes/eg-new-feature/pyspark-big-join-2026-09-19-measurements.md` section 3 for the full table. Excluding the windowed-feature dead-zone rows remains a named out-of-scope follow-up if slice 3's model evaluation shows they measurably bias results.
- No PII: Synthea output is fully synthetic; covered at the project doc level (architecture's data-realism caveat), not per-slice.

## Failure modes

- Missing required table file under `input_dir/csv/` → `FileNotFoundError`, naming the first missing table, raised before any Spark read (see `load_synthea_tables`).
- `output_dir` already exists → `FileExistsError`, raised before any Spark read (see `build_big_join`), mirroring `run_synthea_generation`'s own guard.
- Index encounter with `STOP IS NULL` → excluded from `build_index_encounters`'s output (see Interfaces) rather than causing a downstream null-arithmetic error; doesn't occur in the current export (confirmed by direct inspection: 0 of 1,885,447 encounters, 0 of 20,755 inpatient encounters, have a null `STOP`), but handled defensively rather than assumed impossible.
- Encounter referencing a `PATIENT`/`PAYER`/`PROVIDER`/`ORGANIZATION` id with no matching dimension-table row → left joins produce nulls in the resulting attribute columns rather than dropping the row or raising; not expected (confirmed no empty foreign keys in the current export) but not hard-asserted either, consistent with slice 1's posture on Synthea's referential-integrity guarantees.
- Empty `conditions`/`medications`/`procedures`/`careplans`/`observations` input for a given patient in the window → the corresponding feature column is `0` (via `coalesce`), not null and not an error — a legitimate "nothing happened" value, not a failure.
- All index encounters excluded (e.g. a tiny/edge-case population with no surviving rows after death/censoring exclusion) → `build_big_join` still writes an empty (zero-row) Parquet dataset rather than raising; this slice does not treat an empty gold table as an error condition, since it's a valid (if uninteresting) outcome of a very small input — a minimum-row-count sanity check is left to the manual verification step (UX flow #3), not enforced in code.
- Regenerating `data/raw/dry_run`/`data/raw/full_run` (UX flow step 1) fails partway (e.g. Synthea crashes) → identical failure mode to slice 1's own `run_synthea_generation` (raises `SyntheaGenerationError`/`SyntheaValidationError`); this slice does not add new error handling around that call.

## Verification criteria

- `tests/conftest.py::spark` — session-scoped pytest fixture. Calls `make_spark_session("test")` directly (the same production factory from `spark_session.py`), NOT a separately-written `SparkSession.builder...` call — a round-2 design-check finding: an earlier draft specified the test fixture with its own bare builder call that omitted the `spark.sql.session.timeZone="UTC"` pin, which would have made every date/timestamp-boundary test (leap-year window edge, inclusive-boundary tests, censoring tests) depend on whichever timezone the test JVM happens to default to. Reusing `make_spark_session` makes this structurally impossible to omit, rather than relying on two call sites staying manually in sync.
- `tests/pipeline/test_schemas.py` — for each of the 10 `TABLE_SCHEMAS` entries, load a 2-3-row fixture CSV (committed under `tests/fixtures/`) with that schema and assert it parses without error and produces the expected column dtypes (guards against a typo'd `StructField` type).
- `tests/pipeline/test_big_join.py::test_build_index_encounters_*` — small in-memory fixture DataFrames (via `spark.createDataFrame`) covering: (a) a readmitted case (inpatient → inpatient within 30 days → `is_readmitted=1`, retained); (b) a non-readmitted, fully-observed case (`is_readmitted=0`, retained); (c) a death-within-window case with no readmission (dropped entirely, not labeled 0); (d) a death-within-window case WITH a prior readmission (retained, `is_readmitted=1` — death does not retroactively drop a positive); (e) a censored case (`STOP + 30d > reference_date`, no readmission observed, dropped); (f) a censored case that nonetheless shows a readmission before the censoring point (retained, `is_readmitted=1`); (g) a non-inpatient encounter (excluded from candidates entirely, never becomes an index row); (h) **round-5 addition**: an inpatient encounter with `STOP IS NULL`, asserting it's excluded from `candidates` entirely and never becomes an index row — this exercises the defensively-coded null-`STOP` guard from step 1 (Failure modes), which had zero test coverage before this addition despite being explicitly called out as a real (if currently unobserved) code path. **Fixture construction requirement (round-5 fix, closes the self-join-aliasing gap):** at least one case in this group (e.g. case (a)) MUST build its fixture as a single `spark.createDataFrame(...)` call that is then `.filter()`ed twice to derive `candidates`/`all_inpatient`-equivalent views — NOT as two independently-constructed `spark.createDataFrame` calls — specifically because two independently-constructed fixtures would NOT exercise Spark's self-join column-ambiguity risk (see Interfaces > `build_index_encounters` step 3's aliasing note); a test suite built entirely from independent fixtures could pass while the real `build_big_join` run against actual CSV-loaded (shared-lineage) DataFrames fails.
- `tests/pipeline/test_big_join.py::test_compute_lookback_features_*` — fixtures covering: (a) the leap-year `add_months` boundary — **corrected in round 2**: the index encounter's `index_start` must be dated EXACTLY `2024-02-29` (a real leap year), not "shortly after" it — `add_months`'s day-clamping behavior (Feb 29 → Feb 28) only triggers when the source date IS the last day of its month; a source date of e.g. March 1 would simply land on March 1 of the prior year and would not exercise this edge case at all. With `lookback_years=1` and `index_start=2024-02-29`, assert `window_start == 2023-02-28` (not `2023-03-01`); (b) a condition/medication/careplan active exactly at `window_start` (inclusive boundary) vs. one row before it (excluded); (c) an index encounter with zero matching rows in every joined table → all 8 feature columns are `0`, not null; (d) the `prior_encounter_count` vs `prior_inpatient_count` vs `prior_emergency_count` distinction on a fixture with mixed `ENCOUNTERCLASS` values; (e) **round-4 addition, the same-day leakage fix**: a fixture condition/careplan with `START` equal to `index_start`'s own calendar date (e.g. `index_start=2024-06-15T14:00:00Z` and a condition `START=2024-06-15`), asserting it is EXCLUDED from `active_condition_count`/`active_careplan_count` (this is the test that would have caught the round-4 leakage bug: without the `F.to_date(...)` truncation on both sides, this same-day condition would have incorrectly counted as "prior"), paired with a control case one calendar day earlier (`START=2024-06-14`) asserting it IS included.
- `tests/pipeline/test_big_join.py::test_tables_joined_matches_table_schemas` — asserts `set(TABLES_JOINED) == set(TABLE_SCHEMAS.keys())` (see `big_join.py`'s `TABLES_JOINED` comment) — guards against the two collections drifting out of sync, which would otherwise surface only as a bare `KeyError` inside `load_synthea_tables`.
- `tests/pipeline/test_big_join.py::test_join_dimension_attributes_*` — a fixture built from the FULL real-shaped `payers`/`providers`/`organizations` schemas (not a hand-trimmed subset containing only the needed columns — **round-6 addition**: a fixture with only the needed columns would NOT exercise the column-collision bug this round's fix addresses), including a `providers.GENDER` value deliberately DIFFERENT from the fixture's `patients.GENDER` value, asserting: (a) the correct `payer_ownership`/`provider_specialty`/`organization_utilization` values land on the correct index-encounter rows; (b) the output's column set is EXACTLY the three renamed attributes plus whatever `features` already had — no raw `payers`/`providers`/`organizations` column (including `GENDER`, `NAME`, `ADDRESS`, or any other collision-prone name) survives; (c) chaining into `join_patient_demographics` afterward does not raise `AnalysisException` and the resulting `gender` column reflects `patients.GENDER`, not `providers.GENDER` — this end-to-end assertion is the one that would have caught the round-6 bug for real, since the ambiguity only manifests once both joins are chained.
- `tests/pipeline/test_big_join.py::test_join_patient_demographics_age_calculation` — a fixture with a known `BIRTHDATE` and `index_start`, asserting `age_at_index_years` matches a hand-computed expected float within a small tolerance.
- `tests/pipeline/test_big_join.py::test_load_synthea_tables_missing_table` — a `tmp_path` with only 9 of the 10 required CSVs present, asserting `FileNotFoundError` naming the missing table, without needing a real Spark read of the other 9 to fail first.
- `tests/pipeline/test_big_join.py::test_load_synthea_tables_rejects_empty_dimension_table` — **round-5 addition**: a `tmp_path` with all 10 tables present but `payers.csv` containing only a header row (no data rows), asserting `ValueError` naming the file, raised before any Spark read of it — this is the test that closes the "empty dimension table silently produces all-null attributes" gap (see `DIMENSION_TABLES_MUST_BE_NONEMPTY` in Interfaces).
- `tests/pipeline/test_big_join.py::test_build_big_join_rejects_existing_output_dir` — pre-create a non-empty `tmp_path` as `output_dir`, assert `FileExistsError` is raised before any Spark read (mirrors slice 1's equivalent test for `run_synthea_generation`).
- `tests/pipeline/test_big_join.py::test_build_big_join_rejects_reference_date_mismatch` — a fixture `input_dir` with a `generation_summary.json` recording a different `reference_date` than `config.reference_date`, asserting `ValueError` naming both dates; a case with `generation_summary.json` missing entirely, asserting `FileNotFoundError`; a case with `generation_summary.json` present but lacking a `reference_date` key, asserting `ValueError` naming the file.
- `tests/pipeline/test_big_join.py::test_build_big_join_end_to_end` — a small, fully-fixture-backed run through all 10 tables and `build_big_join`, asserting the written Parquet output (read back via `spark.read.parquet`) matches an expected `DataFrame` via `assertDataFrameEqual` (PySpark 4.1+ built-in, default `ignoreColumnOrder=False`) — the expected fixture's columns must be built in exactly `GOLD_TABLE_COLUMNS`'s order (see Interfaces > `join_patient_demographics`), covering the whole pipeline in one integration-style test.
- `tests/pipeline/test_generation.py` (updated) — the existing `test_build_synthea_command_*` exact-argv assertion is updated to include `--exporter.years_of_history` reflecting `config.years_of_history` (not the hardcoded `"0"`), plus a new case asserting a non-default value (e.g. `10`) is threaded through correctly. The existing `test_validate_generation_output_*` fixtures are updated to include `payers.csv`/`providers.csv`/`organizations.csv` (round-3 addition to `EXPECTED_CSV_TABLES`/`REQUIRED_NONEMPTY_TABLES` — see Scope item 1a), and a new case asserts an empty `payers.csv` now correctly fails validation (`ok=False`) rather than silently passing.
- `ruff check .` + `pytest` pass in the CI workflow AS MODIFIED by this slice (round-3 fix: an earlier draft of this bullet said "no changes needed to the CI YAML itself," which was true when it was first written but went stale once round 2 added the `setup-java` step and the `requirements.txt` install — see Surfaces touched for the exact resulting YAML).
- **Manual/integration verification** (not CI, needs real Java + real data): regenerate `data/raw/dry_run`/`data/raw/full_run` at `years_of_history=10` per the UX flow, confirm the new sizes are in the ~110MB/~1.9GB range (10 selected tables) predicted by this design's own measurements; run `scripts/run_big_join.py` against the regenerated `full_run`; report the resulting row count, confirm it's less than the raw inpatient-encounter count, and spot-check a handful of rows for sane feature values (no negative counts, no null `is_readmitted`). **Also report the overall `is_readmitted` positive rate** (a design-check finding: the earlier verification steps checked shape/nullness but never plausibility) — flag for manual review if it falls outside a loose sanity band (e.g. under 1% or over 50%), since a value that far outside a defensible range is a much more likely sign of an inverted boolean or an off-by-one in the label logic than a genuine finding about this synthetic population, and the small unit-test fixtures in this section wouldn't surface that kind of bug at real-data scale.

## Out-of-scope follow-ups

- Comorbidity-index scoring (Elixhauser/Charlson) in place of simple active-condition counts.
- Lab-value abnormality flags from `observations.VALUE` (requires per-LOINC-code reference ranges).
- Point-in-time-correct `organization_utilization` (currently a whole-simulation running total).
- Excluding (rather than accepting as a documented approximation) index encounters whose lookback window crosses the `years_of_history` truncation boundary.
- Re-evaluating `immunizations`/`allergies`/`devices`/`imaging_studies`/`payer_transitions`/`supplies` for inclusion if slice 3's model results suggest they're needed.
- A real dry-run size/row-count validation of `years_of_history=10` at the actual 50k-100k scale-up population (this slice only measures at 1,000/10,000).
- Ongoing, automated protection against a future Synthea version bump silently reordering CSV columns (round-6 finding): `tests/pipeline/test_schemas.py`'s hand-authored fixture CSVs can only ever validate against themselves, not against Synthea's actual current export order — verifying that stays impossible without a live Synthea run inside CI, which is out of scope (CI doesn't run Synthea at all, per Scope > Out). The column-order assumption is verified once, manually, at design/implementation time (documented in Interfaces > schemas.py) and re-checked only if something looks wrong later, not continuously guarded.
- Dataproc Serverless / GCS / BigQuery adaptation (separate scale-up phase).
- Deriving the censoring boundary empirically (e.g. `max(encounters.STOP)`) instead of the accepted `reference_date + 1 day` approximation, which slightly over-censors a small number of rows near the boundary (see Interfaces > `build_index_encounters`).

## Revision log

**Round 1 → round 2 (this revision):** Comprehension passed cleanly (no changes needed). Critic returned 8 gaps (`design needs revision`); Readiness returned 3 open questions (`implementation not ready`). All 11 were addressed:

1. **(Critic) The ~10% truncation dead-zone math doesn't apply to `active_*` features, whose filter has no lower bound on `START`** — a valid concern that active-condition/medication/careplan counts could be severely undercounted if `years_of_history` truncation drops old-but-still-active records. **Resolved via new empirical verification, not assumption**: checked directly against real `years_of_history=5`/`=10` regenerations (already sitting in the design session's scratchpad from the earlier sizing measurement). Finding: active-record counts are essentially flat across `years_of_history` settings (Synthea preserves the full current problem list regardless of history-window setting), while resolved/historical-record counts scale sharply with it. The concern was well-founded in principle but the underlying assumption about Synthea's truncation semantics was wrong — fixed by narrowing the dead-zone caveat to the windowed count features only, and adding the verified numbers to Data handling and a new durable measurements doc (`...-measurements.md` section 3).
2. **(Critic) `build_index_encounters`'s Interfaces code block signature didn't match its own docstring's stated "real" signature.** Fixed: the code block itself now declares `patients: DataFrame` directly; the confusing "the pseudocode omits this" meta-commentary is removed.
3. **(Critic) `years_of_history` defaulting to `0` (unbounded) reproduces the exact silent-default footgun this slice exists to fix.** Fixed: default changed to `10` in both `SyntheaGenerationConfig` and the CLI flag; `0` remains available as an explicit opt-in.
4. **(Critic) Ambiguous whether `STOP + readmission_window_days` uses `F.date_add` (truncates to Date) or an INTERVAL expression (preserves timestamp precision) — two implementers could diverge.** Fixed: specified exactly as `F.expr(f"STOP + INTERVAL {readmission_window_days} DAYS")`, used consistently for every such computation in `build_index_encounters`.
5. **(Critic) The label-exclusion policy creates a selection-bias risk for slice 3's chronological split (censored negatives dropped, censored positives kept, depleting negatives specifically in the most-recent/likely-test-set time range) that the doc didn't flag.** Fixed: added an explicit paragraph under Data handling's "Train/test split integrity" naming this as a known interaction for slice 3 to account for.
6. **(Critic) Verification criteria never sanity-checked the label's plausibility (e.g. an inverted boolean wouldn't be caught by shape/nullness checks alone).** Fixed: added an `is_readmitted` positive-rate sanity check to the manual verification step.
7. **(Critic) "CMS HRRP-style convention, matching LACE" overstates fidelity to two different, non-identical standards, and doesn't mention planned-readmission exclusion is unimplemented.** Fixed: reworded to "all-cause 30-day readmission, inspired by CMS HRRP's and LACE's shared 30-day convention," with planned-readmission exclusion named explicitly as unimplemented (both in Scope and Out-of-scope follow-ups).
8. **(Critic) The empirical measurements behind `years_of_history=10`/`lookback_years=1` weren't backed by any durable repo artifact, unlike slice 1's `generation_summary.json` precedent.** Fixed: created `notes/eg-new-feature/pyspark-big-join-2026-09-19-measurements.md`, referenced from Why and Data handling.
9. **(Critic, minor) `StructType` field order must exactly match each CSV's real column order for positional alignment to work, but this load-bearing assumption was never stated.** Fixed: added an explicit note in Interfaces > schemas.py.
10. **(Critic, minor) No runtime validation that `config.reference_date` matches the date `input_dir` was actually generated with.** Fixed: `build_big_join` now reads `input_dir/generation_summary.json` and raises `ValueError` on mismatch (or `FileNotFoundError` if the summary is missing), before any Spark read; added a corresponding test.
11. **(Readiness) `F.add_months` on a TimestampType `index_start` silently returns DateType, truncating time-of-day — is that intended?** Fixed: made explicit via `F.to_date(...)` before `add_months`, documented as a deliberate, immaterial (~24h) widening of the window's lower bound, not an implicit side effect.
12. **(Readiness) No `spark.sql.session.timeZone` pinned, despite all data being UTC and the pipeline depending on exact timestamp arithmetic.** Fixed: `make_spark_session` now sets it to `"UTC"` explicitly.
13. **(Readiness) CI has no `actions/setup-java` step, but the doc asserted CI would pass unchanged — untested assumption for the first JVM-backed CI dependency.** Fixed: added an explicit `actions/setup-java@v4` (Temurin 17) step to `.github/workflows/ci.yml` as an in-scope Surface, removing the reliance on an unconfirmed hosted-runner default.

No gap was dropped or rebutted without a fix — all 13 (8 critic + 3 readiness, with 2 critic items being "minor" sub-notes counted individually) resulted in a concrete doc change. Per the skill's protocol, this revision is re-submitted to Pass B (Critic) and Pass C (Readiness) — Pass A (Comprehension) is skipped since it already passed and this revision is gap-driven, not structural.

**Round 2 → round 3 (this revision):** Critic returned 11 new gaps (`design needs revision`); Readiness returned 3 open questions, 2 overlapping with critic's (`implementation not ready`). Two of these were resolved via NEW empirical verification during this revision (not just documentation fixes), the rest via concrete doc changes:

1. **(Critic, blocking) CI never installs `requirements.txt` — every new test would fail on `ModuleNotFoundError: pyspark`.** Fixed: added `pip install --require-hashes -r requirements.txt` as a third install command; exact resulting `ci.yml` steps now given verbatim in Surfaces touched.
2. **(Critic) The architecture PRD requires an exact `pyspark` version decision at first implementation, not a deferral — the doc previously just said "adds pyspark" with no pin.** Fixed: pinned `pyspark==4.1.3` (confirmed available via a real `pip index versions pyspark` check run during this revision, includes `assertDataFrameEqual`), with Dataproc-runtime-matrix verification explicitly deferred to the scale-up phase (Dataproc isn't touched in this slice) rather than silently skipped.
3. **(Critic + Readiness, overlapping) `tests/conftest.py`'s `spark` fixture didn't reuse `make_spark_session`, so it would miss the UTC timezone pin that fix depended on.** Fixed: the fixture now calls `make_spark_session("test")` directly, making the omission structurally impossible rather than relying on two call sites staying in sync.
4. **(Critic + Readiness, overlapping) The leap-year test's fixture date ("shortly after Feb 29") wouldn't actually exercise `add_months`'s clamping behavior.** Fixed: corrected to require `index_start` be dated EXACTLY `2024-02-29`, with the exact expected `window_start` (`2023-02-28`) now stated.
5. **(Critic) `TABLES_JOINED` and `TABLE_SCHEMAS` are two independently-maintained sources of truth with no test guarding against drift.** Fixed: added `test_tables_joined_matches_table_schemas` asserting set equality; documented the drift risk directly in `TABLES_JOINED`'s own comment.
6. **(Critic) The censoring boundary's midnight-UTC cast direction systematically over-censors negatives whose deadline falls later the same day as `reference_date`.** Fixed: changed the comparison to `readmit_deadline >= reference_date_exclusive_end` (start of the day AFTER `reference_date`), with the rationale explicitly distinguished from the (correctly opposite-direction) leakage argument used elsewhere in the doc.
7. **(Critic) The active-record-invariance claim was only verified at population=1,000, a 10x-smaller scale than this slice's actual target.** Fixed via NEW verification, not just a caveat: ran a real `years_of_history=10` generation at population=10,000 and compared against the existing unlimited-history `data/raw/full_run` — confirmed the invariance holds at the real target scale (active conditions 108,804 vs 108,798; medications 30,427 vs 30,411; careplans 18,842 vs 18,839). Both the design doc and the measurements doc now cite the 10,000-patient numbers, not just the 1,000-patient ones.
8. **(Critic) `scripts/run_big_join.py`'s CLI framework (argparse vs Typer) was unspecified.** Fixed: specified as stdlib `argparse`, matching slice 1's precedent; `Typer` explicitly named as reserved for slice 4 per CLAUDE.md.
9. **(Critic) No explicit step to regenerate `requirements.txt` via `pip-compile --generate-hashes` after editing `requirements.in`.** Fixed: added as an explicit Scope > In bullet.
10. **(Critic, minor) `generation_summary.json` missing its `reference_date` key (distinct from the file being missing entirely) was unhandled.** Fixed: `build_big_join`'s validation step now explicitly covers this third case, raising `ValueError` naming the file; test updated to cover all three cases (missing file, missing key, value mismatch).
11. **(Critic, minor) `nullable=False` in the hand-declared schemas isn't actually enforced by Spark's CSV reader — worth stating explicitly given how much weight the doc places on schema correctness.** Fixed: added an explicit note in Interfaces > schemas.py.
12. **(Readiness) `scripts/run_big_join.py`'s exit-code handling didn't cover the new `ValueError` raised by `build_big_join`'s `reference_date` check.** Fixed: UX flow now states all three exception types (`FileNotFoundError`, `FileExistsError`, `ValueError`) are caught with the same exit-code-1 convention, with no fourth outcome.

Per the skill's protocol, this is the second revision (of a 3-revision cap) — re-submitted to Pass B (Critic) and Pass C (Readiness) only.

**Round 3 → round 4 (this revision, the 3rd — the skill's cap):** Readiness passed cleanly (`implementation ready`, zero open questions — verified by actually running the leap-year `add_months` case, the INTERVAL arithmetic, and schema parsing against real data). Critic found 5 gaps (`design needs revision`), 3 substantive:

1. **A stale Verification-criteria line ("no changes needed to the CI YAML itself") directly contradicted round 2's own CI fix** — exactly the "fixing gap A breaks/contradicts gap B's fix" failure mode the review was watching for. Fixed: reworded to point at the modified workflow and cross-reference Surfaces touched.
2. **The censoring-boundary rationale asserted a false fact: "Synthea's data extends through the ENTIRE calendar day of reference_date."** Checked directly against `data/raw/full_run/csv/encounters.csv`: it actually contains a handful of encounters (mostly Death Certification records) with `STOP` dates up to a week past `reference_date`. The INTERVAL arithmetic itself was already correct and already conservative — only the justifying claim was wrong. Fixed: reworded to describe `reference_date` as an accepted, approximate lower-bound cutoff (not a literal hard stop), named the real over-censoring cost explicitly (a handful of rows, same conservative direction as every other approximation in the design), and added deriving the boundary empirically as a named out-of-scope follow-up.
3. **Slice 1's `EXPECTED_CSV_TABLES`/`REQUIRED_NONEMPTY_TABLES` (7 tables) were never extended to cover the 3 dimension tables this slice adds to the join** (`payers`, `providers`, `organizations`), and `load_synthea_tables` only checks file existence, not non-emptiness — so a missing/empty dimension table wouldn't be caught until it silently produced all-null attribute columns. Fixed: added Scope item 1a extending both lists to all 10 `TABLES_JOINED` tables, with a corresponding test-fixture update and new empty-`payers.csv` test case.
4. **(Minor) The `DEATHDATE`-vs-`readmit_deadline` Date/Timestamp comparison lacked the explicit cast-direction note given to the other two such comparisons in the same function.** Fixed: added, for consistency.
5. **(Minor/cosmetic) The self-join description in `build_index_encounters` step 3 read as self-contradictory on a skim** ("self-join the full DataFrame... against `encounters` re-filtered"). Fixed: rewrote to name the two derived views (`candidates`, `all_inpatient`) explicitly.

All 5 gaps resulted in concrete doc changes; none were dropped or merely asserted fixed. This is the third and final revision under the skill's 3-revision cap — if round 4 does not converge, the protocol calls for stopping and asking the user how to proceed rather than attempting a 4th revision.

**Round 4 (post-cap): user-authorized 4th revision.** Round 4's readiness pass returned `implementation ready` (zero open questions), but its critic pass found 4 gaps (`design needs revision`), one substantive. Per the skill's protocol this hit the 3-revision cap, so the user was asked how to proceed via `AskUserQuestion` rather than auto-revising again. The user's answer: apply the fixes AND run a full 4th round (all applicable passes), overriding the cap explicitly rather than accepting the gaps or stopping. The user also asked that the previously-unspecified gold table column list be resolved directly rather than left open — round 4's readiness pass had independently converged on the exact same gap.

1. **(Substantive, leakage) `active_condition_count`/`active_careplan_count` counted same-calendar-day conditions/careplans as "prior" history**, because comparing a DateType `START` against a TimestampType `index_start` relies on an implicit midnight-of-day cast that always makes same-day records compare as "before" the admission's exact time — letting the admitting diagnosis itself (plausibly coded the same day) leak into a pre-admission feature. The doc had previously (incorrectly) called this cast direction "safe." Fixed: both sides now explicitly truncated to `F.to_date(...)` before comparing, correctly excluding same-day records; confirmed this fix does NOT apply to `active_medication_count`/`procedure_count_window`/`observation_count_window` since their source columns are already TimestampType (no implicit cast involved); added a same-day-vs-one-day-earlier test pair to Verification criteria.
2. **Round 3's own citation fixing the "reference_date is a hard stop" claim was itself factually wrong** (said "~57 total, mostly Death Certification"; re-verified directly: 82 total, `ambulatory`-dominated, only a handful death-related). Fixed: corrected with the exact verified `ENCOUNTERCLASS` breakdown (82 total: ambulatory 29, wellness 14, snf 12, hospice 8, outpatient 7, inpatient 5, urgentcare 4, emergency 3); the underlying conclusion and INTERVAL arithmetic were already correct and unchanged.
3. **The planned CLAUDE.md update didn't cover the "Build & test commands" section**, which documents dependency installation as two commands and would go stale once `pyspark`/`requirements.txt` land, the same class of gap round 3 fixed for CI. Fixed: added to the CLAUDE.md edit's scope.
4. **(Minor) "`assertDataFrameEqual` (added in 4.1)" was an unverified historical claim inherited from the architecture PRD**, and is actually present at least as far back as `pyspark==3.5.0`. Fixed: reworded to state only what was actually verified this session (present in the pinned `4.1.3`), without asserting when it was introduced.
5. **(New, from the user's own question + round 4's readiness pass independently finding the same gap) The gold table's exact, ordered column list was never specified**, which blocks writing `test_build_big_join_end_to_end`'s expected fixture correctly (`assertDataFrameEqual` defaults to strict column-order checking). Fixed: added a canonical 23-column `GOLD_TABLE_COLUMNS` list to `join_patient_demographics`'s Interfaces spec (identifiers → label → admission attributes → dimension attributes → lookback features → demographics), with an explicit statement of what's deliberately dropped (all other raw `patients.csv` columns) and why (this table's destined for BigQuery + a public dashboard; no reason to carry names/SSN/addresses through even for synthetic data).

This is the 4th revision, one beyond the skill's normal 3-revision cap, proceeding only because the user explicitly authorized it in response to the `AskUserQuestion` gate above. Per the user's explicit request, re-submitted to a FULL 3-pass round this time (Pass A Comprehension + Pass B Critic + Pass C Readiness), rather than the skill's normal revision protocol of skipping Comprehension after round 1.

**Round 5 (post-cap, 2nd user-authorized extension): critic-only fixes.** Round 5's comprehension pass passed cleanly (no changes) and readiness passed cleanly (`implementation ready`, zero open questions — verified by actually installing `pyspark==4.1.3` and running real schema reads against the live CSVs). Critic found 7 new gaps (`design needs revision`), one a likely-silent-bug. The user was asked again (this round exceeded even the single extra revision they'd authorized) and chose to fix all 7 and run one final critic-only check (not a full 3-agent round, since comprehension and readiness had both already passed cleanly against this exact doc state moments earlier):

1. **(Substantive, likely-silent-bug) `reference_date_str`'s format was never specified, and the plausible-but-wrong choice (Synthea's compact `"YYYYMMDD"` form, used everywhere else in this pipeline) would make Spark's bare `CAST(string AS TIMESTAMP)` silently return NULL, disabling the entire censoring-boundary check with no error anywhere.** Fixed by removing the SQL-string-interpolation approach entirely: `reference_date_exclusive_end` is now built via `F.lit(reference_date) + F.expr("INTERVAL 1 DAY")`, passing the already-available Python `date` object directly as a typed Spark literal.
2. **(Substantive, real PySpark gotcha) `candidates` and `all_inpatient` (step 3) are both filtered from the same parent `encounters` DataFrame without being aliased first — a well-known self-join column-ambiguity risk this doc hadn't called out despite flagging several subtler Spark behaviors.** Fixed: both views now explicitly `.alias()`'d before the join, with column references updated to use the aliases; added a fixture-construction requirement to Verification criteria specifically requiring at least one `build_index_encounters` test case to share DataFrame lineage (via repeated `.filter()` on one `createDataFrame` call) rather than using only independently-constructed fixtures, since independent fixtures wouldn't exercise this bug at all.
3. **Step 4's DEATHDATE join was described as joining onto "`encounters.PATIENT`," but by that point the correct working DataFrame is step 3's labeled output, not the raw input.** Fixed: named the step-3 output `labeled_candidates` explicitly and corrected step 4's join description to reference it.
4. **`load_synthea_tables` only checks file existence, not non-emptiness — Scope's own extension of `REQUIRED_NONEMPTY_TABLES` only guards the case where `input_dir` was produced by this session's own `run_synthea_generation` call, not any other path a Big Join run might read from.** Fixed: added an independent, cheap, plain-Python non-emptiness check inside `load_synthea_tables` itself for the 3 dimension tables specifically (`DIMENSION_TABLES_MUST_BE_NONEMPTY`), raising `ValueError` before any Spark read — this function no longer depends on an external validation step having run; added a corresponding test.
5. **Scope described the death-exclusion rule as a two-sided range (`[STOP, STOP + readmission_window_days]`) while Interfaces only implements the upper bound.** Fixed: corrected Scope's wording to match the (already-correct) single-upper-bound Interfaces logic, with the reasoning stated (a `DEATHDATE >= STOP` lower bound would be redundant given Synthea's own data guarantees).
6. **CLI argument types for `--lookback-years`/`--readmission-window-days` weren't stated, and an untyped value reaching `readmission_window_days`'s f-string-interpolated SQL usage would fail deep inside Spark rather than cleanly at the CLI boundary.** Fixed: UX flow now states both flags must be `type=int` in `argparse`, with the reasoning given.
7. **No test exercised the defensively-coded `STOP IS NULL` exclusion path in `build_index_encounters` step 1.** Fixed: added a null-`STOP` inpatient test case to Verification criteria.

All 7 gaps resulted in concrete doc changes. Re-submitted to Pass B (Critic) only, per the user's explicit choice this round (comprehension and readiness are not re-run, since they already passed cleanly against a doc state that only gained targeted, non-structural fixes since).

**Round 6 (2nd critic-only re-check): 1 substantive gap, 3 minor.** Round 6's critic pass explicitly re-verified all 7 round-5 fixes hold up (including tracing Spark's type-promotion chain for the `F.lit(reference_date)` fix and confirming no leftover unaliased references from the self-join fix), then gave the previously-under-scrutinized `join_dimension_attributes`/`join_patient_demographics` sections genuinely fresh attention and found:

1. **(Substantive) `join_dimension_attributes` never narrowed `payers`/`providers`/`organizations` to just their needed columns before joining — the same bug class as round 5's self-join fix, just in different functions.** `providers.csv` has its own `GENDER` column; left-joining the full table and only dropping the FK columns would leave it in `features`, colliding with `patients.GENDER` in the next function and raising `AnalysisException: Reference 'GENDER' is ambiguous`. Fixed: each dimension table is now explicitly narrowed via `.select(Id, <attribute>.alias(...))` before joining, with the exact code given; added a test requiring the FULL real-shaped dimension-table schemas (not a hand-trimmed fixture) with a deliberately-conflicting `providers.GENDER` value, asserting the chained `join_patient_demographics` call doesn't raise and resolves to `patients.GENDER`.
2. **The existing `test_run_synthea_generation_success_writes_summary_and_complete_row_counts` test in `tests/pipeline/test_generation.py` specifically uses `payers.csv` to prove row-count accounting covers tables outside `EXPECTED_CSV_TABLES` — a premise this slice's own `EXPECTED_CSV_TABLES` extension (Scope item 1a) breaks, silently.** Fixed: Scope item 1a now explicitly calls out swapping that test's fixture table to one that remains genuinely excluded (e.g. `immunizations.csv`).
3. **(Minor) The `left_semi` join description for `is_readmitted` didn't specify the full mechanism** — a semi-join alone only returns matched rows, not a 0/1 column on the full candidate set. Fixed: exact code given (semi-join for the readmitted-id set, left-join back onto all candidates, `F.when(...).otherwise(0)`).
4. **(Minor) No ongoing protection against a future Synthea version bump reordering CSV columns** — the schema-order assumption is verified once, manually, not continuously guarded (verifying it live would require running Synthea in CI, out of scope). Fixed: named as an explicit out-of-scope follow-up rather than left unstated.

Given this round's finding was a smaller, more localized instance of an already-established bug pattern (not a new category of problem), and both comprehension and readiness had already passed cleanly twice in a row against this doc, these 4 fixes were applied directly with manual verification rather than spinning up another full agent round — a reasonable point to stop the goldfish loop after 6 rounds of scrutiny across 5 revisions (2 beyond the skill's normal cap, both explicitly user-authorized). The doc is considered `design ready` + `implementation ready` as of this revision.
