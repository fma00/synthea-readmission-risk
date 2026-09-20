# DESIGN DOC — Calibrated Model Training + MLflow Tracking (Slice 3 of 4)

**Date:** 2026-09-19
**Workflow:** `/eg-new-feature` (elephant/goldfish)
**Parent architecture:** `notes/prds/big-join-architecture-lock-in-2026-09-15.md` and CLAUDE.md's "Architecture" + "MLOps spine" sections (locked spec — not reopened here)
**Input produced by:** slice 2 (`notes/eg-new-feature/pyspark-big-join-2026-09-19.md`) — the gold table at `data/gold/full_run/`
**Precedes:** slice 4 (Typer batch-scoring CLI), which will load this slice's MLflow-logged models
**Decisions made interactively with the user before drafting (all three grounded in measurements taken against the real gold table this session):**
1. `organization_utilization` is **excluded** as a feature.
2. Chronological test window starts **2023-01-01**.
3. Training uses **all** pre-cutoff rows (including the pre-window legacy tail), with an optional `--train-start-date` flag for a clean-period-only sensitivity check.

**Revision note (this paragraph describes the FIRST revision only; later rounds are in the revision log at the bottom):** round 1 of the goldfish design check returned comprehension `passed`, critic `design needs revision` (15 gaps), readiness `implementation not ready` (10 questions). Every claim that could be checked was re-verified empirically before revising (the `.gitignore` collision, the Linux-only lockfile dependencies, the exact split counts, and the reason-code-only AUC — all confirmed; see the revision log at the bottom for each gap's disposition). The largest changes: the `.gitignore` pattern is fixed; the dependency-lock strategy is rewritten for Linux CI; model logging is made explicit instead of relying on autolog; the AUC "leakage band" is removed and replaced by a canary + reference-baseline check; bootstrap intervals are extended and paired.

## Why

Executes the third stage of the architecture: train a `LogisticRegression` baseline and a calibrated `XGBClassifier` challenger on the gold table, evaluate both on calibration (curve + Brier) alongside AUC, and track everything in local MLflow. CLAUDE.md locks three requirements this slice must satisfy and does not reopen: (1) both models report calibrated probabilities, evaluated on calibration curve + Brier score, not accuracy/AUC alone; (2) MLflow with a local file-store/SQLite backend, `mlflow.set_tracking_uri()` called explicitly, `mlflow.autolog()` covering both models in one call; (3) the split is chronological (by `index_start`) AND grouped by `patient_id`, never a random row-level split.

It also discharges the risk slice 2's design doc explicitly handed to this slice (`pyspark-big-join-2026-09-19.md`, Data handling, "Known selection-bias interaction for slice 3's chronological split"): slice 2's label-exclusion policy drops censored *negatives* near the end of the observation window but never drops censored *positives*, so the most recent slice of the timeline — exactly where a chronological test set lives — has a depleted-negatives / inflated-positive-rate bias. **Measured against the real gold table this session (not assumed):** the affected zone is `index_stop >= 2026-08-18` (`reference_date + 1 day − 30 days`); it holds only **2 of 13,222 rows, both positives** (exactly the "censored positives slice 2 keeps" the carried-forward risk describes; every censored negative in that zone was already dropped by slice 2), so the magnitude at this data scale is tiny (~0.015% of rows), but the treatment below is still applied because it is cheap, principled, and would matter more at other scales.

### Real-data findings (measured this session against `data/gold/full_run`, 13,222 rows, 5,508 patients) that shaped this design

- **Positive rate 12.15%** (matches the task brief). Per-year positive rate is noisy at 7–17% with no monotone trend inside the dense period.
- **Timeline shape:** `index_start` spans 1923–2026, but only ~5.1k rows (39%) fall in the fully covered period from 2017-09-16 on (`reference_date` 2026-09-16 − 10y `years_of_history` + 1y lookback). **7,531 rows (57%) predate 2016-09-16** — Synthea kept a sparse, non-random subset of old inpatient encounters outside its own export window. Those rows differ structurally (mean `prior_inpatient_count` 2.5 vs 1.38 in the clean period).
- **Patient overlap across a chronological cutoff is large:** at a 2023-01-01 cutoff, 708 patients have rows on both sides. A chronological-only split would put the same patients in train and test.
- **`provider_specialty` is a constant** (`GENERAL PRACTICE` on all 13,222 rows) — zero information.
- **`organization_utilization` is Synthea's whole-simulation running total per organization** (slice 2's own doc already calls it "not point-in-time correct"): it embeds post-index information (including the test period and the very readmission being predicted), and has only 151 distinct values, i.e. it functions as an organization identifier plus future information.
- **`admission_reason_code` is null in 19% of rows and those rows have a 0.3% positive rate** (vs 14.9% when non-null): missingness is itself highly informative and must be encoded, not imputed away. Some reason codes are Synthea-module artifacts (e.g. small-cell lung cancer index admissions: 91% "readmitted" — almost certainly scheduled treatment cycles, i.e. *planned* readmissions, which slice 2 deliberately does not exclude). This affects how AUC should be interpreted (see Data handling). **Measured (round-2 verification, exact train/test partitions from the algorithm below): a lookup table of per-reason-code readmission rates learned on the training partition and scored on the test window, using NO other feature, already reaches test ROC-AUC 0.933; adding `payer_ownership` reaches 0.934.** A model landing in the 0.9s is therefore expected on this data and is NOT evidence of leakage; the meaningful question is what the models add beyond the reason-code baseline.
- **`marital_status` is null in 6.5% of rows.**
- **Dependencies:** scikit-learn, XGBoost, MLflow, pandas, pyarrow, matplotlib and numpy are NOT installed or pinned (only `pyspark==4.1.3`). A scratch `pip-compile --generate-hashes` run (outside the repo, not committed) confirmed the following resolve together on Python 3.12 today: `scikit-learn==1.9.1`, `xgboost==3.4.1`, `mlflow==3.16.1` (pulls in `mlflow-skinny`/`mlflow-tracing`), `pandas==3.0.6`, `pyarrow==25.0.1`, `matplotlib==3.11.2`, `numpy==2.5.3` — **but the final pins for pandas and numpy are `pandas==2.3.3` and `numpy==2.4.4`, see the compatibility finding below** (user decision, 2026-09-19). **That run is a metadata-level check only** — it does not prove these versions import and work together (XGBoost has previously broken against new scikit-learn releases; MLflow's sklearn autolog skips unsupported versions), hence the smoke gate (UX flow step 2) below. XGBoost on macOS needs `libomp` (present on this dev machine at `/opt/homebrew/opt/libomp`).
- **pandas 3 breaks an existing slice-2 test — round-4 critic finding, verified in a scratch environment with the doc's exact pins (**reproduced independently by the round-5 critic** in a scratch environment: `pyspark.pandas` raises the same `ImportError` under `pandas==3.0.6`; the elephant itself could not, since pandas is not installed in the repo venv).** `pyspark.testing.assertDataFrameEqual` — the DataFrame-equality tool CLAUDE.md names, used by slice 2's end-to-end test (`tests/pipeline/test_big_join.py:671` and `:674`, one test) — refers to `pyspark.pandas` (confirmed in the installed `pyspark/testing/utils.py`), and under `pandas==3.0.6` that import raises `ImportError: cannot import name '_builtin_table' from 'pandas.core.common'`: the scratch run showed `pytest tests` at 1 failed / 44 passed. PySpark 4.1.3 AND 4.2.0 both declare only a floor (`pandas>=2.2.0`, no ceiling), so pip gives no warning. With `pandas==2.3.3` all existing tests pass and the model prototype gives identical metrics and an identical data fingerprint; `numpy` is stepped down to `2.4.4` because `numpy==2.5.3` with pandas 2.3.3 emits a `DeprecationWarning` ("generic unit for NumPy timedelta ... will raise an error in the future") on every `pd.Timedelta(...)` construction, including `length_of_stay_days`; `pip check` is clean and all tests pass at 2.4.4. **Decision (user, 2026-09-19): downgrade to `pandas==2.3.3` + `numpy==2.4.4`, keep slice 2 untouched, and keep a to-do to CHECK LATER WHETHER UPGRADING IS POSSIBLE** (re-test when PySpark's `pyspark.pandas` supports pandas 3 — see Out-of-scope follow-ups; the same to-do is recorded next to the pins in `requirements.in` and in the CLAUDE.md update). New code is written forward-compatibly so the upgrade is cheap: no chained assignment, no in-place mutation of caller frames, explicit `object` dtype for categoricals.
- **The lockfile is compiled on macOS/arm64 but consumed by Linux/x86_64 CI under `--require-hashes` — verified to be a real problem, not a hypothetical.** `pip-compile` evaluates environment markers for the host only. Checked against PyPI metadata for every pinned package in the scratch lockfile: on Linux/x86_64, `xgboost==3.4.1` additionally requires `nvidia-nccl-cu13` (marker `platform_system == "Linux"`) and `sqlalchemy==2.0.54` additionally requires `greenlet` (marker includes `x86_64`; on Apple Silicon the host is `arm64` so it is not pulled in). Neither appears in a macOS-compiled lockfile, and in `--require-hashes` mode pip refuses to install any dependency that is not itself pinned and hashed — so CI's install step would fail. Also verified empirically: a Linux-marked requirement written into `requirements.in` is **silently dropped** by `pip-compile` on macOS (and `pip index versions nvidia-nccl-cu13` returns nothing on macOS: no mac wheel exists), so marker lines in `.in` cannot fix it; and `xgboost-cpu` is not an alternative (its 3.4.1 release publishes Linux/Windows wheels and an sdist only — no macOS wheel, which is what rules it out for the macOS dev machine even though CI would benefit). **Known cost accepted:** XGBoost pulls the ~252 MB Linux-only `nvidia-nccl-cu13` wheel unconditionally, and `ci.yml` has no pip cache, so every CI run downloads it; adding `cache: "pip"` to the `actions/setup-python` step is a named follow-up, not done here. The fix is in Scope and Surfaces touched (a small hand-maintained `requirements-linux-extras.txt`).
- **Docker Desktop is installed on this machine but its daemon is not running** (checked). A Linux dry-run of the lockfile therefore cannot be done unless the user starts Docker; if it is not started, that verification is reported as NOT performed rather than skipped silently.

## Scope

**In:**

- New package `src/readmission_risk/models/` (per CLAUDE.md's `src/` layout: `models/` is the stage sub-module) with the modules listed in Surfaces touched.
- **Loading** the gold Parquet directory with pandas/pyarrow (no Spark, no JVM at training time). The loader is a single function so the later BigQuery `to_dataframe()` source (scale-up phase) is a one-function swap.
- **Feature set** (16 features): the 8 lookback counts, `age_at_index_years`, a derived `length_of_stay_days`, and 6 categoricals (`admission_reason_code`, `payer_ownership`, `gender`, `race`, `ethnicity`, `marital_status`). **Prediction point is index discharge** (`index_stop`): everything about the index stay itself is legitimately known at that moment, which is why `length_of_stay_days = (index_stop − index_start)` in days is allowed (it is LACE's "L" and the only place the model uses `index_stop`).
- **Explicitly excluded columns**, each with a recorded reason (see `EXCLUDED_COLUMN_REASONS` in Interfaces): `patient_id`/`encounter_id` (identifiers), `index_start`/`index_stop` (split keys; only `length_of_stay_days` derived from them is a feature — the raw timestamps would encode calendar time and cannot extrapolate across a chronological split), `admission_reason_description` (redundant with the code), `provider_specialty` (constant), `organization_utilization` (future-informative, user decision above).
- **Split** (`chronological_group_split`): chronological AND patient-disjoint, with a censoring buffer and a label-window purge. Full algorithm in Interfaces.
- **Models:** `LogisticRegression` baseline (natively probabilistic, no reweighting, so no calibration wrapper) and `CalibratedClassifierCV(XGBClassifier pipeline)` challenger with patient-grouped inner CV folds. **No class reweighting or resampling for either model**: at a 12% positive rate it is unnecessary, and it would distort predicted probabilities, which this project ranks above raw discrimination.
- **Evaluation on the held-out test window only:** ROC-AUC, PR-AUC (average precision), Brier score, Brier skill score vs a constant-train-prevalence predictor, log loss, expected calibration error (ECE), mean predicted probability vs observed prevalence (calibration-in-the-large, reported as `calibration_gap = mean_predicted − observed`), and a 10-bin quantile calibration curve. **Patient-clustered bootstrap 95% confidence intervals for ROC-AUC, Brier score, ECE, the signed `calibration_gap` and `abs_calibration_gap`, for each model, plus PAIRED intervals for the difference between the two models on the same metrics** (both models are scored on identical rows and patients, so the paired difference is the correct test; two separate intervals overlapping is a conservative and misleading comparison rule). Calibration is the project's stated primary comparison, so it gets uncertainty estimates too, not just discrimination.
- **MLflow tracking (local SQLite backend):** `mlflow.set_tracking_uri()` called explicitly, ONE `mlflow.autolog(...)` call before both fits (covering params/training-set scoring for both models, with `log_models=False` and `log_datasets=False`), one top-level run per model, plus **explicit** logging of the fitted model (`mlflow.sklearn.log_model`, with an explicit signature and `serialization_format="cloudpickle"` — the default `skops` format was verified to REJECT the calibrated XGBoost model with `UntrustedTypesFoundException`, and no version step-down fixes that — so slice 4's contract does not depend on autolog's version-specific behavior), test-window metrics/CIs and artifacts. Autolog is process-global, so it is disabled again in a `finally` block. Autolog's own `training_*` scores are in-sample and are not used for any conclusion here.
- **CLI:** `scripts/train_models.py` (stdlib `argparse`, matching slice 1/2 precedent; Typer is reserved for slice 4).
- Dependency pins added to `requirements.in` (including `greenlet`, see below), `requirements.txt` regenerated with `pip-compile --generate-hashes`, and a new hand-maintained `requirements-linux-extras.txt` (see Surfaces touched) so the same hash-checked install command works on macOS dev machines and Linux CI.
- **`.gitignore` fix:** the existing bare `models/` pattern (line 48, meant for a top-level trained-model artifact directory) matches ANY directory named `models` at any depth and — verified with `git check-ignore -v` — currently ignores `src/readmission_risk/models/` and `tests/models/`. It is anchored to `/models/`. The MLflow tracking directory is also added.
- README status table and setup notes updated; CLAUDE.md updated post-implementation per the repo's incremental-documentation convention.
- Unit + integration tests (synthetic frames; no Spark, no real data in CI).

**Out (explicitly deferred — see also Out-of-scope follow-ups):**

- Hyperparameter tuning (would need nested patient-grouped, chronological validation; this slice uses fixed, conservative XGBoost hyperparameters and **never tunes against the test window**).
- Hosted MLflow (Cloud Run + Cloud SQL + GCS), MLflow Model Registry, model promotion.
- Batch scoring / Top-N triage CLI (slice 4).
- BigQuery as the training source.
- Explainability/feature attribution (SHAP, permutation importance).
- Planned-readmission exclusion; per-reason-code stratified evaluation.
- Point-in-time `organization_utilization`.
- Rolling-origin (multi-window) backtesting.
- Fairness/subgroup analysis.

## Deviations from (and clarifications of) CLAUDE.md's locked spec — to be recorded in CLAUDE.md after implementation

This doc says the MLOps spine is "locked spec — not reopened"; the following are the places where this slice's implementation differs from, or narrows, the literal CLAUDE.md wording, listed so they are decisions, not silent drift (per the repo's standing "flag standards deviations" rule):

1. **Training source: local Parquet via pandas, not BigQuery `to_dataframe()`.** CLAUDE.md's MLOps spine says both models train on the BigQuery gold table; but its own staging plan puts BigQuery in the scale-up phase and keeps the local proving phase GCP-free, which is where this slice lives. The loader is one function so the swap is one function (scale-up phase).
2. **`mlflow.autolog()` logs params and training-set scoring for both models in ONE call, but model artifacts are logged EXPLICITLY** (`log_models=False` + `mlflow.sklearn.log_model(..., serialization_format="cloudpickle")`), because autolog's default `skops` serialization was verified to reject the calibrated XGBoost model, and because the explicit call makes slice 4's load contract independent of autolog behavior. The autolog call also excludes MLflow's `spark`/`pyspark.ml` flavors (pyspark is a runtime dependency). The locked requirement's intent — one autolog call covering both models — is preserved.
3. **`LogisticRegression` is NOT wrapped in `CalibratedClassifierCV`.** CLAUDE.md requires both models to report calibrated probabilities and names the wrapper for the XGBoost model only. The logistic regression is natively probabilistic (log-loss objective, no class reweighting), and its calibration is evaluated exactly like XGBoost's (curve, Brier, ECE, calibration gap) rather than assumed.
4. **Pinned dependency exceptions:** `pandas==2.3.3` and `numpy==2.4.4` are deliberately older than the newest releases (PySpark's `pyspark.pandas` cannot import under pandas 3), with a standing to-do to check later whether upgrading is possible.
5. **Test-window status:** the reported test metrics are not a virgin holdout (see Data handling, "Disclosure"), which qualifies how the "calibration over raw accuracy" results may be described.

## Surfaces touched

- `src/readmission_risk/models/__init__.py` (new, empty)
- `src/readmission_risk/models/data.py` (new — `load_gold_table`, `prepare_features`, `gold_fingerprint`, feature/excluded column constants)
- `src/readmission_risk/models/split.py` (new — `chronological_group_split`, `SplitResult`)
- `src/readmission_risk/models/features.py` (new — `build_linear_preprocessor`, `build_tree_preprocessor`)
- `src/readmission_risk/models/modeling.py` (new — model builders, `make_grouped_cv_splits`)
- `src/readmission_risk/models/evaluate.py` (new — metrics, calibration curve, bootstrap CI, reliability diagram)
- `src/readmission_risk/models/tracking.py` (new — `mlflow_tracking_uri`, `load_logged_model`: the small, training-free surface slice 4 imports to resolve a logged model)
- `src/readmission_risk/models/training.py` (new — `TrainingConfig`, `train_and_evaluate`, MLflow wiring)
- `scripts/train_models.py` (new — thin CLI)
- `tests/models/__init__.py`, `tests/models/helpers.py` (`make_gold_frame`, `row`, `frame`, `pad_rows`, shared date constants), `tests/models/conftest.py` (fixtures only), `tests/models/test_data.py`, `test_split.py`, `test_features.py`, `test_modeling.py`, `test_evaluate.py`, `test_training.py` (new)
- `requirements.in` (edit — add the 8 pins below), `requirements.txt` (regenerated)
- `requirements-linux-extras.txt` (new, hand-maintained — see below)
- `requirements-dev.txt` — **not regenerated**, but a consistency check is required after the new `requirements.txt` is compiled (see below).
- `.gitignore` (edit — **change line 48 `models/` to `/models/`**, and add the ANCHORED top-level patterns `/mlflow/`, `/mlruns/`, `/mlartifacts/` (unanchored `mlflow/` would repeat the exact bug being fixed on line 48 by matching a directory of that name anywhere), and `*.db` — MLflow 3.16's DEFAULT tracking URI is `sqlite:///<cwd>/mlflow.db`, so any accidentally unconfigured MLflow call creates a stray root-level `mlflow.db` that would otherwise show up untracked). Required check, run right after the edit and again before the final report: `git check-ignore -v src/readmission_risk/models/data.py tests/models/test_data.py scripts/train_models.py` must print nothing (exit status 1), and `git status --short -uall` (the `-uall` matters: plain `git status` collapses new untracked directories to `?? src/readmission_risk/models/`) must list every new source/test file individually as untracked (`??`).
- `README.md` (edit — line 16's slice-2 row is stale ("Not started"): mark slice 2 **Done**, slice 3 **Done** post-implementation; in the Setup section, add `brew install libomp` (macOS only, needed by XGBoost) directly under the existing install commands, and ADD a runtime-install line (README's Setup currently has only `pip install -e .` and the `requirements-dev.txt` install — the runtime `requirements.txt` install exists only in CLAUDE.md, so there is nothing to "change" there) reading `pip install --require-hashes -r requirements.txt -r requirements-linux-extras.txt` between them, and add the train command plus the `mlflow ui` command from UX flow step 5; **add a short "Results and caveats" subsection under Status** holding (i) the holdout-disclosure sentence (test window not a virgin holdout; see the doc's Data handling), (ii) the reason-code-dominance / scripted-lung-cancer-cycle caveat with the reason-code-only reference AUC, and (iii) the measured metrics after the manual verification run; note that moving the repo directory invalidates artifact paths stored in `mlflow/mlflow.db`)
- `CLAUDE.md` (edit, post-implementation — dependency/build-command notes (including the new install command), split design, feature exclusions, MLflow layout, and the slice-3 resolution — CLAUDE.md has no heading called "Requirement carried into slice 3"; the relevant existing paragraph is **"Hard constraint carried into future model-training work"** in the Architecture section, so the resolution is recorded by editing/extending THAT paragraph — plus the pandas/numpy-upgrade to-do, the holdout disclosure, and the "Deviations from the locked spec" list below)
- `.github/workflows/ci.yml` (edit — **the earlier draft of this doc said "no change needed"; that was wrong**). The runtime install line changes from `pip install --require-hashes -r requirements.txt` to `pip install --require-hashes -r requirements.txt -r requirements-linux-extras.txt`. Nothing else in the workflow changes. Same command works on macOS (see below), and is documented in README/CLAUDE.md.
- Not touched: everything under `src/readmission_risk/pipeline/`. This slice does NOT import from it at runtime (see `EXPECTED_GOLD_COLUMNS` in Interfaces — the gold schema is restated in `models/data.py` and a test, not production code, asserts it equals `big_join.GOLD_TABLE_COLUMNS`), so `readmission_risk.models` — which slice 4's scorer and possibly the Streamlit app will import — does not drag in `pyspark`.

`requirements.in` additions (exact pins, matching the existing `pyspark==4.1.3` style; versions are the ones the scratch resolver run confirmed mutually compatible at the metadata level, except pandas/numpy which are the deliberately older pair explained in the compatibility finding (comment lines in `requirements.in` are kept by `pip-compile`); `greenlet` is added explicitly because SQLAlchemy needs it on Linux/x86_64 but not on this Apple-Silicon host — unconditional in `.in`, harmless on macOS where a wheel exists, and it makes `pip-compile` emit a hashed pin):

```
scikit-learn==1.9.1
xgboost==3.4.1
mlflow==3.16.1
# pandas/numpy are deliberately NOT at their newest (3.0.x / 2.5.x): pyspark 4.1.3's pyspark.pandas cannot import under
# pandas 3 (breaks assertDataFrameEqual in tests/pipeline/test_big_join.py), and numpy 2.5 + pandas 2.3 warns on pd.Timedelta.
# TODO: re-check whether upgrading to pandas 3 / numpy 2.5 is possible once PySpark supports pandas 3 (see CLAUDE.md).
pandas==2.3.3
pyarrow==25.0.1
matplotlib==3.11.2
numpy==2.4.4
greenlet==3.5.6
```

`requirements-linux-extras.txt` (new): the ONLY dependencies that `pip-compile` on macOS cannot see, each hash-pinned and gated with an environment marker so pip skips them on macOS. Initial content (hashes to be filled from PyPI's JSON API `urls[].digests.sha256` for the exact pinned version's wheels; NOT invented):

```
# Linux-only dependencies that pip-compile cannot see when run on macOS (it evaluates
# environment markers for the host only). Required by --require-hashes installs on Linux/CI;
# skipped on macOS by the marker. Hand-maintained: if a future xgboost/sqlalchemy bump adds or
# changes a platform-conditional dependency, the Linux install fails loudly with a "hashes
# missing" error -- re-check with the procedure in notes/eg-new-feature/model-training-2026-09-19.md.
nvidia-nccl-cu13==2.31.2 ; platform_system == "Linux" \
    --hash=sha256:<manylinux_2_18_x86_64 wheel digest> \
    --hash=sha256:<manylinux_2_18_aarch64 wheel digest>
```

(**Which hashes:** include the sha256 of EVERY wheel file (`packagetype == "bdist_wheel"`) that PyPI's JSON API lists for that exact version — every Linux architecture/manylinux tag — and no sdist entry (this package is wheel-only, and an sdist hash would let pip fall back to a source build). At design time PyPI lists exactly two wheels for 2.31.2 (`manylinux_2_18_x86_64`, `manylinux_2_18_aarch64`, checked); if implementation finds more, hash them all, as `pip-compile` itself does for the main lockfile. Implementation must also confirm from PyPI metadata that `nvidia-nccl-cu13==2.31.2` itself has no unpinned dependencies; if it does, add them here the same way. The `nvidia-nccl-cu13` version is the newest available at design time — 2.31.2 — and `xgboost` places no upper bound on it.)

**Exact compile commands** (the header of the existing lockfiles contains `--no-index`; that flag is a record of how pip-compile was invoked in an environment that could not reach an index — running the header command LITERALLY fails with `No matching distribution found for pyspark==4.1.3`, verified. Use, from the repo root with the venv active and network access): `pip-compile --generate-hashes --output-file=requirements.txt requirements.in` (the regenerated file's header will still print `--no-index`; that is pip-compile's own bookkeeping, not an instruction to follow). If the dev lockfile must be regenerated (see the consistency check below): `pip-compile --allow-unsafe --generate-hashes -c requirements.txt --output-file=requirements-dev.txt requirements-dev.in` (`--allow-unsafe` matches the existing dev lockfile's flags; `-c requirements.txt` is an ADDITION relative to the existing dev header, deliberate, so the two lockfiles stay mutually consistent — the round-5 critic verified the output is identical either way).

**Dependency-resolution rule, unchanged in spirit:** if `pip-compile` at implementation time reports a conflict (the versions above resolved at metadata level today, but indexes move), step down to the newest mutually compatible set, and record the actual pins in CLAUDE.md and the final report — do not loosen to unpinned ranges. **New: a smoke gate (UX flow step 2) must pass before any modeling code is written.**

**`requirements-dev.txt` consistency check** (`pip install --require-hashes -r requirements-dev.txt` runs AFTER the runtime install and could otherwise downgrade a package the runtime set needs): the packages appearing in BOTH lockfiles at design time are `click` and `packaging`, at identical versions in the scratch runtime lockfile and the existing dev lockfile (`click==8.5.0`, `packaging==26.3`), so no conflict exists today. After the real `requirements.txt` is compiled, re-run the overlap comparison; if any shared package's version differs, regenerate `requirements-dev.txt` with the dev command given under "Exact compile commands" above.

## Interfaces

Dates on every CLI flag and config field are `"YYYYMMDD"` strings, matching the existing `--reference-date` convention (slice 1/2); they are parsed with `datetime.strptime(value, "%Y%m%d").date()` and an invalid value raises `ValueError` naming the field.

```python
# src/readmission_risk/models/data.py

import pandas as pd
from pathlib import Path

LABEL_COLUMN = "is_readmitted"
GROUP_COLUMN = "patient_id"

EXPECTED_GOLD_COLUMNS: tuple[str, ...] = (
    "patient_id", "encounter_id", "index_start", "index_stop", "is_readmitted",
    "admission_reason_code", "admission_reason_description",
    "payer_ownership", "provider_specialty", "organization_utilization",
    "prior_encounter_count", "prior_inpatient_count", "prior_emergency_count",
    "active_condition_count", "active_medication_count", "procedure_count_window",
    "active_careplan_count", "observation_count_window",
    "age_at_index_years", "gender", "race", "ethnicity", "marital_status",
)
# The gold schema, restated here (23 columns) rather than imported from
# readmission_risk.pipeline.big_join.GOLD_TABLE_COLUMNS: big_join imports pyspark at module level
# (~300MB), and readmission_risk.models will be imported by slice 4's scorer and possibly the
# Streamlit dashboard, neither of which should need Spark. The two definitions are kept in sync by
# test_expected_gold_columns_match_big_join (tests import pyspark; production code does not).

COUNT_FEATURES: tuple[str, ...] = (
    "prior_encounter_count", "prior_inpatient_count", "prior_emergency_count",
    "active_condition_count", "active_medication_count", "procedure_count_window",
    "active_careplan_count", "observation_count_window",
    "length_of_stay_days",           # derived by prepare_features; NOT a gold column
)
NUMERIC_OTHER_FEATURES: tuple[str, ...] = ("age_at_index_years",)
CATEGORICAL_FEATURES: tuple[str, ...] = (
    "admission_reason_code", "payer_ownership", "gender", "race", "ethnicity", "marital_status",
)
FEATURE_COLUMNS: tuple[str, ...] = COUNT_FEATURES + NUMERIC_OTHER_FEATURES + CATEGORICAL_FEATURES  # 16, this order

EXCLUDED_COLUMN_REASONS: dict[str, str] = {
    "patient_id": "identifier; used only as the split/CV grouping key",
    "encounter_id": "identifier",
    "index_start": "split key; raw calendar time cannot extrapolate across a chronological split",
    "index_stop": "split key / censoring-buffer key; only length_of_stay_days derived from it is a feature",
    "admission_reason_description": "redundant with admission_reason_code",
    "provider_specialty": "constant (GENERAL PRACTICE on every row) -- zero information",
    "organization_utilization": "whole-simulation running total per organization: embeds post-index information (not point-in-time); user decision 2026-09-19",
}
# Invariant (enforced by test_every_gold_column_is_classified_exactly_once):
#   set(EXPECTED_GOLD_COLUMNS) == {LABEL_COLUMN} | set(EXCLUDED_COLUMN_REASONS) | (set(FEATURE_COLUMNS) - {"length_of_stay_days"})
#   and the three groups are pairwise disjoint. A future slice-2 column therefore cannot silently
#   become a feature OR silently vanish -- the test fails until it is classified.

def load_gold_table(gold_dir: Path) -> pd.DataFrame:
    """Reads the Spark-written Parquet directory (part files + _SUCCESS) via pd.read_parquet.
    Raises FileNotFoundError if gold_dir is not a directory or contains no *.parquet file.
    Then validates, raising ValueError (message names the offending column/count) on:
      - column set != set(EXPECTED_GOLD_COLUMNS) (strict: both missing AND unexpected columns fail,
        so a schema change in slice 2 forces explicit classification here);
      - zero rows;
      - any null in patient_id, encounter_id, index_start, index_stop, is_readmitted, or any
        column in COUNT_FEATURES-minus-length_of_stay_days / age_at_index_years;
      - is_readmitted not in {0, 1};
      - duplicate encounter_id;
      - index_stop < index_start on any row.
    Normalizes: index_start/index_stop -> tz-aware UTC via pd.to_datetime(col, utc=True)
    (naive input is interpreted as UTC, matching the pipeline's spark.sql.session.timeZone=UTC
    pin; aware input is converted; the datetime UNIT is whatever pandas yields -- ns from Spark's INT96
    timestamps -- and is not part of the contract); is_readmitted -> int8; the 8 count columns -> int64;
    age_at_index_years -> float64; organization_utilization is LEFT EXACTLY AS READ (it is excluded from the features, so it is neither cast nor null-checked -- a nullable Parquet int arrives as float64/NaN and `astype("int64")` would raise an unnamed `IntCastingNaNError`; slice 2 builds this column through a left join, so nulls are possible in principle although the real data has none); every string
    column -> Python-object dtype (`astype(object)`; under pandas 3 Parquet strings would come back as its `str` dtype --
    the explicit cast keeps the contract identical on 2.3.3 and on a future pandas 3 upgrade). POST-LOAD DTYPE CONTRACT (this is what tests assert, not the pre-write fixture dtypes): strings
    `object`, the 8 count columns `int64`, age `float64`, label `int8`, timestamps tz-aware UTC (`organization_utilization`: unconstrained). Returns the frame
    sorted by (index_start, encounter_id) with a fresh RangeIndex (deterministic row order)."""


def gold_fingerprint(df: pd.DataFrame) -> str:
    """Pure. `df` = the frame returned by load_gold_table. Returns
    hashlib.sha256(df.sort_values("encounter_id")[list(EXPECTED_GOLD_COLUMNS)]
                     .to_csv(index=False, lineterminator="\n", date_format="%Y-%m-%dT%H:%M:%S.%f", float_format="%.17g")
                     .encode("utf-8")).hexdigest().
    (`lineterminator="\n"` is explicit because pandas otherwise uses `os.linesep`. Known and harmless for Spark output:
    a null and an empty string render identically in CSV, so they hash identically.)
    ALL 23 columns are hashed (an earlier draft hashed only ids + labels, which would not change when gold is
    regenerated with different features). This is the ONLY implementation of the recipe: train_and_evaluate's
    `gold_fingerprint` tag and the tests both call it (the tests perturb frames and call THIS function -- a test
    that re-hashed a modified frame by hand would test hashlib, not the code)."""


def prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    """Pure; does not mutate df. Returns a NEW frame with exactly FEATURE_COLUMNS, in that order:
      - length_of_stay_days = (index_stop - index_start) / pd.Timedelta(days=1), float64;
      - count/age columns cast to float64;
      - each CATEGORICAL_FEATURES column: nulls filled with the literal "MISSING", then cast to
        Python-object dtype (`astype(object)` -- explicit so the input contract is the same on pandas 2.3.3 and on a
        future pandas 3 (whose default is the `str` dtype); MLflow's signature inference and sklearn's encoders
        are both well-defined for object columns of Python strings).
        A constant fill is not a fitted statistic, so doing it here (before any train/test
        split) cannot leak; it is done in pandas rather than inside the sklearn pipeline so
        NaN handling is deterministic.
    Raises ValueError listing any missing input column."""
```

```python
# src/readmission_risk/models/split.py

from dataclasses import dataclass
from datetime import date
import pandas as pd

@dataclass(frozen=True)
class SplitResult:
    train: pd.DataFrame          # subset of the input rows (all gold columns retained), input order preserved
    test: pd.DataFrame
    summary: dict[str, int | float | str]   # exact keys listed below

def chronological_group_split(
    df: pd.DataFrame,             # output of load_gold_table (tz-aware UTC index_start/index_stop)
    *,
    reference_date: date,
    test_start_date: date,
    readmission_window_days: int = 30,
    train_start_date: date | None = None,
) -> SplitResult:
    """Pure. Let W = readmission_window_days; all boundaries are UTC midnights.
      T = test_start_date at 00:00Z;   R_end = (reference_date + 1 day) at 00:00Z.

    Step 1 -- censoring buffer (applies to the WHOLE modeling set, train and test, positives and
      negatives alike): keep a row iff index_stop + W days < R_end. This is exactly the rule slice
      2 uses to decide a negative is observable (`readmit_deadline < reference_date_exclusive_end`,
      big_join.py:build_index_encounters), applied here to positives too, restoring symmetry in the
      only date range where slice 2's policy was asymmetric. Rows removed: n_dropped_censor_buffer.
    Step 2 -- test = kept rows with index_start >= T.
    Step 3 -- pre = kept rows with index_start < T. If train_start_date is given, drop pre rows with
      index_start < (train_start_date at 00:00Z)  (n_dropped_before_train_start).
    Step 4 -- label-window purge: drop pre rows with index_stop + W days >= T (their 30-day outcome
      window (stop, stop + W] -- INCLUSIVE at the upper bound in slice 2 -- reaches into the test period, i.e. their
      LABEL was not yet fully knowable at the cutoff; a row with index_stop + W days == T exactly could be labeled by a
      readmission starting AT T, so it is purged too; see the implementation addendum). n_dropped_label_window_purge.
    Step 5 -- patient disjointness: drop pre rows whose patient_id appears in ANY test row
      (n_dropped_patient_overlap). The remainder is train.

    ORDER OF CHECKS (so the post-conditions never run on an empty frame, where .max()/.min() are
    undefined): (1) argument, tz and non-empty-input validation before Step 1 (a zero-row `df` raises `ValueError` here, so the >1% warning's division by `n_input` can never divide by zero); (2) after Step 5, the emptiness and
    single-class ValueErrors below; (3) ONLY THEN the post-conditions.
    ValueError (input/config problems, message names the cause): index_start or index_stop
    not tz-aware; not (train_start_date < test_start_date < reference_date) when train_start_date
    is given, or test_start_date >= reference_date otherwise; W < 1; empty train or empty test
    after all steps; train or test containing only one class of is_readmitted.
    Post-conditions, each raising RuntimeError("split invariant violated: ...") if false (they are
    invariants of the algorithm, not input validation): train.patient_id and test.patient_id
    sets are disjoint; no encounter_id is in both; train.index_start.max() < T <= test.index_start.min();
    (train.index_stop + W days < T).all().
    Also emits warnings.warn(...) (NOT an error; message contains the literal text "reference_date") if n_dropped_censor_buffer / n_input > 0.01: on
    real data the buffer removes only the handful of censored POSITIVES slice 2 kept (0.015% here;
    every censored negative is already gone), so a drop rate above 1% is a strong hint that
    reference_date is earlier than the Synthea/Big Join run's true reference date.
    summary keys (all ints unless noted): n_input, n_dropped_censor_buffer, n_test, n_dropped_before_train_start,
      n_dropped_label_window_purge, n_dropped_patient_overlap, n_train, n_train_positive, n_test_positive,
      train_positive_rate (float), test_positive_rate (float), n_train_patients, n_test_patients,
      train_index_start_min/train_index_start_max/test_index_start_min/test_index_start_max (ISO-8601 str),
      censor_buffer_cutoff (ISO-8601 str: R_end - W days).
    Accounting identity (tested): n_input == n_dropped_censor_buffer + n_test + n_dropped_before_train_start
      + n_dropped_label_window_purge + n_dropped_patient_overlap + n_train."""
```

```python
# src/readmission_risk/models/features.py

from sklearn.compose import ColumnTransformer

def build_linear_preprocessor() -> ColumnTransformer:
    """For LogisticRegression. Unfitted. Branches (remainder="drop"):
      "counts": COUNT_FEATURES -> Pipeline([("log1p", FunctionTransformer(np.log1p, feature_names_out="one-to-one")),
                                            ("scale", StandardScaler())])   (heavy right skew, e.g. prior_encounter_count max 217;
                                            feature_names_out="one-to-one" so get_feature_names_out() works -- the explainability follow-up needs it)
      "age":    NUMERIC_OTHER  -> StandardScaler()
      "cats":   CATEGORICAL    -> OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=20, sparse_output=False)
    The ColumnTransformer is built with `verbose_feature_names_out=False`, so get_feature_names_out() returns UNPREFIXED
    names (`prior_encounter_count`, `age_at_index_years`, `admission_reason_code_<code>`, ...) rather than sklearn's default
    `counts__prior_encounter_count` / `age__...` / `cats__...`. (Same setting for the tree preprocessor.)
    The transformer names "counts"/"age"/"cats" are part of the interface (tests reach in by name, e.g.
    `pre.named_transformers_["counts"].named_steps["scale"].mean_`).
    Categories with fewer than 20 rows in the data the encoder is fitted on are pooled into an
    'infrequent' bucket, and categories never seen at fit time are mapped to that same bucket at
    transform time (no error, no leakage of test-only categories) -- BUT ONLY for a column that
    actually has at least one infrequent category at fit time. For a column with none,
    handle_unknown="infrequent_if_exist" behaves like "ignore": an unseen category becomes an
    all-zero block for that column. Both behaviors are intentional and both are tested. (On the
    real data, 10 reason codes fall below 20 training rows, so the reason-code column has the
    bucket.)"""

def build_tree_preprocessor() -> ColumnTransformer:
    """For XGBoost. Unfitted. Numeric columns (COUNT_FEATURES + NUMERIC_OTHER) pass through
    unscaled; CATEGORICAL uses the identical OneHotEncoder configuration as above. Transformer names:
    "num" (passthrough, COUNT_FEATURES + NUMERIC_OTHER) and "cats". Both preprocessors are wrapped as the step
    named "preprocess" of a Pipeline whose final step is named "model" (see modeling.py)."""
```

```python
# src/readmission_risk/models/modeling.py

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.pipeline import Pipeline

XGB_PARAMS: dict = dict(
    n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
    min_child_weight=5, reg_lambda=1.0, objective="binary:logistic", eval_metric="logloss",
    tree_method="hist", n_jobs=1,          # n_jobs=1: a fixed thread count for run-to-run determinism; the data is ~10k rows so the cost is negligible
)
# scale_pos_weight deliberately NOT set (would distort probabilities). random_state is injected from the seed.

def build_logistic_regression(seed: int) -> Pipeline:
    """Pipeline([("preprocess", build_linear_preprocessor()),
                 ("model", LogisticRegression(C=1.0, solver="lbfgs", max_iter=2000, random_state=seed))]).
    class_weight is left at its default None (asserted by a test) -- reweighting would break calibration."""

def make_grouped_cv_splits(y: np.ndarray, groups: np.ndarray, n_splits: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """list(sklearn.model_selection.GroupKFold(n_splits=n_splits).split(np.zeros(len(y)), y, groups))
    (GroupKFold is deterministic -- no shuffle -- so the folds are reproducible). Positional indices
    into y/groups. Raises ValueError if groups has fewer than n_splits unique values, or if ANY fold's
    fit part OR calibration part contains a single class (CalibratedClassifierCV cannot calibrate
    on a one-class fold); the message names the fold."""

def build_calibrated_xgboost(seed: int, cv_splits: list[tuple[np.ndarray, np.ndarray]], method: str = "sigmoid") -> CalibratedClassifierCV:
    """CalibratedClassifierCV(
         estimator=Pipeline([("preprocess", build_tree_preprocessor()),
                             ("model", XGBClassifier(**XGB_PARAMS, random_state=seed))]),
         method=method, cv=cv_splits, ensemble=True).
    method must be "sigmoid" or "isotonic" (ValueError otherwise). Default "sigmoid": isotonic
    regression overfits below roughly 1,000 positives, and with ensemble=True (below) each calibrator is fitted on
    only ONE fold's worth of rows -- about a fifth of the training set, i.e. roughly 250 positives on the real data --
    so sigmoid is the right default (the training set's ~1,200 positives in total is NOT the relevant count). With
    ensemble=True the returned model is the average of n_splits (base fit + calibrator) pairs, each
    fitted on (n_splits-1)/n_splits of the training rows -- no single model is fitted on all of them.
    The cv_splits are positional indices, so this model MUST be fit on exactly the (X_train, y_train)
    frame the splits were built from. sklearn cannot check that (it only sees integer positions), so
    validate_cv_splits below is called immediately before fitting."""

def validate_cv_splits(cv_splits: list[tuple[np.ndarray, np.ndarray]], groups: np.ndarray) -> None:
    """`groups` MUST be the patient ids of the rows of the frame that is about to be fit, in fit order.
    Check (a) runs FIRST and includes a length check (`len(groups)` must equal the total number of distinct validation indices), so a shorter/longer `groups` array raises ValueError BEFORE any indexing (never IndexError). Raises ValueError naming the fold if: (a) the calibration (validation) index sets do not together cover
    range(len(groups)) exactly once each; (b) any fold's fit and validation index sets overlap; or (c) any
    patient in `groups` appears in BOTH the fit and the validation part of the same fold. Guards the
    positional coupling above: if a later edit reorders or filters X_train after the splits were built, the
    patient-level leakage this design claims to prevent would otherwise return silently."""
```

```python
# src/readmission_risk/models/evaluate.py

import numpy as np
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class EvaluationResult:
    metrics: dict[str, float]
    # keys: roc_auc, pr_auc, brier_score, brier_skill_score, log_loss, expected_calibration_error,
    #       mean_predicted_probability, observed_prevalence, calibration_gap
    #       (= mean_predicted_probability - observed_prevalence, SIGNED), abs_calibration_gap (= |calibration_gap|),
    #       n_rows, n_positive
    calibration_curve: list[dict[str, float]]
    # one dict per non-empty bin, ascending: {"mean_predicted": float, "observed_fraction": float, "n": int}

def evaluate_probabilities(y_true: np.ndarray, y_prob: np.ndarray, *, train_prevalence: float, n_bins: int = 10) -> EvaluationResult:
    """Pure. Binning: the shared helper `_quantile_bin_ids(y_prob, n_bins) -> np.ndarray[int]` (module-private,
    also used by the bootstrap): `edges = np.unique(np.quantile(y_prob, np.linspace(0, 1, n_bins + 1)))`; if
    `len(edges) < 2` (e.g. every prediction identical) ALL rows go in ONE bin (id 0); otherwise
    `ids = np.clip(np.searchsorted(edges, y_prob, side="right") - 1, 0, len(edges) - 2)` (bins are
    left-closed [e_i, e_{i+1}) with the last bin closed on the right; they are approximately equal-count, but with
    small n or ties there can be FEWER than n_bins distinct edges and some bins empty -- verified: n = 6 gives 6 of
    10 non-empty bins -- so the calibration curve lists only NON-EMPTY bins and ECE sums only over them). `pd.qcut(..., duplicates="drop")` is deliberately NOT used:
    on constant input it returns a Categorical with NO categories and all-NaN codes (verified by the round-4
    critic), i.e. an empty curve, rather than one bin. brier_score = mean((y_prob-y_true)^2);
    brier_skill_score = 1 - brier_score / mean((train_prevalence - y_true)^2), i.e. skill relative to
    a constant predictor that only knows the TRAIN prevalence; expected_calibration_error =
    sum_b (n_b/N)*|observed_fraction_b - mean_predicted_b|; log_loss via sklearn, called POSITIONALLY as
    log_loss(y_true, y_prob_clipped, labels=[0, 1]) with y_prob clipped to [1e-15, 1-1e-15] (sklearn 1.9 renamed the
    second parameter and emits a FutureWarning for the old keyword, once per fit under autolog). Raises ValueError on length mismatch, empty input, non-binary y_true, a
    y_true with a single class (AUC undefined), or any y_prob outside [0, 1] or non-finite."""

BOOTSTRAP_METRICS: tuple[str, ...] = ("roc_auc", "brier_score", "expected_calibration_error", "calibration_gap", "abs_calibration_gap")
# calibration_gap = mean(y_prob) - mean(y_true)   (SIGNED calibration-in-the-large; abs_calibration_gap = its absolute value).
# WHY BOTH: the signed gap says which DIRECTION a model is miscalibrated (per-model interval, reported for direction only),
# but a paired difference of SIGNED gaps cannot say which model is better calibrated (two equal-magnitude opposite-sign gaps
# give a large "difference"), so calibration CLAIMS use expected_calibration_error and abs_calibration_gap, never the signed gap.

def cluster_resample_indices(groups: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """One patient-clustered resample. Draws len(unique(groups)) patients WITH replacement using
    `rng` and returns the concatenated POSITIONAL row indices of every drawn patient (a patient
    drawn twice contributes their rows twice; a patient never drawn contributes none). Rows from one
    patient are correlated, so resampling rows independently would understate the interval. Groups
    are enumerated in sorted-unique order so the result is a deterministic function of (groups, rng
    state). This is the named helper the resampling test targets."""

@dataclass(frozen=True)
class BootstrapResult:
    intervals: dict[str, dict[str, tuple[float, float]]]
    # intervals[model_name][metric] = (lower, upper) for metric in BOOTSTRAP_METRICS
    paired_differences: dict[str, dict[str, tuple[float, float]]]
    # paired_differences["<b>_minus_<a>"][metric] = (lower, upper) of metric(b) - metric(a) on the
    # SAME resamples, for the two models in `probs` in insertion order (a = first, b = second)
    n_resamples_used: int
    n_resamples_skipped: int

def clustered_bootstrap(
    y_true: np.ndarray, probs: dict[str, np.ndarray], groups: np.ndarray, *,
    n_bootstrap: int, seed: int, alpha: float = 0.05, n_bins: int = 10,
) -> BootstrapResult:
    """All inputs are 1-D np.ndarray of equal length (callers pass `.to_numpy()`, not pandas
    Series). Draws n_bootstrap resamples via cluster_resample_indices from ONE
    np.random.default_rng(seed) stream, and for EACH resample computes every metric in
    BOOTSTRAP_METRICS for EVERY model in `probs` on the same rows -- so a paired difference is a
    within-resample difference, not a difference of two independent intervals. Resamples whose
    y_true contains a single class are skipped and counted; warnings.warn if more than 5% are
    skipped (warning message contains the literal text "single-class"); if EVERY resample is skipped (`n_resamples_used == 0`) it raises `ValueError` ("every bootstrap resample was single-class; the test window is too small or degenerate") instead of calling a percentile on an empty array. Percentile intervals at alpha/2 and 1-alpha/2. `probs` must have exactly two entries
    when paired differences are wanted, otherwise paired_differences is {}. n_bootstrap == 0 returns
    BootstrapResult({}, {}, 0, 0). ECE inside a resample uses the same `_quantile_bin_ids`
    helper as evaluate_probabilities (including its single-bin fallback). Deterministic for a fixed seed."""

def plot_reliability_diagram(curve: list[dict[str, float]], title: str, out_path: Path) -> None:
    """matplotlib Figure API (no pyplot global state, safe headless): diagonal reference line, one
    marker line for the curve, axis labels 'Mean predicted probability' / 'Observed fraction
    positive', saved as PNG to out_path (parent dir must exist)."""
```

```python
# src/readmission_risk/models/tracking.py   (imports mlflow only -- no training code; safe for slice 4 to import)

from pathlib import Path

def mlflow_tracking_uri(tracking_dir: Path) -> str:
    """Returns f"sqlite:///{tracking_dir.resolve()}/mlflow.db" (an absolute path after "sqlite:///" yields four
    slashes, which is the correct absolute-path form). This is the SINGLE definition of the tracking URI:
    train_and_evaluate uses it to set the URI, and any consumer must use it to read."""

def load_logged_model(tracking_dir: Path, run_id: str):
    """Impure. Calls mlflow.set_tracking_uri(mlflow_tracking_uri(tracking_dir)), reads run_id's `model_uri` tag via
    MlflowClient().get_run(run_id).data.tags (ValueError naming the run if the tag is absent), and returns
    mlflow.sklearn.load_model(uri): the fitted scikit-learn estimator, ready for
    .predict_proba(prepare_features(...))."""
```

```python
# src/readmission_risk/models/training.py

from dataclasses import dataclass, field
from pathlib import Path

MODEL_NAMES: tuple[str, ...] = ("logistic_regression", "xgboost_calibrated")

@dataclass(frozen=True)
class TrainingConfig:
    gold_dir: Path                      # e.g. Path("data/gold/full_run")
    reference_date: str                 # required, "YYYYMMDD" -- MUST equal the reference_date used for the Synthea run + Big Join that produced gold_dir (no default, mirroring BigJoinConfig)
    test_start_date: str                # required, "YYYYMMDD" (no default: this is a modeling decision the caller must make)
    train_start_date: str | None = None
    tracking_dir: Path = Path("mlflow")  # holds mlflow.db (SQLite) and artifacts/
    experiment_name: str = "readmission-risk"
    readmission_window_days: int = 30    # MUST equal BigJoinConfig.readmission_window_days used for gold_dir
    seed: int = 42
    calibration_cv_folds: int = 5
    calibration_method: str = "sigmoid"
    n_bootstrap: int = 500

@dataclass(frozen=True)
class ModelResult:
    name: str
    run_id: str
    model_uri: str                                       # URI returned by mlflow.sklearn.log_model (also stored as run tag "model_uri")
    test_metrics: dict[str, float]                       # EvaluationResult.metrics (incl. calibration_gap)
    confidence_intervals: dict[str, tuple[float, float]] # metric -> (lo, hi), for metrics in BOOTSTRAP_METRICS; {} if n_bootstrap == 0
    calibration_curve: list[dict[str, float]]

@dataclass(frozen=True)
class PairedComparison:
    point: dict[str, float]                              # metric -> metric(xgboost_calibrated) - metric(logistic_regression), for metrics in BOOTSTRAP_METRICS, computed from the two point-metric dicts: signed calibration_gap difference = gap_b - gap_a; abs_calibration_gap difference = |gap_b| - |gap_a| (NOT |gap_b - gap_a|)
    intervals: dict[str, tuple[float, float]]            # metric -> paired 95% interval of that difference (from clustered_bootstrap); {} if n_bootstrap == 0

@dataclass(frozen=True)
class TrainingResult:
    split_summary: dict[str, int | float | str]
    models: dict[str, ModelResult]                       # keyed by MODEL_NAMES
    comparison: PairedComparison

MIN_TEST_POSITIVES_FOR_RELIABLE_METRICS = 50

def train_and_evaluate(config: TrainingConfig) -> TrainingResult:
    """Impure (filesystem + MLflow). Steps, in order:
    1. Validate config (dates parse; calibration_method in {"sigmoid","isotonic"}; calibration_cv_folds >= 2;
       readmission_window_days >= 1; n_bootstrap >= 0) -> ValueError naming the field.
    2. df = load_gold_table(config.gold_dir). (There is deliberately NO hard check of reference_date
       against max(index_stop): slice 2 keeps censored positives whose discharge can fall AFTER the
       reference date, so such a check could fail spuriously on valid data. A too-early reference_date is
       caught instead by chronological_group_split's >1%-buffer-drop warning.)
    3. split = chronological_group_split(df, ...). X_train = prepare_features(split.train);
       y_train = split.train["is_readmitted"].to_numpy(); groups_train = split.train["patient_id"].to_numpy();
       likewise X_test/y_test/groups_test (y/groups always np.ndarray from here on).
       train_prevalence = float(y_train.mean()).
       If split.summary["n_test_positive"] < MIN_TEST_POSITIVES_FOR_RELIABLE_METRICS: warnings.warn(...)
       whose message contains the word "unreliable" (metrics and intervals cannot be trusted); the run still completes.
       Also here: `fingerprint = data.gold_fingerprint(df)` — computed ONCE, before any MLflow side effect (it is pure and
       identical for both runs; computing it per run would hash the frame twice and could fail after side effects).
    4. (PURE -- runs BEFORE any MLflow side effect, so a user-correctable failure leaves no empty experiment,
       tracking directory or enabled autolog behind:) cv_splits = make_grouped_cv_splits(y_train, groups_train,
       config.calibration_cv_folds); validate_cv_splits(cv_splits, groups_train); models = {...} (below).
       validate_cv_splits is ALSO re-run immediately before the XGBoost model's .fit in step 6b (cheap; guards
       the positional coupling if anything is ever reordered in between).
       models = {"logistic_regression": build_logistic_regression(seed),
                 "xgboost_calibrated": build_calibrated_xgboost(seed, cv_splits, method)}   # this order, always
    5. MLflow setup (ONLY after step 4 has succeeded):
         tracking_dir.mkdir(parents=True, exist_ok=True); abs_dir = tracking_dir.resolve();
         mlflow.set_tracking_uri(tracking.mlflow_tracking_uri(config.tracking_dir))   # explicit, never the default;
                                                    # MUST call the tracking.py helper -- the one definition of the URI, not an inlined f-string
         exp = mlflow.get_experiment_by_name(experiment_name)
         if exp is not None and exp.lifecycle_stage == "deleted": raise ValueError(f"MLflow experiment {name!r} exists in a
             deleted state in {abs_dir}/mlflow.db; restore it or pass a different experiment name")
         if exp is None: mlflow.create_experiment(name, artifact_location=(abs_dir / "artifacts").as_uri())
         mlflow.set_experiment(experiment_name)
         mlflow.autolog(log_models=False, log_datasets=False,
                        exclude_flavors=["spark", "pyspark.ml"])       # ONE call, before either fit
       The `try:` OPENS IMMEDIATELY BEFORE the `mlflow.autolog(...)` call above (so a partial patch followed by an exception is still cleaned up), and everything from there through step 7 runs inside `try: ... finally: mlflow.autolog(disable=True)`
       (autolog is process-global; leaving it on would patch every later sklearn .fit() in the same
       process -- other tests, slice 4's scorer -- and write stray runs into whatever tracking store is
       current). Accepted limit: the absolute artifact_location is stored in mlflow.db, so moving the repo
       directory invalidates stored artifact paths (documented in README).
    6. PHASE A -- for each (name, model) in MODEL_NAMES order, inside `with mlflow.start_run(run_name=name) as run:`
       (top-level, never nested):
         a. mlflow.log_params: seed, reference_date, test_start_date, train_start_date ("none" if None),
            readmission_window_days, n_bootstrap, plus every key of split.summary prefixed "split_"; for the
            xgboost run only also calibration_method and calibration_cv_folds. (Autolog additionally logs the
            estimator's own get_params(); the meaningless huge "cv" list of index arrays on the calibrated
            wrapper may be truncated by MLflow -- harmless, and the meaningful value is the explicit
            calibration_cv_folds param.)
            mlflow.set_tags: model_name; gold_fingerprint; git_dirty ("clean"|"dirty"|"unknown" from
            `git status --porcelain` via subprocess.run(check=False, capture_output=True, text=True) with cwd=None, i.e. the
            process's current working directory; "unknown" if git is unavailable or the
            command fails -- MLflow's own mlflow.source.git.commit tag records the commit but not dirtiness, so
            a run could otherwise not be tied to exact code); sklearn_version, xgboost_version, mlflow_version,
            pandas_version, numpy_version (each `<module>.__version__`).
            gold_fingerprint = the value computed once in step 3 (`data.gold_fingerprint(df)`, the ONE named implementation, Interfaces > data.py)
            computed over the FULL frame returned by load_gold_table (before splitting), ALL 23 columns. (The
            earlier draft hashed only (encounter_id, label): regenerating gold with different features -- another
            lookback_years, a count-logic fix -- keeps the same encounters and labels and would have left the
            fingerprint unchanged, defeating its purpose. Timestamps are tz-aware UTC, so the explicit
            date_format is unambiguous; nulls render as empty fields.)
         b. model.fit(X_train, y_train)   (autolog logs training-set params/scoring here).
         c. y_prob = model.predict_proba(X_test)[:, 1]; result = evaluate_probabilities(y_test, y_prob,
            train_prevalence=train_prevalence); keep y_prob for Phase B.
         d. mlflow.log_metrics({f"test_{k}": float(v) for k, v in result.metrics.items()}). The "test_" prefix
            cannot collide with autolog's own "training_*" metrics, which are IN-SAMPLE (including an accuracy
            figure) and are never used to report or compare model quality.
         e. Artifacts (mlflow.log_dict / log_figure-equivalent via a temp file; every value placed in a JSON artifact is first
            cast to a native Python type -- `int(...)`, `float(...)`, `str(...)`, pandas Timestamps to ISO-8601 strings --
            because numpy scalars are not JSON-serializable; this applies to split_summary.json, calibration_curve.json and
            bootstrap_summary.json): calibration_curve.json,
            split_summary.json (= split.summary), feature_columns.json ({"features": FEATURE_COLUMNS,
            "excluded": EXCLUDED_COLUMN_REASONS}), split_assignment.csv (columns encounter_id, partition in
            {"train","test"}; every row that landed in a partition, none of the dropped ones),
            reliability_diagram.png (plot_reliability_diagram).
         f. info = mlflow.sklearn.log_model(model, name="model", signature=mlflow.models.infer_signature(X_train),
                                             serialization_format=mlflow.sklearn.SERIALIZATION_FORMAT_CLOUDPICKLE)
            (input schema only; `name=` is the MLflow-3 keyword -- if the pinned version rejects it, use
            `artifact_path=`. `serialization_format` MUST be cloudpickle: MLflow 3.16.1's default `skops` format was
            verified to raise UntrustedTypesFoundException on the calibrated XGBoost model
            (sklearn.calibration._CalibratedClassifier, _SigmoidCalibration, xgboost.core.Booster,
            xgboost.sklearn.XGBClassifier), and no version change fixes that. Cloudpickle works for both models and
            round-trips (verified in a prototype). Consequences, stated so slice 4 is not surprised: loading needs the
            SAME library versions as the lockfile, and MLflow emits a pickle-safety warning on load -- acceptable
            for a locally-produced, locally-consumed artifact; not acceptable to load untrusted models.) mlflow.set_tag("model_uri", info.model_uri). Explicit rather than autolog-driven,
            so slice 4's contract does not depend on how a given MLflow version autologs a meta-estimator.
    7. PHASE B -- after both runs are closed: boot = clustered_bootstrap(y_test, {name: y_prob_by_model[name]
       for name in MODEL_NAMES}, groups_test, n_bootstrap=config.n_bootstrap, seed=config.seed). If n_bootstrap == 0,
       PHASE B IS SKIPPED ENTIRELY: no bootstrap or CI/diff-interval metric, no test_bootstrap_resamples_used, no
       bootstrap_summary.json is logged; the point-metric differences (PairedComparison.point) are still computed
       and the test_diff_vs_logistic_regression_<m> POINT metrics are still logged to the xgboost run; the CLI prints
       "(bootstrap skipped: --n-bootstrap 0)" instead of intervals. Otherwise, append to each
       already-closed run via MlflowClient().log_batch(run_id, metrics=[mlflow.entities.Metric(key, value,
       timestamp=int(time.time() * 1000), step=0), ...]) : test_{m}_ci_lower and
       test_{m}_ci_upper for m in BOOTSTRAP_METRICS (from boot.intervals[name]) and test_bootstrap_resamples_used;
       to the xgboost run only, also test_diff_vs_logistic_regression_{m} (point difference, computed from the
       two point-metric dicts, calibration_gap = mean_predicted_probability - observed_prevalence) and
       test_diff_vs_logistic_regression_{m}_ci_lower/_upper (from boot.paired_differences). Also log
       bootstrap_summary.json (boot's contents) to each run via MlflowClient().log_dict. Build and return
       TrainingResult (comparison = PairedComparison(point, intervals)). ModelResult and TrainingResult are FROZEN
       dataclasses, so the ModelResult objects are constructed only AFTER Phase B (their confidence_intervals field
       needs the bootstrap output); Phase A keeps run_id, model_uri, y_prob and the point-metric dict in plain local
       variables. When n_bootstrap == 0 the xgboost run's test_diff_vs_logistic_regression_<m> point metrics are logged
       through the same MlflowClient().log_batch route as above (the run is already closed).
    Exceptions from MLflow (unwritable store, etc.) propagate; nothing is swallowed. A comparison rule
    for the report: a metric difference between the models is only claimed when its PAIRED 95% interval
    excludes 0."""
```

Model artifact contract for slice 4: each run has exactly one logged model (MLflow sklearn flavor, **cloudpickle** serialization) at the URI stored in the run tag `model_uri` (and `ModelResult.model_uri`). The tag holds an MLflow-3 LoggedModel URI (`models:/m-<id>`) that resolves ONLY against the same tracking store — verified: in a fresh process with the default tracking URI, `mlflow.sklearn.load_model` on that URI raises "Logged model with ID 'm-...' not found". Slice 4 therefore obtains a model ONLY through `tracking.load_logged_model(tracking_dir, run_id)` (see the `tracking.py` interface above), never by hand-building a `runs:/<id>/model` string and never by calling `load_model(tag_value)` in a process whose tracking URI is unset. It then calls `predict_proba` on exactly the frame `prepare_features()` returns (16 columns in `FEATURE_COLUMNS` order, categoricals as object dtype); the logged signature records that input schema. Cloudpickle caveats slice 4 inherits: loading needs the same library versions as the lockfile, and MLflow emits a pickle-safety warning on load. No Model Registry in this slice. **Autolog flavors (verified in a prototype in the environment that includes `pyspark`, which is a runtime pin):** the universal `mlflow.autolog()` also enables MLflow's `spark` and `pyspark.ml` flavors when `pyspark` is importable. In that environment every run prints a `PYSPARK_PIN_THREAD` warning, and when a `SparkSession` already exists in the process (e.g. `pytest tests/pipeline tests/models` in one session) it also prints "Exception raised while enabling autologging for pyspark … 'JavaPackage' object is not callable". The `exclude_flavors` names are MLflow's flavor MODULE names: `["pyspark"]` does NOT work (tested); `["spark", "pyspark.ml"]` does, and the warnings disappear. It is therefore applied BY DEFAULT in the single `mlflow.autolog(...)` call; the smoke gate and the unit tests run in the real pyspark-including environment, and at implementation the exclusion names must be re-confirmed against the pinned version's flavor list. **xgboost-flavor contingency:** a prototype found that one autolog call produced exactly 2 runs, no nested runs and no xgboost-flavor collision, so `"xgboost"` is NOT excluded by default; add it to `exclude_flavors` only if the smoke gate or `test_end_to_end_two_runs_and_metrics_logged` shows a collision. Evidence that WOULD trigger it: more than one top-level run per model, or a "param already logged with a different value" exception. NOT triggers (autolog always adds them, expected): `estimator.html` and `training_{confusion_matrix,precision_recall_curve,roc_curve}.png` per run, and a console WARNING that prints the truncated `cv` index arrays of the calibrated wrapper (noise only). Record any adjustment in the final report.

```python
# scripts/train_models.py  (stdlib argparse; thin wrapper, mirrors scripts/run_big_join.py)
# usage: python scripts/train_models.py --gold-dir data/gold/full_run --reference-date 20260916 --test-start-date 20230101
#   --gold-dir PATH (required)            --reference-date YYYYMMDD (required)      --test-start-date YYYYMMDD (required)
#   --train-start-date YYYYMMDD           --tracking-dir PATH (default mlflow)      --experiment-name STR (default readmission-risk)
#   --readmission-window-days INT (default 30, type=int)   --seed INT (default 42, type=int)
#   --calibration-method {sigmoid,isotonic} (default sigmoid, via argparse choices)
#   --calibration-cv-folds INT (default 5, type=int)       --n-bootstrap INT (default 500, type=int)
# Prints, in this order: (1) the split summary, one `key: value` per line; (2) per model, one line
#   `<model_name>: roc_auc=0.9340 [lo, hi] pr_auc=... brier_score=... [lo, hi] brier_skill_score=... expected_calibration_error=... [lo, hi]
#   calibration_gap=... [lo, hi]` (4-decimal floats; the bracketed interval follows each metric that has one and is omitted when
#   --n-bootstrap 0); (3) a `paired (xgboost_calibrated - logistic_regression):` block, one metric per line as `<metric>: <point> [lo, hi]`,
#   or the single line `(bootstrap skipped: --n-bootstrap 0)` in place of the intervals. Exact whitespace is not tested.
# Exit 0 on success. Exit 1, printing "ERROR: <message>" to stderr, on FileNotFoundError or ValueError --
# the two USER-CORRECTABLE failure types (bad path, bad data, bad flag). RuntimeError (a violated split
# invariant = an algorithm bug) and MLflow/OS errors (unwritable store, corrupt DB) are deliberately NOT
# caught: they propagate as an ordinary Python traceback (nonzero exit), because they indicate a bug or a
# broken environment rather than something the caller can fix by changing an argument. This differs from
# run_big_join.py's "exhaustive catch list" wording on purpose and the difference is stated, not implied.
# Not unit-tested (all logic lives in the tested modules), exactly like run_big_join.py; exercised by the
# manual verification run below.
```

## UX flow (pipeline, not UI)

1. **Prerequisite, one-time.** Add the pins to `requirements.in`; compile with the exact command under Surfaces touched > "Exact compile commands"; create `requirements-linux-extras.txt`; install with `pip install -e .`, `pip install --require-hashes -r requirements.txt -r requirements-linux-extras.txt`, `pip install --require-hashes -r requirements-dev.txt`; on macOS also `brew install libomp`. Then run the `requirements-dev.txt` consistency check (Surfaces touched).
2. **Smoke gate — must pass BEFORE any modeling code is written** (a design-check finding: the scratch resolver run only proves the pins are mutually *installable*, not that they *work together*). It runs in the environment produced by step 1's real `--require-hashes` install — not an ad-hoc one — as a throwaway script (scratchpad, not committed) that: imports `sklearn`, `xgboost`, `mlflow`, `pandas`, `pyarrow`, `matplotlib`; fits a small `CalibratedClassifierCV(Pipeline([ColumnTransformer(OneHotEncoder + passthrough), XGBClassifier]), cv=<explicit patient-grouped list>, ensemble=True)` on a toy frame with an object-dtype categorical column; calls `mlflow.set_tracking_uri("sqlite:///.../mlflow.db")`, `mlflow.autolog(log_models=False, log_datasets=False)`, fits inside `mlflow.start_run`, logs the model with `mlflow.sklearn.log_model(..., name="model", signature=infer_signature(X), serialization_format="cloudpickle")`, then `mlflow.autolog(disable=True)`. Pass criteria: no exception; exactly one run created (no nested runs); the model loads through the returned `model_uri` (with the tracking URI set) and its `predict_proba` matches the in-memory model; **AND the existing suite passes in that same environment (`pytest tests/pipeline`; needs the JDK 17 already on this machine) — this is the check that would have caught the pandas 3 / `pyspark.pandas` breakage, which the smoke script alone cannot see.** **Fallback rule if it fails:** for an import/fit incompatibility, step the failing package(s) down to the newest version that passes (most likely candidates: `xgboost` vs `scikit-learn` estimator tags; `mlflow` vs pandas 3), keep everything exact-pinned, and record the change. (The default-`skops` `UntrustedTypesFoundException` is NOT a version problem and is already resolved by `serialization_format="cloudpickle"`.) If autolog interferes with the XGBoost wrapper's inner fits, apply the `exclude_flavors=["xgboost"]` contingency in Interfaces. Nothing past this step proceeds until it passes.
3. `python scripts/train_models.py --gold-dir data/gold/full_run --reference-date 20260916 --test-start-date 20230101`
4. Internally: load gold → validate → censoring buffer → chronological test/train partition → purge → patient-disjoint → `prepare_features` → build both models → PHASE A, for each model: MLflow run, fit on train only, score the test window once, evaluate, log metrics/artifacts/model → PHASE B: one shared patient-clustered bootstrap over both models' test predictions → append CIs and paired differences to both runs.
5. Inspect results: `mlflow ui --backend-store-uri sqlite:///$(pwd)/mlflow/mlflow.db` (documented in README).

## Data handling

**Leakage rubric (each item is a deliberate design decision, not an accident):**

- **Nothing is fitted before the split.** The only pre-split transforms are stateless (`length_of_stay_days` arithmetic; constant `"MISSING"` fill). Every fitted statistic (scaler means/stds, one-hot category sets, XGBoost trees, calibrators) is fitted inside a `Pipeline` on training rows only. Inside `CalibratedClassifierCV`, the preprocessing is part of the cloned base estimator, so it is refit per calibration fold — the calibration fold's rows never influence the preprocessing fitted for that fold. A test asserts scaler statistics equal the training-frame statistics.
- **The test window is scored exactly once per model BY THE IMPLEMENTATION, and from implementation onward no decision (hyperparameters, feature set, calibration method) is made from test metrics.** But see the disclosure immediately below: the window has already been seen at design time. Hyperparameters are fixed constants (`XGB_PARAMS`). If the user changes a setting after seeing test results, that is a new experiment and the test window is no longer a clean holdout — stated here so it is not rediscovered later.
- **Features exclude everything post-index.** `organization_utilization` (whole-simulation total) is excluded; slice 2 already guarantees the 8 lookback counts read only data strictly before `index_start`; `length_of_stay_days` and `age_at_index_years` describe the index stay itself and are known at the prediction point (discharge). The label is never derivable from a feature column.
- **Patient-level leakage:** train and test are patient-disjoint (post-condition asserted at runtime), and calibration folds inside training are patient-grouped (`GroupKFold`). Repeated admissions of one patient can therefore never appear on both sides of any evaluation boundary.
- **Temporal leakage:** every test row starts on/after the cutoff; every train row's 30-day label window ends strictly before the cutoff (label purge), so no training label peeks at post-cutoff outcomes.
- **Reproducibility:** `seed=42` flows into `LogisticRegression`, `XGBClassifier`, and the bootstrap RNG; `GroupKFold` is unshuffled (deterministic); XGBoost `n_jobs=1`; `load_gold_table` returns a deterministically sorted frame; `gold_fingerprint` (exact recipe in Interfaces), all config values, the exact split membership (`split_assignment.csv`), the library versions, and a `git_dirty` flag are logged, on top of the git commit MLflow records itself (which alone cannot say whether the working tree matched that commit). A test asserts two runs with the same inputs produce identical test metrics.

**Resolution of the carried-forward censoring risk, and how to interpret test-window metrics.** Slice 2 keeps a censored positive but drops a censored negative. This slice applies slice 2's own observability rule to *all* rows (Step 1), so within the modeling set a row is present iff its label was fully observable — the asymmetry is removed at its source rather than merely documented. On the real gold table the buffer removes 2 rows (0.015%); the residual test-window bias attributable to censoring is therefore negligible. What remains, and is **not** fixed here (uniform across train and test, i.e. it biases absolute levels but not the train/test comparison):
  1. *Death exclusion:* slice 2 drops index encounters whose patient died within 30 days with no readmission observed but keeps those with an observed readmission, so patients at highest short-term mortality are under-represented among negatives everywhere. The model never sees them, but slice 4's triage list will score them.
  2. *Planned readmissions and reason-code dominance:* slice 2's all-cause label counts scheduled treatment cycles as readmissions. **Measured against the raw encounters (design-time check): the two lung-cancer admission reasons (non-small-cell stage 1, small-cell) account for 72.6% of ALL positives in the gold table (56.7% of test-window positives); for those index stays the readmitting stay has the SAME reason 98.5% / 99.8% of the time, arriving 28.8 ± 1.9 and 24.5 ± 2.1 days after discharge — i.e. fixed-interval treatment cycles that land just inside the 30-day window, a Synthea module script rather than clinical deterioration.** Separately, cardiac-imaging/aortic-valve reasons show next-stay gaps of ~2–3 days with a DIFFERENT reason (a diagnostic → procedure admission workflow), and rows with no recorded reason are 0.3% positive. Removing the lung-cancer rows does not remove the effect: on the remaining 1,745 test rows (7.3% positive) a reason-code-only lookup still reaches AUC 0.920. **Measured: a reason-code-only lookup already reaches test ROC-AUC 0.933** (0.934 with payer added). Reading the results: high absolute AUC (0.9+) is expected on this data and is not a leakage signal by itself; the honest question is what LR/XGBoost add beyond that reference (the manual verification step computes it in a scratch script and reports the models' gain over it, with the paired-bootstrap logic where applicable). Treat reported metrics as a demonstration of the pipeline and methodology on synthetic data, not a clinical result (consistent with the README's data-realism note). Leakage evidence comes from the design rubric above, the label-permutation canary, and the reference-baseline comparison — NOT from an absolute AUC threshold.
  3. *Truncation-era rows (57% of the table):* structurally different from the fully covered period and present only in training (test starts 2023). Expect a train/test feature-distribution shift — **measured after implementation: NOT modest** (mean `prior_inpatient_count` 2.68 for the 7,389 default-run training rows before 2017-09-16 versus 1.43 in the test window; positive rate 13.6% versus 10.1% for the clean training period; see the implementation addendum); `--train-start-date 20170916` re-runs on the clean period for a sensitivity check (~2.3k rows: informational only, too thin to be the primary model).
  4. *Patient-disjoint selection:* removing test patients' earlier rows from training leaves a training population with no post-cutoff admissions — **exact measured cost at the 2023-01-01 cutoff (the algorithm below, run on the real gold table): 44 rows purged by the label-window rule and 1,469 rows removed for patient overlap, leaving 9,631 training rows (1,233 positives, 4,185 patients; positive rate 12.80%) against 2,076 test rows (291 positives, 1,311 patients; positive rate 14.02%).** Expect a small calibration-in-the-large gap (~1 point) attributable to this + genuine drift, not to a bug; the reported `calibration_gap` makes it visible.
  5. *Wrong `reference_date`:* the gold table does not record the reference date. A date that is too EARLY makes the censoring buffer drop unexpectedly many rows — surfaced as a warning when more than 1% of rows are dropped (there is deliberately no hard error, because slice 2 keeps censored positives whose discharge can legitimately fall after the reference date); a date that is too LATE silently under-buffers and cannot be detected here. The CLI help and README state the flag must equal the Synthea/Big Join value; persisting it alongside the gold table (so this slice can validate it) is a named follow-up.

**Disclosure — the 2023+ test window is NOT a virgin holdout (round-4 critic finding; user decision 2026-09-19: disclose prominently).** During design, throwaway prototypes already scored exactly these models (same features, same `XGB_PARAMS`, sigmoid calibration) on exactly this test window; the `2023-01-01` cutoff, the exclusion of `organization_utilization`, and the choice to train on all pre-cutoff rows were all made after looking at measurements on this same data. Reported test metrics are therefore conditional on design choices informed by the data they are evaluated on, and should be read with the quality of development-set estimates, not as a final untouched-holdout result. This sentence (or its equivalent) must appear in the final report, in the README's results wording, and in the CLAUDE.md update. **Plan for a genuinely untouched evaluation (user context: v2 will scale to 100,000–200,000 patients):** the v2 population should be generated with a NEW Synthea seed (not 42) and a fresh `reference_date`; the models and every decision fixed on the 10,000-patient data are then evaluated once on that data, which is a virgin holdout by construction. Named follow-up; nothing in this slice depends on it.

**Class imbalance:** positive rate 12.15% overall, ~12.8% train / ~14.0% test at the chosen cutoff — moderate. No resampling/reweighting (distorts probabilities). PR-AUC is reported next to ROC-AUC because it is the more informative discrimination metric at this prevalence, and Brier skill score is reported against the constant-prevalence predictor.

**Scale:** ~13k rows here (10,000 patients); the user's stated v2 target is 100,000–200,000 patients, i.e. roughly 130k–265k gold rows (linear extrapolation from 13,222 rows per 10,000 patients) — still comfortably in-memory in pandas, XGBoost and the bootstrap (a 500-resample patient-clustered bootstrap over a few tens of thousands of test rows is cheap). At that size the `gold_fingerprint`'s `to_csv` recipe is still fast (seconds). No design choice here depends on the 10k size except the small-sample caveats above. No PII (synthetic).

## Failure modes

- `gold_dir` missing / no Parquet files → `FileNotFoundError` naming the path.
- Gold schema drift (missing OR extra column), zero rows, nulls in required columns, non-binary label, duplicate `encounter_id`, `index_stop < index_start` → `ValueError` naming the problem, before any modeling.
- `reference_date` earlier than the true run reference date: NOT a hard error (see Data handling item 5) — `warnings.warn` when the censoring buffer drops more than 1% of rows.
- MLflow experiment name that exists in a *deleted* state in the store → `ValueError` naming the experiment and the store (otherwise `set_experiment` would raise an opaque MLflow error).
- Invalid date strings, mis-ordered dates, bad `calibration_method`/`calibration_cv_folds`/`readmission_window_days`/`n_bootstrap` → `ValueError` naming the field.
- Empty train or test partition, or a single-class partition → `ValueError` naming the partition and the cutoff (e.g. a `test_start_date` too close to the censoring cutoff).
- Fewer than `calibration_cv_folds` distinct training patients, or a calibration fold lacking a class → `ValueError` naming the fold.
- Unseen categorical level at test time → handled (pooled into the encoder's infrequent bucket), not an error.
- Very few test positives (< 50): `warnings.warn` that the metrics and intervals are unreliable; run still completes.
- MLflow store unwritable / migration failure → exception propagates (no silent fallback to a different tracking location).
- Re-running with the same `--tracking-dir` appends new runs to the same experiment (no overwrite); each run is identified by its `run_id` and `gold_fingerprint`. Not an error.
- Split invariant violated (algorithm bug, not input) → `RuntimeError("split invariant violated: ...")`.

## Verification criteria

**Unit tests (synthetic pandas frames; no Spark JVM, no real data, all in CI).**

Shared test helpers live in `tests/models/helpers.py` (a plain module, imported as `from tests.models.helpers import make_gold_frame, row, REFERENCE_DATE, TEST_START, ...` — `tests/` is already a package via `tests/__init__.py`, and no existing test imports from a conftest, so helpers are NOT put in `conftest.py`, which holds fixtures only):

```python
def make_gold_frame(n_patients: int = 1200, seed: int = 0, *, planted_signal: bool = True,
                    first_admission: str = "2018-01-01", last_admission: str = "2026-06-30") -> pd.DataFrame
```
Returns a schema-exact frame with the 23 `EXPECTED_GOLD_COLUMNS` (plain gold dtypes; `index_start`/`index_stop` tz-aware UTC), built with `rng = np.random.default_rng(seed)`:
- Patient `i` gets id `f"p{i:05d}"` and `k = 1 + min(rng.poisson(1.2), 5)` admissions. Each admission: `index_start` uniform at second resolution between `first_admission` 00:00Z and `last_admission` 00:00Z; length of stay `1 + min(rng.exponential(3.0), 29)` days, so `index_stop = index_start + LOS`; `encounter_id = f"e{row:07d}"` (unique). (`last_admission` 2026-06-30 keeps every row clear of the 2026-08-18 censoring buffer for `reference_date=2026-09-16`.)
- Features: `prior_inpatient_count ~ Poisson(1.0)`; `prior_encounter_count = prior_inpatient_count + Poisson(3)`; `prior_emergency_count ~ Poisson(0.5)`; `active_condition_count ~ Poisson(8)`; `active_medication_count ~ Poisson(4)`; `procedure_count_window ~ Poisson(10)`; `active_careplan_count ~ Poisson(2)`; `observation_count_window ~ Poisson(100)`; `age_at_index_years ~ Uniform(18, 90)`; `organization_utilization ~ integer Uniform(200, 40000)`; `provider_specialty = "GENERAL PRACTICE"`; `payer_ownership ∈ {GOVERNMENT, PRIVATE, NO_INSURANCE}`; `gender ∈ {M, F}`; `race` from 3 values; `ethnicity` from 2; `marital_status ∈ {M, S, D, W, None}`; `admission_reason_code ∈ {"R_HIGH", "R_MID", "R_LOW", None}` with probabilities `[0.2, 0.3, 0.3, 0.2]`, `admission_reason_description = f"desc {code}"` (or `None`).
- Label: with `planted_signal=True`, `logit = -2.5 + 0.8*prior_inpatient_count + 1.6*(code == "R_HIGH") + 0.6*(code == "R_MID") - 4.0*(code is None)`, `is_readmitted ~ Bernoulli(sigmoid(logit))` (overall positive rate ≈ 21–24%, measured at 24.1% for seed 0, n = 2,000; a null reason code strongly implies negative, mirroring the real data). With `planted_signal=False`, `is_readmitted ~ Bernoulli(0.2)` independent of every feature.
- Sizing rationale (so the "learned something" assertions are not flaky). Patient-disjointness plus the label-window purge discard roughly 45–55% of the pre-cutoff rows, so TRAIN is much smaller than the test share suggests. Round-4 critic's simulation of this generator (its draw order may differ slightly from the one pinned above, so treat as approximate): 150 patients → train ≈ 100 rows (≈ 26 positives) / test ≈ 148 (41); 600 → ≈ 390 (≈ 89) / ≈ 540 (≈ 127); 1,200 → ≈ 720 (≈ 170) / ≈ 1,090 (≈ 235); 2,000 → ≈ 1,180 (≈ 270 positives) / ≈ 1,830 (≈ 440) (measured by the round-6 critic for the pinned generator at seed 0: train 1,178 / 269, test 1,827 / 439; 40-seed means 1,198 / 278 and 1,813 / 416). **If a fixed-seed "AUC > 0.7" assertion fails at implementation, strengthen the planted effect sizes above (and record that) — do not lower the threshold below 0.7.**
- **Draw order (so two implementers build the same frame from the same seed):** with ONE `rng = np.random.default_rng(seed)`, draw in exactly this order, each vectorized over all rows: (1) per-patient admission counts `k`; (0) the patient-to-row mapping `patient_index = np.repeat(np.arange(n_patients), k)`; (2) `index_start` offsets, drawn as `first_ts + pd.to_timedelta(rng.integers(0, span_seconds, size=n_rows), unit="s")` (second resolution); (3) LOS days `1 + np.minimum(rng.exponential(3.0, size=n_rows), 29)`, converted with `pd.to_timedelta(np.round(los_days * 86400).astype("int64"), unit="s")` (whole seconds); `organization_utilization` via `rng.integers(200, 40001, size=n_rows)` (inclusive of 40,000); (4) the features in the order listed above (`prior_inpatient_count` first … `organization_utilization` last); (5) the categoricals, in this order, each drawn with `rng.choice(values, size=n_rows, p=probs)`: `payer_ownership` from `["GOVERNMENT", "PRIVATE", "NO_INSURANCE"]` with `[0.55, 0.30, 0.15]`; `gender` from `["M", "F"]` with `[0.5, 0.5]`; `race` from `["white", "black", "asian"]` with `[0.7, 0.2, 0.1]`; `ethnicity` from `["nonhispanic", "hispanic"]` with `[0.85, 0.15]`; `marital_status` from `["M", "S", "D", "W", None]` with `[0.45, 0.25, 0.12, 0.08, 0.10]`; then `admission_reason_code` (probabilities as above); (6) the label draw LAST, exactly `is_readmitted = (rng.random(n_rows) < p).astype("int64")` (with `p` the row's readmission probability from the planted logit, or the constant 0.2 when `planted_signal=False`); counts are `rng.poisson(lam, size=n_rows)` with `lam` as listed. Tests must not depend on knife-edge values of any one fixed seed (see the canary tests for how).
- **Dtypes of the fixture frame BEFORE it is written to Parquet:** the 8 count columns and `organization_utilization` are `int64`; `age_at_index_years` is `float64`; `is_readmitted` is `int64` holding 0/1 (`load_gold_table` narrows it to `int8`); all string columns (`patient_id`, `encounter_id`, reason code/description, `payer_ownership`, `provider_specialty`, `gender`, `race`, `ethnicity`, `marital_status`) are `object` dtype holding Python `str` or `None`; `index_start`/`index_stop` are tz-aware UTC datetimes (the datetime UNIT — ns or us — is unconstrained; the recipe above yields ns on the pinned pandas). Tests that write the frame to Parquet use `DataFrame.to_parquet` (pyarrow). The round trip does NOT reproduce these dtypes exactly (verified under pandas 3.0.6 by the round-3 critic: `object` strings come back as `str` — on the pinned pandas 2.3.3 they stay `object`, but the loader's explicit cast makes the contract independent of that; real Spark INT96 timestamps come back naive `datetime64[ns]`; real Spark ints come back `int32`) — which is why `load_gold_table` normalizes to the POST-LOAD DTYPE CONTRACT in Interfaces and why the round-trip test asserts that contract plus VALUE equality (`pd.testing.assert_frame_equal(..., check_dtype=False)` against the fixture, after sorting by `(index_start, encounter_id)`), not dtype equality with the fixture.
- Split tests build their own tiny, hand-written frames instead, so each boundary is exact, through two helpers in `tests/models/helpers.py`: `row(patient_id: str, start: str, stop: str, label: int = 0, encounter_id: str | None = None) -> dict` — `start`/`stop` are ISO-8601 UTC strings with a trailing `Z` (e.g. `"2026-08-17T23:59:59Z"`), the result is a dict holding ALL 23 gold columns (counts `0`, `age_at_index_years` `50.0`, `organization_utilization` `1000`, string columns fixed constants, `admission_reason_code` `"R_LOW"`, `provider_specialty` `"GENERAL PRACTICE"`), and `encounter_id` defaults to `f"e-{patient_id}-{start}"` (unique per patient and start instant); and `frame(rows: list[dict]) -> pd.DataFrame`, which builds the frame and converts `index_start`/`index_stop` to tz-aware UTC with `pd.to_datetime(..., utc=True)`, so the split function's tz-aware precondition is met; and `pad_rows() -> list[dict]`, four rows that keep every hand-written split fixture valid (the split raises `ValueError` on an empty or single-class partition, so a frame holding only the boundary rows could not even be built): one negative and one positive TRAIN row (patients `pad_tr_0`/`pad_tr_1`, `index_start` 2019-01-01, `index_stop` 2019-01-05) and one negative and one positive TEST row (patients `pad_te_0`/`pad_te_1`, `index_start` 2024-01-01, `index_stop` 2024-01-05), all far from any boundary under test. Every boundary test appends `pad_rows()`; the "1 of 100 / 2 of 100" warning tests pad to exactly 100 rows in total; the naive-timezone reject case builds via `frame(...)` and then strips the zone with `.dt.tz_localize(None)`.
- **Shared test constants in `tests/models/helpers.py`** (alongside `make_gold_frame` and `row`; NOT in `conftest.py`): `REFERENCE_DATE = date(2026, 9, 16)`, `TEST_START = date(2023, 1, 1)` and their string forms `"20260916"` / `"20230101"`. **Every test that says "held-out" (the `test_modeling.py` AUC/canary tests) obtains its train/test frames the same way: `split = chronological_group_split(make_gold_frame(n), reference_date=REFERENCE_DATE, test_start_date=TEST_START)`, then `prepare_features(split.train)` / `prepare_features(split.test)`, fitting on train only and computing AUC on test. Every `test_training.py` test uses `reference_date="20260916"`, `test_start_date="20230101"` unless it says otherwise.**
- A `pytest` autouse fixture in `tests/models/conftest.py` records `mlflow.get_tracking_uri()` before each test and, **in its teardown only** (after the test body has finished), restores it and calls `mlflow.autolog(disable=True)` as a belt-and-braces guard. Because it acts only at teardown, assertions made inside a test body observe only the production code's own cleanup (its `finally`), never the fixture's — which is what `test_autolog_is_disabled_...` below relies on. **The module-scoped end-to-end fixture in `test_training.py` is yield-based: it saves `mlflow.get_tracking_uri()` before calling `train_and_evaluate`, and restores it (and calls `mlflow.autolog(disable=True)`) after the last test in the module — the function-scoped fixture alone cannot do this, because it only restores the value it saw at the start of each individual test.**

Tests:

- `test_data.py`
  - `test_every_gold_column_is_classified_exactly_once` — the invariant stated under `EXCLUDED_COLUMN_REASONS`; also asserts `len(FEATURE_COLUMNS) == 16` and `len(EXPECTED_GOLD_COLUMNS) == 23`.
  - `test_expected_gold_columns_match_big_join` — imports `GOLD_TABLE_COLUMNS` from `readmission_risk.pipeline.big_join` (the only place in the models slice that touches pyspark — test-only, no JVM is started) and asserts it equals `EXPECTED_GOLD_COLUMNS` exactly, in order.
  - `test_load_gold_table_reads_spark_style_directory` — a `tmp_path` directory of two part files plus a `_SUCCESS` marker loads to one frame that satisfies the POST-LOAD DTYPE CONTRACT (strings `object`, the 8 count columns `int64`, age `float64`, label `int8`, timestamps tz-aware UTC; `organization_utilization` unconstrained), with rows sorted by `(index_start, encounter_id)` and values equal to the fixture's (`check_dtype=False`); a variant writes the counts as `int32` and timestamps as naive (what Spark actually emits) and must load to the same contract; a further variant with a NULL `organization_utilization` loads successfully (the column is excluded from features and never validated).
  - `test_load_gold_table_missing_or_empty_dir` — nonexistent path and existing-but-empty dir → `FileNotFoundError`.
  - `test_load_gold_table_rejects_bad_data` (parametrized) — missing column, extra column, zero rows, null label, null count feature, label value 2, duplicate `encounter_id`, `index_stop < index_start` → `ValueError` each naming the cause.
  - `test_prepare_features_shape_and_missing_fill` — output columns == `FEATURE_COLUMNS` exactly and in order; every categorical column has `object` dtype; null `admission_reason_code`/`marital_status` → `"MISSING"`; `length_of_stay_days` for a 36-hour stay is exactly `1.5`; the input frame is unchanged afterwards (`pd.testing.assert_frame_equal` against a pre-call deep copy — no in-place mutation).
- `test_split.py` (each fixture uses `reference_date=2026-09-16`, `W=30`, `test_start=2023-01-01`)
  - `test_censor_buffer_boundary_symmetric` — rows with `index_stop` at `2026-08-17T23:59:59Z` (kept) vs `2026-08-18T00:00:00Z` (dropped), each as a positive AND a negative, so positives in the buffer are dropped too.
  - `test_warns_when_censor_buffer_drops_more_than_one_percent` / `test_no_warning_at_or_below_one_percent` — a fixture where the buffer drops 2 of 100 rows emits a `UserWarning` matching `"reference_date"` (`pytest.warns(UserWarning, match="reference_date")`); 1 of 100 does not.
  - `test_test_partition_is_on_or_after_test_start` — a row at exactly `2023-01-01T00:00:00Z` is test; one a second earlier is not.
  - `test_label_window_purge_boundary` — a pre-cutoff row with `index_stop + 30d == T − 1s` is kept in train; `== T` exactly and `+1s` are both purged (slice 2's window is inclusive at the upper bound — see the addendum), so `n_dropped_label_window_purge == 2` in that fixture.
  - `test_patient_disjoint_drops_overlapping_patients_from_train` — a patient with rows both before and after the cutoff appears only in test; their pre-cutoff rows are counted in `n_dropped_patient_overlap`, absent from train.
  - `test_split_postconditions_hold_property` — over several seeds of `make_gold_frame`, patient sets disjoint, no shared `encounter_id`, `train.index_start.max() < T <= test.index_start.min()`.
  - `test_summary_accounting_identity` — `n_input` equals the sum of the six terms stated in Interfaces, for a fixture that exercises every drop reason at least once.
  - `test_train_start_date_only_filters_train` — rows before `train_start_date` are dropped from train (counted in `n_dropped_before_train_start`) and the test partition is unchanged.
  - `test_split_rejects_bad_inputs` (parametrized) — naive-timezone timestamps, `train_start >= test_start`, `test_start >= reference_date`, `W=0`, empty train, empty test, single-class train, single-class test → `ValueError`. The empty-partition cases assert `ValueError` specifically (NOT `RuntimeError`), pinning the check ordering (emptiness/one-class errors are evaluated before the post-conditions).
- `test_features.py`
  - `test_preprocessors_fit_on_train_only` — after fitting on train, the linear preprocessor's fitted scalers (`named_transformers_["counts"].named_steps["scale"].mean_` and `named_transformers_["age"].mean_`) equal the train-frame statistics (computed independently — for "counts", of `np.log1p` of the columns) and does NOT equal the all-rows statistic on a fixture built so they differ; `transform`ing a test frame does not change the fitted state.
  - `test_linear_preprocessor_feature_names` — after fitting on a `make_gold_frame` train split, `get_feature_names_out()` succeeds (the `feature_names_out="one-to-one"` on the log1p transformer is what makes it possible — without it the call raises `AttributeError`, verified by the round-3 critic), returns a name array whose length equals the transformed matrix width, contains the UNPREFIXED names `"prior_encounter_count"` and `"age_at_index_years"` (guaranteed by `verbose_feature_names_out=False`) and at least one one-hot name starting with `"admission_reason_code_"`.
  - `test_unseen_category_lands_in_infrequent_bucket_when_one_exists` — a fitted column that has a category with < 20 training rows: an unseen category and a rare training category transform to the identical vector (the infrequent-bucket column set to 1).
  - `test_unseen_category_is_all_zero_when_no_infrequent_bucket` — a fitted column where every category has >= 20 training rows: an unseen category transforms without error to an all-zero block for that column (the documented `infrequent_if_exist` fallback).
- `test_modeling.py`
  - **Model configuration for every `test_modeling.py` test unless stated:** model seed `42`, `calibration_cv_folds=5` (`make_grouped_cv_splits(y, groups, 5)`), method `"sigmoid"` — the calibration/canary bounds below were measured over FIXTURE seeds with these settings.
  - `test_logistic_regression_learns_planted_signal` — held-out AUC > 0.7 on `make_gold_frame(2000)` (n PINNED for both learning tests: at n = 1,200 the calibrated XGBoost AUC dipped to 0.722 across 20 seeds — too close to 0.7), `predict_proba` within `[0, 1]`, and `class_weight is None` on the final estimator.
  - `test_grouped_cv_splits_are_patient_disjoint_and_two_class` — no patient in both fit and calibration parts of any fold; a fixture with too few patients or a single-class fold → `ValueError` naming the fold.
  - `test_calibrated_xgboost_structure_and_probabilities` — is a `CalibratedClassifierCV` with `ensemble=True` and the requested `method`; the wrapped `XGBClassifier` has `n_jobs == 1`, the injected `random_state`, and `scale_pos_weight` unset; `predict_proba` rows sum to 1 and lie in `[0, 1]`; an invalid `method` → `ValueError`.
  - `test_calibrated_xgboost_learns_planted_signal` — held-out AUC > 0.7 on `make_gold_frame(2000)`.
  - `test_models_are_calibrated_on_planted_signal` — calibration is the project's stated top priority, so it gets an automated check, not only manual review: on `make_gold_frame(2000)` with the shared split (train ≈ 1,180 rows / ≈ 270 positives, test ≈ 1,830 / ≈ 440 — small, which is what drives the bounds below), **logistic regression** satisfies `expected_calibration_error < 0.06`, `abs(calibration_gap) < 0.04`, `brier_skill_score > 0.10`; **calibrated XGBoost** satisfies `expected_calibration_error < 0.09`, `abs(calibration_gap) < 0.05`, `brier_skill_score > 0.08`. Bounds are set with ≥ 20% headroom over the worst value seen in the round-5 critic's 40-seed measurement (seeds 0–39, n = 2,000, this generator's pinned draw order): LR ECE peak 0.043 and |gap| peak 0.033 (an earlier `< 0.03` bound failed seed 39 and passed seed 8 by 0.0002); XGBoost ECE peak 0.073 and |gap| peak 0.033. (The round-4 six-seed figures — XGBoost ECE 0.039–0.056, BSS 0.117–0.205 — were what sank the original `< 0.05`.) **If the pinned-seed run exceeds a bound, first re-measure across seeds 0–5 — only a persistent exceedance across seeds indicates a model defect; a single-seed miss is not evidence of one. If it IS persistent, investigate the pipeline/model first (preprocessing, calibration wiring, cv-split coupling); loosen a bound only with a recorded justification from a wider multi-seed measurement, never to make a red test green.**
  - `test_models_are_deterministic` — fitting each model twice from scratch with the same seed gives identical `predict_proba` (`np.testing.assert_allclose(..., atol=1e-12)`).
  - `test_label_permutation_canary` — `make_gold_frame(2000)`, the shared split, and **K = 10 different permutations** of the training labels (`np.random.default_rng(i).permutation`, `i = 0..9`): the MEAN held-out AUC of the logistic regression across the 10 permuted fits lies in [0.4, 0.6], AND the un-permuted model's held-out AUC exceeds every one of the 10 permuted AUCs. **Why a mean over permutations, not a single permutation with a "5.8σ" tolerance (the earlier draft's claim was WRONG, prototype-verified):** the sampling standard error of a null AUC (≈ 0.017) is not what varies here; a model fitted to permuted labels has RANDOM coefficients, some of which land on the strong features, so a single permuted AUC has SD ≈ 0.067 (range 0.385–0.651 over 24 draws; 5 of 24 fell outside [0.4, 0.6], and a fixed seed's outcome depended on the implementer's RNG stream). The average over K permutations has SD ≈ 0.067/√10 ≈ 0.021, making ±0.1 ≈ 4.7σ. **If this fails at implementation, increase K — do not widen the tolerance beyond [0.35, 0.65].** This is the standard canary that a pipeline has no label-independent path to the answer.
  - `test_label_permutation_canary_calibrated_xgboost` — the same canary for the calibrated XGBoost path (same `make_gold_frame(2000)`; K = 5 permutations; mean held-out AUC in [0.4, 0.6] and the un-permuted model's AUC exceeds all 5; the XGBoost path's null variance was NOT measured at design time, so if the fixed-seed run fails, raise K, do not widen beyond [0.35, 0.65]). The calibrated wrapper's positional cv-split coupling lives on THIS path, so the canary must cover it, not only the logistic regression.
  - `test_validate_cv_splits_detects_reordered_or_mismatched_groups` — splits built from `groups_a`; `validate_cv_splits(splits, groups_a)` passes; validating the same splits against a permuted copy of the groups (the "X_train was reordered after the splits were built" scenario), against a shorter array, and against splits with an overlapping fit/validation part each raise `ValueError` naming the fold.
- `test_evaluate.py`
  - `test_metrics_match_hand_computed_values` — a 6-row toy case, called with `n_bins=3` PINNED (with the default 10 bins and n = 6, every row lands in its own bin and ECE degenerates to mean|y − p|, testing nothing about binning), with hand-computed AUC, Brier, Brier skill score, ECE, `calibration_gap` and `abs_calibration_gap` (to 1e-9); a second case with `n_bins=10` asserts the documented "non-empty bins only" behavior.
  - `test_evaluate_handles_constant_predictions` — `evaluate_probabilities` with every `y_prob` identical (so `_quantile_bin_ids` takes its single-bin fallback) does not raise: exactly one calibration-curve bin, `roc_auc == 0.5`, `expected_calibration_error == |observed_prevalence − that constant|`; the same single-bin behavior holds for the bootstrap's per-resample ECE (a resample of constant predictions yields a finite ECE, not NaN).
  - `test_calibration_curve_bins_sum_to_n_and_are_ascending`; `test_ece_is_zero_for_perfectly_calibrated_bins` (each bin's mean prediction equals its observed fraction by construction); `test_log_loss_finite_at_extreme_probabilities` (predictions of exactly 0 and 1).
  - `test_evaluate_rejects_bad_inputs` (parametrized) — length mismatch, empty, non-binary `y_true`, single-class `y_true`, `y_prob` outside `[0, 1]`, NaN.
  - `test_cluster_resample_indices_returns_whole_patients_and_is_deterministic` — targets the named helper: for a fixture with unequal-size patients, every returned index set is a union of complete patients' rows (per-patient row counts in the result are whole multiples of that patient's size, and a patient drawn twice appears twice); the same `np.random.default_rng(seed)` state yields identical output.
  - `test_clustered_bootstrap_deterministic_and_covers_point_estimate` — same seed → identical `BootstrapResult`; all five `BOOTSTRAP_METRICS` present per model; the point estimate lies inside its interval for `roc_auc`, `brier_score` and the signed `calibration_gap` on a well-behaved fixture (`abs_calibration_gap` is excluded too: it is folded at 0 and its bootstrap is biased upward like ECE's). **`expected_calibration_error` is deliberately EXCLUDED from that assertion:** quantile-binned ECE is biased upward under resampling, and a simulation of 30 well-calibrated n=800 fixtures had the point ECE fall outside its own 95% percentile interval in 4 of 30 (13%) — asserting containment would be a flaky test of a property that does not hold.
  - `test_clustered_bootstrap_paired_difference_is_within_resample` — with `probs = {"a": p, "b": p}` (identical predictions) every paired-difference interval is exactly `(0.0, 0.0)`, which independent intervals could not produce; with `b` a strictly better ranker than `a`, the paired `roc_auc` interval excludes 0.
  - `test_clustered_bootstrap_all_resamples_single_class_raises` — a fixture with a single positive row among many patients and a tiny `n_bootstrap` engineered so every resample lacks a positive raises `ValueError` matching `"single-class"`.
  - `test_clustered_bootstrap_zero_and_skipped_resamples` — `n_bootstrap=0` → `BootstrapResult({}, {}, 0, 0)`; a fixture engineered so some resamples are single-class reports them in `n_resamples_skipped` and warns (`UserWarning` matching `"single-class"`) when more than 5% are skipped.
  - `test_reliability_diagram_writes_nonempty_png`.
- `test_training.py` (integration; the gold Parquet is written to a directory from `tmp_path_factory` (a MODULE-scoped fixture cannot use the function-scoped `tmp_path`, which raises `ScopeMismatch`; function-scoped tests may use `tmp_path`) from `make_gold_frame(600)`; `tracking_dir` is a `tmp_path_factory` directory too; `n_bootstrap=50` to keep CI fast; the end-to-end call is a module-scoped fixture reused by the assertions below)
  - `test_end_to_end_two_runs_and_metrics_logged` — exactly 2 runs in the experiment (via `mlflow.search_runs`), none carrying a `mlflow.parentRunId` tag (autolog spawned no nested per-fold runs); each run has `test_roc_auc`, `test_pr_auc`, `test_brier_score`, `test_expected_calibration_error`, `test_calibration_gap`, and `test_<m>_ci_lower`/`_ci_upper` for all five `BOOTSTRAP_METRICS`, params `seed`/`test_start_date`/`split_n_train`, tags `model_name`/`gold_fingerprint`/`git_dirty` (∈ `{"clean","dirty","unknown"}`)/`model_uri`/`sklearn_version`/`xgboost_version`/`mlflow_version`/`pandas_version`/`numpy_version`, and artifacts `calibration_curve.json`, `split_summary.json`, `feature_columns.json`, `split_assignment.csv`, `reliability_diagram.png`, `bootstrap_summary.json`; the xgboost run additionally has `test_diff_vs_logistic_regression_roc_auc` and its `_ci_lower`/`_ci_upper`; the extra artifacts/params autolog adds are tolerated, but the run count is exact.
  - `test_gold_fingerprint` — (a) calling `data.gold_fingerprint` directly on frames: identical frames give identical digests; the digest is invariant to input row order; changing ONE label changes it; changing ONE FEATURE value (e.g. a `prior_encounter_count`) with all ids and labels unchanged ALSO changes it (the property the earlier ids-and-labels-only recipe lacked); the digest is a 64-character lowercase hex string. (b) the `gold_fingerprint` tag written by the module's end-to-end run equals `data.gold_fingerprint(load_gold_table(gold_dir))`.
  - `test_tracking_uri_is_explicit_sqlite` — the module-scoped end-to-end fixture records `mlflow.get_tracking_uri()` IMMEDIATELY after `train_and_evaluate` returns (and restores the pre-fixture URI in its own teardown — see the hygiene note below, so nothing leaks into later modules and this test does not depend on a leak); the test asserts that recorded value equals `mlflow_tracking_uri(tracking_dir)` (= `sqlite:///<abs>/mlflow.db`), that the file exists, and that `mlflow.get_experiment_by_name(experiment_name).artifact_location` EQUALS `(tracking_dir.resolve() / "artifacts").as_uri()` (a `file:///...` string, exactly the value step 5 passes to `create_experiment`) — an equality on the URI string, not a path-prefix check. `test_mlflow_tracking_uri_helper` separately asserts the helper's exact string form for a relative and an absolute `tracking_dir`.
  - `test_logged_model_roundtrips_via_load_logged_model` — the test first points `mlflow.set_tracking_uri(...)` at an UNRELATED empty store (simulating slice 4's fresh process), then for both runs calls `tracking.load_logged_model(tracking_dir, run_id)` — the only sanctioned route; it does NOT hand-build a `runs:/<id>/model` string, and a companion assertion shows that calling `mlflow.sklearn.load_model(<the tag's URI>)` directly against the unrelated store fails (documenting why the helper exists). The loaded model's `predict_proba(prepare_features(test_rows))` equals the in-memory result; the logged signature exists and its input column names equal `FEATURE_COLUMNS` in order. `test_load_logged_model_missing_tag_raises_value_error` covers a run without a `model_uri` tag.
  - `test_split_assignment_matches_split_summary` — the logged `split_assignment.csv` has `n_train` "train" rows and `n_test` "test" rows and no `encounter_id` in both.
  - `test_same_inputs_give_identical_test_metrics` — two full runs with separate `tracking_dir`s produce identical `test_*` metrics — compared with `np.testing.assert_allclose(rtol=0, atol=1e-12)` (bit-identical results were observed locally, but BLAS threading on CI is not guaranteed to reproduce them bit-for-bit).
  - `test_autolog_is_disabled_after_success_and_after_failure` — (a) after `train_and_evaluate` returns normally, fitting a plain scikit-learn `LogisticRegression` on tiny data (tracking URI still pointing at the test store) leaves `mlflow.active_run()` as `None` and the experiment's run count unchanged (autolog, if still enabled, would have created and closed a run); (b) with `monkeypatch.setattr("readmission_risk.models.training.evaluate_probabilities", <function raising RuntimeError("boom")>)` — a name `training.py` therefore MUST import into its own namespace (`from .evaluate import evaluate_probabilities`) — `train_and_evaluate` raises `RuntimeError` from INSIDE the try region (after autolog was enabled, during Phase A), and the same tiny-fit check afterwards again shows no new run — measured against the run count taken AFTER the failing call (that call itself leaves one FAILED `logistic_regression` run in the experiment, which is not "new" for this check) — proving the `finally` fires on failure.
  - `test_bad_fold_config_leaves_no_mlflow_side_effects` — a config whose `calibration_cv_folds` exceeds the number of distinct training patients (raising `ValueError` from `make_grouped_cv_splits` in step 4) leaves the config's `tracking_dir` UNCREATED (no `mkdir`, no `mlflow.db`), and — because step 5 never ran, so autolog would otherwise log into whatever the current default store is — the test FIRST points `mlflow.set_tracking_uri(...)` at a separate tmp sqlite store, and afterwards asserts that store contains NO runs and `mlflow.active_run()` is `None` after a plain scikit-learn fit (autolog not enabled): proves step 4 really runs before step 5's MLflow setup.
  - `test_deleted_experiment_raises_value_error` — an experiment created then deleted (`MlflowClient().delete_experiment`) under the target name → `ValueError` naming it.
  - `test_warns_when_few_test_positives` — `make_gold_frame(150, seed=0)` (seed PINNED) with the shared `reference_date="20260916"`/`test_start_date="20230101"`, `calibration_cv_folds=3` and `n_bootstrap=20` (a simulation across seeds 0–7 gave 19–45 test positives — under 50 but with thin margin at some seeds, hence the pin). The test FIRST asserts the fixture precondition (`chronological_group_split(...).summary["n_test_positive"] < 50` and both classes present in train and test) so that a change to the generator fails loudly as a broken precondition rather than as a confusing missing-warning failure; if it ever fails, adjust `n_patients`. With the precondition holding, the run emits a `UserWarning` matching `"unreliable"` and still completes.
  - `test_n_bootstrap_zero_skips_phase_b` — a run with `n_bootstrap=0` completes; no run contains any `test_*_ci_lower`/`_ci_upper` or `test_diff_vs_logistic_regression_*_ci_*` metric, no `test_bootstrap_resamples_used` metric and no `bootstrap_summary.json` artifact; the point metrics and `test_diff_vs_logistic_regression_<m>` point differences ARE still logged; the returned `ModelResult.confidence_intervals` and `PairedComparison.intervals` are `{}` while `PairedComparison.point` is populated.
  - `test_rejects_invalid_config` (parametrized; each case starts from a valid config and changes ONE field): `test_start_date="2023-01-01"` (wrong format), `train_start_date="20230101"` (equal to `test_start_date`), `test_start_date="20261231"` (after `reference_date`), `calibration_method="platt"`, `calibration_cv_folds=1`, `readmission_window_days=0`, `n_bootstrap=-1` → `ValueError` each naming the field.
- `ruff check .` and `pytest` pass locally (full suite, including slice 1/2's tests).
- **Repo-integrity check:** `git check-ignore -v` on every new source/test/script path prints nothing, and `git status --short` lists them all as untracked — verified after the `.gitignore` fix and again in the final report.

**Manual/integration verification on the real gold table (not CI):**

1. `python scripts/train_models.py --gold-dir data/gold/full_run --reference-date 20260916 --test-start-date 20230101` completes with exit 0.
2. **Gold-snapshot identity, checked FIRST:** the run's `gold_fingerprint` tag must equal `9c10be06ca875760564f661761b8f256326246381b8fcfcf30e9f0481a0b5aca` (computed by the round-4 critic with the documented recipe on `data/gold/full_run`; the recipe gave an identical hash under pandas 2.3.3 and 3.0.6; **to be re-derived independently at implementation** — if it does not match, the gold table differs from the design-time snapshot and every pinned count below is untrustworthy until explained). Provenance of that snapshot, verified by the elephant this session: `data/raw/full_run/generation_summary.json` records `population_size` 10,000, `seed` 42, `reference_date` `20260916` (11,490 `patients.csv` rows, 690,829 `encounters.csv` rows), consistent with the gold table's 5,508 patients and the `--reference-date 20260916` used throughout.
3. **Split matches the exact numbers computed for this design by running the algorithm on the real table** (the data and algorithm are deterministic, so ANY deviation is a bug to investigate before proceeding, not noise): `n_input=13222`; `n_dropped_censor_buffer=2`; `n_test=2076` (`n_test_positive=291`, `n_test_patients=1311`, `test_positive_rate≈0.1402`); `n_dropped_before_train_start=0`; `n_dropped_label_window_purge=44`; `n_dropped_patient_overlap=1469`; `n_train=9631` (`n_train_positive=1233`, `n_train_patients=4185`, `train_positive_rate≈0.1280`); the accounting identity holds (2 + 2,076 + 0 + 44 + 1,469 + 9,631 = 13,222).
4. **Sanity checks** (flag for investigation, not hard failures): test ROC-AUC < 0.55 for either model → suspect a bug; Brier skill score ≤ 0 → no better than predicting the train prevalence; `|calibration_gap|` > 0.03 → calibration problem beyond the explained ~1-point drift; test ROC-AUC ≥ 0.99 for either model → investigate for leakage (a one-feature reason-code lookup already gets 0.933, so a near-perfect score on a noisy 30-day label is implausible). **There is no "AUC ≥ 0.95 ⇒ leakage" band** — that band was in the first draft and was removed after measuring that a reason-code-only lookup already reaches 0.933.
5. **Reference baseline** (scratch script, not committed): re-implement the reason-code-only lookup (per-code positive rate from the TRAIN partition, additive smoothing `k=10` toward the train prevalence, null reason = its own key, unseen code = train prevalence) and record its test ROC-AUC (expected ≈ 0.933) and Brier score, ALSO on two subsets of the test window (measured at design time: 331 test rows whose reason is one of the two lung-cancer descriptions, positive rate 49.6%, reason-only AUC 0.763; and the remaining 1,745 rows, positive rate 7.3%, reason-only AUC 0.920) — so the write-up can say how much of any model's AUC is the scripted lung-cancer pathway versus the rest. Report LR and XGBoost's gain over it in AUC and Brier. This — not an absolute AUC threshold — is how "did the model add anything" is answered.
6. **Label-permutation test on real data** (scratch script, not committed). **The earlier draft's criterion — "a single permuted refit's patient-clustered bootstrap 95% interval must include 0.5" — was prototype-tested and is INVALID: on the real split, 7 of 12 permutations produced an interval that excluded 0.5 (permuted-model AUCs ranged 0.357–0.626), because a bootstrap captures test-sampling variance only, while a model fitted on permuted labels carries random coefficients (some on the one-hot `admission_reason_code` columns, which alone reach test AUC 0.933).** Correct formulation: refit each model on K = 100 different permutations of the TRAINING labels and score the untouched test window each time. Pass = the mean permuted AUC is within [0.45, 0.55] AND the real (unpermuted) AUC is above the 95th percentile of the permuted distribution (report the empirical p-value = (1 + #{permuted ≥ real}) / (1 + K)). For the calibrated-XGBoost path use K = 20 if 100 fits is too slow, and say so.
7. **LR vs XGBoost:** report each model's point metrics with intervals and the PAIRED differences. **Pre-specified primary comparison: Brier score** (a proper scoring rule that rewards calibration and discrimination together); every other metric is secondary/exploratory, and the five bootstrap metrics are NOT multiplicity-corrected, so read a lone secondary difference cautiously. Claim a difference on a metric only if its paired 95% interval excludes 0. **The signed `calibration_gap` difference is reported for DIRECTION ONLY and is excluded from the claim rule** (it cannot say which model is better calibrated); calibration claims use `expected_calibration_error` and `abs_calibration_gap`. **Design-time expectation, from the round-5 and round-6 critics' 500-resample paired bootstraps on the real split (XGBoost minus LR; per-model signed gaps LR −0.0175, XGBoost −0.0077): signed gap +0.0098 [0.0007, 0.0182] (direction only); ECE +0.0191 [0.0081, 0.0270]; `abs_calibration_gap` −0.0098 [−0.0167, +0.0025] (includes 0); Brier −0.0016 [−0.0045, 0.0016]; ROC-AUC −0.0077 [−0.0168, 0.0002].** Under this rule: no primary-metric (Brier) difference is claimable; on calibration the two claim metrics DISAGREE — ECE favours LR while `abs_calibration_gap` is inconclusive (its point estimate even leans toward XGBoost). **Pinned wording for that situation: "calibration comparison mixed — ECE favours logistic regression; calibration-in-the-large inconclusive; no overall winner." A calibration-superiority claim requires BOTH `expected_calibration_error` and `abs_calibration_gap` paired intervals to exclude 0 in the same direction; if only one does, report it as "suggested by <metric> only".** The write-up must not be surprised by, or spin, that outcome. `expected_calibration_error` is binning-sensitive and its bootstrap is biased upward — report it, but do not let it alone decide anything. **If the challenger wins the primary metric (Brier) but is WORSE on a calibration metric (a prototype already saw exactly this: calibrated XGBoost Brier 0.0612 vs LR 0.0628, but ECE 0.044 vs 0.025 with the paired ECE difference excluding 0), the report states both facts and declares no overall winner** — because the project ranks calibration above raw discrimination, "the challenger is better" is only claimed when the primary metric and the calibration metrics agree. **What the intervals do and do not cover:** they capture test-sampling variance only (patients resampled); each model is fitted once, so variance from refitting (different seeds/folds) is NOT reflected. Report calibration (Brier, ECE, `calibration_gap`, curve) as the primary comparison per the project's stated priority, discrimination (ROC-AUC, PR-AUC) second.
8. `mlflow ui` shows the `readmission-risk` experiment (plus MLflow's auto-created, empty `Default` experiment, id 0) with two runs, the artifacts above; `mlflow/` is git-ignored.
9. A `--train-start-date 20170916 --experiment-name readmission-risk-sensitivity` run completes; its metrics are reported next to the primary run (informational only). Its split is deterministic too, so the same "any deviation is a bug" rule applies (computed by running the algorithm on the real table): `n_dropped_before_train_start=8091`, `n_dropped_label_window_purge=44`, `n_dropped_patient_overlap=767`, `n_train=2242` (`n_train_positive=227`, `n_train_patients=1396`), the test partition unchanged (2,076 rows), identity 2 + 2,076 + 8,091 + 44 + 767 + 2,242 = 13,222; all five inner calibration folds contain both classes.
10. **Linux lockfile verification (the CI proxy).** Requires Docker Desktop to be running (it was NOT running when this doc was written — the user must start it): `docker run --rm --platform linux/amd64 -v "$PWD":/src:ro python:3.12-slim bash -c 'set -e; apt-get update -qq && apt-get install -y -qq libgomp1 >/dev/null; mkdir /work; cd /src && tar --exclude=./data --exclude=./.venv --exclude=./mlflow --exclude=./tools --exclude=./.git -cf - . | tar -xf - -C /work; cd /work && pip install -e . && pip install --require-hashes -r requirements.txt -r requirements-linux-extras.txt && pip install --require-hashes -r requirements-dev.txt && pytest tests/models -q'`. Notes: `libgomp1` (the OpenMP runtime XGBoost's Linux wheel loads at import) is installed explicitly because the slim image lacks it — it is a system library, not a lockfile matter, and `ubuntu-latest` (CI) ships it already; the `tar --exclude` copy keeps the multi-GB `data/`, `.venv/`, `mlflow/`, `tools/` and `.git` out of the container. (On an Apple-Silicon host `--platform linux/amd64` runs everything under emulation; a failure that looks like a crash, timeout or illegal-instruction inside XGBoost is more likely an emulation artifact than a lockfile problem — re-check natively/`linux/arm64` before blaming the lock. The lockfile check itself, i.e. the pip installs, is unaffected by emulation.) Pass = every pip install completes with no "hashes missing"/unpinned-dependency error and `tests/models` passes (no JDK is needed: nothing in `tests/models` starts a JVM; `test_expected_gold_columns_match_big_join` only imports `pyspark`). **If Docker is not started, or the user declines, this check is reported as NOT performed** — the first CI run on push then becomes the only Linux verification, and the final report says so plainly rather than implying the lockfile was tested on Linux.

## Out-of-scope follow-ups

- Hyperparameter tuning with nested patient-grouped + chronological validation.
- An uncalibrated-XGBoost comparison row, to demonstrate calibration's effect directly.
- Feature attribution / explainability (SHAP or permutation importance) — the brainstorm's "model explainability view".
- Excluding planned readmissions (slice 2 label) and per-reason-code stratified evaluation; excluding or separately handling Synthea-module-artifact reason codes.
- Persisting `reference_date` (and `readmission_window_days`) alongside the gold table so this slice can validate them instead of trusting flags.
- Point-in-time `organization_utilization` (already a slice-2 follow-up); if built, re-admit it as a feature.
- Pure chronological (patient-overlapping) split as a *diagnostic only*, to quantify how much patient memorization would inflate metrics.
- Rolling-origin backtesting across several test windows; sensitivity of results to `test_start_date`.
- BigQuery `to_dataframe()` loader; hosted MLflow; Model Registry; drift monitoring.
- Subgroup/fairness evaluation across `race`/`gender`/`ethnicity`.
- Decision-threshold selection and Top-N triage behaviour (slice 4).
- **Standing to-do (user request, 2026-09-19): check whether upgrading to pandas 3 (and numpy 2.5.x) is now possible.** Trigger to re-test: a PySpark release whose `pyspark.pandas` imports under pandas 3 (PySpark 4.1.3 and 4.2.0 declare only `pandas>=2.2.0`, no ceiling, so nothing will warn — re-test by hand: bump the two pins in `requirements.in`, recompile, run the full suite incl. `tests/pipeline/test_big_join.py`). Recorded here, next to the pins in `requirements.in`, and in CLAUDE.md.
- **Untouched validation on the v2 population:** generate the 100,000–200,000-patient v2 dataset with a NEW Synthea seed and fresh `reference_date`, and evaluate the models (and every decision fixed on the 10k data) on it once — the only way to obtain a virgin holdout after the design-time exposure disclosed under Data handling.
- Committing the reason-code-only reference baseline as a third tracked MLflow run (this slice only computes it in a manual scratch script), so "gain over the trivial baseline" is reproducible from the tracking store.
- A `requirements-linux-extras.txt` generator/checker script (fetch hashes from PyPI, evaluate markers for Linux) so the hand-maintained file cannot silently go stale; and/or a CI matrix leg that also runs on macOS.

## Revision log

**Round 1 → round 2.** Round 1: comprehension `passed`; critic `design needs revision` (15 gaps); readiness `implementation not ready` (10 questions). Each checkable claim was verified empirically first. Disposition of every gap (C = critic's numbering, R = readiness's numbering):

- **C1 / R1 — new package git-ignored (BLOCKING).** Verified with `git check-ignore -v`: `.gitignore:48` `models/` matches `src/readmission_risk/models/` and `tests/models/`. Fixed: anchor to `/models/`; added a `git check-ignore`/`git status` integrity check to Surfaces touched and Verification.
- **C2 — "AUC ≥ 0.95 ⇒ leakage" band contradicts the doc's own analysis.** Verified: a reason-code-only lookup gets test AUC 0.933 (0.934 with payer). Removed the band; replaced with a ≥ 0.99 tripwire, the label-permutation canary (unit + real data) and a reference-baseline comparison; documented the reason-code dominance in Data handling.
- **C3 — lockfile compiled on macOS, consumed on Linux CI (BLOCKING).** Verified against PyPI metadata: `nvidia-nccl-cu13` (xgboost) and `greenlet` (sqlalchemy, x86_64) are Linux-conditional and absent from a macOS lockfile; `pip-compile` on macOS silently drops Linux-marked lines in `.in` (tested); `xgboost-cpu` has no macOS wheel (checked). Fixed: `greenlet` added to `requirements.in`; new hand-maintained hash-pinned `requirements-linux-extras.txt`; CI install line updated (the earlier "no CI change" claim was wrong and is corrected); Step 0 smoke gate added (also answers the "resolves ≠ works together" half of this gap); Docker-based Linux verification specified, with an explicit "reported as NOT performed" rule because the Docker daemon is not running today.
- **C4 — `mlflow.autolog()` is process-global.** Fixed: `try/finally: mlflow.autolog(disable=True)` in `train_and_evaluate`; test `test_autolog_is_disabled_after_success_and_after_failure` proves it independently of the test fixture.
- **C5 / R5 — model-artifact contract depends on unverified autolog behavior; MLflow 3 LoggedModel URIs; signature inference on pandas-3 `str`.** Fixed: `log_models=False` in autolog + explicit `mlflow.sklearn.log_model` with an explicit signature; the contract is the `model_uri` run tag (not a hand-built `runs:/` string); categoricals are `object` dtype in `prepare_features`; test loads via the tag and checks the signature; `exclude_flavors=["xgboost"]` contingency defined with concrete trigger evidence.
- **C6 — comparison rule statistically weak; calibration (the primary metric) had no intervals.** Fixed: `clustered_bootstrap` computes ROC-AUC, Brier, ECE and `calibration_gap` intervals per model AND paired within-resample differences; comparison rule now "paired 95% interval excludes 0"; tests prove pairing (identical predictions ⇒ exactly (0,0)).
- **C7 — `reference_date < max(index_stop)` hard guard can fail spuriously (slice 2 keeps censored positives whose discharge follows the reference date).** Fixed: guard removed; replaced by a `UserWarning` when the buffer drops > 1% of rows (a too-early reference date is what would make it fire); documented in Failure modes and Data handling item 5; tested.
- **C8 / R10 — approximate manual-verification numbers.** Verified by re-running the algorithm on the real table (my own run matched the critic's exactly); the doc now pins every value (2 / 2,076 / 291 / 1,311 / 44 / 1,469 / 9,631 / 1,233 / 4,185 / 12.80% / 14.02%) and states that any deviation is a bug. The doc's earlier "1,484 rows" figure was a pre-purge count and is replaced.
- **C9 — OneHotEncoder "unseen → infrequent bucket" is conditional.** Doc now states both behaviors; two tests (bucket exists / no bucket ⇒ all-zero block).
- **C10 / R3 — unnamed resampling helper.** Named and specified: `cluster_resample_indices(groups, rng)`.
- **C11 — check ordering undefined on empty partitions.** Ordering specified (validation → emptiness/one-class `ValueError` → post-conditions `RuntimeError`); test pins that empty partitions raise `ValueError`.
- **C12 / R8 — CLI failure contract inconsistent.** Doc now says explicitly that only the two user-correctable exception types are caught, `RuntimeError`/MLflow/OS errors propagate as tracebacks by design, and that this deliberately differs from `run_big_join.py`'s "exhaustive" wording.
- **C13 — importing `big_join` drags pyspark into `readmission_risk.models`.** Fixed: the 23-column schema is restated as `EXPECTED_GOLD_COLUMNS` in `models/data.py`; a test-only import asserts equality with `big_join.GOLD_TABLE_COLUMNS`.
- **C14 — MLflow experiment/repro edge cases.** Fixed: deleted-experiment `ValueError`; `git_dirty` tag and library-version tags; the absolute-artifact-path limit is stated as accepted and documented in README.
- **C15 / R9 (README, dev lockfile).** README line 16 staleness confirmed; edit now specifies exactly what changes and where (install line, `libomp`, run + `mlflow ui` commands). `requirements-dev.txt` overlap checked (only `click==8.5.0` and `packaging==26.3`, identical in both) and a re-check step added after real compilation.
- **R2 — `make_gold_frame` unspecified.** Fully specified (signature, per-column generators, planted-signal logit, sizing rationale, rule for tuning effect sizes rather than thresholds).
- **R4 — `gold_fingerprint` recipe.** Exact expression given (newline joiner, `eid:int(label)` rendering, UTF-8, sorted tuples) and tested.
- **R6 — where the `< 50` test-positives warning fires.** Now step 3 of `train_and_evaluate`, with a named constant and a test.
- **R7 — call convention mismatch.** `clustered_bootstrap` is keyword-only for `n_bootstrap`/`seed`; `train_and_evaluate` now states y/groups are converted to `np.ndarray` at step 3 and always called that way.

**Round 2 → round 3.** Round 2: readiness `implementation not ready` (6 questions + 3 minor); critic delivered after a rate-limit interruption, `design needs revision` (13 gaps) — notably it PROTOTYPED the doc's exact pins in a scratch venv against the real gold table, so most of its findings are empirically grounded (and it independently re-confirmed the `.gitignore` collision, all split numbers, the 0.933 reason-code baseline, and the Linux marker sweep over all 91 pinned packages, which found only `nvidia-nccl-cu13`). Dispositions (C = critic, R = readiness, this round's numbering):

- **C1 (BLOCKING) — `mlflow.sklearn.log_model` fails on the calibrated XGBoost model.** Prototype-verified: MLflow 3.16.1's default `skops` serialization raises `UntrustedTypesFoundException` (`_CalibratedClassifier`, `_SigmoidCalibration`, `xgboost.core.Booster`, `XGBClassifier`); no version step-down fixes it. Fixed: `serialization_format="cloudpickle"` (verified working, round-trips) in Scope, Interfaces step 6f, and the smoke gate; slice-4 contract states the cloudpickle caveats (same lockfile at load time; pickle-safety warning). The smoke gate's fallback now explicitly excludes this case.
- **C2 (BLOCKING) — "same flags as the existing header" fails literally.** Reproduced: the header's `--no-index` makes `pip-compile` fail on `pyspark==4.1.3`. Exact commands now spelled out (runtime and dev, incl. `--allow-unsafe` for the dev file).
- **C3 — slice 4 cannot resolve the `model_uri` tag without the tracking URI.** Verified failure mode. Fixed: new `models/tracking.py` (`mlflow_tracking_uri`, `load_logged_model`) is the only sanctioned load route; the contract paragraph and roundtrip test rewritten (test loads from an unrelated store to simulate slice 4's fresh process).
- **C4 — default `mlflow.db` not git-ignored.** `*.db` added to the `.gitignore` edit.
- **C5 — fingerprint too weak (ids + labels only).** Now a hash of the whole sorted 23-column frame via an explicit `to_csv` recipe; the test asserts a feature-only change alters it.
- **C6 — positional cv-split coupling unenforced.** `validate_cv_splits(splits, groups)` added and called right before fitting; a unit test covers reordered/mismatched groups; the label-permutation canary now also covers the calibrated-XGBoost path.
- **C7 — ECE point-in-interval assertion invalid.** Prototype-verified bias (4 of 30 fixtures); assertion restricted to `roc_auc`/`brier_score`/`calibration_gap`. Comparison rule tightened: pre-specified primary metric = Brier score; multiplicity caveat; ECE flagged binning-sensitive; explicit statement that the intervals reflect test-sampling variance only (one fit per model).
- **C8 / R1 — Docker command copies multi-GB `data/`.** Already addressed by the `tar --exclude` form (added after the critic read the doc); plus `libgomp1` installed explicitly (R1) with the reason stated.
- **C9 — README "change the runtime-install line" refers to a line that does not exist.** Reworded to ADD it.
- **C10 — smoke gate circular (ran "before" the lockfile it needs).** UX flow renumbered: install (1) → smoke gate against the real hash-checked environment (2) → train (3).
- **C11 — interface inconsistencies.** `make_grouped_cv_splits` now takes `np.ndarray`; `FunctionTransformer(..., feature_names_out="one-to-one")`; `ColumnTransformer`/`Pipeline` step names pinned ("counts"/"age"/"cats"/"num", "log1p"/"scale", "preprocess"/"model") and used by the tests; `log_loss` called positionally to avoid sklearn 1.9's FutureWarning; `git status` subprocess `cwd=None` specified.
- **C12 — MLflow global-state leak across test modules.** Module-scoped end-to-end fixture is now yield-based and restores the URI/autolog state at module teardown; `test_tracking_uri_is_explicit_sqlite` reads the value recorded immediately after the call instead of depending on leaked state.
- **C13 — fixture sizing margin.** `test_warns_when_few_test_positives` pins `seed=0`, asserts its own precondition first, and uses `calibration_cv_folds=3`.
- **R2 — held-out frame configuration unspecified.** Shared `REFERENCE_DATE`/`TEST_START` constants and the exact split-then-fit-then-score recipe for every "held-out" test are now stated; invalid-config cases have concrete values.
- **R3 — `n_bootstrap == 0` behavior in `train_and_evaluate`.** Phase B skipped entirely (what is/isn't logged and returned is specified), with a test.
- **R4 — failure-injection target and the confusing "bypassed guard" phrase.** Test now patches `readmission_risk.models.training.evaluate_probabilities` (a name `training.py` must import into its namespace); the autouse fixture is stated to act at teardown only.
- **R5 — fixture dtypes.** Fully specified (int64 / float64 / object / `datetime64[us, UTC]`).
- **R6 — which hashes in `requirements-linux-extras.txt`.** Every wheel file PyPI lists for that version, no sdist.
- **Minor:** `Metric` timestamp/step specified; zero-row input now raises `ValueError` in the split's validation stage so the >1% warning cannot divide by zero.

**Empirical FYI from the critic's prototype, carried into the write-up plan (not a design gap):** on the real test window, LR reached AUC 0.934 and calibrated XGBoost 0.926 against 0.933 for the reason-code-only baseline; Brier LR 0.0628 / XGB 0.0612 / baseline 0.0670; ECE LR 0.025 vs XGB 0.044 (paired ECE difference excludes 0). The models therefore add little over the reason-code baseline in AUC on this synthetic data — expected, and the final report must say so plainly rather than present the models as a large improvement.

**Round 3 → round 4.** Round 3: readiness `implementation ready` (zero open questions; 5 tidy-ups noted); critic `design needs revision` (9 gaps; it again prototyped against the real gold table). Dispositions:

- **C1 (BLOCKING) — real-data canary criterion invalid.** Prototype-verified: on the real split, 7 of 12 single permutations gave a bootstrap interval that excluded 0.5 (permuted AUCs 0.357–0.626), because a bootstrap captures only test-sampling variance while a permuted-label model has random coefficients. Replaced with a proper permutation test (K = 100 permutations; mean within [0.45, 0.55]; real AUC above the 95th percentile of the permuted distribution; empirical p-value).
- **C2 (BLOCKING) — unit canary far flakier than claimed.** Prototype-verified: single-permutation AUC SD ≈ 0.067 (not 0.017), 5 of 24 draws outside [0.4, 0.6]. Replaced with mean-over-K = 10 permutations plus "real model beats all permuted", the "5.8σ" claim retracted in the text, an increase-K-don't-widen rule, and a specified generator draw order so two implementers build the same frame.
- **C3 — fixture dtype round trip false under pandas 3.** Verified. `load_gold_table` now normalizes to an explicit POST-LOAD DTYPE CONTRACT (strings → object, counts → int64, label int8, …); the round-trip test asserts that contract plus value equality, and a second variant feeds int32/naive-timestamp input (what Spark actually emits).
- **C4 — autolog evidence gathered without pyspark; pyspark is a runtime pin.** Verified: the universal autolog enables `spark`/`pyspark.ml` flavors and warns/raises noise; `exclude_flavors=["pyspark"]` does not work, `["spark", "pyspark.ml"]` does. Now applied by default; contingency triggers rewritten so autolog's ALWAYS-present extra artifacts (`estimator.html`, `training_*.png`) are not mistaken for a collision.
- **C5 — no automated calibration verification.** Added `test_models_are_calibrated_on_planted_signal` (ECE, |calibration_gap|, Brier skill thresholds) and an explicit reporting rule for when the challenger wins Brier but loses on calibration (declare no overall winner).
- **C6 — tracking URI defined in two places.** The MLflow-setup step (step 5 after the later reorder) must call `tracking.mlflow_tracking_uri`.
- **C7 — calibration-method rationale miscounted positives.** Rewritten in terms of per-fold positives (~250), not the training total (~1,200).
- **C8 — sensitivity run had no pinned numbers.** Exact expected values added (8,091 / 44 / 767 / 2,242 / 227 / 1,396; identity checked).
- **C9 — CI cost not mentioned.** Stated (≈252 MB `nvidia-nccl-cu13` per run, no pip cache); pip cache named as a follow-up; the `xgboost-cpu` rejection restated on the macOS-wheel ground alone.
- **Readiness tidy-ups (all applied):** test helpers in `tests/models/helpers.py`, not `conftest.py`; stale "Step 0"/"UX flow step 4" cross-references fixed; `ModelResult` construction order after Phase B and the `n_bootstrap == 0` logging route specified; warning-message anchors pinned (`"reference_date"`, `"unreliable"`, `"single-class"`) with `pytest.warns(match=...)`; constant-prediction behavior of `evaluate_probabilities` specified and tested.
- **New evidence added to the doc (from the user's mid-turn question about performance):** the lung-cancer-cycle finding (72.6% of gold positives; same-reason 98.5–99.8%; gaps 24.5–28.8 days) and the with/without-lung reference-baseline numbers (AUC 0.763 on lung rows, 0.920 on the rest), in Data handling and the manual reference-baseline step.

**Round 4 → (user-gated) revision 4.** Round 4: readiness `implementation ready` (zero open questions; 7 non-blocking nits); critic `design needs revision` (8 gaps, prototyped again). The 3-revision cap fired; the user chose "clarify scope in chat" and made two decisions (below), after which all gaps were addressed in one further revision. (Re-checked by fresh goldfish in round 5 — see the next entry.)

User decisions: (a) **pandas 2.3.3 + numpy 2.4.4 downgrade**, with a standing to-do to check later whether upgrading is possible; (b) **disclose that the test window is not a virgin holdout**, and — new context — v2 will scale to 100,000–200,000 patients (→ a fresh-seed v2 population becomes the planned untouched validation; scale statements updated).

- **C1 (BLOCKING) — pandas 3 + pyspark 4.1.3 breaks `assertDataFrameEqual`.** Decision (a). Pins, compatibility finding, forward-compatible coding rule, `requirements.in` comment, pandas-3-specific text rewritten, and the smoke gate now also runs the existing `tests/pipeline` suite. (Critic-verified; not reproduced by the elephant — flagged as such in the doc.)
- **C2 — `pd.qcut(constant, duplicates="drop")` yields zero bins.** Replaced with the shared `_quantile_bin_ids` helper (`np.unique`/`np.quantile`/`np.searchsorted`, single-bin fallback), also used by the bootstrap; test moved to `test_evaluate.py` and extended to the bootstrap's per-resample ECE.
- **C3 — calibration thresholds and fixture sizing unsupported.** Train/test sizes tabulated; per-model thresholds set from the critic's multi-seed measurement (XGBoost ECE < 0.08, |gap| < 0.04, BSS > 0.08; LR unchanged); re-measure-across-seeds rule before concluding a defect.
- **C4 — "clean holdout" claim contradicted by design history.** Decision (b): prominent disclosure in Data handling, the leakage-rubric wording corrected, and the disclosure required in the final report, README and CLAUDE.md; untouched-validation plan via the v2 fresh-seed population.
- **C5 — `organization_utilization` int64 cast without null check.** Column now left exactly as read (excluded from features); test added with a null value.
- **C6 — gold provenance / fingerprint pin.** Fingerprint `9c10be06…a5aca` pinned as manual check 2 (to be re-derived at implementation). The critic's claim that `generation_summary.json` said `population_size: 1000` was NOT reproduced: in this checkout it records 10,000 / seed 42 / `20260916`, consistent with the gold table (verified by the elephant this session).
- **C7 — validation after side effects.** `train_and_evaluate` reordered: pure step 4 (folds, `validate_cv_splits`, model construction) now precedes MLflow setup (step 5); `test_bad_fold_config_leaves_no_mlflow_side_effects` added.
- **C8 — smaller items.** `/mlflow/`, `/mlruns/`, `/mlartifacts/` anchored; `test_linear_preprocessor_feature_names` added; new "Deviations from CLAUDE.md's locked spec" section (local Parquet vs BigQuery, explicit `log_model`, LR not wrapped, pandas/numpy pins, holdout status).
- **Readiness nits (all applied):** `tests/models/helpers.py` in Surfaces; the correct CLAUDE.md paragraph named ("Hard constraint carried into future model-training work"); "below" → "above" in the draw-order bullet and the sampling probabilities/values for every categorical specified; the garbled `test_warns_when_few_test_positives` sentence fixed; the constant-prediction test relocated.

**Round 5 → revision 5.** Round 5 (fresh critic + readiness, run after the user-gated revision 4): readiness `implementation not ready` (6 questions, all small spec slips introduced by earlier revisions); critic `design needs revision` (11 gaps, **none blocking**). The critic independently confirmed: the pandas-3/`pyspark.pandas` breakage (reproduced), the numpy 2.5.3 DeprecationWarning, the 93-package hash-checked lock and `pip check`, `pytest tests/pipeline` (45 passed) under the pins, every real-data number including the fingerprint `9c10be06…a5aca`, the `.gitignore` fix in a scratch git repo, and the whole MLflow flow (exclude_flavors, exactly 2 runs, cloudpickle round trip, deleted-experiment behavior, autolog `disable`, bit-identical refits, permutation canaries). Dispositions:

- **Readiness 1 / critic 2 — dtype contradiction I introduced** (line said counts → float64, contract said int64). Fixed: counts → int64, only age → float64.
- **Readiness 2 / critic 6 — test constants in two files.** Now `tests/models/helpers.py` only.
- **Readiness 3 — fingerprint had no named function.** `data.gold_fingerprint(df)` is the single implementation; test (a) perturbs frames and calls it, test (b) checks the run tag equals it.
- **Readiness 4 / 5 — `row(...)` builder and generator sampling calls unspecified.** `row`/`frame` signatures, defaults and tz handling pinned; `index_start` sampling and the exact label-draw call pinned.
- **Readiness 6 — artifact-location comparison form.** (Folded into the end-to-end test: compare against `(tracking_dir.resolve() / "artifacts").as_uri()`.)
- **Critic 1 — calibration bounds still seed-fragile; stated positive rate wrong.** 40-seed measurement adopted; bounds widened with ≥ 20% headroom (LR ECE < 0.06, |gap| < 0.04; XGBoost ECE < 0.09, |gap| < 0.05); positive rate corrected to 21–24%; n pinned to 2,000 for both learning tests.
- **Critic 3 — a paired difference of the SIGNED calibration gap cannot support "who is better calibrated".** Added `abs_calibration_gap` (five bootstrap metrics); the signed gap is direction-only and excluded from the claim rule; the design-time paired numbers are recorded so the expected "no overall winner; LR better calibrated on ECE" outcome is anticipated rather than spun.
- **Critic 4 — holdout disclosure had no README target.** README edit now adds a "Results and caveats" subsection under Status.
- **Critic 5 — module-scoped fixture cannot use `tmp_path`.** Now `tmp_path_factory`.
- **Critic 7 — `get_feature_names_out()` names are prefixed by default.** `verbose_feature_names_out=False` pinned on both preprocessors; test asserts the unprefixed names.
- **Critic 8 — all-resamples-skipped bootstrap crashes on an empty percentile.** Now a named `ValueError`; test added.
- **Critic 9 — `_quantile_bin_ids` prose overstated ("every bin populated").** Corrected (non-empty bins only); the hand-computed toy test pins `n_bins=3`.
- **Critic 10 — the bad-fold test could not detect enabled autolog.** Test now points the tracking URI at a separate tmp store first and asserts it stays empty.
- **Critic 11 nits.** Exact-equality tolerance stated; the `Default` experiment acknowledged; Docker-emulation caveat added; the dev command's added `-c requirements.txt` flagged as deliberate.

**Round 6 → revision 6.** Round 6 (fresh critic + readiness, focused on the round-5 changes): readiness `implementation ready` (zero blocking questions; 9 nits); critic `design needs revision` (8 small gaps, none blocking; it re-verified `gold_fingerprint` = `9c10be06…a5aca`, the generator + 40-seed calibration bounds, `verbose_feature_names_out=False`, and the step 4/5 reorder). All applied:

- **Critic 1 (changes the write-up) — the design-time expectation omitted `abs_calibration_gap`.** Re-run paired bootstrap: `abs_calibration_gap` difference −0.0098 [−0.0167, +0.0025] (includes 0) while ECE +0.0191 [0.0081, 0.0270] favours LR — the two calibration claim metrics DISAGREE. Doc now records both, pins the report wording ("calibration comparison mixed — ECE favours logistic regression; calibration-in-the-large inconclusive; no overall winner"), and requires BOTH metrics' paired intervals to exclude 0 in the same direction for a calibration-superiority claim.
- **Critic 2 — `gold_fingerprint` computed per model, after MLflow starts.** Now computed once in step 3 (pure, before any side effect); `lineterminator="\n"` made explicit; null-vs-empty-string equivalence noted as harmless.
- **Critic 3 — `try` boundary ambiguous.** The `try:` now opens immediately before `mlflow.autolog(...)`.
- **Critic 4 — split boundary fixtures need padding rows.** `pad_rows()` helper specified (one negative + one positive train row, one negative + one positive test row, far from every boundary); naive-timezone case builds via `frame()` then strips the zone.
- **Critic 5 — autolog-failure test baseline ambiguous.** Baseline is the run count AFTER the failing call (which itself leaves one FAILED run).
- **Critic 6 — fixture datetime unit contradiction.** Now "tz-aware UTC, unit unconstrained".
- **Critic 7 — two different fixture sizes for the same call.** Both lines now use the measured values (seed 0: train 1,178 / 269, test 1,827 / 439).
- **Critic 8 — cosmetic and small interface staleness.** Revision-note paragraph and stale log lines fixed; the `abs_calibration_gap` difference is defined as `|gap_b| − |gap_a|`; `validate_cv_splits` check (a) runs first with a length check so a wrong-length `groups` raises `ValueError`, never `IndexError`.
- **Readiness nits:** `frame`/`pad_rows` listed in Surfaces; `git status --short -uall`; generator details pinned (`np.repeat` patient mapping, LOS → whole-second timedelta, `rng.integers(200, 40001)`); model seed/fold count for `test_modeling.py` tests stated; JSON artifacts cast to native Python types; CLI print layout specified; "investigate first, loosen only with recorded justification" rule added for persistent calibration-bound failures.

## Implementation addendum (2026-09-19)

Implemented after the design check was closed by an explicit user override of the no-code gate (rounds 1–6; the critic never returned `design ready`, readiness returned `implementation ready` in rounds 3, 4 and 6). Facts learned or decided during implementation that the doc above does not state:

- **Verified against the real gold table (all pinned numbers reproduced exactly):** `gold_fingerprint` `9c10be06…a5aca`; primary split 2 / 2,076 (291 pos, 1,311 patients) / 0 / 44 / 1,469 / 9,631 (1,233 pos, 4,185 patients); sensitivity split 8,091 / 44 / 767 / 2,242 (227 pos, 1,396 patients). Real metrics — logistic regression AUC 0.9340, Brier 0.0628, ECE 0.0249; calibrated XGBoost AUC 0.9263, Brier 0.0612, ECE 0.0440 — matched the review prototypes to four decimals. Label-permutation tests on real data: LR K=100 permuted mean 0.509 (real 0.934, p = 1/101); XGBoost K=20 permuted mean 0.498 (real 0.926, p = 1/21).
- **Added during pre-commit review (not in the design above):** (1) a `bootstrap_status` run tag (`skipped` / `pending` / `complete`) so a run whose Phase B failed after it was logged is distinguishable from a deliberate `--n-bootstrap 0` run; (2) `src/readmission_risk/models/baselines.py` (`reason_code_lookup`) — the reason-code-only reference as a tested function, so the README's reference row is reproducible by hand (it is still NOT a tracked MLflow run and has no interval); (3) `tests/models/test_cli.py` and a `--verbose` flag on `scripts/train_models.py` (the design said the CLI would stay untested); (4) `git_dirty` is computed in the directory the module lives in, not the process's cwd; (5) an end-to-end test that the logged pipeline was fitted on training rows only, and one that `--train-start-date` flows through.
- **Not verified:** the Linux lockfile check (manual verification step 10 — Docker Desktop was not running); the first CI run on `ubuntu-latest` is therefore the only Linux verification.
- **Measured facts that change how results must be read** (also in README and CLAUDE.md): the default training window includes 7,389 rows (77% of training) before 2017-09-16 — 6,933 outside Synthea's 10-year export window and 456 in the 1-year lookback dead zone — whose features and positive rate differ from the test window's; with `--train-start-date 20170916` XGBoost is clearly worse than LR on AUC and Brier.
- **Purge boundary corrected during pre-commit review (design-doc error):** the design said a train row with `index_stop + W == T` exactly is KEPT. Slice 2's readmission window is `(stop, stop + W]` with an INCLUSIVE upper bound (`a.START <= readmit_deadline`, `big_join.py`), so such a row's label could be set by a readmission starting exactly at `T` — a test-period encounter. The implementation purges it (`index_stop + W < T` is the keep rule, and the post-condition is strict too); `test_label_window_purge_boundary` pins both sides. The real-data counts are unchanged (44 purged), because no row's `stop + 30d` lands exactly on 2023-01-01T00:00:00Z.
