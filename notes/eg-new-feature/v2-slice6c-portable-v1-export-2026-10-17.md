# v2 slice 6c: portable (non-pickle) export of the frozen v1 models, manifest schema 2 (2026-10-17)

Status: built 2026-10-17 (revision 7 design); live steps done 2026-10-17/18 (Results 4, 5) (after design-check round 4; owner chose "apply fixes and re-run once more" at the 3-revision cap). Branch: a feature branch off `v2` (at `7b602d7`), merged into
`v2` by PR.

## Why

PRD `notes/prds/v2-mlops-keyless-scheduled-scale-up-2026-10-09.md`, **FR0.3** (carried into slice 6): "A one-time portable
export of the v1 models: per-fold XGBoost UBJ boosters, the sigmoid calibrator `a_`/`b_` values as JSON, and the
preprocessing parameters (or skops). A parity test shows a maximum absolute difference of 0 against the cloudpickle
predictions on the v1 test rows. This protects the G8 must-have against later library upgrades." It serves **FR16**
(frozen-v1 external validation: "the FR0.3 portable export (slice 6) is the fallback once those pins move"), metric **M8**
("portable-export parity max diff = 0"), and the risk row "v1 pickles unusable for G8 after a library or Python-minor
bump". The slice-2 bundle design (`notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md`, decision 4
and follow-up 2) reserved the slot: "slice 6 introduces manifest `schema_version` 2 (a non-null `portable` and its extra
file roles); the scorer then accepts schema 1 **and** 2".

The two frozen v1 models are cloudpickles. Guard 8 (exact sklearn/xgboost/mlflow/pandas/numpy versions) and guard 14
(Python major.minor) refuse them as soon as any of those pins or the Python minor moves. The portable form re-expresses
the same fitted parameters as data (JSON + XGBoost's own UBJ model format), read by a small predictor whose own code uses only numpy, scipy (`expit`) and xgboost's `Booster`, never scikit-learn
objects or pickle. (Measured: `import xgboost` 3.4.1 itself imports scikit-learn, pandas and cloudpickle as a soft
dependency, `xgboost/compat.py`; so the predictor process loads them, but no portable decision depends on their object
layout. Named risk: a future xgboost/sklearn pair whose import fails with something other than `ImportError` would stop
the predictor importing at all; the UBJ files themselves remain loadable by any later xgboost.) Because the pickle
cannot be loaded in exactly the environment the portable form exists for, each portable bundle also carries a
**self-contained reference** (the parity inputs and the pickle's outputs on them), so parity can be re-proved later
without the pickle and without the gold table.

## Owner decisions (Step 0, 2026-10-17)

| # | Question | Decision |
|---|---|---|
| D1 | Portable format | **XGBoost UBJ boosters + JSON** (preprocessing parameters, calibrator `a_`/`b_`, LR coefficients), read by **our own numpy code** (which never touches scikit-learn objects or pickle; see Why for xgboost's own soft
sklearn import). Not skops: it is installed only transitively via mlflow (which the scorer never imports), it rebuilds scikit-learn objects under whatever scikit-learn is installed (so it does not protect against an sklearn upgrade), and it cannot serialize `XGBClassifier` without widening its trusted types. |
| D2 | Scorer role | **Validate only.** The scorer accepts schema 1 and 2; for schema 2 it checks the portable files' presence, sha256/size (I4) and the manifest's `portable` block (I5). It still scores by unpickling `model.pkl`. The portable predictor is NOT a scorer path. |
| D3 | Champion | **`deploy/champion.json` stays on the schema-1 LR bundle `75a1448729ef…`.** The schema-2 bundles are durable frozen-v1 artifacts (local + GCS). Moving the champion is a follow-up. |
| D4 | FR16 wiring | **No portable mode in `ops/gate_run.py` in 6c.** The 6b gate code is untouched; a `--portable` frozen-v1 fallback is a follow-up, needed only once the v1 pins move. |

## Feasibility, measured before design (2026-10-17, scratch script, not committed)

On the real v1 test rows (bundle `split_assignment.csv` partition `test` joined to `data/gold/full_run_2b`, through
`models.data.prepare_features`; **1,539 rows**, 0 null categoricals after `prepare_features`):

- LR bundle `75a1448729ef…`: a hand-written transform (`np.log1p` → `(x - mean_) / scale_`; `(age - mean_) / scale_`;
  one-hot from `categories_` / `infrequent_categories_`) equals `ColumnTransformer.transform` exactly (max abs diff 0.0);
  `scipy.special.expit(Z @ coef_.T + intercept_)` equals `predict_proba[:, 1]` exactly. Re-measured in round 2 with the
  parameters round-tripped through JSON exactly as the design stores them (`coef` restored as a C-contiguous (1, 57)
  float64 array, `intercept` as a (1,) array, `(Z @ coef.T + intercept).reshape(-1)`, `expit`, `np.stack([1 - p, p],
  axis=1)`): `np.array_equal` with the full (n, 2) `predict_proba` output — True.
- Calibrated XGBoost bundle `30009d198070…`: 5 folds; per fold the hand-written tree transform equals the fold's
  `ColumnTransformer.transform` exactly; the fold booster saved with `save_raw("ubj")`, loaded into a fresh
  `xgboost.Booster`, and called with `inplace_predict(Z)` equals the fold's `XGBClassifier.predict_proba(Z)[:, 1]`
  exactly (both float32); `expit(-(a_ * p + b_))` with `a_`, `b_` as `numpy.float64` scalars, accumulated from zeros and
  divided by 5, equals `CalibratedClassifierCV.predict_proba[:, 1]` exactly.
- `save_raw("ubj")` is byte-deterministic (two unpickles → identical bytes) and a load→save round trip is byte-stable.
- Fitted facts: LR one-hot has an infrequent bucket only for `admission_reason_code` (37 categories, 9 infrequent); all
  category values are `str`; LR `coef_` shape (1, 57), `fit_intercept=True`; each XGB fold: `objective
  binary:logistic`, `missing` NaN, `_get_iteration_range(None) == (0, 0)`, 300 boosted rounds, booster `num_features()`
  54; `CalibratedClassifierCV(method="sigmoid", ensemble=True)`, `classes_ == [0, 1]`; `a_` is `numpy.float64`.
- The v1 test rows contain no unseen category (the unknown-category path is not exercised by real rows), hence the
  deterministic probe rows below.
- The session test fixture (`scoring_env`) covers both one-hot branches too (critic round 1, measured): LR has
  infrequent buckets in 2 of 6 columns; XGB has 5 folds of 300 rounds, `iteration_range` (0, 0), and fold-dependent
  infrequent buckets (`ethnicity` has one in 2 of 5 folds).

So FR0.3's max-abs-diff-0 is reachable for both models; the PRD requirement is not changed. **Rule carried into
implementation:** if at any point parity is not exactly 0 for either model, stop and go through `/eg-prd`; never loosen the
check.

## Scope

**In:**
1. Manifest schema 2 (`bundles/manifest.py`): new file roles for the portable files, a typed `portable` block, and
   stdlib-only helpers that validate a schema-2 files table and `portable` block (shared by the scorer and the portable
   reader). `build_manifest` (MLflow-run export) keeps writing schema 1.
2. Writer side (`bundles/store.py::_expected_bytes`): accepts a schema-2 file set. `bundles/gcs.py` needs no change (it
   uploads whatever `_expected_bytes` returned). `_printable_uri` moves from `scripts/export_bundle.py` to
   `bundles/store.py::printable_bundle_uri` so both CLIs can import it.
3. Scorer (`scoring/bundle_guard.py`): I1 accepts schema 1 and 2; I4 and I5 gain schema-2 rules. Guards 1-14 unchanged,
   and they do not branch on the schema.
4. New package `src/readmission_risk/portable/`: `predictor.py` (load + validate portable files, `predict_proba`,
   re-verify the stored reference) and `export.py` (extract parameters from a validated schema-1 bundle's unpickled
   model, build probes and the reference, check parity, write the schema-2 bundle). Import rules in Interfaces.
5. CLI `scripts/export_portable_bundle.py` + `make export-portable`.
6. Tests (CI, on the session `scoring_env` fixture) and one local real-data parity test (skipped when the v1 data or
   the exported bundles are absent).
7. scipy becomes a direct pin in `requirements.in` (it is now imported directly); `make lock-main` regenerates
   `requirements.txt` (expected: no version or hash change, only the `# via` comment).
8. The one-time run: export both v1 bundles locally (parity 0 recorded), then an **owner-confirmed live step** uploading
   both schema-2 bundles to `gs://synthea-readmission-risk-bundles/` as `bundle-writer`.
9. Docs: `docs/scoring.md` > Model bundles (schema 2, the portable form, the CLI, the partial-upload recovery), this
   note's Results, CLAUDE.md (Architecture v2 bullet + Build & test, and the V-post TODO wording), PRD FR0.3/M8 status
   line, `docs/setup.md:251` V-post step (1) ("the log lists the two bundle prefixes" becomes "the log lists the bundle
   prefixes: the two schema-1 v1 bundles and, once uploaded, the two schema-2 v1 bundles"), `README.md:153` (remove "a
   portable (non-pickle) export of the v1 models" from the smaller follow-ups and mention the portable form where the
   README describes model bundles, one sentence), and `docs/results.md` (M8's evidence location per the PRD: a short
   "Portable export parity (FR0.3)" paragraph with the two new bundle ids, rows checked, max abs diff 0.0, and a link to
   this note).

**Not refused, merely not done:** `export_portable_bundle` accepts any schema-1 bundle of the two mirrored model shapes that
passes `validate_bundle` on the gold it is given (e.g. a slice-6b `main_200k` run); 6c runs it only for the two frozen v1
bundles, and nothing in 6c depends on other exports.

**Out (explicit):** moving the champion (D3); a portable scoring path in the scorer (D2); a portable mode in
`ops/gate_run.py` (D4); exporting new (200k) runs in portable form or wiring portable export into training; a
portable form for any model shape other than the two v1 shapes; changing label, split, leakage controls, guards 1-14,
I1-I5's existing checks; touching `prereg.yaml` (Phase A, `analysis_lock`, `phase_b` are final; 6c's `src/` changes mean
the 6b gate can no longer be re-run against its lock — by design; the committed Phase B result stands).

## Surfaces touched

| File | Change |
|---|---|
| `src/readmission_risk/bundles/manifest.py` | schema-2 constants, `file_role`, `expected_file_names`, `check_schema2_files_table`, `check_portable_block`, `file_entry` via `file_role` |
| `src/readmission_risk/bundles/store.py:59-66` (`_expected_bytes`) | file-set check via `expected_file_names(manifest)` instead of `set(FILE_ROLES)`; new `printable_bundle_uri` (moved from `scripts/export_bundle.py:28-37`) |
| `scripts/export_bundle.py:28-37,67` | delete `_printable_uri`; import `printable_bundle_uri` from `readmission_risk.bundles.store` (behavior unchanged) |
| `src/readmission_risk/scoring/bundle_guard.py:131-133` (I1), `:154-185` (`_read_files`, I4), `:188-210` (`_check_shape`, I5) | accept schema 2; schema-2 file-set and `portable`-block rules via the manifest helpers |
| `src/readmission_risk/portable/__init__.py` (a module docstring only: no imports, so importing the predictor never imports `export`), `predictor.py`, `export.py` | new |
| `scripts/export_portable_bundle.py` | new Typer CLI (thin) |
| `Makefile` | target `export-portable` (+ `.PHONY`, `help` text) |
| `requirements.in`, `requirements.txt` | `scipy==1.18.1` direct pin, via `make lock-main` |
| `tests/bundles/test_manifest.py`, `tests/bundles/test_store.py`, `tests/bundles/test_gcs.py` | schema-2 helper tests, `printable_bundle_uri` tests, schema 2 over the fake `gs://` |
| `tests/test_tooling.py` | `EXPECTED_TARGETS` (l.26-30) gains `export-portable`; `MAKEFILE_VARIABLES` (l.33-36) gains `SOURCE_BUNDLE`, `PORTABLE_ARGS`; tests below |
| `tests/scoring/helpers.py` (`INTEGRITY_CASES`, l.567-600), `tests/scoring/test_bundle_guard.py`, `tests/scoring/test_pipeline.py` | schema-2 cases; the existing `I1-schema-version-2` case is replaced (see Verification) |
| `tests/conftest.py` | session fixture `portable_env` (exports the two fixture bundles to schema 2 once; all imports inside the fixture body) |
| `tests/portable/__init__.py`, `test_predictor.py`, `test_export.py`, `test_v1_parity.py`, `test_cli.py` | new |
| `docs/scoring.md`, `docs/setup.md` (V-post, l.251), `docs/results.md`, `README.md` (l.153), `CLAUDE.md`, PRD, this note | docs (scope item 9) |

Not touched: `models/bundle_export.py` (still writes schema 1; its test `tests/models/test_bundle_export.py:52` still
asserts schema 1 + null portable), `bundles/gcs.py`, `bundles/champion.py`, `deploy/champion.json`, `scoring/pipeline.py`,
`scoring/ranking.py`, `ops/`, `prereg.yaml`.

## Interfaces

### Bundle layout (schema 2)

```
<bundle_id>/
  manifest.json              # schema_version 2, portable block non-null
  model.pkl                  # byte-identical to the source schema-1 bundle's
  split_assignment.csv       # byte-identical to the source's
  portable.json              # role "portable_params"
  portable_reference.csv     # role "portable_reference": parity inputs + the pickle's outputs (see below)
  portable_fold_0.ubj ...    # role "portable_booster"; xgboost_calibrated only, one per calibrated fold, contiguous 0..k-1
```

So a schema-2 LR bundle holds 5 files (manifest included), a schema-2 XGB bundle with 5 folds holds 10.

### `bundles/manifest.py` (stays standard library only)

```python
MANIFEST_SCHEMA_VERSION = 1                 # unchanged: what build_manifest (MLflow-run export) writes
PORTABLE_MANIFEST_SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = (1, 2)
PORTABLE_PARAMS_FILENAME = "portable.json"
PORTABLE_REFERENCE_FILENAME = "portable_reference.csv"
PORTABLE_BOOSTER_RE = re.compile(r"portable_fold_(0|[1-9][0-9]*)\.ubj")   # use with fullmatch
PORTABLE_ROLE_PARAMS, PORTABLE_ROLE_REFERENCE, PORTABLE_ROLE_BOOSTER = "portable_params", "portable_reference", "portable_booster"
PORTABLE_FORMAT_VERSION = 1
PORTABLE_KEYS = frozenset({"format_version", "params_file", "reference_file", "booster_files", "parity", "writer_versions"})
PORTABLE_PARITY_KEYS = frozenset({"rows", "probe_rows", "max_abs_diff"})
PORTABLE_WRITER_VERSION_KEYS = frozenset({"numpy", "scipy", "xgboost", "python", "platform", "blas"})

def booster_filename(fold: int) -> str            # f"portable_fold_{fold}.ubj"
def file_role(name: str) -> str
    # FILE_ROLES[name] for model.pkl / split_assignment.csv; PORTABLE_ROLE_PARAMS for portable.json;
    # PORTABLE_ROLE_REFERENCE for portable_reference.csv; PORTABLE_ROLE_BOOSTER for a name fullmatching
    # PORTABLE_BOOSTER_RE; otherwise ValueError(f"unknown bundle file name {name!r}").
def file_entry(name: str, data: bytes) -> dict    # {"sha256", "size", "role": file_role(name)} (schema-1 output unchanged)
def expected_file_names(manifest: dict) -> set[str]
    # schema 1: set(FILE_ROLES), and manifest.get("portable") must be None (else ValueError: the scorer's I5 would refuse
    # it). schema 2: set(manifest) == TOP_LEVEL_KEYS (else ValueError), check_portable_block passes, and the result is
    # set(FILE_ROLES) | {PORTABLE_PARAMS_FILENAME, PORTABLE_REFERENCE_FILENAME} | set(manifest["portable"]["booster_files"]).
    # ValueError if schema_version is not an int in SUPPORTED_SCHEMA_VERSIONS. Used by the WRITER (store._expected_bytes),
    # so write_bundle cannot produce a schema-2 bundle the scorer's I4/I5 refuse. (Schema 1 keeps today's leniency on the
    # other top-level keys: tests/bundles/test_store.py builds minimal schema-1 manifests; build_manifest always writes the
    # full set.) check_portable_block raises ValueError (never KeyError/TypeError) on any malformed files table, including
    # an entry that is a dict without "role". The new order inside store._expected_bytes (bundles/store.py:59-66) makes
    # it see a verified table anyway: (1) expected_table = {name: file_entry(name, data) for name, data in files.items()}
    # (file_role raises ValueError on an unknown name); (2) manifest.get("files") == expected_table, else today's
    # ValueError; (3) set(files) == expected_file_names(manifest), else ValueError(f"a bundle with this manifest holds
    # exactly the files {sorted(expected)}; got {sorted(files)}"). In the scorer I4 runs before I5 (bundle_guard.py:332-334).
def check_schema2_files_table(table: dict) -> None
    # ValueError (message without key prefix) unless: keys ⊇ set(FILE_ROLES) | {portable.json, portable_reference.csv},
    # every other key fullmatches PORTABLE_BOOSTER_RE. (Entry shapes and roles are checked by the caller, as today.)
def check_portable_block(manifest: dict) -> None
    # ValueError (message without key prefix) unless manifest["portable"] is a dict with exactly PORTABLE_KEYS and:
    #   format_version: int (not bool) == PORTABLE_FORMAT_VERSION; params_file == PORTABLE_PARAMS_FILENAME;
    #   reference_file == PORTABLE_REFERENCE_FILENAME;
    #   booster_files: list of str, each fullmatching PORTABLE_BOOSTER_RE, fold indices exactly 0..k-1 in that order, and
    #     set(booster_files) == {names in manifest["files"] whose role is PORTABLE_ROLE_BOOSTER} (requires manifest["files"]
    #     to be a dict of dicts; anything else is a ValueError, never a KeyError/TypeError);
    #   parity: dict with exactly PORTABLE_PARITY_KEYS; rows int (not bool) > 0; probe_rows int (not bool) >= 0;
    #     max_abs_diff int or float (not bool) == 0;
    #   writer_versions: dict with exactly PORTABLE_WRITER_VERSION_KEYS, str values.
    # It does NOT tie booster_files to model_name (the scorer's I5 runs before guard 4 validates model_name; D2 keeps the
    # scorer's schema-2 check structural). read_portable_bundle adds the model-specific checks.
```

`FILE_ROLES` keeps its two entries (schema-1 meaning); `TOP_LEVEL_KEYS` and `NESTED_KEYS` are unchanged (schema 2 has the
same top-level keys; only `portable` becomes non-null).

### The manifest `portable` block (schema 2)

```json
"portable": {
  "format_version": 1,
  "params_file": "portable.json",
  "reference_file": "portable_reference.csv",
  "booster_files": ["portable_fold_0.ubj", "...", "portable_fold_4.ubj"],   // [] for logistic_regression
  "parity": {"rows": 1539, "probe_rows": <int>, "max_abs_diff": 0.0},
    "writer_versions": {"numpy": "2.4.4", "scipy": "1.18.1", "xgboost": "3.4.1", "python": "3.14.7",
                      "platform": "Darwin-arm64", "blas": "accelerate"}
}
```

`parity.rows` = number of test-partition rows checked; `probe_rows` = number of probe rows checked (both = the reference
file's row counts by kind); `max_abs_diff` = the maximum over BOTH sets and BOTH probability columns, a float that must be
`0.0`. `writer_versions` record the writer's environment (provenance only; nothing refuses on them): `numpy`, `scipy`,
`xgboost` = `importlib.metadata.version(...)`; `python` = `platform.python_version()`; `platform` =
`f"{platform.system()}-{platform.machine()}"`; `blas` = `str(np.show_config(mode="dicts")["Build Dependencies"]["blas"]["name"])`
(measured on the laptop: `accelerate`), or `"unknown"` if that lookup raises `KeyError`/`TypeError`. Bit-exact
reproduction depends on these (Known risk), so a later mismatch can be attributed from the bundle itself.

All other manifest keys of a schema-2 bundle are **copied verbatim from the source schema-1 manifest** (model_name,
gold_label_definition, gold_fingerprint(+definition), params, library_versions, python_version, mlmodel_python_version,
feature_columns, signature, mlflow, git). Only `schema_version` (→ 2), `files` (source entries for `model.pkl` and
`split_assignment.csv` copied, portable entries added), `portable` and `bundle_id` (recomputed by `compute_bundle_id`)
differ. So guards 1-14 see exactly the facts they saw for the source bundle.

### `portable.json` (canonical JSON via `manifest.canonical_json` + `b"\n"`; floats as Python `float`, which JSON writes in shortest round-trip form, so they parse back bit-identically)

One-hot spec (shared): `{"column": <str>, "categories": [<str>, ...], "infrequent_categories": [<str>, ...],
"infrequent_column": <bool>}` — `categories` are the encoder's **frequent** categories in `categories_[i]` order (i.e.
`categories_[i]` minus `infrequent_categories_[i]`); `infrequent_categories` = `list(infrequent_categories_[i])` (in its
order) or `[]` when it is None; `infrequent_column` = `infrequent_categories_[i] is not None` (so `infrequent_column` ⇔
`infrequent_categories != []`, checked at load). `infrequent_categories` is not used to predict (unseen and infrequent
values go to the same column); it is recorded so probes and audits can reach every fitted category. `categories` and
`infrequent_categories` must be disjoint, each without duplicates.

logistic_regression:
```json
{"format_version": 1, "model_name": "logistic_regression",
 "feature_columns": [16 names, FEATURE_COLUMNS order],
 "preprocess": {"kind": "linear",
   "log1p_standardized": {"columns": [9 names], "mean": [9 floats], "scale": [9 floats]},
   "standardized": {"columns": ["age_at_index_years"], "mean": [1 float], "scale": [1 float]},
   "one_hot": [6 one-hot specs, CATEGORICAL_FEATURES order]},
 "output_feature_names": [57 names, == fitted ColumnTransformer.get_feature_names_out()],
 "linear": {"coef": [57 floats], "intercept": <float>}}
```

xgboost_calibrated:
```json
{"format_version": 1, "model_name": "xgboost_calibrated",
 "feature_columns": [16 names],
 "folds": [{"preprocess": {"kind": "tree", "passthrough": {"columns": [10 names]}, "one_hot": [6 specs]},
            "output_feature_names": [names, == that fold's get_feature_names_out()],
            "booster_file": "portable_fold_<k>.ubj",
            "iteration_range": [0, 0],
            "calibrator": {"a": <float>, "b": <float>}}, ... one per calibrated classifier, in calibrated_classifiers_ order,
            booster_file of the k-th entry == portable_fold_<k>.ubj]}
```

`iteration_range` must be exactly `[0, 0]` (all trees); it is stored so a reader never has to infer it.

### `portable_reference.csv` (the self-contained parity reference)

Written and read with Python's `csv` module only, UTF-8: written through `io.StringIO(newline="")` with
`csv.writer(..., lineterminator="\n", quoting=csv.QUOTE_MINIMAL)`, read through `io.StringIO(text, newline="")` with
`csv.reader`. The writer refuses (`ValueError("portable export: ... contains a line break")`) any categorical value or
encounter_id containing `\r` or `\n`, so writer and reader agree on every row boundary. Header, in this order:
the literal line `kind,encounter_id,<FEATURE_COLUMNS joined by ",">,p0,p1` (no spaces; 20 fields).

- `kind`: `test` or `probe`. Test rows first, sorted by `encounter_id`; then probes in `build_probes` order.
- `encounter_id`: the gold `encounter_id` for test rows; for probes `probe-<i>` (0-based in probe order).
- numeric feature cells and `p0`/`p1`: `repr(float(x))` (shortest round-trip; `float(text)` restores the identical
  float64). Categorical cells: the string itself.
- `p0`, `p1`: the **pickle's** `predict_proba` columns 0 and 1 for that row (float64).
- **Batch layout is part of the reference contract.** Measured in design-check round 4 (numpy on Accelerate): the LR
  matrix product gives different bits for a row scored in a batch of 1-17 rows than for the same row inside a larger
  batch. So the reference is defined per **segment**: the `test` rows are one segment (scored by the pickle in ONE
  `predict_proba` call, in file order), the `probe` rows are the other (one call, in file order), and `verify_reference`
  re-scores each segment in exactly one call in file order and compares per segment. Never concatenate segments in one
  call, never split a segment. (Note: this means "the score of an encounter" is not batch-invariant even for the pickle;
  6c only promises equality under the same batch layout.)

The test-row part holds the v1 test patients' feature values (counts, age, LOS, demographics categories, reason code):
the same information class as the gold table and `model.pkl`'s categories, inside the private, gitignored / bucket-held
bundle; nothing of it is committed.

### `portable/predictor.py`

Module-level imports: stdlib (`csv`, `io`, `json`, `math`, `dataclasses`), `numpy`, `scipy.special.expit`, `xgboost`, and
`readmission_risk.bundles.manifest` and `readmission_risk.bundles.store` (both stdlib-only; `store` imports `bundles.gcs`
— and through it google packages — lazily and only for a `gs://` URI). **Import rule, two parts:** (a) static: the
source of `predictor.py` contains no import of `sklearn`, `pandas`, `cloudpickle`, `pickle`, `joblib`, `mlflow`,
`pyspark` or `google` (regex test on the file, like `tests/scoring/test_pipeline.py::test_scoring_source_has_no_mlflow_import`)
and no call of `pickle.loads`/`cloudpickle.loads`; (b) subprocess: after `import readmission_risk.portable.predictor`,
`mlflow`, `pyspark` and `google.cloud` are not in `sys.modules`. (sklearn, pandas and cloudpickle DO appear in
`sys.modules`, because `import xgboost` imports them; see Why. That is xgboost's soft dependency, not ours.) The
predictor only calls `frame[col].to_numpy(...)` on the frame it is given, and the reference reader builds plain numpy
arrays.

```python
@dataclass(frozen=True, eq=False)
class OneHotSpec:
    column: str
    categories: np.ndarray            # object array of str (frequent categories, in order)
    infrequent_categories: tuple[str, ...]
    infrequent_column: bool
    def output_names(self) -> list[str]   # [f"{column}_{c}" ...] + ([f"{column}_infrequent_sklearn"] if infrequent_column)
    def transform(self, values: np.ndarray) -> np.ndarray   # float64 (n, len(categories) + int(infrequent_column))

@dataclass(frozen=True, eq=False)
class LinearPreprocess:
    log1p_columns: tuple[str, ...]; log1p_mean: np.ndarray; log1p_scale: np.ndarray
    scaled_columns: tuple[str, ...]; scaled_mean: np.ndarray; scaled_scale: np.ndarray
    one_hot: tuple[OneHotSpec, ...]
    def transform(self, X) -> np.ndarray

@dataclass(frozen=True, eq=False)
class TreePreprocess:
    passthrough_columns: tuple[str, ...]
    one_hot: tuple[OneHotSpec, ...]
    def transform(self, X) -> np.ndarray

@dataclass(frozen=True, eq=False)
class PortableFold:
    preprocess: TreePreprocess
    booster: xgboost.Booster
    iteration_range: tuple[int, int]  # always (0, 0)
    a: np.float64                     # calibrator a_, MUST be np.float64 (see below)
    b: np.float64

@dataclass(frozen=True, eq=False)
class PortableLogisticRegression:
    model_name: str                   # "logistic_regression"
    feature_columns: tuple[str, ...]
    preprocess: LinearPreprocess
    coef: np.ndarray                  # float64, C-contiguous, shape (1, n_out): np.asarray(json_list, dtype=np.float64).reshape(1, -1)
    intercept: np.ndarray             # float64, shape (1,): np.asarray([json_float], dtype=np.float64)
    def predict_proba(self, X) -> np.ndarray   # (len(X), 2) float64

@dataclass(frozen=True, eq=False)
class PortableCalibratedXGBoost:
    model_name: str                   # "xgboost_calibrated"
    feature_columns: tuple[str, ...]
    folds: tuple[PortableFold, ...]
    def predict_proba(self, X) -> np.ndarray

PortableModel = PortableLogisticRegression | PortableCalibratedXGBoost

@dataclass(frozen=True, eq=False)
class Reference:
    kinds: np.ndarray                 # object array, "test" | "probe"
    encounter_ids: np.ndarray         # object array
    features: dict[str, np.ndarray]   # numeric columns float64, categorical columns object arrays of str
    proba: np.ndarray                 # (n, 2) float64: the pickle's outputs
    def segment(self, kind: str) -> "Reference": ...   # the rows of one kind, in file order (a new Reference)
    def __len__(self) -> int: ...
    def __getitem__(self, column: str) -> "_Column": ...   # minimal frame protocol, so predict_proba needs no pandas
    @property
    def columns(self) -> tuple[str, ...]: ...      # tuple(self.features): no duplicates by construction; NOT a field

@dataclass(frozen=True, eq=False)
class _Column:
    values: np.ndarray
    def to_numpy(self, dtype=None) -> np.ndarray   # np.asarray(self.values, dtype=dtype) (a copy only if dtype differs)

def load_portable(files: Mapping[str, bytes]) -> PortableModel
    # files: {name: bytes} holding portable.json and every booster file it names; any other entry is ignored here
    # (read_portable_bundle enforces the exact set). Parses portable.json and validates it strictly: exact key sets at
    # every level; types (str / bool / int not bool / float or int → float64, never NaN/inf, never bool); list lengths
    # (mean/scale == columns; coef == len(output_feature_names)); model_name in ("logistic_regression",
    # "xgboost_calibrated"); format_version == 1; feature_columns a non-empty list of unique str (its length is not hard-coded: the predictor cannot
    # import models.data; extract_portable checks it equals FEATURE_COLUMNS and read_portable_bundle checks it equals the
    # manifest's feature_columns); every column the preprocess
    # names is in feature_columns, and each feature column is used exactly once per preprocess; output_feature_names ==
    # the names the preprocess implies (numeric column names in order, then each spec's output_names()); one-hot specs
    # as described above; folds >= 1; folds[k].booster_file == booster_filename(k); iteration_range == [0, 0].
    # Each booster: xgboost.Booster(); load_model(bytearray(data)) (any xgboost.core.XGBoostError → ValueError);
    # booster.num_features() == len(that fold's output_feature_names) and booster.feature_names is None.
    # Every defect: ValueError("portable: ...").

def parse_reference(data: bytes, feature_columns, categorical_columns) -> Reference
    # Strict: UTF-8; header exactly as specified; >= 1 "test" row; kinds only test/probe, test rows first; test-row
    # encounter_ids unique and strictly ascending; probe ids exactly probe-0, probe-1, ... in order; numeric cells parse
    # with float() and are finite (deliberately: the gold table has no null numerics, `models/data.py`
    # `_REQUIRED_NONNULL_COLUMNS`, so the reference never covers XGBoost's NaN-as-missing path; that path is covered by
    # the CI test `test_input_contract_refusals` against the pickle instead); p0/p1 in [0, 1]; categorical cells any str.
    # ValueError("portable: reference ...") otherwise. categorical_columns = the columns covered by one-hot specs.

def read_portable_bundle(bundle_uri: str) -> tuple[PortableModel, Reference]
    # Reads through bundles.store only (list_bundle_entries, read_bundle_file), so local paths and gs:// both work.
    # Checks, in order, each failure ValueError("portable: ..."). Every bundles.store call (check_bundle_uri,
    # list_bundle_entries, read_bundle_file, bundle_uri_leaf) is wrapped like the scorer's _store_call
    # (bundle_guard.py:93-102): FileNotFoundError, ValueError and OSError (bundles.gcs maps GCS read failures to OSError)
    # become ValueError("portable: <message>"); every manifest-helper ValueError (file_role, check_schema2_files_table,
    # check_portable_block, compute_bundle_id) is re-raised with the "portable: " prefix too, never escaping bare.
    #  1. manifest.json present; UTF-8 JSON object; schema_version == 2 (type int).
    #  2. bundle_id a 64-hex str == compute_bundle_id(manifest); raw bytes == manifest_bytes(manifest) (canonical);
    #     bundle_uri_leaf(bundle_uri) == bundle_id.
    #  3. manifest["files"] is a dict; check_schema2_files_table(table) FIRST (so every name is known); then each entry is
    #     shaped {"sha256": 64-hex str, "size": int, "role": str} with role == file_role(name); then
    #     check_portable_block(manifest); then the directory listing == {manifest.json} | table keys.
    #  4. The portable files and split_assignment.csv are read and verified (sha256 + size against the table):
    #     portable.json, portable_reference.csv, each booster, split_assignment.csv (plain text, loadable in any
    #     environment). model.pkl is NOT read (it need not be loadable).
    #  5. model-specific: manifest model_name == portable.json model_name; logistic_regression ⇔ booster_files == [];
    #     xgboost_calibrated ⇔ booster_files == [booster_filename(k) for k in range(len(folds))] (non-empty);
    #     manifest feature_columns == portable.json feature_columns.
    #  6. load_portable({...the verified portable files}), parse_reference(...); reference row counts by kind ==
    #     manifest portable.parity rows / probe_rows; split_assignment.csv parsed with the csv module (header exactly
    #     encounter_id,partition; partition values train/test; ids unique) and the reference's test encounter_ids ==
    #     sorted(ids whose partition is "test") exactly (same order).
    # It does NOT run the scorer's guards (no gold, no library-version or Python check: surviving those changes is the
    # point).

def verify_reference(model: PortableModel, reference: Reference) -> float
    # For each segment in ("test", "probe") with >= 1 row (test always has >= 1): got =
    # model.predict_proba(reference.segment(kind)) in ONE call; requires got.shape == the segment's proba shape and
    # np.array_equal(got, segment proba). Returns 0.0. Otherwise ValueError("portable: reference mismatch: max abs diff
    # <repr> over <n> rows (<n_test> test, <n_probe> probe); this environment does not reproduce the v1 model").
```

**Known risk: bit-exactness across environments.** `verify_reference` demands exact equality, but every CI test and the
export itself run in the writer's environment. A later numpy/BLAS (e.g. Linux OpenBLAS vs macOS Accelerate for the LR
matrix product), scipy `expit` or xgboost float32 accumulation change could move a result by one ulp, and then the FR16
fallback refuses exactly when it is needed. 6c does three things about it: (1) the risk is named here and in
`docs/scoring.md`; (2) the policy is fixed now: a `verify_reference` mismatch means "this environment does not reproduce
the frozen v1 model"; the FR16 fallback (follow-up 1) must then report FR16 as not reproducible in that environment, and
any tolerance is a requirement change through `/eg-prd` (FR0.3's "max abs diff 0"), never a silent loosening; (3)
cross-environment measurements are recorded in Results (acceptance 5): `read_portable_bundle` + `verify_reference` on
both exported v1 bundles (i) in a scratch venv outside the repo (scratchpad, Python 3.14), the repo installed with
`pip install --no-deps -e .`, run twice: (i-a) with the newest releases of numpy, scipy, xgboost, scikit-learn and pandas;
(i-b) with the newest numpy, scipy and xgboost only (no scikit-learn, no pandas: xgboost's soft sklearn import must then
fall back, and `read_portable_bundle` + `verify_reference` need neither); each install unhashed from PyPI, so an
owner-confirmed step; the repo's lockfiles are not touched; the exact package versions are recorded; and (ii) on Linux in a
throwaway `python:3.14` Docker container with the repo's hash-locked `requirements.txt` + Linux extras, if Docker is
available on the laptop (else recorded as "not measured"). Each outcome (match, or the max abs diff) is recorded as found;
a mismatch does not block 6c (the export environment's parity is the FR0.3 deliverable) but is reported to the owner and
carried into follow-up 1. (CI's Linux runs only re-export the fixture bundles, writer and reader in one environment, so
they do not measure this.) **Caveat on scope:** a passing `verify_reference` proves bit-equality on the reference rows
(1,539 v1 test rows + probes) only, not on other inputs such as FR16's 157,827 `main_200k` modeling rows: some library
paths depend on value range or matrix size (extreme-input branches of `expit`/`log1p`, BLAS kernel or threading choice),
and results depend on the batch layout (measured: batches of 1-17 rows differ from the same rows in larger batches, the
size range of the scorer's run-date windows too).
Follow-up 1 must report FR16-via-portable results as "reference-verified", not as proven identical to the pickle on the
FR16 rows.

All predictor dataclasses are `frozen=True, eq=False`: they hold numpy arrays, dicts or an `xgboost.Booster`, so the
generated `__eq__`/`__hash__` would raise (the repo's precedent: `ModelBundle`, `scoring/bundle_guard.py:76`). Tests compare
their fields, never the objects.

Input contract of `predict_proba(X)` (any object with a `columns` attribute, `X[col].to_numpy(dtype=...)` and `len(X)`; a
pandas DataFrame or a `Reference`; an object without `columns` → `ValueError("portable: input has no columns attribute")`):
- checked in this order, each a `ValueError("portable: ...")`: `len(X) == 0` → `input has no rows` (scikit-learn refuses
  zero samples too); duplicate names in `X.columns` → `input has duplicate column(s) [...]`; every
  name in `feature_columns` must be present (`input is missing column(s) [...]`); extra columns are ignored; column order
  is irrelevant; any exception raised by `to_numpy(dtype=...)` (e.g. a pandas nullable `Float64` column holding `pd.NA`)
  is re-raised as `cannot convert column <col>: <exc>`.
- numeric columns: `X[col].to_numpy(dtype=np.float64)`. LR: every numeric value must be finite (else `ValueError("portable:
    non-finite numeric input in <col>")`; scikit-learn's `LogisticRegression` refuses non-finite input too), and the
  transformed matrix must be finite too (a count <= -1 makes `np.log1p` return -inf/NaN, which scikit-learn's
  `decision_function` refuses): `ValueError("portable: non-finite value after the linear transform (a count <= -1?)")`. XGB: NaN is
  allowed (XGBoost's missing value, `missing=np.nan`), ±inf is refused with the same message.
- categorical columns: `X[col].to_numpy(dtype=object)`; every value must be `type(v) is str` (else `ValueError("portable:
  non-string value in categorical column <col>")`); `prepare_features` guarantees this (nulls → "MISSING").

Prediction (exactly mirroring scikit-learn 1.9.1's operation order, as measured):

- **OneHotSpec.transform(values):** `out = np.zeros((n, len(categories) + int(infrequent_column)))` (float64); for `j, c in
  enumerate(categories)`: `out[values == c, j] = 1.0`; if `infrequent_column`: `out[~np.isin(values, categories), -1] =
  1.0` (values and categories are both object arrays of str, so comparisons are str-only). An infrequent OR unseen value
  goes to the infrequent column when the encoder has one, and becomes an all-zero block when it has none
  (`handle_unknown="infrequent_if_exist"` semantics, `models/features.py:21-25`).
- **LinearPreprocess.transform:** `a = np.log1p(counts)`; `a = (a - mean) / scale`; `b = (age - mean) / scale`; `Z =
  np.hstack([a, b, *one_hot blocks])` (mean/scale float64 arrays).
- **TreePreprocess.transform:** `Z = np.hstack([passthrough numeric (float64), *one_hot blocks])`.
- **LR** (scikit-learn's own form, `decision_function` + `_predict_proba_lr`; measured bit-identical with JSON-restored
  parameters): `d = (Z @ coef.T + intercept).reshape(-1)`; `p = expit(d)`; return `np.stack([1 - p, p], axis=1)`.
- **calibrated XGBoost:** `mean = np.zeros((n, 2))`; per fold: `p = fold.booster.inplace_predict(Z_fold,
  iteration_range=fold.iteration_range, missing=np.nan)` (float32, 1-D); `proba = np.zeros((n, 2))`; `proba[:, 1] =
  expit(-(fold.a * p + fold.b))`; `proba[:, 0] = 1.0 - proba[:, 1]`; `proba[(1.0 < proba) & (proba <= 1.0 + 1e-5)] =
  1.0`; `mean += proba`; after the loop `mean /= len(folds)`; return `mean`. **`a`, `b` must be `np.float64`, not Python
  `float`**: under NumPy 2's promotion rules a Python float times a float32 array stays float32, which breaks parity.

### `portable/export.py`

Imports: `numpy`, `pandas`, sklearn classes (for `isinstance` checks), `xgboost`, `from readmission_risk.models import
data as gold_data` (called as `gold_data.load_gold_table_with_metadata(...)` and `gold_data.prepare_features(...)`, so tests
patch `readmission_risk.models.data.load_gold_table_with_metadata`),
`readmission_risk.scoring.bundle_guard` (`validate_bundle`, `load_bundle_model`), `readmission_risk.bundles.*`, and the
predictor **as a module**: `from . import predictor` and calls `predictor.load_portable(...)`,
`predictor.parse_reference(...)`, `predictor.verify_reference(...)` (so tests patch
`readmission_risk.portable.predictor.load_portable`).

```python
PROBE_BASE_ROWS = 5
PROBE_UNSEEN = "__portable_probe_unseen__"

@dataclass(frozen=True, eq=False)
class PortableExportResult:
    bundle_id: str
    bundle_uri: str
    manifest_sha256: str
    created: bool            # local: False when an identical bundle existed; gs://: always True (the writer cannot read)
    source_bundle_id: str
    model_name: str
    parity_rows: int
    probe_rows: int
    max_abs_diff: float      # always 0.0 on success

def extract_portable(model) -> tuple[dict, dict[str, bytes]]
def build_probes(X_test: pd.DataFrame, document: dict) -> pd.DataFrame
def export_portable_bundle(source_bundle_uri: str, gold_dir: Path, bundles_root: Path | str) -> PortableExportResult
```

**`extract_portable(model)`** — pure. From an unpickled v1 model returns (portable.json document, {booster filename: UBJ
bytes}). Refuses with `ValueError("portable export: unsupported model shape: <what>")` anything it does not mirror exactly:
- **LR:** an sklearn `Pipeline` whose step names are exactly `["preprocess", "model"]`; `model` is a `LogisticRegression`
  with `classes_` equal to `[0, 1]`, `coef_.ndim == 2`, `coef_.shape == (1, n)`, `intercept_.shape == (1,)`; `preprocess` a
  valid ColumnTransformer (below) whose `transformers_` are exactly `counts`, `age`, `cats` in that order (plus at most
  one trailing `("remainder", "drop", ...)`); `counts` = a `Pipeline` with steps exactly `[("log1p", FunctionTransformer),
  ("scale", StandardScaler)]` where the FunctionTransformer has `func is np.log1p`, `kw_args is None`, `validate is
  False`; every StandardScaler (counts' and `age`) has `with_mean` and `with_std` True and non-None `mean_`/`scale_`;
  `cats` a valid encoder (below).
- **XGB:** a `CalibratedClassifierCV` with `method == "sigmoid"`, `ensemble is True`, `classes_` equal to `[0, 1]`; every
  entry of `calibrated_classifiers_` has `method == "sigmoid"`, `classes` equal to `[0, 1]` (read by
  `_CalibratedClassifier.predict_proba` through `LabelEncoder().fit(self.classes)`), and exactly one calibrator, whose
  class name is `_SigmoidCalibration` and whose `a_`/`b_` are finite floats; its `estimator` is a `Pipeline` with steps
  exactly `["preprocess", "model"]` and `not hasattr(estimator, "decision_function")` (scikit-learn's
  `_get_response_values` prefers `decision_function` over `predict_proba`; the mirror assumes the latter is used); `preprocess` a valid ColumnTransformer whose UNFITTED `transformers` list is exactly `[("num", "passthrough",
  cols), ("cats", <encoder>, cols)]` and whose fitted `transformers_` are `num` then `cats` (+ optional trailing
  `("remainder", "drop", ...)`), where the fitted `num` entry is what scikit-learn 1.9.1 substitutes for "passthrough"
  (measured on the v1 model): a `FunctionTransformer` with `func is None`, `inverse_func is None`, `kw_args is None`,
  `validate is False`, `feature_names_out == "one-to-one"`; `model` an `XGBClassifier` with `objective ==
  "binary:logistic"`, `booster in (None, "gbtree")`, `classes_` equal to `[0, 1]`, `np.isnan(missing)`,
  `_get_iteration_range(None) == (0, 0)` (refuses early-stopped models: xgboost 3.4.1 returns `(0, best_iteration + 1)`
  for them; `_get_iteration_range` is private but it is what `XGBClassifier.predict_proba` itself uses; acceptable for a
  one-time export under the pinned 3.4.1), and `get_booster().feature_names is None`.
- **valid ColumnTransformer:** `remainder == "drop"`, `sparse_output_ is False`, and its transform output is not
  configured as pandas: `getattr(ct, "_sklearn_output_config", {}).get("transform", "default") == "default"` for the
  ColumnTransformer and for the enclosing Pipeline (a private attribute set by `set_output`, accepted for the same reason
  as `_get_iteration_range`).
- **valid encoder:** a `OneHotEncoder` with `handle_unknown == "infrequent_if_exist"`, `drop is None`, `sparse_output is
  False`, `dtype is np.float64`, every `categories_[i]` an object array of `str`.
- Also refuses if (a) the column lists differ from `models.data`'s `COUNT_FEATURES` / `NUMERIC_OTHER_FEATURES` /
  `CATEGORICAL_FEATURES` (LR: counts = COUNT_FEATURES, age = NUMERIC_OTHER_FEATURES; XGB: num = COUNT_FEATURES +
  NUMERIC_OTHER_FEATURES; cats = CATEGORICAL_FEATURES), or (b) the names the document implies differ from the fitted
  `get_feature_names_out()` (as a list of str).
- Booster bytes: `bytes(fitted_xgbclassifier.get_booster().save_raw("ubj"))`, keyed `booster_filename(k)` for fold k.

**`build_probes(X_test, document)`** — deterministic, from the document being exported (so probes cannot drift from what
is tested). `base` = the first `PROBE_BASE_ROWS` rows of `X_test` in index order (`X_test` is indexed by `encounter_id`,
sorted ascending; all of it if shorter). For each categorical column `c` in `CATEGORICAL_FEATURES` order: `values(c) =
[PROBE_UNSEEN] + sorted(U ∪ {"MISSING"})` where `U` = the union, over every one-hot spec of `c` in the document (one for
LR; one per fold for XGB), of `categories ∪ infrequent_categories` (so "MISSING" appears once whether or not it is a fitted
category). If `PROBE_UNSEEN` is in any `U`, `build_probes` raises `ValueError("portable export: probe value
'__portable_probe_unseen__' is a fitted category of <c>")` (the unseen path would otherwise go untested). For each `v` in `values(c)`: a copy of `base` with
column `c` set to `v`. Concatenated in that order; index reset to a RangeIndex; dtypes as `prepare_features` produces
(float64 numerics, object categoricals). `probe_rows = len(result) = len(base) * Σ_c len(values(c))`. This covers every
fitted category (frequent ones absent from the test rows, values frequent in one fold and infrequent or unseen in another),
the unseen path, and `MISSING`.

**`export_portable_bundle`** steps, in order (each failure a `ValueError` starting `"portable export: "`, except errors raised
by `validate_bundle`, which keep their own keys; nothing is written before step 9):

1. `source_bundle_uri` must be a local path (`gs://` → `ValueError("portable export: the source bundle must be a local
   path (download it first: docs/scoring.md > Model bundles)")`).
2. `gold, metadata = gold_data.load_gold_table_with_metadata(gold_dir)`; its own errors (FileNotFoundError / ValueError)
   propagate unprefixed, like `validate_bundle`'s.
3. `bundle = validate_bundle(source_bundle_uri, gold=gold, metadata=metadata)` — the FULL scorer validation (I1-I5 and
   guards 1-14): the source must be scoreable in this environment (v1 pins, Python 3.14) on this gold table. Then require
   the source manifest's `schema_version == 1` (schema 2: `"portable export: bundle <id12> is already schema 2"`).
4. Re-read the source `manifest.json` and `split_assignment.csv` via `bundles.store.read_bundle_file` (closing the window
   between validation and copy): `source_manifest = json.loads(raw)` must satisfy `compute_bundle_id(source_manifest) ==
   bundle.bundle_id` and `raw == manifest_bytes(source_manifest)`; the split bytes' sha256 and size must equal
   `source_manifest["files"]["split_assignment.csv"]`; `sha256_hex(bundle.model_bytes)` must equal
   `source_manifest["files"]["model.pkl"]["sha256"]`. The model bytes copied are `bundle.model_bytes`.
5. `model = load_bundle_model(bundle)`.
6. `test_ids = sorted(bundle.partition_of.index[bundle.partition_of == "test"])` (all test-partition rows, not a run-date
   window; ≥ 1 by guard 11); `X_test = prepare_features(gold.set_index("encounter_id", drop=False).loc[test_ids])`, whose
   index is the encounter_id (`gold` itself is not mutated: `set_index` returns a new frame).
7. `document, boosters = extract_portable(model)`; `probes = build_probes(X_test, document)`; the two pickle calls below are
   exactly the two reference segments (batch-layout contract): `ref_test =
   model.predict_proba(X_test)`, `ref_probe = model.predict_proba(probes)` (no warning filter: with `drop=None`, scikit-learn 1.9.1's
   `OneHotEncoder.transform` does not warn on unseen categories, `_encoders.py:1046`; measured in design-check round 6;
   no test asserts a warning) (as float64 arrays; each must be finite with
   shape (n, 2)); write `portable_reference.csv` bytes from `X_test`/`probes` and `ref_test`/`ref_probe` (format above);
   `portable_bytes = canonical_json(document) + b"\n"` (a NaN/inf parameter makes `canonical_json` raise; re-raised as
   `portable export: ...`).
8. Parity, on the bytes that will be written (round-tripped, not in-memory objects): `portable_model =
   predictor.load_portable({portable.json: portable_bytes, **boosters})`; `reference =
   predictor.parse_reference(reference_bytes, ...)`; first require `reference.proba` to equal `np.vstack([ref_test,
   ref_probe])` exactly and the parsed features to equal the frames' values (the CSV round trip is lossless); then
   `predictor.verify_reference(portable_model, reference)`. On any mismatch: `ValueError("portable export: parity failed
   for bundle <source id12>: <the verify_reference message>; a requirement change, see
   notes/eg-new-feature/v2-slice6c-portable-v1-export-2026-10-17.md")`, nothing written.
9. Build the schema-2 manifest (copy rule above; `files` = source entries + `file_entry(...)` for `portable.json`,
   `portable_reference.csv` and each booster; `portable` block with `booster_files` in fold order, `parity = {"rows":
   len(X_test), "probe_rows": len(probes), "max_abs_diff": 0.0}`, `writer_versions`; `bundle_id = compute_bundle_id(...)`),
   then `write_bundle(files, manifest, bundles_root)` (local: idempotent no-op for identical content; `gs://`:
   create-only, always `created=True`).
10. Return the result (`manifest_sha256 = sha256_hex(manifest_bytes(manifest))`).

### `scoring/bundle_guard.py` (schema-2 acceptance; guards 1-14 untouched)

- **I1** (`_read_manifest`, l.131-133): `type(schema) is not int or schema not in SUPPORTED_SCHEMA_VERSIONS` → `manifest:
  bundle at {uri} has unsupported schema_version {schema!r} (this scorer reads 1 and 2)`.
- **I4** (`_read_files`): the files table must be a dict; schema 1: keys exactly `set(FILE_ROLES)` (unchanged message);
  schema 2: `check_schema2_files_table(table)`, its ValueError re-raised as `files: the files table of bundle at {uri}
  <message>`. Each entry's shape check stays, with `entry["role"] != file_role(name)` replacing `!= FILE_ROLES[name]`. The
  directory listing must equal `{manifest.json} | table keys` (generalised from today's fixed set; same message). Every
  file's size and sha256 are verified (as today; all bytes read).
- Order is unchanged and relied on: `validate_bundle` runs `_read_manifest` (I1-I3), then `_read_files` (I4), then
  `_check_shape` (I5) (`bundle_guard.py:332-334`), so `check_portable_block` sees an I4-validated files table.
- **I5** (`_check_shape`): schema 1: `portable` must be null (unchanged). Schema 2: `check_portable_block(manifest)`, its
  ValueError re-raised as `manifest shape: portable of bundle at {uri} <message>`; a null `portable` in schema 2 is one
  such failure. The scorer does NOT parse `portable.json`, the reference, or load boosters (D2), and does not tie
  `booster_files` to `model_name` (structural only; `read_portable_bundle` does that).
- `ModelBundle` gains nothing; the predictions frame is unchanged (a schema-2 bundle's `model_bundle_id` is its own id).

### CLI: `scripts/export_portable_bundle.py`

Options: `--source-bundle <local path>` (required; `gs://` refused, step 1), `--gold-dir` (default
`data/gold/full_run_2b`), `--bundles-dir` (default `bundles`; may be `gs://…`), `--impersonate-service-account` (as
`export_bundle.py`; calls `gcp.configure`, which only affects GCS access, i.e. the upload, since the source and gold are
local). Output on success, exactly these lines in this order:

```
bundle_id: <id>
bundle_uri: <printable_bundle_uri(...)>
status: created | already existed            # gs://: always "created" (the writer cannot read to compare)
source_bundle_id: <id>
parity: max abs diff 0.0 on <rows> test rows and <probe_rows> probe rows
not promoted: deploy/champion.json stays on the schema-1 bundle (notes/eg-new-feature/v2-slice6c-portable-v1-export-2026-10-17.md)
```

No champion.json text is printed (D3). When `bundle_uri` is absolute and outside the current directory, stderr gets `WARNING: bundle_uri is an absolute,
machine-specific path` (the `export_bundle.py` text minus its "do not commit it" clause, which is about the champion
pointer this CLI never prints). Exit 0 success; 1
with `ERROR: <message>` on `FileNotFoundError`/`ValueError` (parity failure included); Typer usage errors 2. A GCS error
other than "already exists" during the upload (403, network) is NOT mapped: it ends the process with a traceback and exit
1, exactly as `scripts/export_bundle.py` behaves today; the partial-upload runbook applies.

`printable_bundle_uri(bundle_uri) -> tuple[str, bool]` in `bundles/store.py` is `export_bundle.py`'s `_printable_uri`
moved verbatim (renamed); `export_bundle.py` imports it. (`scripts/` is not a package and the CLI tests load scripts with
`spec_from_file_location`, so shared helpers live under `src/`.)

`make export-portable SOURCE_BUNDLE=bundles/<id> [GOLD_DIR=...] [PORTABLE_ARGS=...]` → `$(BIN)/python
scripts/export_portable_bundle.py --source-bundle $(SOURCE_BUNDLE) --gold-dir $(GOLD_DIR) $(PORTABLE_ARGS)` (`GOLD_DIR`
defaults to `data/gold/full_run_2b`, `Makefile:13`, like `train` and `score`), failing with an ERROR line when `SOURCE_BUNDLE` is empty (same
pattern as `train`'s `TRAIN_EXPERIMENT` check, `Makefile:75`).

### Live step (owner-confirmed): upload to GCS

`CLOUDSDK_CORE_PROJECT=synthea-readmission-risk`; the same CLI with `--source-bundle bundles/<schema-1 id> --bundles-dir
gs://synthea-readmission-risk-bundles --impersonate-service-account <bundle-writer SA>`, per `docs/setup.md` >
Infrastructure (Terraform) bundle upload. It re-runs the export (deterministic: the same bundle id as the local export;
compared in Results). `write_files` uploads with `retry=None`, create-only, `manifest.json` last.

**Partial-upload recovery** (documented in `docs/scoring.md` > Model bundles): a failure mid-upload leaves a prefix
without `manifest.json` that the writer can never complete (re-runs report "already exists"). Recovery: the owner, as
their own (privileged) identity, lists the prefix, deletes it (`gcloud storage rm --recursive
--all-versions gs://synthea-readmission-risk-bundles/<id>/`, the recipe in `docs/setup.md` > Scoring into BigQuery),
and re-runs the upload. **Acceptance:** as a reader (owner ADC), the listing of each prefix shows exactly 5 (LR) / 10
(XGB) objects, and `read_portable_bundle("gs://…/<id>")` plus `verify_reference` pass on both, and
`validate_bundle("gs://…/<id>", gold=…, metadata=…)` passes on both.

## Flow

load gold + metadata → validate the local schema-1 source bundle with the scorer's full guard set → unpickle → select all
its test rows → `prepare_features` → extract parameters + UBJ boosters → build probes from the document → pickle outputs
on test rows and probes → serialize `portable.json` and `portable_reference.csv` → reload both through the predictor →
`verify_reference` (exact equality) → write the schema-2 bundle (local, idempotent; or GCS, create-only) → print. Later,
in an environment that reproduces the writer's arithmetic (measured: macOS, not Linux; Results 5): `read_portable_bundle` → `verify_reference` re-proves parity without the pickle or the gold. The
scorer, given a schema-2 bundle (by `--bundle`), validates it and scores from `model.pkl` exactly as for its source.

## Data handling

- No training, no refit, no split change: the models are the frozen v1 runs; the rows are the bundle's own recorded test
  partition (guards 11/12 already proved it consistent with the gold table and `test_start_date`). Predictions on test
  rows are used only for an equality check, never for any choice (no leakage concern: nothing is fitted or selected).
- Probes are synthetic, derived deterministically from test rows and the document.
- Determinism: no randomness; UBJ bytes deterministic (measured); `portable.json` canonical JSON; the reference CSV is a
  deterministic function of its inputs. So the schema-2 `bundle_id` is a pure function of: the source bundle, the gold
  test rows' feature values, the probe rule (`build_probes`, `PROBE_BASE_ROWS`, `PROBE_UNSEEN`), the reference format,
  `writer_versions`, and the pickle's outputs. A re-export in the same environment with the same code is an idempotent
  no-op; a change to the probe rule, the reference format or any writer version produces a different id (expected and
  harmless: nothing points at these ids, D3).
- `pandas`: `gold` is never mutated (`set_index` returns a new frame; probes are built with `.copy()` + column
  assignment on the copy, no chained indexing).
- Nothing patient-level is committed. The reference file holds the v1 test rows' feature values (same information class
  as the gold table) inside the private bundles (gitignored locally; private bucket in GCS).
- Redaction: no billing account, budget id or owner email in anything committed (the bundle-writer SA is referred to as in
  `docs/setup.md`).

## Failure modes

| Situation | Behavior |
|---|---|
| Source bundle is `gs://` | `portable export: the source bundle must be a local path ...` |
| Source bundle fails any I1-I5 / guard 1-14 (wrong env, wrong gold) | `validate_bundle`'s ValueError, unchanged key; nothing written |
| Source already schema 2 | `portable export: ... already schema 2` |
| Model shape not one of the two mirrored shapes (other encoder settings, isotonic, `ensemble=False`, early stopping, dart/gblinear, pandas output, sparse output, kw_args on log1p, extra pipeline step, renamed transformer) | `portable export: unsupported model shape: <what>` |
| Derived output names ≠ `get_feature_names_out()` | `portable export: unsupported model shape: output feature names differ` |
| Non-finite parameter (JSON cannot hold NaN/inf) | `portable export: ...` (from `canonical_json`) |
| Pickle output non-finite or wrong shape | `portable export: ...` |
| Parity ≠ 0 (any row, either column, test or probes) or lossy reference round trip | refused, nothing written; per rule, go to `/eg-prd` |
| Identical bundle already exists locally | `status: already existed`, exit 0 |
| Different content under the same id (only by tampering) | `write_bundle`'s ValueError |
| GCS object exists | "already exists" (create-only writer), exit 1 |
| Partial GCS upload | exit 1; recovery runbook above |
| Scorer given schema 2 with a missing/extra/renamed/re-roled portable file | I4 `files:` |
| Scorer given schema 2 with `portable` null / malformed / non-zero parity / booster list ≠ table | I5 `manifest shape:` |
| Scorer given schema 3, or schema `True` | I1 `manifest:` |
| Schema-1 bundle with only `schema_version` changed to 2 | I4 `files:` (no portable files) |
| `read_portable_bundle`: any check 1-6 fails (incl. LR with boosters, model_name or feature_columns disagreeing, wrong directory, non-canonical manifest) | `portable: ...` |
| `load_portable` on malformed `portable.json` / corrupt UBJ / feature-count mismatch / booster with feature names | `portable: ...` (`XGBoostError` re-raised as ValueError) |
| `parse_reference` on a malformed reference | `portable: reference ...` |
| `verify_reference` mismatch (e.g. a future environment computes differently) | `portable: reference mismatch: ...` |
| Predictor input missing a column / non-string categorical / non-finite LR numeric / inf XGB numeric | `portable: ...` |

## Verification criteria

All CI tests use the session fixture (`scoring_env`: two small models trained on `make_gold_frame(300, seed=1)`, the
calibrated XGBoost with its real 5-fold structure) and need no JVM and no real data. A new session fixture `portable_env`
in `tests/conftest.py` (**all imports inside the fixture body**, like `scoring_env`, because CI's
`pipeline-dataproc-runtime` job loads `tests/conftest.py` without sklearn, xgboost, scipy or mlflow) calls
`export_portable_bundle` once for each fixture bundle (gold `scoring_env.gold_dir`) into a session tmp dir and exposes
`lr_portable_uri`, `xgb_portable_uri`, and the two `PortableExportResult`s.

`tests/bundles/test_manifest.py`
- `test_file_role_known_names_and_boosters` (model.pkl, split, portable.json, portable_reference.csv, portable_fold_0.ubj, portable_fold_12.ubj)
- `test_file_role_refuses_unknown_names` (`notes.txt`, `portable_fold_01.ubj`, `portable_fold_-1.ubj`, `portable_fold_0.UBJ`)
- `test_expected_file_names_per_schema` (schema 1; schema 2 with [] and with two boosters)
- `test_expected_file_names_refuses_bad_schema_or_portable` (schema 3, `True`, schema 2 with portable None / booster_files not a list)
- `test_check_portable_block` (parametrized: valid; each key missing/extra; format_version 2 and `True`; params_file / reference_file renamed; booster list non-contiguous / out of order / ≠ table; parity rows 0 / `True`; max_abs_diff `1e-17` / `False`; writer_versions with an int)
- `test_check_schema2_files_table` (valid; missing portable.json; missing reference; a `portable_fold_01.ubj` key; `notes.txt`)
- `test_file_entry_schema1_unchanged`

`tests/bundles/test_store.py`
- `test_write_schema2_bundle_with_boosters` (layout, read-only, idempotent rewrite)
- `test_schema2_file_set_must_match_portable_block` (missing booster / extra booster / missing portable.json / missing reference → ValueError before disk)
- `test_printable_bundle_uri` (gs:// unchanged; inside cwd → relative POSIX; outside → absolute, False)

`tests/portable/test_predictor.py`
- `test_lr_portable_matches_pickle_exactly`, `test_xgb_portable_matches_pickle_exactly` (fixture test rows; `np.array_equal` on the full (n, 2) arrays)
- `test_one_hot_semantics` (hand-built `OneHotSpec`: frequent → its column; infrequent and unseen → infrequent column; unseen with no infrequent column → all-zero block; `output_names`)
- `test_every_fitted_category_unseen_and_missing_match_pickle` (fixture models on `build_probes` output; equal arrays)
- `test_input_columns_by_name_extra_ignored_missing_refused`
- `test_input_contract_refusals` (zero rows; duplicate column names; LR count of -1 and -2 → `portable: non-finite value after the linear transform`; a `Float64` column with `pd.NA` → `portable: cannot convert`; LR NaN numeric; XGB inf numeric; XGB NaN numeric accepted and equal to the pickle's output; None / `pd.NA` / int in a categorical column → `portable: non-string`)
- `test_booster_best_iteration_attribute_is_ignored` (a fold booster carrying `best_iteration` set via `set_attr` still
  predicts with the stored `iteration_range` `[0, 0]`, i.e. all trees, equal to `inplace_predict(Z, iteration_range=(0, 0))`)
- `test_calibrator_scalars_are_float64` (`model.folds[k].a` / `.b` are `np.float64`)
- `test_load_portable_refuses` (parametrized: missing key, extra key, coef length ≠ names, output names ≠ derived, booster file absent, corrupt booster bytes, booster num_features mismatch, iteration_range [0, 10], booster_file of fold 1 named fold 0, format_version 2, model_name unknown, a float field given as the string "NaN", `infrequent_column` true with empty `infrequent_categories`, overlapping categories)
- `test_parse_reference_round_trip_and_refusals` (exact float round trip incl. `5e-324` and `0.1`; wrong header; no test row; probe before test; non-finite numeric; p1 = 1.5)
- `test_read_portable_bundle_checks` (parametrized on tampered copies of the fixture schema-2 bundles, made with `tamper_bundle`: portable.json bytes edited with `rehash=False` → sha256 mismatch; LR bundle with a stray booster listed; model_name mismatch between manifest and portable.json; feature_columns mismatch; directory not equal to id; non-canonical manifest; schema-1 bundle; reference row count ≠ parity block) → each `portable:`
- `test_read_portable_bundle_does_not_read_the_pickle` (a copy whose `model.pkl` is replaced by garbage, table re-hashed → `read_portable_bundle` + `verify_reference` pass)
- `test_verify_reference_detects_a_one_ulp_change` (reference with one `p1` moved by `np.nextafter` → `portable: reference mismatch`)
- `test_predictor_source_has_no_forbidden_imports` (static regex over `src/readmission_risk/portable/predictor.py`: no `import`/`from` of sklearn, pandas, cloudpickle, pickle, joblib, mlflow, pyspark, google; no `pickle.loads`/`cloudpickle.loads`)
- `test_predictor_import_does_not_load_mlflow_pyspark_google_or_our_sklearn_modules` (subprocess: `import readmission_risk.portable.predictor`; `mlflow`, `pyspark`, `google.cloud`, `readmission_risk.portable.export`, `readmission_risk.scoring`, `readmission_risk.models` not in `sys.modules`; this is what still catches an accidental dependency on our sklearn-dependent modules, since `import xgboost` loads sklearn itself)

`tests/test_tooling.py`
- `test_live_targets_refuse_without_required_variables` (l.307) gains `("export-portable", "set SOURCE_BUNDLE=")` in its
  loop, and a dry-run assertion: `scripts/export_portable_bundle.py --source-bundle bundles/x --gold-dir
  data/gold/full_run_2b --bundles-dir y` is in `_dry_run("export-portable", "SOURCE_BUNDLE=bundles/x", "PORTABLE_ARGS=--bundles-dir y")`.
  (`test_makefile_declares_the_agreed_targets` and `test_make_help_lists_every_target` pass once `EXPECTED_TARGETS` and
  the Makefile agree.) The Makefile's refusal message is `ERROR: set SOURCE_BUNDLE=<a local schema-1 bundle directory>`.

`tests/bundles/test_gcs.py` (schema 2 over `gs://`, with the existing fake from `tests/bundles/fake_gcs.py`, as
`test_write_files_manifest_last_if_generation_match_zero_retry_none` at l.99 does)
- `test_write_schema2_bundle_to_gs` (the fixture schema-2 XGB bundle's 10 files: 10 uploads, `manifest.json` last, all
  `if_generation_match=0`, `retry=None`)
- `test_read_portable_bundle_from_gs` (`read_portable_bundle("gs://…")` + `verify_reference` pass against the fake)
- `test_validate_schema2_bundle_from_gs` (`validate_bundle("gs://…", gold=…, metadata=…)` passes for the schema-2 LR bundle)

`tests/portable/test_export.py`
- `test_export_creates_valid_schema2_bundles` (both fixture models; `validate_bundle` passes; manifest keys other than schema_version/files/portable/bundle_id equal the source's; model.pkl and split bytes identical; `parity.max_abs_diff == 0.0`, `rows == number of test rows`, `probe_rows == len(build_probes(X_test, document))` with the formula; file counts 5 / 10)
- `test_exported_bundle_self_verifies` (`read_portable_bundle` + `verify_reference` on both)
- `test_export_is_idempotent` (second call: same id, `created is False`)
- `test_export_refuses_schema2_source`, `test_export_refuses_gs_source`
- `test_export_refuses_on_parity_failure_and_writes_nothing` (monkeypatch `readmission_risk.portable.predictor.load_portable` to wrap the real model so one output cell moves by `np.nextafter` → `portable export: parity failed`; bundles root unchanged)
- `test_extract_refuses_unsupported_shapes` (parametrized, models built in the test with sklearn/xgboost on tiny data: OneHotEncoder `drop="first"`; `sparse_output=True`; isotonic calibration; `ensemble=False`; an XGBClassifier made to look early-stopped by fitting a normal tiny model and then
`get_booster().set_attr(best_iteration="5")` on one fold's classifier, so `_get_iteration_range(None)` returns `(0, 6)`; `booster="dart"`; log1p with `kw_args`; pipeline with an extra step; renamed transformer; `set_output(transform="pandas")`)
- `test_build_probes_refuses_a_fitted_unseen_marker` (a document whose spec lists `PROBE_UNSEEN` as a category → ValueError)
- `test_build_probes_formula_and_determinism` (same input twice → equal frames; row count formula; every fitted category of every fold appears for its column; `PROBE_UNSEEN` and `MISSING` present)
- `test_export_source_failing_a_guard_is_refused` (source tampered to a wrong `reference_date` → guard 7's message)
- `test_export_does_not_mutate_gold` (monkeypatched loader returns a frame; compare to a deep copy afterwards)

`tests/portable/test_cli.py`
- `test_cli_prints_the_six_lines` (CliRunner, exit 0; exact line prefixes and order; no champion JSON)
- `test_cli_error_exit_1` (missing source bundle → `ERROR:`, exit 1)
- `test_cli_refuses_gs_source` (exit 1)
- `test_cli_gs_destination_prints_created` (a local `fake` fixture in `tests/portable/test_cli.py`, a copy of `tests/bundles/test_gcs.py:26-30`: `FakeStorage()` from
`tests/bundles/fake_gcs.py` monkeypatched onto `readmission_risk.gcp.storage_client`; the same copy serves the
`tests/bundles/test_gcs.py` schema-2 tests, which use the existing fixture there; no conftest change; `--bundles-dir gs://<fake bucket>`; the six lines, `status: created`, `bundle_uri: gs://…/<id>`; a second run exits 1 with `already exists`)

`tests/portable/test_v1_parity.py` — **the FR0.3 / M8 parity tests on the real v1 data.** A module-level helper
discovers, under `bundles/` (repo root), every schema-2 bundle (a directory whose `manifest.json` parses with
`schema_version == 2`) whose `mlflow.run_id` is one of the v1 run ids (constants `fe9633ca42384ea5aeaba83e13d65701` = LR,
`abe9c605509b49dda4d8ba0aef511a60` = XGB; no bundle ids hard-coded). Several schema-2 bundles per run id are allowed
(e.g. after a re-export under different `writer_versions`, which the Data handling section calls harmless): every one is
checked. Two tests, so the portable half keeps running after the v1 pins move:
- `test_v1_portable_bundles_self_verify` — parametrized over the two run ids (LR, XGB), so each model passes or skips
  on its own; a parameter is skipped (reason names the run) only when no schema-2 bundle exists for that run id. For each discovered bundle: `read_portable_bundle` + `verify_reference` pass, and `parity.rows == 1539`.
  Needs neither the gold table nor the pickle-loading environment.
- `test_v1_portable_matches_the_pickle_on_fresh_rows` — parametrized the same way; additionally skipped if
  `data/gold/full_run_2b/` is absent, or (with the guard message as the reason) if `validate_bundle` on the real gold
  raises a ValueError whose message starts with `library version mismatch` (guard 8's drift message) or `python version
  mismatch` (guard 14 step (vi), drift only); every other `validate_bundle` failure, including guard 14's provenance
  defects (ii)-(v), is a test failure. For each discovered
  bundle: the portable `predict_proba` on freshly prepared real test rows (gold + the bundle's `split_assignment.csv`,
  not the reference) is `np.array_equal` to `load_bundle_model(...).predict_proba` (max abs diff 0.0); both sides score
  the SAME prepared frame (all test rows, sorted by `encounter_id`) in ONE `predict_proba` call each (batch-layout
  contract).

`tests/scoring/helpers.py` + `tests/scoring/test_bundle_guard.py`
- Replace `I1-schema-version-2` (would no longer fail at I1) with `I1-schema-version-3` (`manifest: `) and add
  `I4-schema1-relabelled-2` (schema 1 bundle with `schema_version` set to 2 → `files: `).
- Mechanism for all schema-2 refusal cases: they run through the SAME path as today's cases,
  `tests/scoring/test_pipeline.py::test_each_check_refuses_exactly_its_defect` (l.102-115: `score_run_date` with
  `forbid_model_loading`, a `Recorder` and `_config(env, out, **overrides)`). Its body is factored into a module-level
  helper `_assert_case_refuses(env, case, tmp_path, monkeypatch)`, called by the existing test (unchanged behavior) and by
  two new parametrized tests in the same file: `test_guards_refuse_schema2_bundles` (every case of `BUNDLE_CASES`, with
  `env = dataclasses.replace(scoring_env, lr_bundle_uri=portable_env.lr_portable_uri)`; `ScoringEnv` is a frozen
  dataclass and every builder reads `env.lr_bundle_uri` / `env.manifest()`) and `test_schema2_integrity_cases` (every case of
  the new `SCHEMA2_INTEGRITY_CASES`, with `env = dataclasses.replace(scoring_env,
  lr_bundle_uri=portable_env.xgb_portable_uri)`, so their builders are ordinary `_integrity(...)` builders tampering the
  schema-2 XGB bundle). Both tests request the `scoring_env` and `portable_env` fixtures.
- Schema-2 integrity cases (`SCHEMA2_INTEGRITY_CASES` in `tests/scoring/helpers.py`, `starts_with=True` like
  `INTEGRITY_CASES`): `I4-s2-missing-params`, `I4-s2-missing-reference`,
  `I4-s2-missing-booster`, `I4-s2-extra-booster-unlisted` (file present, not in table), `I4-s2-bad-booster-name` (table
  entry `portable_fold_01.ubj`; built with one top-level `set={"files": <whole table with the renamed key>}` because
  `tamper_bundle`'s `_set` does not create nested keys), `I4-s2-wrong-role`, `I4-s2-booster-byte-flipped`, `I5-s2-portable-null`,
  `I5-s2-portable-missing-key`, `I5-s2-format-version-2`, `I5-s2-booster-list-mismatch`, `I5-s2-non-contiguous-folds`,
  `I5-s2-parity-nonzero` (`1e-17`), `I5-s2-parity-rows-bool`, `I5-s2-writer-version-int`. All refuse before any unpickle
  (existing `forbid_model_loading` pattern) with the integrity key at the start of the message.
- Guards unweakened on schema 2: `test_guards_refuse_schema2_bundles` above runs **every** case of `BUNDLE_CASES`
  (`tests/scoring/helpers.py` l.425-509) with its existing key, extras and forbidden strings.
- `test_schema2_bundle_validates_like_its_source` (both models: `ModelBundle` fields equal the source's except `bundle_id`/`bundle_uri`; `model_bytes` identical).

`tests/scoring/test_pipeline.py`
- `test_scoring_a_schema2_bundle_gives_identical_scores` (same run date with the schema-2 LR bundle and with its source; predictions frames equal except `model_bundle_id`). The existing no-mlflow/no-pyspark subprocess and static tests are unchanged and must still pass.

**Real-data acceptance (recorded in Results, not CI):**
1. Both v1 schema-2 bundles exported locally; for each the CLI prints `parity: max abs diff 0.0 on 1539 test rows and <n>
   probe rows` (both lines pasted into Results with the new ids).
2. `pytest -rs tests/portable/test_v1_parity.py` **passes** locally — the pasted summary line must show `4 passed` (two tests × two run ids) and no skip, not
   `skipped`; the ids it discovered must equal the CLI's.
3. `python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --run-date 20250630 --bundle bundles/<id>
   --output-dir <scratchpad>/<name>` once with the schema-2 LR bundle and once with its source `bundles/75a1448729ef…`,
   each into its own scratchpad output dir (never the default `data/predictions/`, which `scripts/make_readme_assets.py`
   reads); the two `predictions.parquet` frames are equal under `pandas.testing.assert_frame_equal(check_exact=True)`
   after dropping `model_bundle_id`, and `model_bundle_id` equals each bundle's id.
4. Owner-confirmed upload; the GCS re-export prints the same bundle ids as the local export; the acceptance checks of the
   Live step pass (object counts 5 / 10; `read_portable_bundle` + `verify_reference` + `validate_bundle` on the `gs://` URIs).
5. Cross-environment measurements (Interfaces > Known risk): outcomes of (i) the newest-releases scratch venv and (ii)
   the Linux container (or "not measured"), each with the library versions used and the max abs diff found.
6. Gate: `ruff check .` and `pytest` green; `make lock-main` leaves every version and hash unchanged
   (`tests/test_lockfiles.py` passes; `git diff requirements.txt` shows only `# via` lines).

## Out-of-scope follow-ups (noted, not built)

1. FR16 fallback wiring: a `--portable` frozen-v1 mode in `ops/gate_run.py` using `read_portable_bundle` +
   `verify_reference` first (D4); needed once the v1 pins move; its results are "reference-verified" only (Known risk,
   caveat on scope). Blocked on follow-up 7's decision: measured, `verify_reference` refuses on Linux.
7. Cross-platform reference policy (Results 5): tolerance (via `/eg-prd`), an additional Linux-written reference while the
   pickles still load, or a macOS-only fallback. Decide before building follow-up 1, and before the v1 pins move if (b)
   is wanted.
2. Moving `deploy/champion.json` to the schema-2 LR bundle (D3), as a PR with this note's parity table.
3. A portable scoring path in the scorer (D2) — only if a champion ever has to be scored after a pin bump.
4. Portable export for future (200k / retrained) runs, or at training time; a more general model-shape coverage.
5. A champion-compatibility check for library bumps (still open from the CI hygiene bundle); a CI job that runs
   `verify_reference` against a schema-2 bundle under bumped pins would be the natural home (needs a bundle readable from
   CI).
6. Still open, not 6c: slice 7 (monitoring tick, ledger, issues, burn-in); slice 8 (BigQuery gold table with the connector
   MERGE test, dashboard, model card); V-post and L-post after v2 merges into `main`; the pandas 3 check; the 45
   cross-machine QALY rows.

## Design-check log

- **Round 1** (comprehension + critic + readiness): comprehension passed. Critic: 12 gaps; readiness: 6 questions. All
  addressed in revision 2:
  - critic 1 (no stored reference, so parity cannot be re-proved where the portable form is needed): new
    `portable_reference.csv` (inputs + pickle outputs, lossless) with role `portable_reference`, `parse_reference`,
    `verify_reference`; parity at export runs through the written bytes.
  - critic 2 / readiness 1 (`build_probes` argument and coverage): `build_probes(X_test, document)`, every fitted category
    (frequent + infrequent, all folds) + unseen + `MISSING`; `infrequent_categories` recorded in the spec.
  - critic 3 (input contract): str-only categoricals, finite LR numerics, NaN-but-not-inf XGB numerics; tests.
  - critic 4 (early stopping / booster type): refuse unless `(0, 0)` and `booster in (None, "gbtree")`; loader requires
    `[0, 0]`.
  - critic 5 / readiness 5 (`read_portable_bundle` cross-checks): six ordered checks incl. canonical form, directory,
    model-specific booster rule, model_name/feature_columns; I5 stays structural (stated).
  - critic 6 (gs:// source + impersonation): source must be local; GCS status always "created".
  - critic 7 (partial upload): recovery runbook and object-count + read-back acceptance.
  - critic 8 (missing shape checks): `kw_args`/`validate`, scaler attributes, sparse/pandas output, `coef_.ndim`,
    booster feature names.
  - critic 9 (import rule): predictor imports `bundles.manifest` + `bundles.store`, never google at import time (test).
  - critic 10 / readiness note (fixture imports; patch target): `portable_env` imports inside the body; export calls
    `predictor.load_portable` through the module.
  - critic 11 (verification can pass skipped): v1 parity test discovers bundles by run id (no hard-coded ids) and the
    Results must show it passing.
  - critic 12 (bundle-id determinism wording): the full dependency list is stated.
  - readiness 2 (class names / attributes): dataclasses named with attributes.
  - readiness 3 (`_printable_uri` reuse): moved to `bundles/store.py::printable_bundle_uri`.
  - readiness 4 (guard cases on schema 2): all `BUNDLE_CASES` via `dataclasses.replace(scoring_env, lr_bundle_uri=...)`.
  - readiness 6 (CLI output order, parity-failure wording): exact six lines; message text specified via
    `verify_reference`.

- **Round 2** (critic + readiness): critic 10 gaps, readiness 3 questions. All addressed in revision 3:
  - critic 1 / readiness 1 (`import xgboost` loads sklearn, pandas, cloudpickle; measured): import rule restated as a
    static source check + a subprocess check for mlflow/pyspark/google only; D1 and Why reworded; the soft dependency named
    as a risk.
  - critic 2 (no cross-environment evidence): "Known risk: bit-exactness across environments" with a fixed mismatch policy
    and two recorded measurements (acceptance 5).
  - critic 3 (LR formula not the measured one): the design now uses scikit-learn's exact form ((1, n) `coef`, (1,)
    intercept, `@ coef.T`, `reshape(-1)`), re-measured bit-identical with JSON-restored parameters (Feasibility).
  - critic 4 (v1 parity test fails when it matters; multiple bundles): split into a self-verify test (no gold, no pickle) and
    a pickle-comparison test skipped on guard 8/14; every schema-2 bundle per run id is checked.
  - critic 5 (`read_portable_bundle` wrapping/order): store errors wrapped like `_store_call`, helper errors prefixed,
    `check_schema2_files_table` before `file_role`.
  - critic 6 (no offline `gs://` schema-2 tests): three fake-GCS tests.
  - critic 7 (writer looser than scorer): `expected_file_names` refuses a non-null schema-1 `portable` and a schema-2
    manifest without the full key set.
  - critic 8 (unlocked sklearn behavior): per-fold `classes == [0, 1]` and no `decision_function` checks.
  - critic 9 (NaN in reference; ids): deliberate finite-only reference stated; ids unique/ascending, probe ids sequential.
  - critic 10 (citations, I4-before-I5): corrected and stated.
  - readiness 2 and 3 (where the schema-2 case runners live): both in `tests/scoring/test_pipeline.py`, through
    `score_run_date` via a factored helper, with `dataclasses.replace(scoring_env, lr_bundle_uri=...)`.

- **Round 3** (critic + readiness): readiness **implementation ready** (five self-settled points, now written down: the
  bad-booster-name builder, "MISSING" inside the sort, gold-loader errors unprefixed, `__init__.py` import-free, the
  expected unknown-category warning). Critic: 9 gaps, all addressed in revision 4: `_expected_bytes` order and the wrong
  comment (1); import-free `__init__.py` + the subprocess test also forbids our own sklearn-dependent modules (2); gold
  loader patch target (3); zero rows, duplicate columns, conversion errors (4); cross-environment package sets, run with
  and without sklearn/pandas (5); the reference-rows-only caveat carried into follow-up 1 (6); `PROBE_UNSEEN` collision
  refused (7); `GOLD_DIR` passed by the Makefile target (8); a `gs://` CLI test (9).

- **Round 4** (critic + readiness): readiness 1 question (the fitted form of "passthrough" is a `FunctionTransformer`;
  measured, fixed in the XGB shape rule). Critic 9 gaps. **3-revision cap reached; owner decision: apply the fixes and
  re-run once more.** Revision 5: batch layout made part of the reference contract (per-segment calls) and named in the
  caveat (1); `tests/test_tooling.py` surfaces and tests (2); `tests/bundles/test_gcs.py` in the surfaces table (3); LR
  non-finite-after-transform refusal (4); acceptance 3 writes to scratch output dirs and compares exactly (5);
  `read_portable_bundle` ties the reference test ids to `split_assignment.csv` (6); CSV writer/reader settings and the
  line-break refusal (7); `writer_versions` gains python/platform/blas (8); `_Column` defined, feature count not
  hard-coded (stated why), unmapped GCS errors documented as today's behavior (9).

- **Round 5** (critic + readiness, the extra round the owner authorized): readiness **implementation ready** (no open
  questions). Critic: 5 small gaps, folded into revision 6 without a further goldfish round: V-post wording and the
  README / `docs/results.md` updates added to scope item 9 (1, 2); a named construction for the early-stopping refusal
  case plus a `best_iteration`-attribute test (3); `Reference.columns` an explicit property (4); the v1 parity tests
  parametrized per run id with the skip narrowed to guard 8's and guard 14 (vi)'s drift messages (5).

- **Round 6** (critic + readiness, run on revision 6 at the owner's request): readiness **implementation ready** (no open
  questions; its five self-settled points noted: `metadata=` in the live acceptance's `validate_bundle`, `portable_env`
  attribute names `lr_result`/`xgb_result`, any portable file for `I4-s2-wrong-role`, `np.errstate` optional on the
  log1p refusal path, the CLAUDE.md edit goes in the "Model bundle replaces MLflow for scoring" bullet). Critic: 8
  hygiene gaps, folded into revision 7: `4 passed` (1); the warning filter removed, its premise was wrong (2); `eq=False`
  on the predictor dataclasses (3); `columns` attribute required (4); exporting other bundles "not refused, merely not
  done" (5); a local `fake` fixture copy (6); one call per side in the fresh-rows test (7); literal CSV header, CLI
  warning wording (8).
- **Gate closed by owner override after round 6** (2026-10-17): revision 7 accepted. Readiness closed in rounds 3, 5 and 6;
  the critic never formally closed (12 → 10 → 9 → 9 → 5 → 8 gaps, the last two rounds hygiene-level, every gap folded in).

## Results

Built 2026-10-17 on branch `v2-slice6c-portable-export` (off `v2` at `7b602d7`).

**Acceptance (real data, the owner's Mac: Python 3.14.7, numpy 2.4.4, scipy 1.18.1, xgboost 3.4.1, scikit-learn 1.9.1, Accelerate):**

1. Both v1 schema-2 bundles exported locally with `make export-portable`:

   | Model | Schema-2 bundle | Source (schema 1) | Parity |
   |---|---|---|---|
   | logistic_regression | `fb44c38910b4e0d747c6e1900ccf9331b0e93d2f1766b600967472c22fdfc381` | `75a1448729ef…` (champion) | max abs diff 0.0 on 1539 test rows and 325 probe rows |
   | xgboost_calibrated | `bf66d1cb272b121ff79c23d7de3faf3530b91adef38102caecf712a06e2390ff` | `30009d198070…` | max abs diff 0.0 on 1539 test rows and 325 probe rows |

   An earlier scratch export (before the CLI existed) produced the same two ids: the export is deterministic.
2. `pytest -rs tests/portable/test_v1_parity.py`: `4 passed` (no skip); the discovered bundles are the two above.
   Before the export the same command gave `4 skipped`, each with a named reason.
3. `score_discharges.py --run-date 20250630` with `--bundle bundles/fb44c38910b4…` and with `--bundle bundles/75a1448729ef…`,
   each into its own scratchpad `--output-dir`: both exit 0, 42 rows, frames equal under `assert_frame_equal(check_exact=True)`
   after dropping `model_bundle_id`.
4. **GCS upload (owner, 2026-10-17, after PR #21 merged as `fa95294`):** both bundles uploaded create-only as
   `bundle-writer` with `make export-portable … PORTABLE_ARGS="--bundles-dir gs://synthea-readmission-risk-bundles
   --impersonate-service-account <bundle-writer SA>"`; the re-exports printed the local ids. Read back as the owner:
   `fb44c38910b4…` lists 5 objects and `bf66d1cb272b…` 10 (manifest included, so no partial upload);
   `read_portable_bundle("gs://…")` + `verify_reference` → 0.0 for both; `validate_bundle("gs://…", gold=full_run_2b)`
   passes for both.
5. **Cross-environment measurements (2026-10-18, owner-approved unpinned installs in scratch venvs outside the repo; the
   repo's `src/` put on `sys.path`, nothing installed into or written to the repo).** `verify_reference` on both local
   schema-2 bundles:

   | Run | Environment | LR | XGBoost |
   |---|---|---|---|
   | control | repo `.venv`: macOS arm64, Accelerate, numpy 2.4.4, scipy 1.18.1, xgboost 3.4.1, sklearn 1.9.1, pandas 2.3.3 | 0.0 | 0.0 |
   | i-a | macOS, newest releases: numpy **2.5.4**, pandas **3.0.6** (scipy, xgboost, sklearn already newest = the pins) | 0.0 | 0.0 |
   | i-b | macOS, numpy 2.5.4, scipy 1.18.1, xgboost 3.4.1, **no scikit-learn, no pandas** (xgboost's soft sklearn import falls back) | 0.0 | 0.0 |
   | i-c1 | macOS, numpy 2.5.4, **xgboost 3.3.0, scipy 1.17.1**, no sklearn/pandas (added: an older minor, as no newer one exists) | 0.0 | 0.0 |
   | i-c2 | macOS, numpy 2.5.4, **xgboost 3.0.5, scipy 1.16.3**, no sklearn/pandas | 0.0 | **refused, max abs diff 0.975** |
   | ii-arm | Docker `python:3.14.7-slim` linux/arm64, hash-locked `requirements.txt` + Linux extras (same pins), scipy-openblas | **refused, 3.9e-16** | **refused, 2.6e-9** |
   | ii-x86 | the same image as linux/amd64 (emulated), scipy-openblas | **refused, 3.3e-16** | **refused, 2.6e-9** |

   - **i-c2 cause (measured):** xgboost ≥ 3.1 writes `base_score` as a vector (`[1.584507E-2]`); 3.0.5 does not parse it
     and silently uses its default 0.5, so all 300 trees load but every margin is shifted. An older reader of a newer
     model file is outside XGBoost's compatibility promise; the point is that `verify_reference` refused instead of
     returning wrong scores.
   - **ii (decisive extra check):** in both Linux containers the original pickle (same pins) and the portable form agree
     **exactly** (0.0, both segments, both models), and the pickle itself differs from the macOS-written reference by the
     same amounts. The XGBoost difference is identical on arm64 and x86-64, so it comes from the macOS vs Linux builds,
     not the CPU. So: the portable form is faithful on every platform measured (FR0.3's requirement holds), but the
     self-contained bit-exact re-check passes only on the writer's platform family.
   - **Consequence (not decided here):** the FR16 fallback (follow-up 1) cannot run bit-exactly on Linux (CI, a Linux
     laptop) against this reference. Options for its design: (a) a tolerance, which changes FR0.3's "max abs diff 0" and
     goes through `/eg-prd`; (b) additionally store a Linux-written reference (produce it now, in a Linux container, while
     the pickles still load under the pins; needs a manifest/format change); (c) run the fallback on macOS only. Recorded
     as follow-up 7.
6. Gate: `ruff check .` clean; `pytest` 1,654 passed, 2 skipped (both pre-existing environment skips); `make lock-main`
   changed only one `# via` comment in `requirements.txt` (scipy is now `-r requirements.in`); `tests/test_lockfiles.py` passes.

**Deviations found during implementation (each measured, none loosens a check):**

- **pandas nullable `Float64` + `pd.NA`:** the design (round 3) expected `to_numpy(dtype=float64)` to raise; pandas 2.3.3
  converts `pd.NA` to NaN instead, so such input is refused by the LR path as "non-finite numeric input", not as "cannot
  convert". The test pins the measured behavior and a separate case (a non-numeric string in a numeric column) exercises
  "cannot convert".
- **`check_portable_block`** now also requires every files-table entry to carry a string `role` (the design's "never
  KeyError/TypeError" rule, which the first implementation tolerated silently; caught by `test_check_portable_block`).
- `np.log1p` is wrapped in `np.errstate` (silences the RuntimeWarning on the refused count <= -1 path; numbers unchanged).
- The sklearn clip `(1.0 < proba)` is written `(proba > 1.0)` (ruff SIM300); the same boolean array.

**Tests added:** `tests/portable/` (predictor 50, export 23, CLI 4, v1 parity 4), schema-2 cases in `tests/bundles/`
(manifest, store, GCS), `tests/scoring/` (15 schema-2 integrity cases, all 48 guard cases re-run on a schema-2 bundle,
identical scores, `ModelBundle` equality), `tests/test_tooling.py` (target, variables, refusal, dry run).
