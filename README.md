# synthea-readmission-risk

Predicts 30-day hospital readmission risk from [Synthea](https://synthea.mitre.org/)-generated synthetic patient data. The pipeline ("The Big Join + The Triage List") joins a patient's encounters, conditions, medications, and procedures into a per-patient-encounter feature table, trains a calibrated logistic-regression baseline alongside an XGBoost challenger, and surfaces a Top-N discharge-risk triage list for a team-lead-style reviewer.

Full architecture rationale lives in [notes/prds/big-join-architecture-lock-in-2026-09-15.md](notes/prds/big-join-architecture-lock-in-2026-09-15.md); the standing architecture summary is in [CLAUDE.md](CLAUDE.md#architecture).

**Data-realism note:** Synthea is a synthetic patient generator, not real-world data. Notable limitations relevant to this project: it codes conditions in SNOMED-CT rather than ICD-10-CM, its disease modules are largely isolated from one another (limited comorbidity interaction), coverage is concentrated in a few dozen well-modeled conditions, it does not model hospital resource capacity, and it lacks the messiness (missing values, coding errors, inconsistent documentation) of real clinical data. Treat model performance here as a demonstration of the pipeline and methodology, not a clinically validated result.

## Status

Staged build, "weeks not months": a **local proving phase** (generate a modest population locally, prove the pipeline correctness-first) precedes a **scale-up phase** (the same code against a larger population on GCP). See [CLAUDE.md](CLAUDE.md#architecture) for the full staging plan.

| Slice | What | Status |
|---|---|---|
| 1 | Local Synthea patient generation | **Done** — see [notes/eg-new-feature/local-synthea-generation-2026-09-16.md](notes/eg-new-feature/local-synthea-generation-2026-09-16.md) |
| 2 | PySpark "Big Join" feature engineering | **Done** — see [notes/eg-new-feature/pyspark-big-join-2026-09-19.md](notes/eg-new-feature/pyspark-big-join-2026-09-19.md) |
| 3 | Calibrated LogisticRegression + XGBoost, tracked in local MLflow | **Done** — see [notes/eg-new-feature/model-training-2026-09-19.md](notes/eg-new-feature/model-training-2026-09-19.md) and [Results and caveats](#results-and-caveats-slice-3) |
| 4 | Typer CLI Top-N triage scoring | Not started |

## Setup

Requires Python 3.12 (pinned via [.python-version](.python-version)).

```sh
# Create and activate the virtualenv
python3.12 -m venv .venv
source .venv/bin/activate

# Install: three separate invocations, since a local editable install can't carry
# pip's --require-hashes guarantee that the lockfiles use.
pip install pip-tools        # one-time, unpinned bootstrap so pip-compile is available
pip install -e .
pip install --require-hashes -r requirements.txt -r requirements-linux-extras.txt
pip install --require-hashes -r requirements-dev.txt
```

macOS only: XGBoost needs the OpenMP runtime — `brew install libomp`. A JDK 17+ must be on `PATH` for PySpark's local mode (the Big Join and its tests).

`requirements-linux-extras.txt` holds the few dependencies `pip-compile` cannot see when run on macOS (they are Linux-only); each is gated by an environment marker, so pip skips them on macOS and the same command works on both platforms. `pandas` and `numpy` are deliberately pinned below their newest releases (PySpark's `pyspark.pandas` cannot import under pandas 3) — see the comment in [requirements.in](requirements.in).

## Verify the code works

```sh
ruff check .
pytest
```

This is the fast path to confirm the codebase is sound — no external downloads and no generated data (it does need a JDK on `PATH` for the PySpark tests). It's what CI runs on every push.

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

## Training the models (Slice 3)

Trains a `LogisticRegression` baseline and a calibrated `XGBClassifier` challenger on the gold table from slice 2, scores a chronological, patient-disjoint held-out window once, and tracks everything in a local SQLite-backed MLflow store (`mlflow/`, gitignored).

```sh
# Prerequisite: the gold table (slice 2)
python scripts/run_big_join.py --input-dir data/raw/full_run --output-dir data/gold/full_run --reference-date 20260916

# Train + evaluate. --reference-date MUST equal the value used for the Synthea run and Big Join.
python scripts/train_models.py --gold-dir data/gold/full_run --reference-date 20260916 --test-start-date 20230101

# Browse runs, metrics, calibration diagrams
mlflow ui --backend-store-uri sqlite:///$(pwd)/mlflow/mlflow.db
```

MLflow's autolog also writes in-sample `training_*` metrics into each run; only the `test_*` metrics are results.

The MLflow store records absolute artifact paths, so moving the repository directory invalidates the paths stored in `mlflow/mlflow.db`.

### Results and caveats (Slice 3)

Measured on the real gold table (10,000-patient Synthea population, `--test-start-date 20230101`): 9,631 training rows / 1,233 positives, 2,076 test rows / 291 positives; 95% patient-clustered bootstrap intervals in brackets.

| Model | ROC-AUC | Brier score | Expected calibration error |
|---|---|---|---|
| Logistic regression | 0.934 [0.902, 0.958] | 0.0628 [0.0482, 0.0778] | 0.025 [0.015, 0.039] |
| Calibrated XGBoost | 0.926 [0.892, 0.951] | 0.0612 [0.0454, 0.0778] | 0.044 [0.030, 0.059] |
| Reason-code-only lookup (reference, no model; `readmission_risk.models.baselines.reason_code_lookup`) | 0.933 | 0.0670 | — |

- **These are not virgin-holdout numbers.** During design, throwaway prototypes already scored these models on exactly this test window, and the cutoff date, the feature exclusions and the training-row choice were all made after looking at this data. Read them as development-set-quality estimates. The planned untouched evaluation is the larger v2 population, to be generated with a new seed.
- **The reference row is a point estimate, reproducible by hand** (it is not a tracked MLflow run and has no interval of its own; the paired model-versus-lookup intervals quoted below come from the recipe at the end of this snippet — a manual real-data check, not a CI test):
  ```python
  from datetime import date; from pathlib import Path
  from readmission_risk.models.baselines import reason_code_lookup
  from readmission_risk.models.data import load_gold_table
  from readmission_risk.models.evaluate import evaluate_probabilities
  from readmission_risk.models.split import chronological_group_split
  sp = chronological_group_split(load_gold_table(Path("data/gold/full_run")), reference_date=date(2026, 9, 16), test_start_date=date(2023, 1, 1))
  y_tr, y_te = sp.train["is_readmitted"].to_numpy(), sp.test["is_readmitted"].to_numpy()
  p = reason_code_lookup(sp.train["admission_reason_code"], y_tr, sp.test["admission_reason_code"])
  print(evaluate_probabilities(y_te, p, train_prevalence=float(y_tr.mean())).metrics)  # roc_auc 0.9329, brier_score 0.0670

  # paired model-vs-lookup intervals: reload a logged model (run_id is printed by scripts/train_models.py)
  from readmission_risk.models.data import prepare_features
  from readmission_risk.models.evaluate import clustered_bootstrap
  from readmission_risk.models.tracking import load_logged_model
  p_lr = load_logged_model(Path("mlflow"), "<logistic_regression run_id>").predict_proba(prepare_features(sp.test))[:, 1]
  r = clustered_bootstrap(y_te, {"lookup": p, "logistic_regression": p_lr}, sp.test["patient_id"].to_numpy(), n_bootstrap=500, seed=42)
  print(r.paired_differences["logistic_regression_minus_lookup"])  # brier_score (-0.0062, -0.0022), roc_auc (-0.0041, 0.0072), ...
  ```
- **Training-window sensitivity — the default run trains on many structurally different rows.** `--train-start-date` defaults to unset, so 7,389 of the 9,631 training rows (77%) predate 2017-09-16 (= 2016-09-16, the start of Synthea's 10-year export window, plus the 1-year feature lookback): 6,933 of them lie outside the export window, where Synthea kept only a sparse, non-random subset of old encounters, and 456 lie in the lookback "dead zone". Their features differ from the test window's — mean `prior_inpatient_count` 2.68 versus 1.43, mean `active_condition_count` 14.1 versus 17.0 — and their positive rate is 13.6% versus 10.1% for the training rows from 2017-09-16 on (why is not established; the lung-cancer treatment cycles are one untested candidate). The test window contains none of these rows. Re-running with `--train-start-date 20170916` (2,242 rows / 227 positives; same test window) gives: logistic regression AUC 0.927 [0.895, 0.952], Brier 0.0616; calibrated XGBoost AUC 0.901 [0.860, 0.930], Brier 0.0685. XGBoost is then clearly worse than logistic regression on AUC (paired difference −0.026 [−0.046, −0.011]) and Brier (+0.007 [+0.003, +0.011]); ECE is inconclusive (−0.003 [−0.008, +0.011]), while XGBoost has a significantly smaller absolute calibration-in-the-large gap (−0.021 [−0.028, −0.002]). So "the models are indistinguishable" holds only under the default training-window choice, which was a deliberate decision to keep the larger training set.
- **No clear winner (default run).** The paired differences (XGBoost minus logistic regression) are inconclusive on Brier score and ROC-AUC; ECE favours logistic regression while the calibration-in-the-large gap is inconclusive. Against the reason-code-only lookup, neither model gains anything on ROC-AUC (paired 95% intervals of the difference: LR [−0.004, +0.007], XGBoost [−0.015, +0.002], both include 0), but both are measurably better on Brier score (LR −0.0042, interval [−0.0062, −0.0022]; XGBoost −0.0058, interval [−0.0091, −0.0026]) and on PR-AUC (0.742 / 0.755 versus 0.705, point estimates), and LR is also better calibrated (ECE difference [−0.016, −0.004]) — small gains in absolute terms, on top of a lookup that already does most of the work.
- **Most of the signal is Synthea's scripted care pathways, not clinical risk.** Two lung-cancer admission reasons account for 72.6% of all positives; the readmitting stay has the same reason 98.5–99.8% of the time, arriving 24.5–28.8 days after discharge (fixed-interval treatment cycles just inside the 30-day window). The label counts these planned readmissions (a follow-up in the slice-2 design). Treat model performance as a demonstration of the pipeline and methodology, not a clinically validated result.
