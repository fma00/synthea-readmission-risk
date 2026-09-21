# Batch scoring (Slice 4: Top-N triage list)

> Terminology: "slice N" names the build stages in the [README Status table](../README.md#status) (1 Synthea generation, 2 Big Join, 2b unplanned label, 3 models, 4 Top-N CLI); "v2 population" is the planned scale-up cohort (see the README Roadmap).

`scripts/score_discharges.py` ranks discharges by predicted risk and prints the Top-N. **It is a demo, not as-of scoring:** it scores discharges that already exist in the gold table, restricted to the rows the chosen model's own MLflow run recorded as its held-out `test` partition (`split_assignment.csv`), never the rows it was trained on. Building features for unlabelled recent discharges is a named follow-up. The design, its measurements and its 5-round design check are in [notes/eg-new-feature/batch-scoring-cli-2026-09-20.md](../notes/eg-new-feature/batch-scoring-cli-2026-09-20.md).

```sh
# Score the held-out discharges of the 30 days ending 2025-06-30 with the logistic-regression baseline (the default)
python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --experiment-name readmission-risk-2b --run-date 20250630

# The other model, or an explicit run; add --show-observed-outcomes for a sanity check against what actually happened
python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --experiment-name readmission-risk-2b --model xgboost_calibrated --run-date 20250630
python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --run-id 2bb281f6ab4e47c588eea078cc712201 --run-date 20250630 --show-observed-outcomes
```

The `--run-id` value in the last command is a local MLflow run id from the author's store; use `--model` or your own run id.

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
- **Weak models, development-set rows.** The models beat neither a reason-code-only lookup nor each other convincingly (see [Results and caveats](results.md)), so the Top-N mostly ranks by admission reason: on the real `20250630` batch of 42, the logistic-regression Top-10 is six CABG-history stays, two NSTEMI and two drug-abuse admissions (`risk_score` 0.0816 down to 0.0149). The 2023+ test window was used while designing the models and the label; it is not a virgin holdout. Both models under-predict on average, so read `risk_score` as a ranking, not an absolute risk.
- **Membership is not as-of.** Which gold rows exist depends on whole-export information (the terminal rule and the death/censoring keep-rule), so a real as-of list would contain rows this one omits, notably patients who died within 30 days.
- **Simulated run date.** Patients already readmitted before the run date still appear, and the same discharge appears in up to W consecutive run-date partitions.
- **`--show-observed-outcomes` counts are tiny** (the real batch has 1 readmission in 42 discharges): they are a sanity check, not evidence.
- The notice text is specific to this dataset and must be revisited before the untouched v2 population or real as-of scoring is used with this scorer.
