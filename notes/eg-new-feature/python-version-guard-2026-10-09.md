# Python-version provenance guard (guard 14) — design, 2026-10-09

Feature request (via `/eg-new-feature`): record the Python version at training time and check it in the scorer's run
guards before unpickling, and decide how legacy runs without the tag are treated.

Owner decisions taken in Step 0 (2026-10-09): legacy runs fall back to the Python version MLflow wrote into the logged
model's `MLmodel`; a major.minor mismatch is **refused with no override**; the comparison granularity is **major.minor**.

## Why

CLAUDE.md, Architecture > Python version note, "Known gap (flagged, not fixed)": training records five library versions
as run tags and the scorer's guard 8 compares them, but neither records nor checks the Python version, although the
models are cloudpickles. Cloudpickle/pickle compatibility is a property of the interpreter's major.minor version
(MLflow's own load-time check, `mlflow/pyfunc/__init__.py::_warn_potentially_incompatible_py_version_if_necessary`,
compares major.minor and only *warns*). Development moved to Python 3.14.7 on 2026-10-09 and CI tests 3.12 + 3.14, so a
run trained under one Python and scored under another is now a realistic situation. The gap was named in session
826a4602 (2026-10-09), which measured that the 3.12.11-trained `readmission-risk-2b` runs score identically under 3.14
(max |Δscore| 0.0, same ranks, run date 20250630). That compatibility is an observation about this dataset and these
library versions, not a guarantee, which is why the guard refuses rather than trusts it.

## Scope

**In:**

1. Training records a new run tag `python_version` = `"X.Y.Z"` of the training interpreter, on both model runs.
2. The scorer's `validate_run` gains guard 14 (key `"python version"`), run before any model is unpickled:
   - a. Recorded version = the `python_version` tag if present; for **legacy** runs without the tag (all 8 runs in the
     current store), fall back to `flavors.python_function.python_version` in the logged model's `MLmodel` file, which
     MLflow wrote at log time and which `mlflow.models.get_model_info` reads without unpickling. (All 8 existing runs:
     `MLmodel` = 3.12.11, verified 2026-10-09.)
   - b. Neither source present → refuse.
   - c. Tag and `MLmodel` both present but disagreeing at major.minor → refuse (inconsistent provenance).
   - d. A present-but-unparseable value in either source → refuse.
   - e. Recorded major.minor ≠ running major.minor → refuse. **No override flag** (consistent with guards 1-13 and
     slice 4's no-override rule). Patch differences (3.14.7 vs 3.14.8) pass.
3. Documentation of the new guard and its consequence (below).

**Out (explicit):**

- Retraining `readmission-risk-2b` under 3.14. **Consequence, accepted by the owner:** after this change, the documented
  command `score_discharges.py --gold-dir data/gold/full_run_2b --experiment-name readmission-risk-2b ...` is
  **refused** under the default Python 3.14 environment (those runs were trained on 3.12.11). Scoring them needs a 3.12
  environment, or a retrain under 3.14 (new run ids → README figure provenance, `docs/scoring.md` run id, sample table and
  `docs/results.md` numbers must be re-verified). Named follow-up F1.
- Surfacing the recorded Python version in `ModelRun` / the stdout report / the predictions Parquet.
- Guarding `models.tracking.load_logged_model` itself (it remains an unguarded primitive used by tests and `clone_run`;
  the scorer only calls it after `validate_run` has passed).
- Recording/checking the platform (OS/arch) or the PySpark/Java versions.
- Recording/checking the interpreter implementation (`sys.implementation.name`, CPython vs PyPy) or a free-threaded
  build (`3.14t`, whose `sys.version_info` is identical). Only CPython (GIL build) is used today. Follow-up F5.
- Editing `notes/eg-new-feature/batch-scoring-cli-2026-09-20.md` ("guards 1-13" at its lines ~159 and ~330): design
  notes are frozen historical records in this repo. Instead, the `runs.py` module docstring gains a second pointer:
  "Guard 14 (Python version): notes/eg-new-feature/python-version-guard-2026-10-09.md."
- Any change to guard 8 or `installed_versions()` (its key set stays the five library tags).

## Surfaces touched

| File | Change |
|---|---|
| `src/readmission_risk/models/tracking.py` | + `current_python_version()` |
| `src/readmission_risk/models/training.py` | + one tag in the existing `mlflow.set_tags({...})` dict (lines ~263-276) |
| `src/readmission_risk/scoring/runs.py` | + guard 14 after guard 13 (end of `validate_run`, before `return ModelRun`); + module-level helpers; docstring "guards 1-13" → "guards 1-14" |
| `tests/models/test_training.py` | tag-presence loop at line ~131 extended; one new consistency test |
| `tests/scoring/helpers.py` | `clone_run` gains `mlmodel_python_version` / `drop_mlmodel_python_version` / `mlmodel_python_function_flavor`; 10 new `GUARD_CASES` (G1-G10, each with its own builder returning its extras — `_simple` returns `()`); constants `_RUNNING` / `_OTHER_MINOR` / `_OTHER_PATCH`; "38 in total" → the count pytest collects (48 by the G-list) |
| `tests/scoring/test_runs.py` | 5 positive tests (P1-P5) + 2 unit tests (U1 parser, U2 `_mlmodel_python_version`) |
| `docs/setup.md:63` | one comment line above the `train_models.py` command (see "Documentation changes") |
| `README.md:121` | "13 guards" → "14 guards" |
| `docs/scoring.md` | see "Documentation changes" below |
| `CLAUDE.md` | see "Documentation changes" below |

### Documentation changes (exact places and caveats)

The reference runs in `readmission-risk-2b` (and every other run in the current store) were trained on Python 3.12.11,
so under the default 3.14 environment every documented scoring command is refused after this change. Each place:

- `docs/scoring.md:9`, `:12`, `:13` (the three example commands): directly below the code block, add one paragraph:
  "**Python version.** A run is only scored under the Python major.minor it was trained with (guard 14; the models are
  cloudpickles). The reference runs in `readmission-risk-2b` were trained on Python 3.12.11, so on the default 3.14
  environment these commands are refused with `python version mismatch`. To score them, use a Python 3.12 environment
  (any Python 3.12 interpreter, e.g. `pyenv shell 3.12.11` or Homebrew's `python3.12`)
  outside the repository, with the same prerequisites as [setup.md](setup.md) (on macOS `brew install libomp` for
  XGBoost), run from the repository root:" followed by the code block
  `python3.12 -m venv ~/.venvs/readmission-py312 && source ~/.venvs/readmission-py312/bin/activate` and the same three
  install commands as `docs/setup.md` (`pip install -e .`; `pip install --require-hashes -r requirements.txt -r requirements-linux-extras.txt`;
  `pip install --require-hashes -r requirements-dev.txt`), "or retrain under 3.14 with a fresh `--experiment-name`." (The
  venv lives outside the repo so its name cannot collide with tracked files; `.gitignore` covers only `.venv/`,
  `venv/`, `env/` and `env*/`.) No claim about a JDK is made, because S2 runs on a machine that has one.
- `docs/setup.md:63`: add a comment line directly above the `train_models.py` command: "# In a store that already
  holds runs under this experiment name (e.g. the 3.12-trained reference runs), pass a fresh --experiment-name, or
  --model becomes ambiguous for the scorer (docs/scoring.md)." (A fresh clone has no store, so the command as written
  stays correct for it.)
- `docs/scoring.md:30` ("13 guards" sentence): "13 guards" → "14 guards", and after "the same library versions as the
  lockfile (the model is a cloudpickle)" insert ", the same Python major.minor (from the run's `python_version` tag, or
  for runs trained before that tag existed, the version MLflow wrote into the logged model's `MLmodel`)".
- `CLAUDE.md`, Python version note: replace the sentence beginning "**Known gap (flagged, not fixed):**" with
  "**Resolved 2026-10-09 (`notes/eg-new-feature/python-version-guard-2026-10-09.md`):** training records a
  `python_version` run tag and the scorer's guard 14 refuses a run whose Python major.minor (tag, or for legacy runs the
  logged model's `MLmodel`) differs from the running interpreter's, with no override. Consequence: the 3.12-trained
  reference runs, measured above to load and score correctly under 3.14, are now refused under 3.14 by design; score
  them from a 3.12 environment (recipe in `docs/scoring.md`) or retrain under 3.14 (named follow-up)."
- `CLAUDE.md`, Slice-4 paragraph: "13 guards" → "14 guards"; append ", and the Python major.minor (guard 14)" to the
  guard list.
- `CLAUDE.md`, Build & test, "Score a Top-N triage list" line: append "(the `readmission-risk-2b` runs were trained on
  Python 3.12.11 and are refused under 3.14 by guard 14; see `docs/scoring.md` > Python version)".
- `CLAUDE.md`, Build & test, "Regenerate the README figure" line: append "(the figure reads MLflow artifacts only and
  works under 3.14; regenerating the `predictions.parquet` it reads requires scoring, which for the 3.12-trained
  reference runs needs a 3.12 environment)".
- `docs/results.md:49` (the `make_readme_assets.py` command): add after the command block: "Regenerating the
  `predictions.parquet` it reads requires scoring; the `readmission-risk-2b` runs were trained on Python 3.12.11, so
  that needs a Python 3.12 environment (see [scoring.md](scoring.md), Python version). The figure itself reads MLflow
  artifacts only and works under 3.14."
- `CLAUDE.md`, Build & test, "Train models" line: append "(in a store that already holds runs under the target
  experiment name, pass a fresh `--experiment-name`, or the scorer's `--model` resolution becomes ambiguous; runs are
  scored only under the Python major.minor they were trained with)".
- `CLAUDE.md`, Build & test, Test line: 527 total / 123 scoring tests / "38 guard case" counts → the numbers pytest
  reports after implementation (counted, not predicted).

No data tables, gold schema, features, split, label, or pipeline stages change. Existing MLflow runs are **not** modified.

## Interfaces

1. `src/readmission_risk/models/tracking.py` (add `import sys`):

   ```python
   def current_python_version() -> str:
       """'X.Y.Z' of the running interpreter -- sys.version_info's major, minor and micro, the same format MLflow
       writes into a logged model's MLmodel (flavors.python_function.python_version)."""
       v = sys.version_info
       return f"{v.major}.{v.minor}.{v.micro}"
   ```

2. `training.py`: in the existing `mlflow.set_tags({...})` call, after `"numpy_version"`, add
   `"python_version": tracking.current_python_version(),` (`training.py` already does `from . import tracking`).

3. `runs.py` (add `import re`; import `current_python_version` from `readmission_risk.models.tracking`):

   ```python
   _PYTHON_VERSION_TAG = "python_version"
   _PYTHON_VERSION_RE = re.compile(r"([0-9]+)\.([0-9]+)(?:\.([0-9]+))?")  # [0-9], not \d: \d also matches non-ASCII digits

   def parse_python_major_minor(value: object) -> tuple[int, int] | None:
       """(major, minor) of an 'X.Y' or 'X.Y.Z' string (re.fullmatch, ASCII digits 0-9 only); None for anything else,
       including non-strings (a hand-edited MLmodel can hold a YAML float such as 3.12)."""

   def _mlmodel_python_version(info) -> object | None:
       """info.flavors['python_function'].get('python_version'), or None if flavors / the python_function flavor /
       the key is absent OR if info.flavors or info.flavors['python_function'] is not a dict (a hand-edited MLmodel;
       treated as absent, never an AttributeError -- the module's error contract, runs.py:5-7). Returns the raw value
       (not necessarily a str)."""
   ```

   Treating a non-dict structure as absent is safe: with no tag, step (ii) then refuses; with a tag, the tag decides.

   **Why the project owns the format and parser instead of importing MLflow's:** `mlflow.utils.PYTHON_VERSION` and
   `mlflow.utils.get_major_minor_py_version` exist but live in `mlflow.utils`, which is not MLflow's documented public
   API and may move or change between MLflow versions; the guard is a contract between this project's trainer and
   scorer, so both sides use `current_python_version()` / `parse_python_major_minor()` defined here. Test T2 detects
   if MLflow's `MLmodel` format ever diverges from ours. Do NOT import those `mlflow.utils` helpers.

   Guard 14, appended after guard 13 inside `validate_run`, using `tags` and `info` (both already in scope: `info` is
   guard 10's `get_model_info` result). Steps in this exact order; every message contains the guard key
   `"python version"` (lower case):

   Steps (i)-(vi) MUST run in this order; (vi) relies on (iii)/(iv) having rejected anything unparseable. Steps
   (iii)/(iv) keep the parsed tuples, and (v)/(vi) compare those tuples; nothing is re-parsed from `str()` of a raw value.

   - (i) `tag = tags.get("python_version")`; `mlmodel = _mlmodel_python_version(info)`; `running = current_python_version()`;
     `running_mm_t = parse_python_major_minor(running)`; `tag_mm = parse_python_major_minor(tag) if tag is not None else None`;
     `mlmodel_mm = parse_python_major_minor(mlmodel) if mlmodel is not None else None`
   - (ii) if `tag is None and mlmodel is None` → `ValueError(f"python version: MLflow run {run_id} has no 'python_version' tag and its logged model's MLmodel records no python_version, so the interpreter its cloudpickle was written by is unknown")`
   - (iii) if `tag is not None and tag_mm is None` → `ValueError(f"python version: MLflow run {run_id} tag 'python_version' is {tag!r}, not a version of the form X.Y or X.Y.Z")`
   - (iv) if `mlmodel is not None and mlmodel_mm is None` → `ValueError(f"python version: the logged model of MLflow run {run_id} records MLmodel python_version {mlmodel!r}, not a version of the form X.Y or X.Y.Z")`
   - (v) if `tag is not None and mlmodel is not None and tag_mm != mlmodel_mm` → `ValueError(f"python version: MLflow run {run_id} has tag python_version {tag!r} but its logged model's MLmodel records {mlmodel!r} (inconsistent provenance)")`
   - (vi) `recorded, recorded_mm_t, source = (tag, tag_mm, "run tag") if tag is not None else (mlmodel, mlmodel_mm, "logged model's MLmodel")`
     (at this point `recorded` is a `str`, guaranteed by (iii)/(iv)); if `recorded_mm_t != running_mm_t` → `ValueError(f"python version mismatch for MLflow run {run_id}: trained under Python {recorded} (from the {source}), running Python {running}; a cloudpickled model is only loaded under the Python major.minor it was written by. Retrain under Python {running_mm} or score with Python {recorded_mm}")`, where `recorded_mm = "{}.{}".format(*recorded_mm_t)` and `running_mm = "{}.{}".format(*running_mm_t)` (e.g. `"3.12"`, `"3.14"`).

   The guard reads only run tags and `MLmodel` YAML; nothing is unpickled. `validate_run`'s return value (`ModelRun`)
   is unchanged. Placement rationale: guard 14 needs `info` (guard 10); appending after 13 keeps guards 1-13's
   numbering, which code comments, the slice-4 design note and tests already reference.

4. `tests/scoring/helpers.py::clone_run`: new keywords `mlmodel_python_version: object = None` (any YAML-serialisable
   value, so a float can be written; `None` = leave MLflow's value) and `drop_mlmodel_python_version: bool = False`
   and `mlmodel_python_function_flavor: object = None` (replaces the whole `flavors.python_function` value; `None` =
   leave it). At most one of the three may be set (assert). Applied right after `log_model`: locate the clone's `MLmodel` at
   `Path(url2pathname(urlparse(MlflowClient().get_logged_model(info.model_id).artifact_location).path)) / "MLmodel"`
   (`from urllib.parse import urlparse`, `from urllib.request import url2pathname`: `url2pathname` percent-decodes,
   which matters because the experiment's artifact location comes from `Path.as_uri()`),
   `yaml.safe_load` it, set or delete `flavors.python_function.python_version`, `yaml.safe_dump` it back (mechanism
   verified 2026-10-09 in a scratch store: `get_model_info` reads the edited value back). Module constants:
   `_RUNNING = current_python_version()`; `_OTHER_MINOR = f"{major}.{minor + 1}.0"`;
   `_OTHER_PATCH = f"{major}.{minor}.{micro + 1}"` (from `sys.version_info`, so the cases hold on the 3.12 and 3.14 CI
   legs alike).

## UX flow (pipeline)

`train_models.py` → `train_and_evaluate` → per-model run tags now include `python_version` → (later)
`score_discharges.py` → `score_run_date` → `validate_run` guards 1-13 → guard 14 → select window → only then `loader()`.
On refusal: the CLI's existing `ValueError` convention (`ERROR: <message>` on stderr, exit 1, nothing written).

## Data handling

No dataset, feature, label, split or seed changes; no leakage surface. Reproducibility improves: the training
interpreter becomes part of the run's recorded provenance. Existing runs are read, never mutated (the fallback reads
MLflow's own metadata). Tests clone runs in a temp tracking dir only.

## Failure modes

Each → `ValueError` containing `"python version"`, via the CLI's `ERROR:`/exit-1 path; no model loaded, nothing written.

- Legacy run, `MLmodel` present and same major.minor → **passes** (silently; source not reported — follow-up F2).
- Legacy run, `MLmodel` present, other major.minor (the real `readmission-risk-2b` runs under 3.14) → step (vi).
- No tag and no `MLmodel` python_version → step (ii).
- Malformed tag (`"3.x"`, `""`, `"abc"`) → (iii). An empty tag is not `None`, so it is malformed, not absent.
- `MLmodel` value non-string (YAML float `3.12`) or malformed → (iv).
- Tag/`MLmodel` disagree at major.minor → (v). Disagreement only at micro → passes (major.minor is the contract).
- `MLmodel` has no `python_function` flavor at all, or `flavors` / `flavors.python_function` is not a dict → treated as "no `MLmodel` python_version" (falls to tag, or (ii)); never an `AttributeError`.
- A guard 1-13 defect is reported first (first failure wins, existing behaviour).
- Class imbalance / empty data: not applicable (no data path changes).

## Verification criteria

Training (`tests/models/test_training.py`):

- T1 extend the existing tag loop (line ~131) with `"python_version"`, and assert its value == `current_python_version()` on both runs.
- T2 `test_python_version_tag_matches_logged_mlmodel`: for both runs, tag == `get_model_info(model_uri).flavors["python_function"]["python_version"]` (proves step (v) cannot false-fire on a fresh run; would catch an MLflow format change).

Guard cases (`GUARD_CASES`, run through `score_run_date` by the existing `test_each_guard_refuses_exactly_its_defect`
with `forbid_model_loading` + strict loader/writer; guard_key `"python version"`). Every case's `forbidden` includes
`"library version"` (proves guard 8 did not fire) plus the sub-step-specific strings below, so each case proves WHICH
step of guard 14 refused. "MLmodel = running" means the clone's `MLmodel` is left as MLflow wrote it (the running version).

| Case | Build | Extras (must appear) | Forbidden (must not appear), besides `"library version"` |
|---|---|---|---|
| G1 `14-other-minor` | `tags={"python_version": _OTHER_MINOR}`, `mlmodel_python_version=_OTHER_MINOR` | `f"trained under Python {_OTHER_MINOR} (from the run tag)"`, `f"running Python {_RUNNING}"` | `"inconsistent provenance"` |
| G2 `14-legacy-other-minor` | `drop_tags=("python_version",)`, `mlmodel_python_version=_OTHER_MINOR` | `f"trained under Python {_OTHER_MINOR} (from the logged model's MLmodel)"` | `"inconsistent provenance"` |
| G3 `14-tag-vs-mlmodel` | `tags={"python_version": _OTHER_MINOR}` (MLmodel = running) | `"inconsistent provenance"` | `"mismatch"` |
| G4 `14-malformed-tag` | `tags={"python_version": "3.x"}` | `"tag 'python_version' is '3.x'"` | `"MLmodel python_version"` |
| G5 `14-malformed-mlmodel` | `drop_tags=("python_version",)`, `mlmodel_python_version="garbage"` | `"MLmodel python_version 'garbage'"` | `"tag 'python_version'"` |
| G6 `14-no-version-anywhere` | `drop_tags=("python_version",)`, `drop_mlmodel_python_version=True` | `"is unknown"` | `"not a version of the form"` |
| G7 `14-empty-tag` | `tags={"python_version": ""}` | `"tag 'python_version' is ''"` | `"MLmodel python_version"` |
| G8 `14-mlmodel-yaml-float` | `drop_tags=("python_version",)`, `mlmodel_python_version=3.1` (a float: how an unquoted `3.10` parses) | `"MLmodel python_version 3.1,"` | `"tag 'python_version'"` |
| G9 `14-mlmodel-vs-tag` | tag inherited (= running), `mlmodel_python_version=_OTHER_MINOR` (mirror of G3: a correct tag in front of a foreign MLmodel; only step (v) refuses it) | `"inconsistent provenance"`, `f"records '{_OTHER_MINOR}'"` | `"mismatch"` |
| G10 `14-non-dict-python-function-flavor` | `drop_tags=("python_version",)`, `mlmodel_python_function_flavor="x"` (a hand-edited MLmodel whose `flavors.python_function` is a scalar: end-to-end proof of "never an AttributeError") | `"is unknown"` | `"not a version of the form"` |

`GUARD_CASES` grows by these 10 cases (38 → 48 by this list); the final count in comments and CLAUDE.md is the number
pytest collects after implementation. If MLflow's SQLite store turns an empty tag value into something other than `""`
(G7's premise), G7 is replaced by asserting whatever MLflow actually stores and the doc's failure-mode line is updated.

Positive tests (`tests/scoring/test_runs.py`, `validate_run` must return a `ModelRun`):

- P1 `test_legacy_run_without_tag_passes_on_mlmodel_fallback`: `drop_tags=("python_version",)`.
- P2 `test_patch_difference_passes`: tag and `MLmodel` both = `_OTHER_PATCH`.
- P3 `test_python_version_guard_ignores_micro_disagreement`: tag = `_RUNNING`, `MLmodel` = `_OTHER_PATCH`.
- P4 `test_tag_alone_passes_without_mlmodel_python_version`: tag = `_RUNNING` (inherited), `drop_mlmodel_python_version=True` (proves step (ii) uses `and`, not `or`).
- P5 `test_legacy_run_with_patch_difference_passes`: `drop_tags=("python_version",)`, `MLmodel` = `_OTHER_PATCH` (the fallback branch of (vi) on a passing input).

Unit (`tests/scoring/test_runs.py`):

- U1 `test_parse_python_major_minor`: `"3.14.7"`→(3,14), `"3.12"`→(3,12), `"10.1.0"`→(10,1); `None` for `"3"`, `"3.x"`, `""`, `" 3.14.7"`, `"3.14.7rc1"`, `"3.14.7.1"`, `"3.14.7\n"`, `"\uff13.\uff11\uff14"` (full-width digits), `3.12` (float), `None`.
- U2 `test_mlmodel_python_version_treats_non_dict_as_absent`: calls `_mlmodel_python_version` with a stand-in object whose `.flavors` is `{"python_function": "x"}`, `{"python_function": ["x"]}`, `"x"`, `None` and `{}` → `None` each; and `{"python_function": {"python_version": "3.12.11"}}` → `"3.12.11"`.

Existing tests unchanged and passing: `test_valid_run_passes_all_guards` (fresh runs pass guard 14),
`test_installed_versions_keys_match_training_tags` (key set still the five).

Gate: `ruff check .` + full `pytest` green on 3.14 locally.

Real-data sanity check (read-only against the real store, output to a scratch dir):

- S1 under 3.14.7 (`.venv`), reading the REAL store at the CLI's default `--tracking-dir mlflow` (read-only: `validate_run` writes nothing) and writing only under a scratch `--output-dir`, once per reference run, bound by id so a later retrain into the experiment cannot turn this into an ambiguity refusal: `python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --run-id <id> --run-date 20250630 --output-dir <scratch>/s1` for `<id>` = `2bb281f6ab4e47c588eea078cc712201` (logistic_regression) and `681107e057f84c7f9fea53883b913542` (xgboost_calibrated) → exit code 1 each, stderr contains `python version mismatch` and `trained under Python 3.12.11 (from the logged model's MLmodel), running Python 3.14.7`, and `<scratch>/s1/run_date=20250630/` does not exist afterwards. This **is** the expected outcome.
- S2 the `docs/scoring.md` 3.12 recipe, run as written except for the venv location (a scratch dir outside the repo instead of `~/.venvs/readmission-py312`): `python3.12 -m venv <scratch>/py312`, activate, the three install commands, then the S1 command for the logistic_regression run with `--output-dir <scratch>/s2` → exit code 0, and the written `predictions.parquet` equals the existing `data/predictions/run_date=20250630/predictions.parquet` (logistic_regression, run 2bb281f6…, 42 rows): same `model_name` and `model_run_id`, same `encounter_id` set, and per `encounter_id` EXACTLY equal `rank` and `risk_score` (bit-for-bit float equality, as measured in session 826a4602; any difference fails S2 and is reported). The report records the actual 3.12 interpreter version used (on this machine `python3.12` may resolve to Homebrew's 3.12.7 rather than pyenv's 3.12.11 — then S2 also exercises the patch-difference pass against the real 3.12.11-trained run). If the recipe cannot be verified (e.g. no network for pip), the final report says so and docs/scoring.md is not changed to claim more than was verified.

## Out-of-scope follow-ups (noted, not built)

- F1 Retrain `readmission-risk-2b` under Python 3.14 (fresh experiment name), re-verify `docs/results.md` numbers, regenerate README assets, update `docs/scoring.md`'s example run id — or keep a 3.12 environment for the reference runs.
- F2 Surface the recorded Python version and its source (tag vs `MLmodel` fallback) in `ModelRun` and the stdout report.
- F3 Guard `load_logged_model` itself (or route all loads through `validate_run`).
- F4 A Spark-4.0.1 / Dataproc-runtime CI leg (already named in CLAUDE.md).
- F5 Record/check the interpreter implementation and free-threaded build alongside the version.
- F6 The v2 PRD (`notes/prds/v2-mlops-keyless-scheduled-scale-up-2026-10-09.md`, owned by a separate, concurrent
  session) becomes stale at its lines ~177 ("13 guards"), ~182 ("training records neither Python version nor checks
  it") and ~188 ("all 38 guard cases are ported"). Deliberately NOT edited here (another session is writing it); the
  owner relays it. Note for that PRD: a manifest has no MLmodel fallback, so its Python-version fact maps to the tag.

## Design-check log

- **Round 1 (2026-10-09):** comprehension passed (notes: define `running_mm`/`recorded_mm`; make "MLmodel = running"
  explicit — both done). Readiness: implementation ready (watch item: whether MLflow stores `""` tags faithfully — G7
  now carries a fallback instruction). Critic: 8 gaps, all addressed in this revision: (1) non-dict `MLmodel` structure
  → treated as absent, P5 (renamed U2 in round 2); (2) non-string `MLmodel` value tested end to end → `clone_run` keyword widened to `object`,
  G8; (3) tag-only path → P4; (4) sub-step-specific `forbidden` strings per case; (5) every affected doc place named
  with its exact caveat, plus a 3.12-venv recipe outside the repo ("Documentation changes"); (6) `url2pathname`
  percent-decoding; (7) why the project owns its version format/parser rather than importing `mlflow.utils`;
  (8) counts are "as pytest collects after implementation".
- **Round 2 (2026-10-09):** readiness: implementation ready (nits folded in: Surfaces table counts, own builders for
  G cases, placement of the docs/scoring.md paragraph = immediately after the code block's closing fence, before the
  existing `--run-id` paragraph). Critic: 9 gaps, all addressed: (1) counts reconciled, P5 → U2; (2) `[0-9]` instead
  of `\d`, U1 gains full-width and trailing-newline cases; (3) slice-4 note left frozen, `runs.py` docstring pointer
  added; (4) docs/setup.md:63 comment; (5) 3.12 recipe names libomp/no-JDK and is verified by S2; (6) S1 explicit about
  the real store and the absent output dir; (7) G9 mirror case for step (v); (8) step order mandatory, (v)/(vi) use the
  already-parsed tuples; (9) implementation/free-threaded build → Out + F5.
- **Round 3 (2026-10-09):** readiness: implementation ready. Critic: 8 gaps, all addressed: (1) garbled step (vi)
  condition removed — all steps now use tuples parsed once in (i); (2) G10 drives a non-dict `python_function` flavor
  through `score_run_date` (new `clone_run` keyword); (3) the unverifiable "no JDK needed" claim dropped; (4) "run from
  the repository root"; (5) `.gitignore` claim corrected; (6) CLAUDE.md "Train models" line covered; (7) P5 legacy
  patch-difference pass; (8) ordering bullet moved above (i), U1 before U2, list formatting fixed.
- **Round 4 / 3-revision cap (2026-10-09):** readiness: implementation ready (third consecutive round; note: T2 must
  set the tracking URI itself, since the `e2e` fixture restores the original). Critic: 4 gaps, no logic holes, all
  consistency/verification-precision items: (1) v2 PRD staleness → F6, not edited (owned by a concurrent session);
  (2) `docs/results.md:49` caveat added; (3) S2 exact equality + actual 3.12 patch recorded + recipe allows any 3.12
  interpreter; (4) S1 binds both reference run ids. **Owner chose "Override and proceed anyway" at the 3-revision cap:**
  these resolutions were applied but the critic was NOT re-run on this final revision.
