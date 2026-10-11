# v2 slice 2 — model bundle + manifest (FR4–FR7) — design, 2026-10-11

Status: design, revision 6 — final. Rounds 1–5 run; at round 5 readiness signed off and the critic raised 6 concrete gaps; the owner chose **override and proceed** (2026-10-11). The 6 fixes below were written in by the elephant and were NOT re-reviewed by a goldfish; /eg-precommit-review reviews the code. Branch `v2`, draft PR #4. Source requirements:
`notes/prds/v2-mlops-keyless-scheduled-scale-up-2026-10-09.md` FR4–FR7 and the slice-2 line ("guard mapping table;
manifest fixtures replace `clone_run`; `forbid_model_loading` still applies; the scorer drops its mlflow import;
`champion.json`"). Guard origin: `notes/eg-new-feature/batch-scoring-cli-2026-09-20.md`; guard 14:
`notes/eg-new-feature/python-version-guard-2026-10-09.md`.

Preconditions checked 2026-10-11: CI on PR #4 green on all three jobs (run 38120033507); `pipeline-dataproc-runtime`
reported `186 passed, 1 warning`, no skips.

## Why

Today the scorer (`scripts/score_discharges.py` → `scoring/pipeline.py`) reads the MLflow SQLite store directly:
`scoring/runs.py::validate_run` runs 14 guards against run tags, params, artifacts and the logged model's `MLmodel`,
then `models/tracking.py::load_logged_model` unpickles through `mlflow.sklearn.load_model`. v2 schedules scoring in
GitHub Actions against cloud storage (slices 3–4), where there is no local MLflow store. The PRD (FR4–FR7, G7/G19)
replaces that contract with an immutable, content-addressed **bundle** the scorer can validate without MLflow, and a
committed pointer, `deploy/champion.json`, so promotion is a PR and rollback is `git revert`. MLflow stays the experiment
record.

## Step-0 decisions (owner, 2026-10-11)

| # | Question | Decision |
|---|---|---|
| 1 | Storage | Local, gitignored `bundles/<bundle_id>/` behind a storage seam (`bundles/store.py`), the same pattern as `scoring/store.write_partition`. The GCS writer and reader land in slice 3. |
| 2 | FR7 no-overwrite | Enforced locally: build in a temp dir and rename atomically; never overwrite; re-exporting identical content is an idempotent no-op; different content (or any pre-existing entry) under an existing id is an error; files and dir are made read-only. The three GCS controls (objectCreator-only writer SA, `ifGenerationMatch=0`, read-only scorer SA) wait for slice 3. |
| 3 | Gold fingerprint | Today's recipe (`models/data.py::gold_fingerprint`), recorded with `gold_fingerprint_definition = "pandas-csv-sha256-v1"`. The scorer refuses an unknown definition. Slice 5 (FR12) adds a new definition id without invalidating existing bundles. |
| 4 | Portable form | Schema slot only (`portable: null`). FR0.3 (slice 6) fills it, with its max-abs-diff-0 parity test. |
| 5 | Export path | A separate command over an existing MLflow run (`scripts/export_bundle.py`, `make export`). Training is unchanged in this slice; slice 6 wires export into the retrain job. **Deviation from FR4's wording "Training exports"**, recorded here. |
| 6 | `bundle_id` | sha256 of the canonical JSON of the manifest **without** its `bundle_id` key. The manifest lists every other file's sha256 and size, and holds no timestamp, so it is deterministic. `champion.json`'s `sha256` is the sha256 of the `manifest.json` bytes. The manifest includes the MLflow run id and model URI, so **a retrained, byte-identical model gets a different id by design** (provenance is part of identity). |
| 7 | `champion.json` | Created and committed, pointing at the local bundle (`bundles/<id>`), plus a strict reader; the scorer defaults to it. The bundle stays gitignored; slice 3 uploads it and a PR repoints `bundle_uri` to `gs://`. The FR15 gate table is deferred to slice 6. CI checks the committed file's schema only. |
| — | Guard 8 | **All five libraries stay checked, mlflow included**, read with `importlib.metadata.version` (no import). |
| — | CLI | Default model source: `deploy/champion.json` (`--champion` to point elsewhere). Override: `--bundle <dir>`. The two are mutually exclusive. `--tracking-dir`, `--experiment-name`, `--model` and `--run-id` are removed. |
| — | Dirty-tree v1 runs | Both `readmission-risk-v1` runs carry `git_dirty=dirty` (commit `51f1d62`). They are exported as-is; the manifest records it and the report prints it (not refused, as today). Reproducibility caveat: the predictions were shown identical to the 3.12 runs; a clean-tree retrain is the slice-6 challenger. |

Elephant decisions taken in revision 2 (flagged to the owner in the hand-off, not silently):
- `cloudpickle==3.1.2` becomes a **direct** pin in `requirements.in` (it is already in `requirements.txt` at that
  version, only as an mlflow transitive). Recompile, literally: `make lock-dev` first only if `.venv-lock312` is absent
  (it builds that 3.12 venv with `requirements-dev.txt`, which includes pip-tools; its own recompile of
  `requirements-dev.txt` must then show no diff, else revert it), then
  `.venv-lock312/bin/pip-compile --generate-hashes --output-file=requirements.txt requirements.in` (no `--upgrade`,
  existing pins kept). The only accepted `requirements.txt` diff is cloudpickle's `# via` comment (gains
  `-r requirements.in`). Any other diff → revert and report.
- Predictions gain `model_bundle_id`, schema version `"1"` → `"2"` (lineage: which bundle scored a partition, needed for
  G19 rollback).

## Scope

**In:**
1. A new mlflow-free package `src/readmission_risk/bundles/` (`manifest.py`, `store.py`, `champion.py`).
2. An exporter `src/readmission_risk/models/bundle_export.py` (MLflow side) + `scripts/export_bundle.py` + `make export`.
3. Scorer switch: `scoring/runs.py` is deleted and replaced by `scoring/bundle_guard.py` (named so it cannot be confused with the `readmission_risk.bundles` package; all imports of that package are absolute) (`validate_bundle`,
   `load_bundle_model`). `ScoringConfig` / CLI / report / predictions are updated. The scorer imports no mlflow,
   directly or transitively (enforced by a subprocess test and a static test).
4. Two new import-free modules so the scorer can reach shared facts without importing `training.py`/`tracking.py`
   (both import mlflow): `models/constants.py` (`MODEL_NAMES`) and `models/versions.py` (`current_python_version`).
   `training.py` and `tracking.py` re-export them, so the "single definition" and every existing import keep working.
   `resolve_run_id` moves verbatim to `models/tracking.py`.
5. Predictions gain `model_bundle_id`; partition schema version `"2"`.
6. Test port: guard cases become bundle fixtures (`tamper_bundle`); a case-by-case table below fixes every port.
7. Export of both `readmission-risk-v1` runs into `bundles/`; `deploy/champion.json` points at the
   **logistic-regression** bundle (the scorer's documented default model, and the better AUC).
8. PRD: each of FR4–FR7 gets a trailing "**Slice 2 (2026-10-11, `notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md`):** ..."
   sentence: FR4 — done for the export command and manifest; training-time export and the portable form → slice 6;
   `gs://` → slice 3; fingerprint definition `pandas-csv-sha256-v1` until FR12. FR5 — done (23/140 semantic test
   changes, under the kill trigger). FR6 — `deploy/champion.json` and its reader done; the gate table → slice 6. FR7 —
   local no-overwrite done; the three GCS controls → slice 3. The slice-2 line (PRD l.365) gets "**Done 2026-10-11.**".
   CLAUDE.md: the v2-architecture bullet "Model bundle replaces MLflow for scoring" gets "**Landed in slice 2
   (2026-10-11, <design note>):** `scripts/export_bundle.py` / `make export`; the scorer reads `deploy/champion.json` or
   `--bundle`, runs integrity checks + the 14 guards on the manifest, never imports mlflow; GCS (slice 3), portable form
   and training-time export (slice 6) pending."; the Slice 4 paragraph's guard sentence and its "it does import mlflow,
   which loads pyspark" clause are amended to say that since slice 2 the guards read a bundle manifest and the scorer
   imports neither; the "Score a Top-N triage list" build command and the Make-targets list (`export`) are updated, an
   "Export a model bundle" command is added, and the test count is updated.
9. Docs and tooling: `docs/scoring.md`, README scoring command, `docs/setup.md` (Makefile section), CLAUDE.md (Slice 4
   paragraph, build commands, v2 architecture bullet, test count), PRD status notes on FR4–FR7, `.gitignore`
   (`/bundles/`, anchored), Makefile (`score`, `export`, the `train` guard messages), `requirements.in` (cloudpickle), README repository-layout block.

**Out (named follow-ups, see the end).**

## Surfaces touched

| File | Change |
|---|---|
| `src/readmission_risk/models/constants.py` | **new**: `MODEL_NAMES: tuple[str, ...] = ("logistic_regression", "xgboost_calibrated")`; no imports. |
| `src/readmission_risk/models/versions.py` | **new**: `current_python_version()` moved verbatim from `tracking.py:23-28` (imports only `sys`). |
| `src/readmission_risk/models/training.py:56` | the definition is deleted and `from .constants import MODEL_NAMES` is added to the module's top import block (with the other `from .` imports, ruff-sorted; not at l.56, which would trip E402/I001) (still importable from `training`; `scripts/train_models.py` and `tests/models/test_training.py` unchanged). |
| `src/readmission_risk/models/tracking.py` | `from .versions import current_python_version` (re-export, `# noqa: F401` not needed if listed in `__all__`; use `__all__`); gains `resolve_run_id`, `_require_store`, `_MAX_RUNS_SEARCHED` moved verbatim from `scoring/runs.py:35,132-169` (by symbol) (with `from .constants import MODEL_NAMES`). `load_logged_model` stays. |
| `src/readmission_risk/bundles/__init__.py` | **new**, docstring only. |
| `src/readmission_risk/bundles/manifest.py` | **new**; stdlib only. |
| `src/readmission_risk/bundles/store.py` | **new**; stdlib only. |
| `src/readmission_risk/bundles/champion.py` | **new**; stdlib only. |
| `src/readmission_risk/models/bundle_export.py` | **new**; imports mlflow. Holds `_mlmodel_python_version` (moved from `runs.py:60-70`). |
| `scripts/export_bundle.py` | **new** Typer CLI (thin). |
| `src/readmission_risk/scoring/runs.py` | **deleted**. |
| `src/readmission_risk/scoring/bundle_guard.py` | **new**; imports `cloudpickle`, `sklearn.exceptions`, pandas, `readmission_risk.models.constants`, `readmission_risk.models.versions`, `readmission_risk.models.data` (`FEATURE_COLUMNS`, `COUNT_FEATURES`, `NUMERIC_OTHER_FEATURES`, `CATEGORICAL_FEATURES`, `gold_fingerprint`), `readmission_risk.pipeline.gold_metadata`, `readmission_risk.bundles.{manifest,store,champion}`, and the sibling `.constants` (`readmission_risk.scoring.constants`). **Absolute imports throughout** except that one sibling: a relative `.pipeline` from `scoring/` would mean `scoring.pipeline`. |
| `src/readmission_risk/scoring/pipeline.py:19-20,25,33-45,60-71,74-136` | imports: `MODEL_NAMES` no longer needed (model-name config removed) — drop the `models.training` and `models.tracking` imports; config, model-source resolution, loader, `build_predictions` args. |
| `src/readmission_risk/scoring/constants.py` | `DEFAULT_MODEL` removed; `DEFAULT_CHAMPION_PATH = Path("deploy/champion.json")` (imports `pathlib` only). |
| `src/readmission_risk/scoring/ranking.py:23-40,92-155` | `"model_bundle_id"` inserted in `PREDICTION_COLUMNS` immediately before `"model_run_id"`; `PREDICTION_DTYPES["model_bundle_id"] = "object"`; `build_predictions(..., model_bundle_id: str, ...)` keyword placed before `model_run_id`, column filled with `_constant(model_bundle_id)`. |
| `src/readmission_risk/scoring/store.py:23` | `SCHEMA_VERSION = "2"`; `model_bundle_id` is a non-nullable string by the existing default rule. |
| `src/readmission_risk/scoring/report.py:47` | model line (see Report). |
| `scripts/score_discharges.py` | flags (see CLI); docstring drops the MLflow pickle-warning sentence. |
| `src/readmission_risk/reporting/assets.py:20` | `from readmission_risk.models.tracking import resolve_run_id`. |
| `Makefile` | `score`: help comment `## Score the Top-N triage list for RUN_DATE with the champion bundle (deploy/champion.json); extra flags via SCORE_ARGS` and recipe `$(BIN)/python scripts/score_discharges.py --gold-dir $(GOLD_DIR) --run-date $(RUN_DATE) $(SCORE_ARGS)`; `EXPERIMENT ?= readmission-risk-v1` keeps its value and gets no comment of its own (the variable block has none); its role is now "the experiment `make export` reads and `make train` must not reuse", stated in the `export` and `train` help comments and in `docs/setup.md`'s Makefile table; new `export` target (`## Export the EXPORT_MODEL run of EXPERIMENT as a bundle; extra flags via EXPORT_ARGS`): `$(BIN)/python scripts/export_bundle.py --experiment-name $(EXPERIMENT) --model $(EXPORT_MODEL) $(EXPORT_ARGS)`; new variables `EXPORT_MODEL ?= logistic_regression`, `EXPORT_ARGS ?=` (aligned like the others); `.PHONY` gains `export`; the `train` target's two guard messages: the first says an experiment that already holds runs makes `make export`'s `--model` ambiguous (was: the scorer's), the second says `TRAIN_EXPERIMENT must differ from the export experiment $(EXPERIMENT)` (was: the scoring experiment); the `train` help comment unchanged otherwise. |
| `tests/test_tooling.py:26,32,108-113` | `export` in the target list; `EXPORT_MODEL`, `EXPORT_ARGS` in the variable list; score expectation `".venv/bin/python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --run-date 20250630"`; new `test_make_export_passes_the_reference_defaults` (`--experiment-name readmission-risk-v1 --model logistic_regression`, and `EXPORT_MODEL=xgboost_calibrated` reaches the line). |
| `requirements.in` / `requirements.txt` | `cloudpickle==3.1.2` direct pin (see Step-0 elephant decisions). |
| `.gitignore` | **`/bundles/`, anchored** (with a comment: model bundles, local until v2 slice 3). Unanchored `bundles/` would also ignore `src/readmission_risk/bundles/` and `tests/bundles/` (the file already anchors `/data/` and `/models/` for this reason). |
| `tests/test_docs.py:56,87` | `MUST_BE_IGNORED` gains `"bundles/0123/manifest.json"`; `MUST_NOT_BE_IGNORED` gains `"deploy/champion.json"`, `"src/readmission_risk/bundles/manifest.py"`, `"tests/bundles/test_store.py"`. |
| `deploy/champion.json` | **new**, committed. |
| `tests/conftest.py` | gains the session-scoped `scoring_env` fixture, moved from `tests/scoring/conftest.py` (built lazily: only sessions that request it train). **No new top-level import**: `mlflow`, `tests.scoring.helpers` and `readmission_risk.models.*` are imported inside the fixture body only, because CI's `pipeline-dataproc-runtime` job loads this file under `requirements-dataproc-3.0.txt` (no mlflow/sklearn/xgboost/cloudpickle). The fixture body saves/restores the MLflow tracking URI as today. `tests/scoring/conftest.py` keeps `_restore_mlflow_global_state`; `tests/models/conftest.py` keeps its own equivalent. |
| `tests/scoring/helpers.py` | `ScoringEnv` gains `bundles_root`, `lr_bundle_uri`, `xgb_bundle_uri` (`build_scoring_env` exports both runs with `export_bundle`); `tamper_bundle`, `BundleCase`, `BUNDLE_CASES`, `INTEGRITY_CASES` replace `GuardCase`/`GUARD_CASES`/`clone_run`/`_edit_mlmodel`; `forbid_model_loading` updated; `make_result` builds a `ModelBundle`; `PREDICTION_KWARGS` gains `"model_bundle_id": "b" * 64`. |
| `tests/models/helpers.py` | gains `clone_run` + `_edit_mlmodel` (moved from `tests/scoring/helpers.py`, plus a new `mlmodel_sklearn_serialization_format` keyword) for the relocated `resolve_run_id` tests and the exporter tests. |
| `tests/bundles/{__init__,test_manifest,test_store,test_champion}.py` | **new**. |
| `tests/models/test_bundle_export.py`, `tests/models/test_export_cli.py`, `tests/models/test_tracking.py` | **new** (`test_export_cli.py` loads `scripts/export_bundle.py` with `importlib.util` and drives it with Typer's `CliRunner`, like `tests/scoring/test_cli.py`). |
| `tests/scoring/test_runs.py` | **deleted**, replaced by `tests/scoring/test_bundle_guard.py`. |
| `tests/scoring/test_pipeline.py`, `test_cli.py`, `test_report.py`, `test_store.py`, `test_ranking.py` | ported per the table below. |
| docs | `docs/scoring.md`: commands (l.9-13, 25, 27), the Python-version paragraph and 3.12 recipe (l.16-25: a 3.12-trained run is scored by exporting it to a bundle — `python scripts/export_bundle.py --run-id <id>`, which works under 3.14 because export never unpickles — then, in the 3.12 environment, `python scripts/score_discharges.py --gold-dir <matching gold> --bundle bundles/<id> --run-date ...`; guard 14 still refuses it under 3.14), flags table (l.33-37), output column list + schema version 2 + the mixed-partition note (l.39), guard paragraph (l.41), a new "Bundles" section (export, champion, trust boundary, deletion recipe, `git clean -fdx` caveat, and **the fresh-clone route**: the committed `deploy/champion.json` names a bundle reproducible only from the owner's local MLflow store until slice 3 puts bundles in GCS; anyone else runs `make train TRAIN_EXPERIMENT=<x>`, then `make export EXPERIMENT=<x>`, then `make score SCORE_ARGS="--bundle bundles/<id>"`, the last two printing/using the id the export reports). README l.108's surrounding text gets one sentence pointing to that route. `README.md`: l.37 and l.112 link `validate_run` in `scoring/runs.py` → `validate_bundle` in `scoring/bundle_guard.py`; l.108 command (drop `--experiment-name readmission-risk-v1`); l.144 table row (`runs.py` → `bundle_guard.py`, and "guard-checked model loading (14 guards)" → "manifest-checked model bundles (14 guards + integrity checks)"); the repository-layout block (l.169-184) gains `src/readmission_risk/bundles/   # model bundle format: manifest, local store, champion pointer` and `deploy/champion.json            # the live model bundle (promotion = a PR editing it)`, and the Makefile line lists `export`. `docs/setup.md` l.46 and l.102 (`--model` ambiguity is now the exporter's) and the Makefile table (`make export`, `make score`). CLAUDE.md; the PRD. Implementation runs `grep -rn "runs\.py\|validate_run\|--model\|--run-id\|--experiment-name\|--tracking-dir" README.md docs CLAUDE.md` and fixes every scorer reference it returns. |

## Interfaces

### Bundle layout

```
bundles/<bundle_id>/              dir 0o555
  manifest.json                   0o444, canonical JSON (below) + "\n"
  model.pkl                       0o444, the MLflow logged model's model.pkl byte for byte (a plain cloudpickle of the
                                  sklearn estimator; verified 2026-10-11 to load with cloudpickle under `python -I` with
                                  no mlflow/pyspark imported)
  split_assignment.csv            0o444, the run's split_assignment.csv artifact byte for byte
```

`feature_columns.json` is not copied: its one checked fact (`features`) is a manifest field.

### `bundles/manifest.py`

```python
MANIFEST_FILENAME = "manifest.json"
MODEL_FILENAME = "model.pkl"
SPLIT_FILENAME = "split_assignment.csv"
FILE_ROLES = {MODEL_FILENAME: "model_cloudpickle", SPLIT_FILENAME: "split_assignment"}  # every non-manifest file, exactly
MANIFEST_SCHEMA_VERSION = 1
FINGERPRINT_DEFINITION_V1 = "pandas-csv-sha256-v1"
TOP_LEVEL_KEYS = frozenset({"schema_version", "bundle_id", "model_name", "gold_label_definition", "gold_fingerprint",
    "gold_fingerprint_definition", "params", "library_versions", "python_version", "mlmodel_python_version",
    "feature_columns", "signature", "mlflow", "git", "files", "portable"})
NESTED_KEYS = {
    "params": frozenset({"reference_date", "readmission_window_days", "test_start_date"}),
    "library_versions": frozenset({"sklearn_version", "xgboost_version", "mlflow_version", "pandas_version", "numpy_version"}),
    "mlflow": frozenset({"run_id", "run_status", "run_lifecycle_stage", "model_logged_by_run_id", "model_uri"}),
    "git": frozenset({"commit", "dirty"}),
}
HEX64_RE = re.compile(r"[0-9a-f]{64}")   # used with fullmatch

def sha256_hex(data: bytes) -> str
def canonical_json(obj: dict) -> bytes:
    """json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii").
    ValueError on NaN/inf (allow_nan=False) or a non-JSON value (TypeError re-raised as ValueError)."""
def compute_bundle_id(manifest: dict) -> str:
    """sha256_hex(canonical_json({k: v for k, v in manifest.items() if k != "bundle_id"}))"""
def manifest_bytes(manifest: dict) -> bytes:
    """canonical_json(manifest) + b"\n" -- the exact bytes of manifest.json. ValueError if manifest.get("bundle_id") !=
    compute_bundle_id(manifest)."""
def file_entry(name: str, data: bytes) -> dict:
    """{"sha256": sha256_hex(data), "size": len(data), "role": FILE_ROLES[name]}"""
```

### Manifest fields (schema_version 1)

Every key in `TOP_LEVEL_KEYS` and `NESTED_KEYS` is always present; no other keys. A fact the source run lacks is `null`.
The exporter records faithfully; the scorer judges.

```json
{
  "schema_version": 1,
  "bundle_id": "<64 hex>",
  "model_name": "logistic_regression",                 // tag model_name, or null
  "gold_label_definition": "unplanned_readmission_v1", // tag, or null
  "gold_fingerprint": "<64 hex>",                      // tag, or null
  "gold_fingerprint_definition": "pandas-csv-sha256-v1",   // written unconditionally (the only recipe today), even when
                                                       // the gold_fingerprint tag is absent
  "params": {"reference_date": "20260916", "readmission_window_days": "30", "test_start_date": "20230101"},
                                                       // run params verbatim (MLflow params are strings), or null each
  "library_versions": {"sklearn_version": "1.9.1", "xgboost_version": "3.4.1", "mlflow_version": "3.16.1",
                       "pandas_version": "2.3.3", "numpy_version": "2.4.4"},   // tags verbatim, or null each
  "python_version": "3.14.7",                          // tag python_version, or null
  "mlmodel_python_version": "3.14.7",                  // _mlmodel_python_version(model_info): null if absent or either
                                                       // level not a dict; a JSON-native value (str, int, float, bool,
                                                       // list, dict) recorded as-is; anything else (a YAML date) -> str(v)
  "feature_columns": [...],                            // feature_columns.json "features" value verbatim (any JSON), or
                                                       // null if the key is absent
  "signature": [{"name": "...", "type": "double", "required": true}],
                                                       // [{"name": d.get("name"), "type": d.get("type"),
                                                       //   "required": d.get("required")} for d in
                                                       // model_info.signature.inputs.to_dict()], or null if there is no
                                                       // signature or no inputs (a TensorSpec yields type "tensor",
                                                       // which the scorer refuses)
  "mlflow": {"run_id": "<32 hex>",                    // run.info.run_id
             "run_status": "FINISHED",                 // run.info.status
             "run_lifecycle_stage": "active",          // run.info.lifecycle_stage
             "model_logged_by_run_id": "<32 hex>",     // model_info.run_id (null if MLflow returns None; guard 13 refuses)
             "model_uri": "models:/m-..."},            // the run's model_uri tag (always present: export refuses without it)
  "git": {"commit": "51f1d62...", "dirty": "dirty"},   // tags mlflow.source.git.commit, git_dirty, or null each
  "files": {"model.pkl": {"sha256": "...", "size": 5197, "role": "model_cloudpickle"},
            "split_assignment.csv": {"sha256": "...", "size": 339474, "role": "split_assignment"}},
  "portable": null
}
```

No timestamp, hostname or absolute path. `allow_nan=False`.

### `bundles/store.py` (the storage seam; local in slice 2)

```python
def local_bundle_path(bundle_uri: str) -> Path:
    """A bundle_uri is a local path string: absolute, or relative to the current working directory (like every other
    default path in the CLIs), resolved against the CWD at call time -- also when it came from a champion file elsewhere
    (`--champion other/dir/champion.json` holding `bundles/<id>` means `./bundles/<id>`). ValueErrors (no key prefix; the
    caller adds one): "GCS bundle URIs arrive in v2 slice 3: <uri>" for "gs://..."; "unsupported bundle URI scheme
    '<scheme>://': <uri>" for any other "<scheme>://" (regex `^[A-Za-z][A-Za-z0-9+.-]*://`); "empty bundle URI" for ""."""

def write_bundle(files: Mapping[str, bytes], manifest: dict, bundles_root: Path) -> tuple[str, bool]:
    """Returns (bundle_uri, created); bundle_uri = str(Path(bundles_root) / manifest["bundle_id"]) (not resolved).
    Validates BEFORE touching disk (ValueError): set(files) == set(FILE_ROLES); manifest["files"] == {n: file_entry(n, d)};
    data = manifest_bytes(manifest) (bundle_id check). Then:
      1. target = bundles_root / bundle_id. If target exists (os.path.lexists, any kind of entry):
           identical (a directory whose entries are exactly {manifest.json, *FILE_ROLES}, each a regular file byte-equal to
           what would be written) -> return (uri, False), nothing written;
           anything else (an empty dir, a file, different bytes, extra entries) -> ValueError("bundle <id> already exists
           at <uri> with different content; bundles are never overwritten").
      2. Else: bundles_root.mkdir(parents=True, exist_ok=True); tmp = bundles_root / f".tmp-{bundle_id}-{uuid4().hex}";
         mkdir tmp; write each file (open "xb"), flush + os.fsync, chmod 0o444; os.rename(tmp, target); chmod target 0o555
         (`# noqa: S103` on that one line, with the comment "read+traverse for everyone, like a normal directory minus
         write; the bundle is immutable, not secret" -- ruff's S rule set flags group/other execute bits);
         fsync bundles_root (os.open(dir, O_RDONLY) + os.fsync; skipped with a no-op on platforms raising OSError).
         On an exception before the rename the temp dir is removed (chmod u+w on it and its files first) and the exception
         re-raised. If os.rename raises OSError: the temp dir is removed; then if os.path.lexists(target), step 1's
         comparison is run (identical -> (uri, False); else the ValueError); otherwise the original OSError is re-raised; the remaining check-then-rename window (an empty dir created by
         another process between the check and the rename would be replaced) is accepted for a single-user local store and
         closed in GCS by ifGenerationMatch=0 (slice 3)."""

def list_bundle_entries(bundle_uri: str) -> list[str]:
    """Sorted names of ALL entries directly in the bundle directory (files, subdirectories, symlinks alike).
    FileNotFoundError(f"bundle not found: no bundle at {bundle_uri}") if the path does not exist; ValueError(f"bundle not
    found: {bundle_uri} is not a directory") if it exists but is not a directory."""

def read_bundle_file(bundle_uri: str, name: str) -> bytes:
    """Bytes of one regular file in the bundle directory. ValueError if name contains "/" or "\\" or is "." / "..";
    FileNotFoundError if absent; ValueError if os.lstat shows it is not a regular file (a symlink or directory);
    other OSError (e.g. PermissionError) propagates (validate_bundle converts it)."""
```

### `bundles/champion.py`

```python
@dataclass(frozen=True)
class Champion:
    bundle_uri: str
    sha256: str            # sha256 of the bundle's manifest.json bytes

def read_champion(path: Path) -> Champion:
    """FileNotFoundError(f"no champion pointer at {path}: pass --bundle, or export a bundle and promote it (docs/scoring.md)")
    if absent. ValueError("champion.json: ...") if: not UTF-8 JSON; not an object; keys != {"bundle_uri", "sha256"};
    bundle_uri not a non-empty str; sha256 not a str fullmatching HEX64_RE."""

def champion_json(bundle_uri: str, manifest_sha256: str) -> str:
    """json.dumps({"bundle_uri": bundle_uri, "sha256": manifest_sha256}, indent=2) + "\n" -- the committed file's format."""
```

`deploy/champion.json`: `{"bundle_uri": "bundles/<lr bundle id>", "sha256": "<sha256 of its manifest.json>"}`.

### `models/bundle_export.py` (MLflow side)

```python
@dataclass(frozen=True)
class ExportResult:
    bundle_id: str
    bundle_uri: str
    manifest_sha256: str
    created: bool          # False: an identical bundle already existed

def build_manifest(run, model_info, features_value, files: Mapping[str, bytes]) -> dict:
    """Pure: the manifest (bundle_id included) from an mlflow Run, a ModelInfo, the feature_columns.json "features" value
    (the _ABSENT sentinel when the key is absent -> null) and the file bytes; rules as in the schema above."""

def export_bundle(tracking_dir: Path, run_id: str, bundles_root: Path) -> ExportResult:
```

`export_bundle` steps (each refusal is `ValueError("export: ...")` unless stated):
1. `_require_store(tracking_dir)` (FileNotFoundError, no db created); `mlflow.set_tracking_uri(mlflow_tracking_uri(tracking_dir))`
   inside a try/finally that restores the previous URI; `client = MlflowClient()`.
2. `client.get_run(run_id)`; `MlflowException` → "run not found".
3. tag `model_uri` absent → refuse ("no model_uri tag").
4. `model_info = mlflow.models.get_model_info(model_uri)`; `MlflowException` → refuse.
5. `flavors = model_info.flavors`; `flavors["sklearn"]["serialization_format"] != "cloudpickle"` (or the keys absent / not
   dicts) → refuse ("the scorer loads cloudpickle models only"); `flavors["sklearn"]["pickled_model"] != "model.pkl"`
   exactly → refuse; `flavors["sklearn"].get("code")` not None → refuse ("models logged with code paths are not
   bundleable: the bundle carries model.pkl only"; the real v1 MLmodels have `code: null`).
6. Model bytes: `mlflow.artifacts.download_artifacts(artifact_uri=model_uri, dst_path=tmp)` (downloads the logged model
   directory through MLflow, wherever its store keeps it; **verified 2026-10-11 on the real store with MLflow 3.16.1**: a
   `models:/m-<id>` URI resolves and the returned directory contains `model.pkl`), then read `<returned dir>/model.pkl`; `(MlflowException, OSError)` from the download and `OSError` from the read → refuse (named types, not `except Exception`: the repo enables ruff's BLE rules; this is `runs.py::_download_artifact`'s pattern). A
   store moved after training breaks this exactly as it breaks `load_logged_model` today (MLflow records absolute artifact
   URIs); out of scope.
7. `client.download_artifacts(run_id, "feature_columns.json", tmp)` and `"split_assignment.csv"`; missing → refuse.
   `feature_columns.json` not JSON, or not a JSON object → refuse. The `features` value is taken verbatim (absent → null).
8. `manifest = build_manifest(...)`; `write_bundle({"model.pkl": ..., "split_assignment.csv": ...}, manifest, bundles_root)`.
9. Return `ExportResult(..., manifest_sha256=sha256_hex(manifest_bytes(manifest)))`.

Everything else (status, lifecycle, missing tags/params, odd Python versions, no signature) is recorded and judged by
the scorer.

`scripts/export_bundle.py`: `--tracking-dir` (default `mlflow`), `--bundles-dir` (default `bundles`), and either
`--run-id` or both `--experiment-name` and `--model` (resolved with `models.tracking.resolve_run_id`). Any other
combination → `typer.BadParameter` (exit 2). The `bundle_uri` printed in the champion text is the bundle directory
relative to the current working directory (POSIX separators) when the resolved directory lies inside it; otherwise the
absolute path, and stderr gets `WARNING: bundle_uri is an absolute, machine-specific path; do not commit it`. Stdout (the `bundle_uri:` line prints the same relative-or-absolute form as the champion text): `bundle_id: <id>`, `bundle_uri: <uri>`, `status: created` or
`status: already existed`, a blank line, `champion.json for this bundle:`, then `champion_json(...)`. `ValueError` /
`FileNotFoundError` → `ERROR: <msg>` on stderr, exit 1.

### `scoring/bundle_guard.py` (mlflow-free)

```python
@dataclass(frozen=True, eq=False)
class ModelBundle:
    bundle_id: str
    bundle_uri: str             # exactly the string validate_bundle was given (the champion's or --bundle's), unresolved
    mlflow_run_id: str          # manifest mlflow.run_id; written to predictions.model_run_id
    model_name: str
    test_start_date: str        # YYYYMMDD
    gold_fingerprint: str
    git_commit: str | None
    git_dirty: str | None
    partition_of: pd.Series     # encounter_id -> "train" | "test"
    model_bytes: bytes = field(repr=False)   # the sha256-verified model.pkl bytes; the loader unpickles THESE

_DISTRIBUTIONS = {"sklearn_version": "scikit-learn", "xgboost_version": "xgboost", "mlflow_version": "mlflow",
                  "pandas_version": "pandas", "numpy_version": "numpy"}

def installed_versions() -> dict[str, str]:
    """{key: importlib.metadata.version(dist)}; "not installed" on PackageNotFoundError. Never imports mlflow."""

def parse_python_major_minor(value: object) -> tuple[int, int] | None    # moved verbatim from runs.py:51-57

def validate_bundle(bundle_uri: str, *, gold: pd.DataFrame, metadata: GoldMetadata,
                    expected_manifest_sha256: str | None = None, champion_path: Path | None = None) -> ModelBundle:
    """champion_path is used only in the I2 message (the pointer actually followed)."""

def load_bundle_model(bundle: ModelBundle):
    """with warnings.catch_warnings(): warnings.simplefilter("error", sklearn.exceptions.InconsistentVersionWarning);
    model = cloudpickle.loads(bundle.model_bytes)  -- always the module attribute `cloudpickle.loads`, never a from-import.
    RuntimeError if the result has no callable predict_proba."""
```

**Uniform error rule** (carried from `runs.py`'s module docstring): every absent, malformed or unreadable bundle fact is a
`ValueError` whose message contains the check's key — **including an absent bundle path** ("bundle not found: no bundle
at <uri>", a ValueError exactly as today's "run not found" is): `validate_bundle` converts `list_bundle_entries`'
FileNotFoundError. The only FileNotFoundError the scorer raises for model sources is `read_champion`'s for an absent
default/explicit champion pointer. Any `OSError` raised while listing or reading the bundle (e.g. `PermissionError`) is
converted to `ValueError("<key>: cannot read bundle at <uri>: <exc>")` with the key of the check in progress; likewise
every `ValueError` raised by `bundles.store` (`local_bundle_path`, `list_bundle_entries`, `read_bundle_file`: scheme,
non-directory, symlink, traversal) is re-raised as `ValueError(f"{key}: {exc}")` with the key of the check in progress — unless its message already starts
with `f"{key}:"`, in which case it passes through unchanged (no "bundle not found: bundle not found: ...")
(`bundle not found` for the URI and directory, `manifest` while reading manifest.json, `files` while reading the other
files); `read_champion`
converts `OSError` other than FileNotFoundError (`IsADirectoryError`, `PermissionError`) to `ValueError("champion.json:
cannot read <path>: <exc>")`. Never a `KeyError`, `TypeError`, `AttributeError` or JSON/pandas parse error. Type checks
are strict (`type(x) is int`, so `True` is not `1`; `type(x) is str`).

**Message template.** Integrity messages (I1–I5) identify the bundle only by its URI (`bundle at {uri}`), never by a
manifest value, so building a message can never fail. Guard messages (1–14), which run after I5 has type-checked
`mlflow.run_id`, use `who = f"bundle {bundle_id[:12]} (MLflow run {run_id})"` with the verified `bundle_id` and the
32-hex `run_id`. Each guard's message keeps today's wording with "MLflow run {run_id}" replaced by `{who}`, and keeps
today's key substring (table below).

**Integrity keys carry their colon.** Every integrity message starts with its key followed by `": "` (`bundle not found: `,
`manifest: `, `champion sha256: `, `bundle_id: `, `files: `, `manifest shape: `), and the integrity test cases assert the
key **with the colon** at the start of the message (`message.startswith(key)`), so `manifest:` cannot be satisfied by
"manifest shape:" or "pins manifest", and `files:` not by prose. Guard keys (1–14) stay substring matches as today.

**Check order inside `validate_bundle`** — I1 (directory, read manifest bytes) → I2 → I1 (parse) → I3 → I4 → I5 →
guards 1–14. `uri_path = local_bundle_path(uri)`
is computed first (its ValueError carries key `bundle not found`).

| Key (substring in the message) | Check |
|---|---|
| I1 `bundle not found` | `list_bundle_entries(uri)`; its FileNotFoundError becomes ValueError "bundle not found: no bundle at <uri>"; a non-directory is ValueError (same key). A symlinked bundle directory is allowed (see I3). |
| I1 `manifest` (read) | `manifest.json` in the entries, else ValueError "manifest: bundle at <uri> has no manifest.json"; its bytes read. |
| I2 `champion sha256` | only with `expected_manifest_sha256`, and **on the raw bytes, before any parsing** (the trust anchor is checked first): `sha256_hex(manifest bytes) == expected`, else "champion sha256: {champion_path} pins manifest <x> but the manifest.json of bundle at <uri> has sha256 <y>" (the path actually followed). |
| I1 `manifest` (parse) | UTF-8 JSON object, else ValueError "manifest: ..."; `type(schema_version) is int and == 1`, else "manifest: unsupported schema_version ...". |
| I3 `bundle_id` | `manifest.get("bundle_id")` (absent → "bundle_id: manifest has no bundle_id") is a str fullmatching HEX64_RE; `== compute_bundle_id(manifest)` (a `canonical_json` failure → ValueError "bundle_id: ..."); `==` the directory name `uri_path.resolve().name` (resolved, so `--bundle .` from inside the bundle and a symlinked bundle directory both compare the real directory's name); the file's bytes `== manifest_bytes(manifest)` (non-canonical formatting refused). |
| I4 `files` | `manifest.get("files")` (absent → "files: manifest has no files table") is a dict with keys exactly `FILE_ROLES`; each value a dict with keys exactly `{"sha256","size","role"}`, `sha256` fullmatching HEX64_RE, `type(size) is int`, `role == FILE_ROLES[name]`; `list_bundle_entries(uri) == sorted({MANIFEST_FILENAME, *FILE_ROLES})` (a missing or unlisted entry refused, each named); each file read via `read_bundle_file` (a symlink/dir refused) with matching size and sha256. The model and split bytes are kept from this read. |
| I5 `manifest shape` | `set(manifest) == TOP_LEVEL_KEYS` (missing and unknown keys listed); for each key of `NESTED_KEYS`, the value is a dict with exactly those keys. Plus the types of the facts no guard judges but the output uses: `mlflow.run_id` is a str fullmatching `[0-9a-f]{32}`; `mlflow.model_uri` is a str; `git.commit` and `git.dirty` are each a str or null; `portable` is null (schema 1 has no portable form; see follow-up 2). Every other value is type-checked by the guard that uses it. |

Note: I3 runs before I5 but only needs `bundle_id`; `compute_bundle_id` hashes whatever is there. I5 runs before any
guard, so guards never meet a missing key or a non-dict container. **Every guard type-checks the value it uses before
using it** (a value of the wrong type is refused with the guard's key, never compared, hashed or sliced): guard 2's two
values must be str; guard 4 str; guard 5 str or null; guard 6 `gold_fingerprint_definition` str (else refused before the
registry lookup) and `gold_fingerprint` str or null; guard 7 and 12 params str or null; guard 8 values str or null; guard
13 `model_logged_by_run_id` str (null or non-str refused as "model provenance: ... records no model run"); guard 14 as
today (non-str is "not a version").

### Guard → manifest mapping (all 14, one to one; none dropped)

| # | Guard today (`runs.py::validate_run`) | Manifest fact | Scorer rule; key kept from today | Semantics vs today |
|---|---|---|---|---|
| 1 | run exists in the store | the bundle directory and its `manifest.json` | I1; key **`bundle not found`** (today `run not found`) | same rule; the identifier is a bundle URI |
| 2 | run FINISHED; not deleted | `mlflow.run_status`, `mlflow.run_lifecycle_stage` | `== "FINISHED"` (`not FINISHED`); `== "active"` (`deleted`) | same, but frozen at export: an MLflow deletion after export no longer refuses (bundles are immutable, G19) |
| 3 | `model_uri` tag present | `files["model.pkl"]` (role `model_cloudpickle`); `mlflow.model_uri` provenance | I4 (`files`) | same intent; a run without `model_uri` cannot be exported |
| 4 | `model_name` in `MODEL_NAMES` | `model_name` | null → "has no 'model_name'" (`model_name`); not in `MODEL_NAMES` (`model_name`) | same |
| 5 | `gold_label_definition` present and equal | `gold_label_definition` | null → "has no 'gold_label_definition' ... predates slice 2b"; `!=` (`gold_label_definition`) | same |
| 6 | `gold_fingerprint` == `gold_fingerprint(gold)` | `gold_fingerprint_definition`, `gold_fingerprint` | definition not in `{"pandas-csv-sha256-v1": gold_fingerprint}` → "gold_fingerprint definition ... unknown" (`gold_fingerprint`); then equality (`gold_fingerprint`) | same + definition check (new) |
| 7 | params `reference_date`, `readmission_window_days` | `params.*` | `== metadata.reference_date`; `== str(metadata.readmission_window_days)`; each message names only its param (`reference_date` / `readmission_window_days`) | same |
| 8 | five library tags == installed | `library_versions` | each `== installed_versions()[k]`, mismatches joined (`library version`) | same five; installed versions via `importlib.metadata` (verified equal to `__version__` for all five) |
| 9 | `features` == `FEATURE_COLUMNS` | `feature_columns` | `type is list and == list(FEATURE_COLUMNS)`; message "feature_columns.json of {who} lists features ..." (key **`feature_columns.json`** kept) | same |
| 10 | signature input names == `FEATURE_COLUMNS` | `signature` | list of dicts each with keys exactly `{"name","type","required"}`, str `name`/`type`, `required is True` for every entry, names `== FEATURE_COLUMNS`, **and** types `"double"` for `COUNT_FEATURES + NUMERIC_OTHER_FEATURES`, `"string"` for `CATEGORICAL_FEATURES` (`signature`) | names same; dtype check added (FR4); v1 signatures satisfy it |
| 11 | `split_assignment.csv` well-formed, ids in gold | the verified split bytes | `_read_assignment(data: bytes, gold, who)`: today's checks on `pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)`; messages "split_assignment.csv: file of {who} cannot be parsed: ..." etc. (key **`split_assignment.csv`** kept) | same |
| 12 | split consistent with `test_start_date` + gold | `params.test_start_date` + split | `_check_split_consistency` moved verbatim (`split consistency`) | same |
| 13 | model logged by this run | `mlflow.model_logged_by_run_id`, `mlflow.run_id` | equal, else "model provenance: the model of {who} was logged by run {x!r}" (`model provenance`) | same fact, captured at export |
| 14 | Python major.minor (i)–(vi) | `python_version` (tag), `mlmodel_python_version` | `_check_python_version(python_version, mlmodel_python_version, who)`: identical steps, order and message texts, with "MLflow run {run_id}" → `{who}`; source labels "run tag" / "logged model's MLmodel" kept (`python version`) | exact semantics kept |

### `scoring/pipeline.py`

```python
@dataclass(frozen=True)
class ScoringConfig:
    gold_dir: Path
    run_date: str
    output_dir: Path = Path("data/predictions")
    bundle_uri: str | None = None        # explicit bundle; mutually exclusive with champion_path
    champion_path: Path | None = None    # None and bundle_uri None -> constants.DEFAULT_CHAMPION_PATH (relative to CWD)
    top_n: int = DEFAULT_TOP_N
    window_days: int | None = None
    show_observed_outcomes: bool = False

# ScoringResult.run is renamed ScoringResult.bundle: ModelBundle
def score_run_date(config, *, loader=load_bundle_model, writer=write_partition) -> ScoringResult
```

`_validate_config` (pure, before any I/O): today's run_date/top_n/window_days checks; `bundle_uri` None or a non-empty
`str`, else ValueError "bundle_uri must be ..."; `champion_path` None or a `Path`, else ValueError; both set →
`ValueError("--bundle and --champion are mutually exclusive")`. Step 4 of `score_run_date`: if `bundle_uri` is None,
`path = config.champion_path or DEFAULT_CHAMPION_PATH; champion = read_champion(path)` and validate `champion.bundle_uri`
with `expected_manifest_sha256=champion.sha256, champion_path=path`; else validate `config.bundle_uri` with no expected sha. Step 6:
`model = loader(bundle)`. Step 7: `build_predictions(..., model_bundle_id=bundle.bundle_id, model_run_id=bundle.mlflow_run_id,
model_name=bundle.model_name, gold_fingerprint=bundle.gold_fingerprint, ...)`. Everything else unchanged.

### CLI (`scripts/score_discharges.py`)

`bundle: str | None = typer.Option(None, help="A bundle directory to score with. Mutually exclusive with --champion.")`;
`champion: Path | None = typer.Option(None, help="The champion pointer to follow (when neither --bundle nor --champion is
given: deploy/champion.json, relative to the current directory).")`. The Typer default of both is `None`, so passing
`--bundle` alone never trips the mutual-exclusion check. Removed: `--tracking-dir`, `--experiment-name`, `--model`,
`--run-id`. Error handling unchanged.

### Report

`model: {model_name} bundle={bundle_id[:12]} mlflow_run_id={mlflow_run_id} (trained at commit {commit7}, working tree
{git_dirty or 'unknown'})` — the "trained at commit ..., working tree ..." tail is unchanged.

## UX flow (pipeline)

```
train_models.py → MLflow run (unchanged)
export_bundle.py --experiment-name readmission-risk-v1 --model logistic_regression
   → bundles/<id>/{manifest.json, model.pkl, split_assignment.csv}  (+ prints champion.json text)
promotion = a commit/PR editing deploy/champion.json  (FR15 gate table: slice 6)
score_discharges.py --gold-dir ... --run-date ...  (defaults to deploy/champion.json)
   → config → gold → champion → I1..I5 → guards 1..14 → batch → cloudpickle.loads(verified bytes) → score → write
```

Deleting a bundle (they are read-only on purpose): `chmod -R u+w bundles/<id> && rm -rf bundles/<id>` (documented in
`docs/scoring.md`). pytest's `tmp_path` cleanup already handles read-only trees (it chmods on PermissionError).

## Data handling

- No change to the gold table, the label, features, the split, or training. No new randomness; no seeds involved.
- **Split integrity**: the bundle carries the run's `split_assignment.csv` byte for byte with its sha256; the scorer
  still scores only the bundle's test partition and still re-derives guard 12 against the gold table and
  `test_start_date`. A bundle cannot change which rows count as held out without changing its id.
- **Leakage**: the scorer still drops `LABEL_SIDE_COLUMNS` before scoring; the label is still read only for the opt-in
  summary after scoring. Unchanged code paths.
- **Reproducibility**: `bundle_id` is deterministic (test: two exports of one run into two roots give identical bytes).
  The champion pointer is in git, so "which model scored this partition" is answerable from git + `model_bundle_id`.
- **Trust boundary**: cloudpickle can execute code on load. Integrity is anchored in `champion.json`'s sha256 (committed,
  reviewed) → manifest → per-file sha256. A `--bundle` path without a champion is anchored only in its self-consistent id
  (whoever can write the bundle can recompute the hashes); docs say so. Local bundles are trusted local input, like the
  MLflow store was.
- **Dirty-tree caveat**: the v1 champion records `git.dirty = "dirty"`; reported, not refused.

## Failure modes

| Situation | Behaviour |
|---|---|
| `deploy/champion.json` absent (default source) | FileNotFoundError → `ERROR:` exit 1, says to pass `--bundle` or export + promote. |
| champion.json malformed / extra keys / bad sha256 | ValueError `champion.json: ...`, exit 1. |
| champion points at a missing bundle (fresh clone: bundles are gitignored) | ValueError `bundle not found: no bundle at bundles/<id>` (exit 1); docs/scoring.md says to run `make export` first (the export reproduces the same id from the same MLflow store). |
| bundle_uri is a file, not a directory | ValueError `bundle not found: ... is not a directory`. |
| manifest bytes changed in any way (edited, re-hashed, invalid JSON, other schema_version) | with a champion: **I2** `champion sha256` (checked on the raw bytes before parsing); with `--bundle`: I1 (parse) or I3 `bundle_id`, as applicable. Pinned by tests. |
| manifest edited and re-hashed, `--bundle` | I3 passes only if the directory is renamed to the new id; otherwise I3 (directory name). |
| a file swapped/truncated | I4 sha256/size mismatch. |
| extra/missing file, subdirectory, symlink | I4. |
| unreadable file or directory (PermissionError) | ValueError with the key of the check in progress. |
| champion path is a directory / unreadable | ValueError `champion.json: cannot read ...`. |
| `mlflow.run_id` null/not 32-hex, `git.commit` an int, `portable` non-null | I5 `manifest shape`. |
| a guard's value of the wrong type (e.g. `gold_fingerprint_definition` a list) | refused with that guard's key. |
| manifest missing a key / unknown key / `params` not an object | I5 `manifest shape`. |
| `gs://` bundle URI | ValueError "bundle not found: GCS bundle URIs arrive in v2 slice 3: <uri>". |
| unknown fingerprint definition / schema_version | guard 6 / I1. |
| NaN in a manifest value at export | ValueError from `canonical_json`. |
| partitions written before this slice (schema "1") next to new ones (schema "2") | not supported as one dataset; `docs/scoring.md` says to re-score or delete old partitions (they are local, gitignored demo output). The implementation re-scores `data/predictions/run_date=20250630` (the only one the README assets read) and lists any other local schema-1 partition to the owner (not deleted without asking). Slice 4's BigQuery table starts at schema 2. |
| export of an identical bundle again | no-op, `status: already existed`. |
| export when the id's path exists with different content (incl. an empty dir) | ValueError; nothing written. |
| export interrupted | temp `.tmp-*` dir removed; never a partial bundle under the final name. |
| concurrent exports of the same run | the rename loser re-compares; identical → `already existed`. |
| mlflow not installed in the scoring env (future) | guard 8 refuses (`installed 'not installed'`); accepted (owner decision). |
| `InconsistentVersionWarning` at unpickle | exception from `load_bundle_model` → traceback (a broken environment). |
| Python minor differs | guard 14, unchanged semantics. |

## Verification criteria

### Test infrastructure

- `scoring_env` (now in `tests/conftest.py`) trains once per session as today, then `export_bundle`s both runs into
  `<tmp>/bundles` and records `lr_bundle_uri` / `xgb_bundle_uri`.
- `tamper_bundle(src_uri, dst_root, *, set=None, delete=(), files=None, rehash=True, manifest_raw=None, dir_name=None,
  extra_entries=None, drop_manifest=False, symlink_files=None) -> str` (tests/scoring/helpers.py). It never calls `write_bundle`. Steps: read the source manifest
  (JSON) and files, then in this order: (a) apply `set`/`delete` edits whose path does NOT start with `"files"`; (b) apply
  `files` (`{name: bytes}` replaces, `{name: None}` removes the file); (c) if `rehash`, recompute
  `files[name]["sha256"/"size"]` for every file still present (a removed file's entry is kept as it was); (d) apply
  `set`/`delete` edits whose path starts with `"files"`; (e) if `rehash`, `bundle_id = compute_bundle_id(manifest)`.
  Path rules are in "`tamper_bundle` keyword semantics" below (dotted strings or tuples; `set` may create a key only at
  the top level, otherwise the parent must exist); serialize with `manifest_bytes`-equivalent
  canonical bytes, or write `manifest_raw` verbatim when given; directory name = `dir_name`, else the manifest's `bundle_id` if it is a str, else the source directory's name;
  write all bytes into `dst_root/<dir name>/` as ordinary writable files (no chmod); create each `extra_entries` name
  (`{"x.txt": b"..."}` file, or `{"sub/": None}` directory); then for each `symlink_files` name (keyword
  `symlink_files: Mapping[str, Path] | None = None`), replace that file with a symlink to the given target. Returns the
  directory path as a str.
- `BundleCase` (frozen dataclass, tests/scoring/helpers.py): `case_id: str`, `key: str`, `build: Callable[[ScoringEnv,
  Path], tuple[dict, tuple[str, ...]]]` taking `(env, tmp_path)` and returning `(config_overrides, extras)` where
  `config_overrides` is a dict of `ScoringConfig` fields — `{"bundle_uri": ...}` for ordinary cases, `{"champion_path":
  ...}` for the I2 cases (the build writes the champion file into `tmp_path`) — and `forbidden: tuple[str, ...] = ()`.
  `BUNDLE_CASES` and `INTEGRITY_CASES` are lists of `BundleCase`; one parametrized test
  `test_each_check_refuses_exactly_its_defect` runs both lists (`ids=case_id`), building `_config(env, out,
  **config_overrides)` (when `champion_path` is given, `_config` drops its default `bundle_uri`).
- `forbid_model_loading(monkeypatch)` patches exactly `cloudpickle.load`, `cloudpickle.loads`, `pickle.load`,
  `pickle.loads` to raise AssertionError (not `pickle.Unpickler`). Patching `pickle` is intentional and broader than
  before (`cloudpickle.loads is pickle.loads`); the gold-load and selection paths (pandas/pyarrow Parquet, JSON sidecar)
  do not unpickle. If a guard case trips the patch for a non-model reason, that is a finding to investigate, not to
  silence. New `test_forbid_model_loading_catches_the_real_loader`
  proves `load_bundle_model` raises under it.
- `_config` in `test_pipeline.py` always sets `bundle_uri=env.lr_bundle_uri` unless the test passes `champion_path` or
  its own `bundle_uri`, so no test reads the repository's `deploy/champion.json` by accident. The CLI's `_real_args`
  always passes `--bundle`.

### Case table: every one of the 48 GUARD_CASES

M = mechanical port (same defect, same refusal key, represented in the manifest); S = semantic (counted below).
Unless noted, a case uses `tamper_bundle(env.lr_bundle_uri, ..., rehash=True)` and runs through the bundle path
(`bundle_uri=`), so I2 is not involved.

| Case | New build | Key | M/S |
|---|---|---|---|
| 1-bogus-run-id | `bundle_uri = str(tmp/"bundles"/("0"*64))` (absent) | `bundle not found` | S |
| 2-status-failed | `set={"mlflow.run_status": "FAILED"}` | `not FINISHED` | M |
| 2-soft-deleted | `set={"mlflow.run_lifecycle_stage": "deleted"}` | `deleted` | S (deletion now frozen at export) |
| 3-no-model-uri | → `I4-missing-model` (`files={"model.pkl": None}`) | `files` | S |
| 4-unknown-model-name | `set={"model_name": "random_forest"}` | `model_name` | M |
| 4-no-model-name | `set={"model_name": None}` | `model_name` | M |
| 5-other-label-definition | `set={"gold_label_definition": "all_cause_v0"}` | `gold_label_definition` | M |
| 5-no-label-definition | `set={"gold_label_definition": None}` | `gold_label_definition` | M |
| 6-wrong-fingerprint | `set={"gold_fingerprint": "0"*64}` | `gold_fingerprint` | M |
| 6-no-fingerprint | `set={"gold_fingerprint": None}` | `gold_fingerprint` | M |
| 7-reference-date-mismatch | `set={"params.reference_date": "20260917"}`, forbidden `readmission_window_days` | `reference_date` | M |
| 7-window-mismatch | `set={"params.readmission_window_days": "14"}`, forbidden `reference_date` | `readmission_window_days` | M |
| 7-no-reference-date | `set={"params.reference_date": None}` | `reference_date` | M |
| 7-no-window | `set={"params.readmission_window_days": None}` | `readmission_window_days` | M |
| 8-old-sklearn | `set={"library_versions.sklearn_version": "0.0.0"}` | `library version` | M |
| 8-no-numpy-tag | `set={"library_versions.numpy_version": None}` | `library version` | M |
| 9-features-reversed | `set={"feature_columns": reversed}` | `feature_columns.json` | M |
| 9-features-extra | `set={"feature_columns": [*FEATURE_COLUMNS, "extra"]}` | `feature_columns.json` | M |
| 9-no-features-key | `set={"feature_columns": None}` | `feature_columns.json` | M |
| 9-not-json | retired from the scorer: export-time refusal, tested in `test_bundle_export.py` | — | S |
| 9-features-not-a-list | `set={"feature_columns": "abc"}` | `feature_columns.json` | M |
| 9-no-artifact | retired from the scorer: export-time refusal, tested in `test_bundle_export.py` | — | S |
| 10-signature-reversed | `set={"signature": reversed entries}` | `signature` | M |
| 10-no-signature | `set={"signature": None}` | `signature` | M |
| 10-bad-model-uri | retired: no such state in a bundle; export-time refusal (unreadable model info) tested in `test_bundle_export.py` | — | S |
| 11-wrong-header | `files={"split_assignment.csv": header replaced}` | `split_assignment.csv` | M |
| 11-duplicate-id | split bytes + duplicated line | `split_assignment.csv` | M |
| 11-unknown-partition | split bytes, `,test`→`,validation` once | `split_assignment.csv` | M |
| 11-no-test-row | split bytes, all `,test`→`,train` | `split_assignment.csv` | M |
| 11-id-not-in-gold | split bytes + `not-in-gold,test` | `split_assignment.csv` | M |
| 11-empty-file | `files={"split_assignment.csv": b""}` | `split_assignment.csv` | M |
| 11-no-artifact | → `I4-missing-split` (`files={"split_assignment.csv": None}`) | `files` | S |
| 12-train-relabelled-test | split bytes as today's `_relabel_case`, same count sentence | `split consistency` | M |
| 12-test-relabelled-train | idem | `split consistency` | M |
| 12-dropped-added-as-train | idem | `split consistency` | M |
| 12-no-test-start-date | `set={"params.test_start_date": None}` | `split consistency` | M |
| 12-bad-test-start-date | `set={"params.test_start_date": "not-a-date"}` | `split consistency` | M |
| 13-model-of-another-run | `set={"mlflow.model_logged_by_run_id": env.xgb_run_id}`, forbidden `signature` | `model provenance` | M |
| 14-other-minor | `set={"python_version": OTHER_MINOR, "mlmodel_python_version": OTHER_MINOR}`; same extras/forbidden | `python version` | M |
| 14-legacy-other-minor | `set={"python_version": None, "mlmodel_python_version": OTHER_MINOR}` | `python version` | M |
| 14-tag-vs-mlmodel | `set={"python_version": OTHER_MINOR}` | `python version` | M |
| 14-malformed-tag | `set={"python_version": "3.x"}` | `python version` | M |
| 14-malformed-mlmodel | `set={"python_version": None, "mlmodel_python_version": "garbage"}` | `python version` | M |
| 14-no-version-anywhere | `set={"python_version": None, "mlmodel_python_version": None}` | `python version` | M |
| 14-empty-tag | `set={"python_version": ""}` | `python version` | M |
| 14-mlmodel-yaml-float | `set={"python_version": None, "mlmodel_python_version": 3.1}` | `python version` | M |
| 14-mlmodel-vs-tag | `set={"mlmodel_python_version": OTHER_MINOR}` | `python version` | M |
| 14-non-dict-python-function-flavor | moved to `test_bundle_export.py` (exporter records `null`) | — | S |

New bundle cases (not counted; additions): `6-unknown-fingerprint-definition` (`set={"gold_fingerprint_definition":
"spark-v9"}`, key `gold_fingerprint`), `6-fingerprint-definition-not-a-string` (`set={"gold_fingerprint_definition": ["x"]}`,
key `gold_fingerprint`), `10-signature-dtype-mismatch` (first entry's type `"string"`, key `signature`), `10-signature-optional-column` (entry 0's `"required": False`, key `signature`),
`13-logged-by-null` (`set={"mlflow.model_logged_by_run_id": None}`, key `model provenance`), `2-status-not-a-string`
(`set={"mlflow.run_status": 1}`, key `not FINISHED`).

`tamper_bundle` keyword semantics: `set` keys are dotted strings (split on every dot) or tuples (used verbatim, needed
for file names with a dot, e.g. `("files", "model.pkl", "role")`); `set` may create a new key only at the top level (below
it the parent must exist); edits under `"files"` run in step (d), after the sha/size recompute, so a deliberately wrong
role or size survives; `drop_manifest=True` writes no
manifest.json; `manifest_raw` (bytes) replaces the serialized manifest verbatim.

`INTEGRITY_CASES` (new; through `bundle_uri=` unless the row says champion):

| Case | Build | Key |
|---|---|---|
| I1-not-a-directory | `bundle_uri` = a regular file in tmp | `bundle not found: ` |
| I1-no-manifest | `drop_manifest=True` | `manifest: ` |
| I1-not-json | `manifest_raw=b"not json"` | `manifest: ` |
| I1-not-object | `manifest_raw=b"[]\n"` | `manifest: ` |
| I1-schema-version-2 | `set={"schema_version": 2}` | `manifest: ` |
| I1-schema-version-true | `set={"schema_version": True}` | `manifest: ` |
| I3-edited-without-rehash | `set={"model_name": "xgboost_calibrated"}`, `rehash=False` | `bundle_id: ` |
| I3-renamed-dir | `dir_name="f"*64` | `bundle_id: ` |
| I3-non-canonical-bytes | `manifest_raw=json.dumps(valid manifest, indent=2).encode()` | `bundle_id: ` |
| I4-missing-model | `files={"model.pkl": None}` (ex-3-no-model-uri) | `files: ` |
| I4-missing-split | `files={"split_assignment.csv": None}` (ex-11-no-artifact) | `files: ` |
| I4-model-byte-flipped | `files={"model.pkl": first byte XOR 1}`, `rehash=False` | `files: ` |
| I4-split-truncated | `files={"split_assignment.csv": bytes[:-10]}`, `rehash=False` | `files: ` |
| I4-extra-file | `extra_entries={"notes.txt": b"x"}` | `files: ` |
| I4-subdirectory | `extra_entries={"sub/": None}` | `files: ` |
| I3-no-bundle-id | `delete=("bundle_id",)`, `rehash=False` (directory keeps the source id) | `bundle_id: ` |
| I4-no-files-key | `delete=("files",)` (rehash recomputes `bundle_id`; step (c) is skipped when `files` is absent) | `files: ` |
| I4-symlinked-model | `symlink_files={"model.pkl": <a copy of the real model.pkl outside the bundle>}` (sha256 still matches) | `files: ` |
| I1-gs-uri | `bundle_uri="gs://b/x"` | `bundle not found: ` |
| I1-other-scheme | `bundle_uri="s3://b/x"` | `bundle not found: ` |
| I2-invalid-json-under-champion | `manifest_raw=b"not json"` behind a champion pinning the original sha; forbidden `manifest:` | `champion sha256: ` |
| I4-wrong-role | `set={("files", "model.pkl", "role"): "x"}` | `files: ` |
| I4-size-is-bool | `set={("files", "model.pkl", "size"): True}` | `files: ` |
| I5-missing-key | `delete=("git",)` | `manifest shape: ` |
| I5-unknown-key | `set={"extra": 1}` | `manifest shape: ` |
| I5-params-not-object | `set={"params": "x"}` | `manifest shape: ` |
| I5-run-id-null | `set={"mlflow.run_id": None}` | `manifest shape: ` |
| I5-run-id-not-hex | `set={"mlflow.run_id": "run-1"}` | `manifest shape: ` |
| I5-git-commit-int | `set={"git.commit": 7}` | `manifest shape: ` |
| I5-portable-not-null | `set={"portable": {"x": 1}}` | `manifest shape: ` |
| I2-champion-sha-mismatch | champion `{bundle_uri: env.lr_bundle_uri, sha256: "0"*64}` | `champion sha256: ` |
| I2-edited-without-rehash-under-champion | the I3-edited-without-rehash bundle behind a champion pinning the original manifest sha; forbidden `bundle_id` | `champion sha256: ` |

All guard and integrity cases run through `score_run_date(config, loader=strict_loader, writer=strict_writer)` with
`forbid_model_loading(monkeypatch)` applied **after** the fixture is built, asserting the key substring, the extras and
forbidden substrings, no loader/writer call and no output dir — today's harness.

### Kill-trigger count (FR5): 23 of 140 = 16.4% (threshold 28 = 20%)

Rule: a test is **semantic** if its contract changes — the scenario it exercises no longer exists in the scorer
(retired or moved out), or its expected outcome changes (refusal key, exception type, an accepted input now refused or
vice versa, an output format or schema). It is **mechanical** if the same defect and the same outcome are expressed
through the bundle (a run tag absent → the manifest field `null`; a param or tag edited → the field edited; artifact
bytes edited → bundle file bytes edited and re-hashed) or only plumbing changes (`run_id=` → `bundle_uri=`, `result.run`
→ `result.bundle`, a new kwarg in a shared fixture). The PRD itself prescribes the port ("All guard cases ... are ported to
manifest fixtures"), so a faithful port is not a semantic change. Added tests do not count.

| File (tests before) | Semantic tests | Count |
|---|---|---|
| `test_pipeline.py` guard cases (48) | 1-bogus-run-id, 2-soft-deleted, 3-no-model-uri, 9-not-json, 9-no-artifact, 10-bad-model-uri, 11-no-artifact, 14-non-dict-python-function-flavor | 8 |
| `test_pipeline.py` `_BAD_CONFIGS` (15) | `model_name: random_forest`, `run_id: ""`, `model_name + run_id` (replaced by `bundle_uri: ""`, `bundle_uri: 3`, `bundle_uri + champion_path`; plus one added entry, not counted, `champion_path: "deploy/champion.json"` (a str, not a Path)) | 3 |
| `test_pipeline.py` others (13) | `test_default_model_and_explicit_run_id` (becomes `test_default_source_is_the_champion_and_explicit_bundle`), `test_valid_config_parses_and_run_id_may_accompany_an_experiment_name` (becomes `..._and_bundle_and_champion_are_exclusive`). Mechanical: end_to_end, label_does_not_reach_ranking (`set={"gold_fingerprint": ...}`), default_window_days (`set={"params.readmission_window_days": "14"}`), refusals_before_load_and_write (`"0"*32` → an absent bundle path, still a ValueError; loader arg is the bundle), postconditions, idempotent_rerun (`model_name=` → `bundle_uri=env.xgb_bundle_uri`; `"0"*32` → absent bundle, ValueError), row-order, observed, in-sample, horizon. Also semantic: `test_scoring_modules_do_not_import_the_big_join` (replaced by the stronger `test_scoring_modules_never_import_mlflow`) | 3 |
| `test_runs.py` (13) | `test_resolve_run_id_unique_and_ambiguous`, `test_missing_tracking_db_is_refused_without_creating_it`, `test_missing_experiment_error_lists_available_names` (scorer no longer resolves runs; moved to `tests/models/test_tracking.py`), `test_mlmodel_python_version_treats_non_dict_as_absent` (moved to the exporter). Mechanical: installed_versions (same keys, same equality to the training tags), valid ×2, python-version passes P1–P5 (same accepted inputs, as manifest fields), parse | 4 |
| `test_cli.py` (5) | `test_cli_maps_every_flag_to_config_and_defaults`, `test_cli_reports_user_errors_with_exit_1` (flag set), `test_cli_end_to_end_prints_report_and_writes_partition` (report line). Mechanical: help, observed | 3 |
| `test_report.py` (2) | `test_report_contents_and_order` (model line). Mechanical: observed/missing-git (its asserted tail is unchanged) | 1 |
| `test_store.py` (9) | `test_partition_round_trip_and_layout` (schema version `b"2"`) | 1 |
| `test_ranking.py` (21), `test_selection.py` (14) | none (column lists come from `PREDICTION_COLUMNS`; `PREDICTION_KWARGS` gains a key) | 0 |
| **Total** | | **23** |

Stop rule: if implementation finds any further test that needs a semantic change, it is added to this table; if the
total exceeds 28, implementation stops and the owner is asked whether to fall back to the additive layer.

### New tests

1. `tests/bundles/test_manifest.py`: `test_canonical_json_is_key_order_independent_and_compact`;
   `test_bundle_id_excludes_its_own_key_and_changes_with_any_other_field`; `test_bundle_id_known_answer` (a fixed small
   dict → a hard-coded digest computed once and pinned); `test_canonical_json_refuses_nan_and_non_json`;
   `test_manifest_bytes_refuses_a_wrong_bundle_id`.
2. `tests/bundles/test_store.py`: `test_write_bundle_layout_permissions_and_uri` (0o444 / 0o555, no `.tmp-*` left);
   `test_rewrite_identical_is_a_noop` (`created is False`, mtimes unchanged); `test_existing_id_with_different_content_is_refused`;
   `test_existing_empty_directory_is_refused`; `test_validation_happens_before_touching_disk`;
   `test_failed_write_leaves_no_temp_dir_and_no_bundle` (monkeypatched `os.rename` raises with no target → the OSError
   propagates, no temp dir, no bundle); `test_rename_race_with_identical_bundle_is_a_noop` (the patched `os.rename` first
   writes an identical bundle at the target, then raises → `(uri, False)`); `test_rename_race_with_different_content_is_refused`; `test_gs_and_other_schemes_are_refused`;
   `test_read_refuses_symlink_directory_and_path_traversal`; `test_missing_bundle_and_non_directory`.
3. `tests/bundles/test_champion.py`: round trip; parametrized refusals; absent → FileNotFoundError;
   `test_committed_champion_json_is_valid` (reads the repo's `deploy/champion.json`; schema only).
4. `tests/models/test_bundle_export.py` (uses `scoring_env`): `test_manifest_records_the_run_faithfully` (every field vs
   the run, recomputed independently); `test_model_and_split_bytes_are_copied_verbatim`;
   `test_export_is_deterministic_across_roots`; `test_reexport_into_same_root_is_idempotent`;
   `test_exported_model_loads_without_mlflow` (subprocess `[sys.executable, "-c", ...]`: `cloudpickle.loads` of the bundle's
   model.pkl, `"mlflow" not in sys.modules`, and `predict_proba` on the fixture's features written to a temp Parquet
   equals the in-process `load_logged_model(...)` result, max abs diff 0); refusals: missing store (no db created),
   unknown run, no `model_uri`, unreadable model info (`clone_run(tags={"model_uri": "models:/m-doesnotexist"})`;
   ex-`10-bad-model-uri`), missing `split_assignment.csv`, missing `feature_columns.json` (ex-`9-no-artifact`),
   non-JSON `feature_columns.json` (ex-`9-not-json`), non-cloudpickle format (`clone_run(mlmodel_sklearn_serialization_format="skops")`),
   non-null `code` (`clone_run(mlmodel_sklearn_code="code")`, a second new `clone_run` keyword rewriting `flavors.sklearn.code`;
   `clone_run`'s "at most one mlmodel_* keyword" assertion is extended to the two new keywords);
   faithful recordings: no `python_version` tag → `null`; scalar `python_function` flavor → `mlmodel_python_version: null`
   (ex-`14-non-dict-python-function-flavor`); YAML float → `3.1`; the `_mlmodel_python_version` unit test moved verbatim.
   In the new `tests/models/test_export_cli.py`: `test_export_cli_prints_ids_and_champion_json` (stdout lines; `status: created`
   then `status: already existed`; the printed champion text parses with `read_champion` semantics),
   `test_export_cli_option_combinations` (`--run-id` alone OK; `--experiment-name` + `--model` OK; neither, both, or only one
   of the pair → exit 2), `test_export_cli_champion_path_is_relative_inside_cwd` (with `monkeypatch.chdir`), and
   `test_export_cli_user_errors_exit_1` (unknown run → `ERROR:`).
5. `tests/models/test_tracking.py`: the three `resolve_run_id` tests, assertions verbatim (the store-absent check keeps
   only its `resolve_run_id` half).
6. `tests/scoring/test_bundle_guard.py`: `test_valid_bundle_passes` (both models; fields as today + `bundle_id`, model bytes
   sha); `test_installed_versions_keys_match_manifest_and_training_tags`; `test_installed_versions_never_imports_mlflow`
   (subprocess); P1–P5 as manifest edits; `test_parse_python_major_minor` (verbatim); `test_load_bundle_model_round_trip`
   (scores equal `load_logged_model`'s, max abs diff 0); `test_load_bundle_model_escalates_inconsistent_version_warning`
   (monkeypatched `cloudpickle.loads` that emits the warning); `test_forbid_model_loading_catches_the_real_loader`.
7. `tests/scoring/test_pipeline.py`: `test_scoring_modules_never_import_mlflow` (subprocess: import every
   `readmission_risk.scoring.*` and `readmission_risk.bundles.*` module, and exec `scripts/score_discharges.py` via
   `importlib.util.spec_from_file_location` under a non-`__main__` name; assert `"mlflow"` and `"pyspark"` not in
   `sys.modules` and the Big Join absent; replaces `test_scoring_modules_do_not_import_the_big_join`); `test_scoring_source_has_no_mlflow_import`
   (static regex `^\s*(import|from)\s+mlflow\b` over `src/readmission_risk/scoring/**/*.py`,
   `src/readmission_risk/bundles/**/*.py`, `scripts/score_discharges.py`); `test_default_source_is_the_champion_and_explicit_bundle`
   (`monkeypatch.chdir(tmp)` with `tmp/deploy/champion.json` pointing at `env.lr_bundle_uri` as an absolute path: the real
   relative default is exercised; then `bundle_uri=env.xgb_bundle_uri` scores XGBoost; the loader receives the resolved
   bundle); `test_champion_relative_bundle_uri_resolves_against_cwd` (the bundle copied under `tmp/bundles/<id>`, the
   champion at `tmp/elsewhere/champion.json` holding the relative `bundles/<id>`, `monkeypatch.chdir(tmp)`, passed as
   `champion_path` → scores; the committed form is exercised); the end-to-end test additionally asserts `model_bundle_id == lr bundle id` and `model_run_id == env.lr_run_id`.
8. `tests/scoring/test_cli.py`: flag mapping with `--bundle`/`--champion`; defaults `None`. `test_cli_reports_user_errors_with_exit_1`'s
   cases become: missing gold dir (kept), in-sample window (kept), `--bundle` with `--champion` (replaces `--model` with
   `--run-id`), **no champion file under the CWD and no `--bundle`** (replaces "unknown model"; `monkeypatch.chdir(tmp)`),
   and a champion pointing at an absent bundle — each exit 1, stderr starting `ERROR:`, empty stdout, no output dir.
   In `test_pipeline.py`, `test_default_champion_failure_paths_refuse_before_load_and_write`: under `monkeypatch.chdir(tmp)`
   with no `deploy/champion.json` → `FileNotFoundError` (message contains `no champion pointer`), and with a champion
   whose `bundle_uri` is absent → `ValueError` `bundle not found`; both with the strict loader/writer, no calls, no output dir.
9. `tests/test_tooling.py` as listed under Surfaces.

### Gates and real-data checks

- `make lint` clean; `make test` green; the new total recorded in CLAUDE.md.
- `tests/conftest.py` stays loadable without mlflow: if `.venv-dataproc` exists locally, `make test-runtime` passes;
  in any case CI's `pipeline-dataproc-runtime` job on PR #4 must stay green with `186 passed`, 0 skipped.
- `git diff requirements.txt` after the cloudpickle pin shows only cloudpickle's `# via` lines.
- **Before any code change**, with the current code, exactly:
  `.venv/bin/python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --experiment-name readmission-risk-v1 --model logistic_regression --run-date 20250630 --output-dir <scratchpad>/pre-lr`
  and the same with `--model xgboost_calibrated --output-dir <scratchpad>/pre-xgb`. Each report's `run_id=` must be
  `fe9633ca…` (LR) / `abe9c605…` (XGBoost), the two v1 runs.
- After implementation: `make export` (LR) and `make export EXPORT_MODEL=xgboost_calibrated`; re-running prints
  `already existed` with the same ids. `deploy/champion.json` written from the LR export's output.
- `--bundle bundles/<xgb id> --output-dir <scratchpad>/post-xgb` → equals `pre-xgb` on every pre-existing column
  (`risk_score` max abs diff 0, identical ranks/order), plus `model_bundle_id`. Run first and into a scratch dir, so it
  never touches `data/predictions`.
- Then `make score` (champion, LR) → `data/predictions/run_date=20250630` equals `pre-lr` likewise. This is the partition
  the README assets read.
- `python scripts/make_readme_assets.py --experiment-name readmission-risk-v1 --predictions data/predictions/run_date=20250630/predictions.parquet`
  → `git status docs/img` shows no change.
- A tampered copy of the champion bundle with one byte of model.pkl flipped (manifest untouched), behind a temp champion
  pinning the real manifest sha → refused with `files` (I2 passes: the manifest bytes are unchanged); via `--bundle` →
  `files`. A second copy with one manifest field edited (re-hashed, directory renamed) behind the same champion →
  `champion sha256`. All before any unpickle.

## Out-of-scope follow-ups (noted, not built)

1. GCS writer/reader in `bundles/store.py` and FR7's three IAM/upload controls — slice 3.
2. Portable model form (FR0.3) — slice 6. Decided now for forward compatibility: slice 6 introduces manifest
   `schema_version` 2 (a non-null `portable` and its extra file roles); the scorer then accepts schema 1 **and** 2, so
   the v1 champion bundle stays scoreable and rolling back across that change stays a `git revert` (G19).
3. Training-time export wired into the retrain job — slice 6.
4. The FR15 gate table as a check output on promotion PRs — slice 6.
5. FR12 fingerprint: a new `gold_fingerprint_definition` id in the scorer's registry — slice 5.
6. Refusing dirty-tree bundles — undecided; would need a clean-tree v1 retrain.
7. Recording and checking cloudpickle's version at training time (it is now a direct pin, but runs do not tag it).
8. Guard 8 requires mlflow installed in the scoring environment (owner decision); revisit for a slimmer environment.
9. A CI check that `deploy/champion.json`'s bundle exists — possible once bundles live in GCS (slice 3/4).
10. MLflow stores moved after training (absolute artifact URIs) break export, as they break `load_logged_model` today.

11. `git clean -fdx` cannot delete read-only bundle directories; documented in `docs/scoring.md` (use the chmod recipe
    first).

## Design-check log

- **Round 1** (comprehension + critic + readiness): comprehension passed. Critic: 17 gaps; readiness: 9 questions. All
  addressed in revision 2: `current_python_version` and `MODEL_NAMES` moved to import-free modules (critic 1–2);
  `clone_run` uses in `test_pipeline.py` mapped (3); full case table and a recount, 22/140, with keys kept from today so
  guard 9/11 keys do not change (4–5, readiness 3); I5 manifest shape check, the uniform error rule and strict types
  (6, readiness 4); message template (7, readiness 5); `--champion` Typer default `None` (8); I1 directory-first
  semantics, OSError conversion, `list_bundle_entries` (9); `_read_assignment` bytes signature (10); exporter byte source
  via `mlflow.artifacts.download_artifacts`, `pickled_model == "model.pkl"`, signature recording rule (11, readiness 6);
  cloudpickle direct pin (12); deletion recipe and `tamper_bundle` never using `write_bundle` (13, readiness 1–2);
  Makefile `train` message, `EXPERIMENT` use (14); `_config` always passes a bundle, fixture locations (15); id depends
  on provenance by design (16); parent-dir fsync, existing-empty-dir refusal, JSON-native recording rule (17);
  champion default test via `chdir` (readiness 7); I2-before-I3 outcome corrected and pinned (readiness 8); pre-slice
  XGBoost/LR partitions generated before any change (readiness 9).
- **Round 2** (critic + readiness): critic 16 gaps, readiness 5 questions. Revision 3: integrity messages use only the
  URI, guard messages a verified 32-hex run id (critic 1, readiness 2); I5 types the output-bound facts and every guard
  type-checks its own value, with new cases (critic 2–3, readiness 3); the XGBoost comparison writes to a scratch dir
  before `make score` (critic 4); every README/docs reference listed, incl. README l.37/112/144 links to `runs.py`
  (critic 5, readiness 5); mixed schema-1/2 partitions policy (critic 6); slice 6 bumps manifest schema to 2 and the
  scorer accepts both (critic 7); OSError conversion for listing and the champion pointer (critic 8); I2 names the
  champion path followed (critic 9, readiness nit); I3 compares the resolved directory name (critic 10);
  `download_artifacts` verified (critic 11); `scoring/bundles.py` renamed `scoring/bundle_guard.py`, absolute imports
  (critic 12); line citations corrected (critic 13, readiness nit); Makefile second `train` message (critic 14); the
  Big Join import test counted as semantic, total 23/140 (critic 15); `git clean` caveat (critic 16); an absent bundle
  stays a ValueError so the ported tests keep their contract (readiness 1); `BundleCase` defined (readiness 4).
- **Round 3** (critic + readiness): critic 13 gaps, readiness 4 questions. Revision 4: `.gitignore` anchored as
  `/bundles/` (the unanchored form would have ignored the new package and its tests) + `tests/test_docs.py` entries
  (critic 1); `tests/conftest.py` gains no top-level import, kept loadable by the Dataproc job (critic 2); store
  ValueErrors re-raised with the check's key, URI messages fixed, `gs://`/other-scheme/symlinked-file integrity cases
  (critic 3–4, readiness 1); `write_bundle` rename-failure branch specified and tested (critic 5); I2 runs on the raw
  manifest bytes before parsing (critic 6); `mlflow.*` and fingerprint-definition sources stated (critic 7, readiness 2);
  relative champion `bundle_uri` resolution tested, export CLI prints a CWD-relative path or warns (critic 8); export CLI
  tests in a new `tests/models/test_export_cli.py` (critic 9, readiness 3); README layout block (critic 10); the literal
  re-lock command (critic 11); pyspark absence asserted (critic 12); the broader `pickle` patch is intentional and
  explained (critic 13); PRD and CLAUDE.md text specified (readiness 4).
- **Round 4** (critic + readiness): readiness **implementation ready** (6 non-blocking defaults); critic 9 minor gaps.
  The 3-revision cap was reached; the owner chose "fix all 9 gaps and run Pass B/C one more time". Revision 5: `make
  score` help text and `EXPERIMENT`'s role (1); the 3.12 recipe in `docs/scoring.md` l.16-25 rewritten for bundles (2);
  exact pre-change baseline commands (3); the tamper acceptance check names the single key per path and adds an I2 case
  (4); pipeline and CLI tests for "no champion" and "champion → absent bundle" (5); `champion_path` str case (6);
  `tamper_bundle` steps (a)-(e), no contradictory sentence left (7); signature `required` recorded and checked, the
  exporter refuses a non-null `code` flavor (8); absolute imports throughout `bundle_guard.py` (9). Readiness's six
  defaults written in: no double key prefix, one `bundle_uri` form in export output, one GCS message wording, the
  `tamper_bundle` path rule, the `score` help text, `MODEL_NAMES` import in the top block.
- **Round 5** (critic + readiness): readiness **implementation ready**; critic 6 gaps. Past the cap, the owner chose
  **override and proceed**. Fixes written in without a further goldfish round: `0o555` kept with a documented
  `# noqa: S103` (1); integrity keys carry their colon and are asserted with `startswith` (2); missing `bundle_id` /
  `files` keys handled via `.get` with cases I3-no-bundle-id and I4-no-files-key (3); the fresh-clone route documented
  (4); export step 6 names `(MlflowException, OSError)` (5); `ModelBundle.bundle_uri` is the string as given (6).
  **Gate status: Pass C closed (`implementation ready`, rounds 4 and 5); Pass B not formally closed — overridden by the
  owner at round 5.**

## Implementation record (2026-10-11)

**Kill trigger, measured: 23 of 140 = 16.4%** (threshold 28). The actual list equals the table above: 8 guard cases
(1-bogus-run-id renamed `1-bogus-bundle`; 2-soft-deleted; 3-no-model-uri → `I4-missing-model`; 11-no-artifact →
`I4-missing-split`; 9-not-json, 9-no-artifact and 10-bad-model-uri → export-time refusals in
`tests/models/test_bundle_export.py`; 14-non-dict-python-function-flavor → an exporter recording test), 3 `_BAD_CONFIGS`,
3 other pipeline tests, 4 `test_runs.py` tests (moved to `tests/models/test_tracking.py` and the exporter tests), 3 CLI
tests, 1 report test, 1 store test. No further semantic change was needed.

**Deviations from the doc, all small:**
- `BundleCase` gained `starts_with: bool` so the harness asserts integrity keys with `message.startswith(key)` and guard
  keys as substrings, as the doc specifies; the field is how one list can carry both.
- Guard 2's lifecycle message reads "was deleted or not active at export (lifecycle stage ...)" (key `deleted` kept).
- Three `# noqa: TRY004` lines (champion.json not an object, feature_columns.json not an object, manifest not an object)
  and one on `load_bundle_model`'s RuntimeError: ruff prefers TypeError for type checks, but the design's uniform rule
  makes malformed input a ValueError (the CLI's user-error type) and a broken model a RuntimeError. Each noqa carries
  that reason. `0o555` carries the planned `# noqa: S103`.
- `tests/models/helpers.py` now imports mlflow at module level (for `clone_run`); `tests/pipeline` does not import it,
  verified by `make test-runtime` (185 passed, 1 skipped locally; CI enables the skipped environment check).

**Gates and real-data checks (all passed):** `make lint` clean, also on a clean export of the tracked + new files;
`make test` 737 passed, 1 skipped (738 collected; was 622); `make test-runtime` 185 passed, 1 skipped; `requirements.txt` diff is only
cloudpickle's `-r requirements.in` line. Pre-change baselines scored with the old code for 20250630 (LR run
`fe9633ca…`, XGBoost run `abe9c605…`); after the change, `make export` produced bundles `75a1448729ef…` (LR) and
`30009d198070…` (XGBoost), identical to a scratch-root export (deterministic ids), and a re-export printed
`already existed`. `--bundle` XGBoost into a scratch dir and `make score` (champion, LR) into `data/predictions` each equal
their baseline exactly on every pre-existing column (`assert_frame_equal`, 42 rows, max abs diff 0.0), plus
`model_bundle_id`. `make_readme_assets.py` regenerated `docs/img/*` with no git diff. A flipped byte in model.pkl was
refused with `files:` through the champion and through `--bundle`; an edited, re-hashed and renamed manifest
(`git.dirty` "dirty" → "clean") behind the real champion pin was refused with `champion sha256:`. The README refusal
example re-ran with the new command and printed the same message.
