# synthea-readmission-risk

Predicts 30-day hospital readmission risk from [Synthea](https://synthea.mitre.org/)-generated synthetic patient data. The pipeline ("The Big Join + The Triage List") joins a patient's encounters, conditions, medications, and procedures into a per-patient-encounter feature table, trains a calibrated logistic-regression baseline alongside an XGBoost challenger, and surfaces a Top-N discharge-risk triage list for a team-lead-style reviewer.

Full architecture rationale lives in [notes/prds/big-join-architecture-lock-in-2026-09-15.md](notes/prds/big-join-architecture-lock-in-2026-09-15.md); the standing architecture summary is in [CLAUDE.md](CLAUDE.md#architecture).

**Data-realism note:** Synthea is a synthetic patient generator, not real-world data. Notable limitations relevant to this project: it codes conditions in SNOMED-CT rather than ICD-10-CM, its disease modules are largely isolated from one another (limited comorbidity interaction), coverage is concentrated in a few dozen well-modeled conditions, it does not model hospital resource capacity, and it lacks the messiness (missing values, coding errors, inconsistent documentation) of real clinical data. Treat model performance here as a demonstration of the pipeline and methodology, not a clinically validated result.

## Status

Staged build, "weeks not months": a **local proving phase** (generate a modest population locally, prove the pipeline correctness-first) precedes a **scale-up phase** (the same code against a larger population on GCP). See [CLAUDE.md](CLAUDE.md#architecture) for the full staging plan.

| Slice | What | Status |
|---|---|---|
| 1 | Local Synthea patient generation | **Done** — see [notes/eg-new-feature/local-synthea-generation-2026-09-16.md](notes/eg-new-feature/local-synthea-generation-2026-09-16.md) |
| 2 | PySpark "Big Join" feature engineering | Not started |
| 3 | Calibrated LogisticRegression + XGBoost, tracked in local MLflow | Not started |
| 4 | Typer CLI Top-N triage scoring | Not started |

## Setup

Requires Python 3.12 (pinned via [.python-version](.python-version)).

```sh
# Create and activate the virtualenv
python3.12 -m venv .venv
source .venv/bin/activate

# Install: two separate invocations, since a local editable install can't carry
# pip's --require-hashes guarantee that the dev lockfile uses.
pip install pip-tools        # one-time, unpinned bootstrap so pip-compile is available
pip install -e .
pip install --require-hashes -r requirements-dev.txt
```

## Verify the code works

```sh
ruff check .
pytest
```

This is the fast path to confirm the codebase is sound — no external downloads, no Java, no generated data. It's what CI runs on every push.

## Running the actual pipeline (Slice 1: Synthea generation)

This step is separate from the above: it exercises the real Synthea tool, not just this repo's code around it. It requires a JDK 17+ on `PATH` and a one-time ~200MB download, and produces real synthetic patient data on disk (a 10,000-patient run is roughly 17GB — see the note in [CLAUDE.md](CLAUDE.md) about `exporter.years_of_history=0`). Skip this unless you actually want to generate data.

```sh
# Download and checksum-verify the Synthea jar into tools/synthea/ (gitignored)
./scripts/setup_synthea.sh

# Dry run first: a small population to sanity-check output and timing before committing
# to the full run (Synthea's own generation time isn't predictable in advance).
python scripts/generate_synthea_data.py --population 1000 --seed 42 --output data/raw/dry_run/

# Full local-proving-phase population
python scripts/generate_synthea_data.py --population 10000 --seed 42 --output data/raw/full_run/
```

Each run writes Synthea's CSV export to `<output>/csv/*.csv` and a durable `<output>/generation_summary.json` (population size, seed, reference date, wall-clock duration, per-table row counts, and any non-fatal warnings). Generated data is gitignored — it's reproducible from the commands above, not tracked in version control.
