# Setup and running the pipeline

> Terminology: "slice N" names the build stages in the [README Status table](../README.md#status) (1 Synthea generation, 2 PySpark feature join, 2b unplanned label, 3 models, 4 Top-N CLI); "v2 population" is the planned scale-up cohort (see the README Roadmap).

## Setup

Developed on Python 3.14 (pinned via [.python-version](../.python-version)); CI also tests Python 3.12, the Python of the Dataproc Serverless runtime the scale-up phase targets. The same hash-pinned lockfiles install on both.

```sh
# Create and activate the virtualenv
python3.14 -m venv .venv
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
zizmor --offline .
pytest
```

This is the fast path to confirm the codebase is sound — no generated data, and no external downloads beyond what the dependency install already fetched (it does need a JDK on `PATH` for the PySpark tests). It's what CI runs on every push to `main` and on every pull request. `zizmor` audits the GitHub Actions workflows. `zizmor --offline .` (the command above) never touches the network and skips the online audits CI runs, which query the GitHub API (known-vulnerable actions, and a check that each pin's `# vX.Y.Z` comment matches its SHA); `GH_TOKEN=$(gh auth token) zizmor .` reproduces CI's audit exactly. A plain `zizmor .` goes online whenever `GH_TOKEN`, `GITHUB_TOKEN` or `ZIZMOR_GITHUB_TOKEN` is set.

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
# table built before slice 2b (all-cause label, no _gold_metadata.json) or before 2026-10-10 (no label_horizon column) is
# refused by training and the scorer. The results in results.md were measured
# on data/gold/full_run_2b.
python scripts/run_big_join.py --input-dir data/raw/full_run --output-dir data/gold/full_run_2b --reference-date 20260916

# Train + evaluate. --reference-date MUST equal the value used for the Synthea run and the feature join.
# In a store that already holds runs under this experiment name (e.g. the reference runs), pass a fresh
# --experiment-name, or --model becomes ambiguous for the scorer (docs/scoring.md).
python scripts/train_models.py --gold-dir data/gold/full_run_2b --reference-date 20260916 --test-start-date 20230101 --experiment-name readmission-risk-v1

# Browse runs, metrics, calibration diagrams
mlflow ui --backend-store-uri sqlite:///$(pwd)/mlflow/mlflow.db
```

MLflow's autolog also writes in-sample `training_*` metrics into each run; only the `test_*` metrics are results.

The MLflow store records absolute artifact paths, so moving the repository directory invalidates the paths stored in `mlflow/mlflow.db`.

## Cloud project and cost guard (prepared for the scale-up phase)

Nothing runs in the cloud yet; this records the account-level setup done on 2026-10-09 ahead of the scale-up, so it can be reproduced and later adopted by Terraform (`terraform import`). Replace `<BILLING_ACCOUNT_ID>` with your own (`gcloud billing accounts list`).

```sh
gcloud projects create synthea-readmission-risk --labels=purpose=portfolio,repo=synthea-readmission-risk
gcloud billing projects link synthea-readmission-risk --billing-account=<BILLING_ACCOUNT_ID>

# Budget alert: a notification to the billing account's admins, NOT a spend cap. The amount must be in the billing
# account's currency (CHF here, so CHF 20 rather than USD 20).
gcloud services enable billingbudgets.googleapis.com --project=synthea-readmission-risk
gcloud billing budgets create --billing-account=<BILLING_ACCOUNT_ID> --billing-project=synthea-readmission-risk \
  --display-name="synthea-readmission-risk monthly" --budget-amount=20CHF --calendar-period=month \
  --filter-projects=projects/synthea-readmission-risk \
  --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0 \
  --threshold-rule=percent=1.0,basis=forecasted-spend

# Quota check for us-central1 (the chosen region)
gcloud services enable compute.googleapis.com dataproc.googleapis.com --project=synthea-readmission-risk
gcloud compute regions describe us-central1 --project=synthea-readmission-risk --format="table(quotas.metric,quotas.limit,quotas.usage)"
gcloud compute project-info describe --project=synthea-readmission-risk --format="table(quotas.metric,quotas.limit,quotas.usage)"
```

Quotas found on 2026-10-09 (a new project; Google can change defaults): `CPUS` 200 in us-central1, `CPUS_ALL_REGIONS` 32, `DISKS_TOTAL_GB` 4,096, `INSTANCES` 24. A Serverless for Apache Spark batch on the standard tier consumes `CPUS` and `DISKS_TOTAL_GB`; its default footprint is a 4-vCPU driver plus two 4-vCPU executors (12 vCPUs) with 100 GiB of disk per core (1,200 GiB), and it autoscales. The binding limit is therefore `CPUS_ALL_REGIONS` = 32 vCPUs, i.e. a driver plus 7 executors, so the scale-up job should set `spark.dynamicAllocation.maxExecutors=7` rather than let autoscaling run into the quota. No increase was requested; revisit only if the first real run is too slow.

