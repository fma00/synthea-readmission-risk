# Setup and running the pipeline

> Terminology: "slice N" names the build stages in the [README Status table](../README.md#status) (1 Synthea generation, 2 PySpark feature join, 2b unplanned label, 3 models, 4 Top-N CLI); "v2 population" is the planned scale-up cohort (see the README Roadmap).

## Setup

Requires Python 3.12 (pinned via [.python-version](../.python-version)).

```sh
# Create and activate the virtualenv
python3.12 -m venv .venv
source .venv/bin/activate

# Install: three separate invocations, since a local editable install can't carry
# pip's --require-hashes guarantee that the lockfiles use.
pip install -e .
pip install --require-hashes -r requirements.txt -r requirements-linux-extras.txt
pip install --require-hashes -r requirements-dev.txt
```

macOS only: XGBoost needs the OpenMP runtime — `brew install libomp`. A JDK 17+ must be on `PATH` for PySpark's local mode (the feature join and its tests).

`requirements-linux-extras.txt` holds the few dependencies `pip-compile` cannot see when run on macOS (they are Linux-only); each is gated by an environment marker, so pip skips them on macOS and the same command works on both platforms. `pandas` and `numpy` are deliberately pinned below their newest releases (PySpark's `pyspark.pandas` cannot import under pandas 3) — see the comment in [requirements.in](../requirements.in).

## Verify the code works

```sh
ruff check .
pytest
```

This is the fast path to confirm the codebase is sound — no external downloads and no generated data (it does need a JDK on `PATH` for the PySpark tests). It's what CI runs on every push.

## Running the actual pipeline (Slice 1: Synthea generation)

This step is separate from the above: it exercises the real Synthea tool, not just this repo's code around it. It requires a JDK 17+ on `PATH` and a one-time ~200MB download, and produces real synthetic patient data on disk (about 7.5 GB for the 10,000-patient reference run, measured 2026-09-21; 4.3 GB of that is `claims_transactions.csv`, which the feature join does not read; the generator's default is `exporter.years_of_history=10`). Skip this unless you actually want to generate data.

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

## Training the models (Slice 3)

Trains a `LogisticRegression` baseline and a calibrated `XGBClassifier` challenger on the gold table from slice 2, scores a chronological, patient-disjoint held-out window once, and tracks everything in a local SQLite-backed MLflow store (`mlflow/`, gitignored).

```sh
# Prerequisite: the gold table (slice 2 + 2b). Build into a FRESH --output-dir: the script refuses an existing one, and a gold
# table built before slice 2b (all-cause label, no _gold_metadata.json) is refused by training. The results in results.md were measured
# on data/gold/full_run_2b.
python scripts/run_big_join.py --input-dir data/raw/full_run --output-dir data/gold/full_run_2b --reference-date 20260916

# Train + evaluate. --reference-date MUST equal the value used for the Synthea run and the feature join.
python scripts/train_models.py --gold-dir data/gold/full_run_2b --reference-date 20260916 --test-start-date 20230101 --experiment-name readmission-risk-2b

# Browse runs, metrics, calibration diagrams
mlflow ui --backend-store-uri sqlite:///$(pwd)/mlflow/mlflow.db
```

MLflow's autolog also writes in-sample `training_*` metrics into each run; only the `test_*` metrics are results.

The MLflow store records absolute artifact paths, so moving the repository directory invalidates the paths stored in `mlflow/mlflow.db`.
