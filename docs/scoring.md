# Batch scoring (Slice 4: Top-N triage list)

> Terminology: "slice N" names the build stages in the [README Status table](../README.md#status) (1 Synthea generation, 2 PySpark feature join, 2b unplanned label, 3 models, 4 Top-N CLI); "v2 population" is the planned scale-up cohort (see the README Roadmap).

`scripts/score_discharges.py` ranks discharges by predicted risk and prints the Top-N. **It is a demo, not as-of scoring:** it scores discharges that already exist in the gold table, restricted to the rows the chosen model's training run recorded as its held-out `test` partition (`split_assignment.csv`), never the rows it was trained on. Building features for unlabelled recent discharges is a named follow-up. The design, its measurements and its 5-round design check are in [notes/eg-new-feature/batch-scoring-cli-2026-09-20.md](../notes/eg-new-feature/batch-scoring-cli-2026-09-20.md). Since v2 slice 2 the scorer reads the model from a **model bundle**, not from MLflow (see [Model bundles](#model-bundles) below and [the design note](../notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md)).

```sh
# Export the two reference runs as bundles (once; prints each bundle id and the champion.json text that would promote it)
make export                                   # logistic regression
make export EXPORT_MODEL=xgboost_calibrated

# Score the held-out discharges of the 30 days ending 2025-06-30 with the champion bundle (deploy/champion.json)
python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --run-date 20250630

# Another bundle; add --show-observed-outcomes for a sanity check against what actually happened
python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --run-date 20250630 --bundle bundles/<bundle id> --show-observed-outcomes
```

**Python version.** A bundle is only scored under the Python major.minor its model was trained with (guard 14; the models are cloudpickles). The reference runs in `readmission-risk-v1` were trained on Python 3.14.7 (2026-10-10), so the commands above run in the default environment. The older `readmission-risk-2b` runs (Python 3.12.11, trained on the gold table before it gained the `label_horizon` column) are kept only as history: the fingerprint guard refuses them against the current table, and guard 14 refuses them under 3.14. To score a run trained on Python 3.12 against a matching gold table, first export it to a bundle in the default environment (`python scripts/export_bundle.py --run-id <id>`; exporting never unpickles, so it works under 3.14), then score that bundle from a Python 3.12 environment (any Python 3.12 interpreter, e.g. `pyenv shell 3.12.11` or Homebrew's `python3.12`) outside the repository, with the same prerequisites as [setup.md](setup.md) (on macOS `brew install libomp` for XGBoost), run from the repository root with `--bundle bundles/<id>`. This 3.12 recipe is not exercised by CI since 2026-10-11; CI tests only the pipeline subpackage on 3.12:

```sh
python3.12 -m venv ~/.venvs/readmission-py312 && source ~/.venvs/readmission-py312/bin/activate
pip install -e .
pip install --require-hashes -r requirements.txt -r requirements-linux-extras.txt
pip install --require-hashes -r requirements-dev.txt
```

or retrain under 3.14 with a fresh `--experiment-name` and export that run.

| Flag | Meaning |
|---|---|
| `--gold-dir`, `--run-date` (required) | the gold table (with `_gold_metadata.json`) and the last day (`YYYYMMDD`, UTC) of the window |
| `--window-days` | window length in UTC calendar days; default the gold table's readmission window (30). A row is in the window iff `run_date - (W-1) days 00:00Z <= index_stop < run_date + 1 day 00:00Z` |
| `--bundle` / `--champion` | the model source: an explicit bundle directory, or a champion pointer to follow; with neither, `deploy/champion.json` relative to the current directory. Mutually exclusive |
| `--top-n` | how many rows to print (default 10); the whole ranked batch is written regardless |
| `--show-observed-outcomes` | opt-in: prints how the observed readmissions line up with the Top-N, joined after scoring and never written to the file |
| `--output-dir` | default `data/predictions` (gitignored) |

**Output.** `<output-dir>/run_date=YYYYMMDD/predictions.parquet`, one file per run date, replaced as a unit (temp file + atomic rename): re-running a run date overwrites the whole partition, so scoring the same date with another bundle replaces the first one's file. Columns, in order: `as_of_date`, `rank` (1 = highest risk; ties in `risk_score` are broken by `encounter_id` ascending, so ranks are always distinct), `is_top_n`, `patient_id`, `encounter_id`, `discharge_time` (UTC), `admission_reason_code`, `admission_reason_description`, `risk_score`, `model_bundle_id` (the bundle that scored the partition), `model_run_id` (the MLflow run the bundle was exported from), `model_name`, `top_n`, `scoring_window_days` (the trailing-window length; not the 30-day label window), `partition` (always `test`), `label_definition`, `gold_fingerprint`, `gold_reference_date`. There is no label column. The demo notice and `readmission_risk.schema_version` (`"2"` since v2 slice 2, which added `model_bundle_id`) are stored in the file's schema metadata. Reading the parent directory (`pd.read_parquet("data/predictions")`) works and adds a categorical `run_date` column, provided every partition has the same schema version: re-score or delete partitions written before v2 slice 2 rather than mixing them with new ones.

**What is refused (exit 1, `ERROR:` on stderr, nothing written).** A window that contains any discharge that is not a held-out row of the chosen model (a training row, a row dropped by the split, or the one censoring-buffer row) is refused rather than silently truncated. So is a window that reaches past the **observation horizon** (the reference date minus the 30-day label window: 2026-08-18 on the real table): the gold table keeps a non-readmitted discharge only once its outcome window has closed, so later windows would hold readmitted discharges only, and no comparison against the rows that do exist could notice the rest are missing. On the real table with a 30-day window that leaves 1,287 valid run dates, `20230208` through `20260817` (batches of 21-49 rows, median 35). Before any model is loaded, integrity checks verify the bundle (the champion's sha256 pin on `manifest.json`, the content-hash `bundle_id`, every file's size and sha256, and the manifest's shape), and then 14 guards check the bundle's manifest against the gold table: it must come from a finished, non-deleted `train_and_evaluate` run, trained on **exactly this gold table** (fingerprint) and label definition, with matching `reference_date` / `readmission_window_days`, the same library versions as the lockfile (the model is a cloudpickle), the same Python major.minor (from the run's `python_version` tag, or for runs trained before that tag existed, the version MLflow wrote into the logged model's `MLmodel`, both copied into the manifest at export), the current feature contract and signature (names, types, all required), a consistent train/test assignment, and a model that belongs to the run.

## Model bundles

A bundle is an immutable, content-addressed directory, `bundles/<bundle_id>/`, holding `manifest.json`, `model.pkl` (the MLflow logged model's cloudpickle, byte for byte) and `split_assignment.csv` (the run's train/test assignment, byte for byte). `bundle_id` is the sha256 of the manifest's canonical JSON without its own id; the manifest lists every other file's sha256, and carries no timestamp, so exporting the same run again gives the same id (and `status: already existed`). A bundle is never overwritten. MLflow remains the experiment record; the scorer never imports it.

- **Export:** `python scripts/export_bundle.py --run-id <id>` or `--experiment-name <name> --model <model>` (`make export`, with `EXPERIMENT` and `EXPORT_MODEL`). It refuses a run whose model or artifacts it cannot read; everything else is recorded in the manifest and judged by the scorer. Re-running `train_models.py` under an existing experiment name makes `--model` ambiguous (it refuses and lists the run ids): use a fresh `--experiment-name` per training run, or pass `--run-id`.
- **Champion:** `deploy/champion.json` names the live bundle as `{"bundle_uri", "sha256"}`, where `sha256` pins that bundle's `manifest.json`. Promotion is a PR that edits it (the exporter prints the text); rollback is `git revert`. A relative `bundle_uri` is resolved against the current directory.
- **Trust:** the model is a cloudpickle, which can run code when loaded, so it is unpickled only after every check has passed. Behind the champion, integrity is anchored in the committed sha256 pin. A bundle passed with `--bundle` is anchored only in its own self-consistent hashes: anyone who can write it can recompute them, so treat bundles as trusted local input, like the MLflow store.
- **Fresh clone:** bundles are gitignored until v2 slice 3 moves them to cloud storage, so the committed champion names a bundle that only the author's local MLflow store reproduces (`make export`). Anyone else trains, exports and scores their own: `make train TRAIN_EXPERIMENT=<x>`, then `make export EXPERIMENT=<x>`, then `make score SCORE_ARGS="--bundle bundles/<id>"` with the id the export printed.
- **Deleting a bundle:** bundles are read-only on purpose, so `rm -rf` and `git clean -fdx` fail on them; run `chmod -R u+w bundles/<id> && rm -rf bundles/<id>` (or `chmod -R u+w bundles` before a `git clean`).

**Read the list with these limits in mind** (the CLI prints the first one on every run):
- **Weak models, development-set rows.** The models beat neither a reason-code-only lookup nor each other convincingly (see [Results and caveats](results.md)), so the Top-N mostly ranks by admission reason: on the real `20250630` batch of 42, the logistic-regression Top-10 is six CABG-history stays, two NSTEMI and two drug-abuse admissions (`risk_score` 0.0816 down to 0.0149). The 2023+ test window was used while designing the models and the label; it is not a virgin holdout. Both models under-predict on average, so read `risk_score` as a ranking, not an absolute risk.
- **Membership is not as-of.** Which gold rows exist depends on whole-export information (the terminal rule and the death/censoring keep-rule), so a real as-of list would contain rows this one omits, notably patients who died within 30 days.
- **Simulated run date.** Patients already readmitted before the run date still appear, and the same discharge appears in up to W consecutive run-date partitions.
- **`--show-observed-outcomes` counts are tiny** (the real batch has 1 readmission in 42 discharges): they are a sanity check, not evidence.
- The notice text is specific to this dataset and must be revisited before the untouched v2 population or real as-of scoring is used with this scorer.

A five-row sample of this output (with the same demo caveats) is in the [README](../README.md#example-output); `scripts/make_readme_assets.py` regenerates it from a saved predictions file.
