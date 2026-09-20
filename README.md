# synthea-readmission-risk

Predicts **unplanned** 30-day hospital readmission risk from [Synthea](https://synthea.mitre.org/)-generated synthetic patient data. The pipeline ("The Big Join + The Triage List") joins a patient's encounters, conditions, medications, and procedures into a per-patient-encounter feature table, trains a calibrated logistic-regression baseline alongside an XGBoost challenger, and surfaces a Top-N discharge-risk triage list for a team-lead-style reviewer.

Full architecture rationale lives in [notes/prds/big-join-architecture-lock-in-2026-09-15.md](notes/prds/big-join-architecture-lock-in-2026-09-15.md); the standing architecture summary is in [CLAUDE.md](CLAUDE.md#architecture).

**Data-realism note:** Synthea is a synthetic patient generator, not real-world data. Notable limitations relevant to this project: it codes conditions in SNOMED-CT rather than ICD-10-CM, its disease modules are largely isolated from one another (limited comorbidity interaction), coverage is concentrated in a few dozen well-modeled conditions, it does not model hospital resource capacity, and it lacks the messiness (missing values, coding errors, inconsistent documentation) of real clinical data. Treat model performance here as a demonstration of the pipeline and methodology, not a clinically validated result.

## Status

Staged build, "weeks not months": a **local proving phase** (generate a modest population locally, prove the pipeline correctness-first) precedes a **scale-up phase** (the same code against a larger population on GCP). See [CLAUDE.md](CLAUDE.md#architecture) for the full staging plan.

| Slice | What | Status |
|---|---|---|
| 1 | Local Synthea patient generation | **Done** — see [notes/eg-new-feature/local-synthea-generation-2026-09-16.md](notes/eg-new-feature/local-synthea-generation-2026-09-16.md) |
| 2 | PySpark "Big Join" feature engineering | **Done** — see [notes/eg-new-feature/pyspark-big-join-2026-09-19.md](notes/eg-new-feature/pyspark-big-join-2026-09-19.md) |
| 2b | Unplanned-readmission label (planned/scripted readmissions excluded) + gold-table provenance (`_gold_metadata.json`) | **Done** — see [notes/eg-new-feature/readmission-label-planned-exclusion-2026-09-20.md](notes/eg-new-feature/readmission-label-planned-exclusion-2026-09-20.md) |
| 3 | Calibrated LogisticRegression + XGBoost, tracked in local MLflow | **Done** — see [notes/eg-new-feature/model-training-2026-09-19.md](notes/eg-new-feature/model-training-2026-09-19.md) and [Results and caveats](#results-and-caveats-slice-3) |
| 4 | Typer CLI Top-N triage scoring (a demo over held-out gold rows) | **Done** — see [notes/eg-new-feature/batch-scoring-cli-2026-09-20.md](notes/eg-new-feature/batch-scoring-cli-2026-09-20.md) and [Batch scoring](#batch-scoring-slice-4-top-n-triage-list) |

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
# Prerequisite: the gold table (slice 2 + 2b). Build into a FRESH --output-dir: the script refuses an existing one, and a gold
# table built before slice 2b (all-cause label, no _gold_metadata.json) is refused by training. The results below were measured
# on data/gold/full_run_2b.
python scripts/run_big_join.py --input-dir data/raw/full_run --output-dir data/gold/full_run_2b --reference-date 20260916

# Train + evaluate. --reference-date MUST equal the value used for the Synthea run and Big Join.
python scripts/train_models.py --gold-dir data/gold/full_run_2b --reference-date 20260916 --test-start-date 20230101 --experiment-name readmission-risk-2b

# Browse runs, metrics, calibration diagrams
mlflow ui --backend-store-uri sqlite:///$(pwd)/mlflow/mlflow.db
```

MLflow's autolog also writes in-sample `training_*` metrics into each run; only the `test_*` metrics are results.

The MLflow store records absolute artifact paths, so moving the repository directory invalidates the paths stored in `mlflow/mlflow.db`.

### Results and caveats (Slice 3, on the slice-2b gold table)

Measured on `data/gold/full_run_2b` (10,000-patient Synthea population; **unplanned**-readmission label from slice 2b — see [notes/eg-new-feature/readmission-label-planned-exclusion-2026-09-20.md](notes/eg-new-feature/readmission-label-planned-exclusion-2026-09-20.md); `--test-start-date 20230101`): 6,391 training rows / 96 positives (1.50%), 1,539 test rows / **41 positives (2.66%)**; 95% patient-clustered bootstrap intervals in brackets. Every figure here (and the recipe to re-derive it) is in [notes/eg-new-feature/readmission-label-planned-exclusion-2026-09-20-measurements.md](notes/eg-new-feature/readmission-label-planned-exclusion-2026-09-20-measurements.md).

| Model | ROC-AUC | Brier score | Expected calibration error |
|---|---|---|---|
| Logistic regression | 0.869 [0.820, 0.919] | 0.0255 [0.0184, 0.0340] | 0.013 [0.008, 0.023] |
| Calibrated XGBoost | 0.823 [0.751, 0.884] | 0.0257 [0.0181, 0.0347] | 0.018 [0.013, 0.029] |
| Reason-code-only lookup (reference, no model; `readmission_risk.models.baselines.reason_code_lookup`) | 0.873 | 0.0252 | 0.012 |

- **The result is weak, and small-sample.** The test window holds 41 positives, below slice 3's own 50-positive reliability threshold, so `train_models.py` prints its "metrics and intervals are unreliable" warning on this run (expected; the threshold was deliberately not lowered) and the intervals are wide (LR's AUC interval spans 0.10). Brier skill scores against the training prevalence are 0.022 (LR) and 0.014 (XGBoost): barely better than always predicting the base rate.
- **Neither model beats the reason-code lookup.** Paired differences against the lookup: logistic regression — ROC-AUC [−0.036, +0.031], Brier [−0.0004, +0.0011], ECE [−0.002, +0.006], all including 0; calibrated XGBoost — ROC-AUC [−0.094, −0.011] and ECE [+0.002, +0.013] (both *worse*), Brier [−0.0009, +0.0020] (inconclusive). Between the two models, XGBoost is worse than logistic regression on ROC-AUC (paired −0.047 [−0.092, −0.010]) and on ECE (+0.0048 [0.0018, 0.0091]); Brier is inconclusive (+0.0002 [−0.0009, +0.0013]).
- **These are not virgin-holdout numbers.** During design, throwaway prototypes scored these models on exactly this test window, and the cutoff date, the feature exclusions, the training-row choice and (now) the label rule were all decided after looking at this data. Read them as development-set-quality estimates. The planned untouched evaluation is the larger v2 population with a new seed; at a ~1.6% positive rate it must be sized for statistical power, not just freshness.
- **What to expect from the larger (100k–200k patients) v2 population — two different things.** *Measurement precision will improve for sure:* 10–20× the patients means roughly 1,000–2,000 training and 400–800 test positives at the current rate (arithmetic, not a measurement), which removes the 50-positive reliability warning and narrows the intervals about 3–4×, so the models can finally be told apart from the lookup. *Model performance is expected to improve only up to a ceiling set by the admission reason.* In a learning-curve check on the current data the lookup is already saturated (test ROC-AUC 0.863 → 0.871 → 0.873 at 25/50/100% of the training data), while logistic regression (0.764 → 0.837 → 0.869) and XGBoost (0.692 → 0.753 → 0.808) are still climbing because they must learn the reason table from very few positives; expect them to catch up to the lookup and XGBoost's deficit to shrink. Beyond that, within CABG-history and CHF no patient feature separates readmitted from non-readmitted stays (per-feature ROC-AUC 0.44–0.46 and 0.56–0.58), consistent with Synthea generating these events from scripted disease-module pathways plus random draws, so more patients should not add signal the simulator never encoded. Expect all three approaches to land around 0.87–0.90 with the models at or slightly above the lookup, not far above it (an expectation, not a measurement; the aortic-valve reasons look informative within reason but on only 68 and 27 rows and probably reflect care-pathway stage). Recipe: the measurements note.
- **The positive rate shifts between partitions (1.50% train vs 2.66% test)**, and both models under-predict on the test window (calibration gap −0.010 LR, −0.012 XGBoost: mean prediction ≈ 1.7% against 2.66% observed). That is what a prevalence shift looks like; it should not be read as a model defect on its own.
- **The label is much cleaner than before but still reason-concentrated.** Slice 2b removed the scripted lung-cancer chemotherapy cycles (in the pre-2b all-cause label they were 72.6% of the positives, with the same reason on the readmitting stay in 98.5% / 99.8% of cases arriving 28.8 ± 1.9 / 24.5 ± 2.1 days after discharge) and the overlapping-stay fragments of surgical episodes. What remains, 151 positives: CABG-history 45, CHF 30, aortic stenosis 20, aortic regurgitation 17, and so on; in the **test window CABG-history alone is 23 of 41 (56%)** — same-reason repeats a median 16.6 days after discharge. On inspection they are fixed 24-hour post-operative stays that carry a generic procedure bundle (discharge, post-op education, stair-climbing and walking tests) but no chemotherapy/radiation code, and only 1 of the 45 readmitting stays begins through the emergency department (versus 27 of 30 for CHF), so they look like a scripted pathway rather than deterioration; the rule has no planned-procedure marker with which to exclude them. That is why a reason-code lookup is still hard to beat. Whether to iterate the label further is an open decision: the design note's "Label v2" section measures four candidate stricter definitions, and at this cohort size none leaves enough test positives to evaluate (at most 18, mostly 9–12), so adoption is deferred to the larger population.
- **The reference row is a point estimate, reproducible by hand** (it is not a tracked MLflow run and has no interval of its own; the paired intervals above come from the recipe at the end of this snippet, run once per model pair, a manual real-data check rather than a CI test):
  ```python
  from datetime import date; from pathlib import Path
  from readmission_risk.models.baselines import reason_code_lookup
  from readmission_risk.models.data import load_gold_table
  from readmission_risk.models.evaluate import evaluate_probabilities
  from readmission_risk.models.split import chronological_group_split
  sp = chronological_group_split(load_gold_table(Path("data/gold/full_run_2b")), reference_date=date(2026, 9, 16), test_start_date=date(2023, 1, 1))
  y_tr, y_te = sp.train["is_readmitted"].to_numpy(), sp.test["is_readmitted"].to_numpy()
  p = reason_code_lookup(sp.train["admission_reason_code"], y_tr, sp.test["admission_reason_code"])
  print(evaluate_probabilities(y_te, p, train_prevalence=float(y_tr.mean())).metrics)  # roc_auc 0.8726, brier_score 0.0252

  # paired model-vs-lookup intervals: reload a logged model (run_id is printed by scripts/train_models.py); clustered_bootstrap
  # yields paired differences only for EXACTLY two models, so run it once per pair
  from readmission_risk.models.data import prepare_features
  from readmission_risk.models.evaluate import clustered_bootstrap
  from readmission_risk.models.tracking import load_logged_model
  p_lr = load_logged_model(Path("mlflow"), "<logistic_regression run_id>").predict_proba(prepare_features(sp.test))[:, 1]
  r = clustered_bootstrap(y_te, {"lookup": p, "logistic_regression": p_lr}, sp.test["patient_id"].to_numpy(), n_bootstrap=500, seed=42)
  print(r.paired_differences["logistic_regression_minus_lookup"])  # roc_auc (-0.036, 0.031), brier_score (-0.0004, 0.0011), ...
  ```
- **Training-window sensitivity (`--train-start-date 20170916`, run under its own experiment `readmission-risk-2b-sensitivity`) rests on only 24 training positives.** It drops 4,715 of the 6,391 default training rows (74%) that predate 2017-09-16, leaving 1,676 rows / 24 positives (grouped calibration folds hold 8, 3, 7, 2 and 4 positives), against the same 41 test positives. Their positive rates before and after the cutoff are similar (72 of 4,715 = 1.53% versus 24 of 1,676 = 1.43%). Logistic regression: ROC-AUC 0.860 [0.805, 0.907], Brier 0.0246; XGBoost: ROC-AUC 0.778 [0.700, 0.844], Brier 0.0255; XGBoost is worse on ROC-AUC (paired −0.082 [−0.144, −0.037]) and ECE (+0.0068 [0.0011, 0.0095]), Brier inconclusive. Given 24 positives, treat this as a direction, not an estimate. (The pre-2b README's claims about feature means and the outside-window/dead-zone breakdown of those training rows were not re-derived for the new label and have been removed.)
- **A thin leakage margin, checked and documented.** The new label depends on the *content* of the readmitting stay and on earlier stays' end times, not only on when it started. Measured on this data: of the 6,391 training rows, 118 have any in-window stay, and none has a classifying record dated on or after the test start; the latest is 2022-12-25T01:59:59Z, about 6 days before it. A second, related property (cohort membership) also holds (0 of 470 candidates dropped because of a stay ending on or after the test start). Both are properties of this dataset, not guarantees; tightening the purge is a named follow-up.
- **Provenance is enforced.** The gold table ships a `_gold_metadata.json`; training refuses a table without it (e.g. one built before slice 2b) or one whose `reference_date` / `readmission_window_days` differ from the training config. Treat model performance as a demonstration of the pipeline and methodology, not a clinically validated result.

## Batch scoring (Slice 4: Top-N triage list)

`scripts/score_discharges.py` ranks discharges by predicted risk and prints the Top-N. **It is a demo, not as-of scoring:** it scores discharges that already exist in the gold table, restricted to the rows the chosen model's own MLflow run recorded as its held-out `test` partition (`split_assignment.csv`), never the rows it was trained on. Building features for unlabelled recent discharges is a named follow-up. The design, its measurements and its 5-round design check are in [notes/eg-new-feature/batch-scoring-cli-2026-09-20.md](notes/eg-new-feature/batch-scoring-cli-2026-09-20.md).

```sh
# Score the held-out discharges of the 30 days ending 2025-06-30 with the logistic-regression baseline (the default)
python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --experiment-name readmission-risk-2b --run-date 20250630

# The other model, or an explicit run; add --show-observed-outcomes for a sanity check against what actually happened
python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --experiment-name readmission-risk-2b --model xgboost_calibrated --run-date 20250630
python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --run-id 2bb281f6ab4e47c588eea078cc712201 --run-date 20250630 --show-observed-outcomes
```

| Flag | Meaning |
|---|---|
| `--gold-dir`, `--run-date` (required) | the gold table (with `_gold_metadata.json`) and the last day (`YYYYMMDD`, UTC) of the window |
| `--window-days` | window length in UTC calendar days; default the gold table's readmission window (30). A row is in the window iff `run_date - (W-1) days 00:00Z <= index_stop < run_date + 1 day 00:00Z` |
| `--model` / `--run-id` | `logistic_regression` (default) or `xgboost_calibrated`, resolved to the single FINISHED run of that model in `--experiment-name`; or an explicit run id. Mutually exclusive |
| `--experiment-name` | default `readmission-risk` (the trainer's default); the real runs are in `readmission-risk-2b`. Ignored with `--run-id` |
| `--top-n` | how many rows to print (default 10); the whole ranked batch is written regardless |
| `--show-observed-outcomes` | opt-in: prints how the observed readmissions line up with the Top-N, joined after scoring and never written to the file |
| `--output-dir`, `--tracking-dir` | defaults `data/predictions` (gitignored) and `mlflow` |

**Output.** `<output-dir>/run_date=YYYYMMDD/predictions.parquet`, one file per run date, replaced as a unit (temp file + atomic rename): re-running a run date overwrites the whole partition, so scoring the same date with the other model replaces the first model's file. Columns, in order: `as_of_date`, `rank` (1 = highest risk; ties in `risk_score` are broken by `encounter_id` ascending, so ranks are always distinct), `is_top_n`, `patient_id`, `encounter_id`, `discharge_time` (UTC), `admission_reason_code`, `admission_reason_description`, `risk_score`, `model_run_id`, `model_name`, `top_n`, `scoring_window_days` (the trailing-window length; not the 30-day label horizon), `partition` (always `test`), `label_definition`, `gold_fingerprint`, `gold_reference_date`. There is no label column. The demo notice is stored in the file's schema metadata. Reading the parent directory (`pd.read_parquet("data/predictions")`) works and adds a categorical `run_date` column.

**What is refused (exit 1, `ERROR:` on stderr, nothing written).** A window that contains any discharge that is not a held-out row of the chosen model (a training row, a row dropped by the split, or the one censoring-buffer row) is refused rather than silently truncated. So is a window that reaches past the **observation horizon** (the reference date minus the 30-day label window: 2026-08-18 on the real table): the gold table keeps a non-readmitted discharge only once its outcome window has closed, so later windows would hold readmitted discharges only, and no comparison against the rows that do exist could notice the rest are missing. On the real table with a 30-day window that leaves 1,287 valid run dates, `20230208` through `20260817` (batches of 21-49 rows, median 35). Before any model is loaded, 13 guards check the run against the gold table: it must be a finished, non-deleted `train_and_evaluate` run, trained on **exactly this gold table** (fingerprint) and label definition, with matching `reference_date` / `readmission_window_days`, the same library versions as the lockfile (the model is a cloudpickle), the current feature contract and signature, a consistent train/test assignment, and a model that belongs to the run. `resolve_run_id` also refuses when an experiment holds more than one FINISHED run for a model, so re-running `train_models.py` under an existing experiment name makes `--model` ambiguous: use a fresh `--experiment-name` per training run, or pass `--run-id`.

**Read the list with these limits in mind** (the CLI prints the first one on every run):
- **Weak models, development-set rows.** The models beat neither a reason-code-only lookup nor each other convincingly (see Results and caveats above), so the Top-N mostly ranks by admission reason: on the real `20250630` batch of 42, the logistic-regression Top-10 is six CABG-history stays, two NSTEMI and two drug-abuse admissions (`risk_score` 0.0816 down to 0.0149). The 2023+ test window was used while designing the models and the label; it is not a virgin holdout. Both models under-predict on average, so read `risk_score` as a ranking, not an absolute risk.
- **Membership is not as-of.** Which gold rows exist depends on whole-export information (the terminal rule and the death/censoring keep-rule), so a real as-of list would contain rows this one omits, notably patients who died within 30 days.
- **Simulated run date.** Patients already readmitted before the run date still appear, and the same discharge appears in up to W consecutive run-date partitions.
- **`--show-observed-outcomes` counts are tiny** (the real batch has 1 readmission in 42 discharges): they are a sanity check, not evidence.
- The notice text is specific to this dataset and must be revisited before the untouched v2 population or real as-of scoring is used with this scorer.

