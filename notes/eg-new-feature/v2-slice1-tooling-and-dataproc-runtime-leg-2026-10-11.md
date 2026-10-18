# v2 slice 1: Makefile + pre-commit, and a Dataproc-runtime-3.0 test leg (2026-10-11)

Status: design, pre-implementation (revision 4 + round-4 corrections; design check closed by owner override at the 3-revision cap, 2026-10-11). Branch `v2` (draft PR #4 into `main`).

## Why

The v2 PRD (`notes/prds/v2-mlops-keyless-scheduled-scale-up-2026-10-09.md`) defines slice 1, "Versions B, remainder +
tooling" (PRD line 359), as:

- **FR2** (PRD line 193): CI tests the cloud runtime's Spark version. Commit `c2526ab` added a Python 3.12 + 3.14
  full-suite matrix, but both legs install the main lockfile's `pyspark==4.1.3`. So CI proves the code runs on
  Python 3.12, not that it runs on Spark 4.0.1, which is what Dataproc Serverless runtime 3.0 ships. Remaining work: a
  hash-locked {Python 3.12, `pyspark==4.0.1`} set compiled under a 3.12 interpreter, and a CI leg running
  `tests/pipeline` under it. "The slice decides whether that leg replaces the current 3.12 full-suite leg or is added
  next to it."
- **FR3** (PRD line 194): the Spark job's code depends only on libraries that runtime 3.0 bundles, and ships as a
  `--py-files` zip.
- **FR0.1 / OQ6**, carried over from the v1 closeout (PRD lines 20, 175, 385): a `Makefile` (targets setup, lint,
  test, train, score, tf-validate) and pre-commit hooks running ruff and zizmor.

Owner decisions taken in this conversation (2026-10-11):

1. **FR3 is enforced by a test now.** A test fails if the Spark job's code imports anything outside the standard
   library plus an allowlist of runtime-bundled packages. Building the `--py-files` zip waits for slice 5, when the
   job first runs on Dataproc.
2. **The new leg replaces the 3.12 full-suite leg.** The owner asked whether there was any reason to keep the 3.12
   full-suite leg. The reasons found were all weak: (a) `requires-python` claims 3.12 for the whole package; (b) the
   `docs/scoring.md` recipe for scoring 3.12-trained runs, which are history only and are refused anyway by the
   fingerprint guard against the current gold table; FR16 says no 3.12 environment is needed; (c) the README and
   setup wording. In v2 only the Big Join runs on 3.12. The owner chose to **replace the leg and narrow the claims**:
   `requires-python` stays `>=3.12,<3.15`, because the pipeline subpackage must still install and run on 3.12, but
   the docs and comments now say that only `tests/pipeline` is CI-tested on 3.12.
3. **The runtime set mirrors runtime 3.0**, not just pyspark: pandas, pyarrow and numpy at the runtime's bundled minor
   versions, and the CI job runs Java 21 like the runtime.
4. **`tf-validate` is left out until slice 3**, when `terraform/` exists.

Facts the design rests on (checked 2026-10-11):

- **Google's runtime-3.0 page** (https://docs.cloud.google.com/dataproc-serverless/docs/concepts/versions/spark-runtime-3.0)
  lists latest sub-version **3.0.14 (2026-08-25)** with Apache Spark 4.0.1, Python 3.12, **Java 21**, Scala 2.13 and
  BigQuery connector 0.44.0. Its Python library list (image `serverless-spark-3.0-debian-12`, no sub-version stated,
  minor versions only) includes **pandas 2.3, pyarrow 19.0 and numpy 2.1**. py4j is not listed.
- **Feasibility probe** (scratchpad only, no repo change). Compiled with pip-compile under pyenv's Python 3.12.11, the
  set `pyspark==4.0.1, pandas==2.3.*, pyarrow==19.0.*, numpy==2.1.*, pytest` resolves to pyspark 4.0.1, py4j
  0.10.9.9, pandas 2.3.3, pyarrow 19.0.1, numpy 2.1.3, pytest 9.1.1, python-dateutil 2.9.0.post0, pytz 2026.5,
  tzdata 2026.5, six 1.17.0, iniconfig 2.3.1, packaging 26.3, pluggy 1.6.0 and pygments 2.21.0. It installs with
  `--require-hashes`, and `pip check` is clean. `pytest tests/pipeline` gives **160 passed, 0 failed in 65 s on Java
  17** (`SPARK_LOCAL_IP=127.0.0.1` was needed only because of the probe's command sandbox). The only warning is
  pandas-on-Spark's `PYARROW_IGNORE_TIMEZONE` UserWarning. **No Big Join code change is needed.**
- **What `tests/pipeline` and the pipeline package import.** `src/readmission_risk/pipeline/*.py` imports only the
  stdlib, `pyspark` and its own siblings (relative imports). `tests/pipeline` adds `pandas` (`pd.read_parquet`),
  `pytest` and `pyspark.testing.assertDataFrameEqual`. `tests/conftest.py` imports only
  `readmission_risk.pipeline.spark_session`. `scripts/run_big_join.py` imports only the stdlib and
  `readmission_risk.pipeline.*`.
- **pre-commit 4.6.2** (what pip-compile resolves today) renamed the hook language `system` to `unsupported`; `system`
  survives only as a deprecated alias (`pre_commit/clientlib.py:196`). pre-commit does **not** put its own
  interpreter's `bin/` on `PATH`, so a local hook whose entry is plain `ruff` fails when the committer's shell has no
  activated venv (for example VS Code's git panel).
- `.gitignore` ignores `.venv/`, `venv/`, `env/` and `env*/`, but **not** `.venv-dataproc/` or `.venv-lock312/`.
- The local machine has JDK 17 and 25, but **no JDK 21**.
- `scripts/train_models.py:54` defaults `--experiment-name` to `readmission-risk`. Training into an experiment that
  already holds runs makes the scorer's `--model` resolution ambiguous (CLAUDE.md, Build & test commands).

## Scope

**In:**

1. `requirements-dataproc-3.0.in` → `requirements-dataproc-3.0.txt`: a new hash-locked set compiled under Python 3.12.
   It is self-contained (installed alone, never combined with `requirements.txt`) and used only by the new CI job and
   by the local `make setup-runtime`.
2. CI (`.github/workflows/ci.yml`):
   - `lint-and-test` drops the matrix and runs on Python 3.14 only.
   - A new job, `pipeline-dataproc-runtime` (Python 3.12, Java 21, the runtime set), runs `pytest tests/pipeline`.
   - `audit-workflows` is unchanged.
3. The FR3 import-allowlist test, plus lockfile and runtime-environment assertions:
   `tests/pipeline/test_runtime_dependencies.py`.
4. A `Makefile` (GNU Make 3.81-compatible, the macOS `/usr/bin/make`; CI's ubuntu-24.04 has 4.3) with targets `help`,
   `setup`, `hooks`, `lint`, `test`, `pre-commit`, `train`, `score`, `setup-runtime`, `test-runtime`, `lock-dev` and
   `lock-runtime`.
5. `.pre-commit-config.yaml` with two local hooks, ruff and zizmor (offline). `pre-commit` is added to
   `requirements-dev.in`, and `requirements-dev.txt` is recompiled under 3.12 with the existing flags.
6. `tests/test_tooling.py`: tests for the Makefile and the pre-commit config.
7. `.gitignore`: ignore `.venv-*/`. Add matching entries to `tests/test_docs.py`'s `MUST_BE_IGNORED` and
   `MUST_NOT_BE_IGNORED`.
8. Doc and claim updates for the narrowed 3.12 coverage and the new tooling: `README.md`, `docs/setup.md`,
   `docs/scoring.md`, `pyproject.toml` (comment only), `CLAUDE.md`, and status lines in the PRD.

**Out (explicitly):**

- The `tf-validate` Makefile target (slice 3, with `terraform/`).
- Building or submitting the `--py-files` zip (slice 5).
- Any change to Big Join, model, scoring or reporting code.
- Changing the main lockfile `requirements.txt`, the Linux extras, or `lint-and-test`'s Java version (stays 17).
- A `make lock` target for the main lockfile.
- Running pre-commit in CI. CI already runs ruff and zizmor directly, so pre-commit in CI would be a second copy of the
  same check.
- `ruff format`, which stays not adopted (CLAUDE.md, Lint configuration).
- Dependabot for pip (stays excluded; see `.github/dependabot.yml`).

## Surfaces touched

| File | Change |
|---|---|
| `requirements-dataproc-3.0.in` | new |
| `requirements-dataproc-3.0.txt` | new, generated by `make lock-runtime` |
| `requirements-dev.in` | add `pre-commit` |
| `requirements-dev.txt` | recompiled by `make lock-dev` (existing pins kept; adds pre-commit and its deps; header unchanged) |
| `.github/workflows/ci.yml` | matrix removed from `lint-and-test`; new job `pipeline-dataproc-runtime`; comments updated |
| `Makefile` | new |
| `.pre-commit-config.yaml` | new |
| `.gitignore` | add `.venv-*/` under "Virtual environments" |
| `tests/pipeline/test_runtime_dependencies.py` | new |
| `tests/test_tooling.py` | new (at the `tests/` root, the same documented exception as `tests/test_docs.py`: it tests repo tooling, not a package) |
| `tests/test_docs.py` | extend `MUST_BE_IGNORED` / `MUST_NOT_BE_IGNORED` |
| `tests/scoring/helpers.py` | line 379 comment only: "every CI leg (3.12 and 3.14)" → "under whichever interpreter runs the suite (CI: 3.14)" |
| `README.md` | line 3 badge, the "Supply chain" bullet (item 6 of the reviewer tour), repository-layout block |
| `docs/setup.md` | Python sentence (line 7); new "Makefile and pre-commit" and "Dataproc runtime test environment" subsections |
| `docs/scoring.md` | line 16: the 3.12 recipe is no longer CI-tested |
| `pyproject.toml` | `requires-python` trailing comment |
| `CLAUDE.md` | Stack-table Python row (line 61), Python version note (line 65), CI bullet (line 88), v2 "Versions" (line 99) and "Carried" bullets, Build & test commands incl. the "Python version:" item (line 112) and its "both CI legs re-verify them" sentence |
| `notes/prds/v2-…-2026-10-09.md` | one status sentence each on FR0.1 (Makefile/pre-commit), FR2, FR3 and slice 1 |

## Interfaces

### 1. `requirements-dataproc-3.0.in` (exact content)

```
# Test-only mirror of the Python libraries Dataproc Serverless runtime 3.0 bundles, for CI's pipeline-dataproc-runtime
# job and `make setup-runtime` ONLY. Never installed on Dataproc (the runtime brings its own libraries; PRD FR3) and
# never combined with requirements.txt. Compile under Python 3.12 with `make lock-runtime`.
# Source: https://docs.cloud.google.com/dataproc-serverless/docs/concepts/versions/spark-runtime-3.0, checked
# 2026-10-11: latest sub-version 3.0.14 (2026-08-25) = Spark 4.0.1, Python 3.12, Java 21; Python libraries listed by
# minor version only (pandas 2.3, pyarrow 19.0, numpy 2.1), so the patch is the newest at compile time
# (`make lock-runtime` passes --upgrade, so every rerun re-resolves). Refresh by hand
# when that page changes; nothing alerts on it (see the slice-1 design note's follow-ups).
pyspark==4.0.1
pandas==2.3.*
pyarrow==19.0.*
numpy==2.1.*
# Same pytest as requirements-dev.txt, so both CI jobs run one test runner; its transitive pins may differ (accepted).
pytest==9.1.1
```

The lockfile is produced by `pip-compile --generate-hashes --output-file=requirements-dataproc-3.0.txt
requirements-dataproc-3.0.in`, run by a 3.12 interpreter. There is no `--allow-unsafe`, because the set needs neither
pip nor setuptools. The header must therefore read "autogenerated by pip-compile with Python 3.12". The `pytest` pin
must equal `requirements-dev.txt`'s (tested); when the dev pin moves, `.in` moves with it and `make lock-runtime` reruns.

### 2. `.github/workflows/ci.yml`

`lint-and-test`:
- `strategy:` removed.
- `python-version: "3.14"` (the floating minor, as in `audit-workflows`).
- Comment rewritten to say: 3.14 is the development interpreter; Python 3.12 / Spark 4.0.1 is covered for the pipeline
  only, by `pipeline-dataproc-runtime`.
- Everything else (Java 17, the three installs, `ruff check .`, `pytest`, the cache paths) is unchanged.

New job, inserted between `lint-and-test` and `audit-workflows`. The action SHAs are copied verbatim from the existing
steps:

```yaml
  # The Big Join runs on Dataproc Serverless runtime 3.0 (Python 3.12, Spark 4.0.1, Java 21), not on the 3.14 /
  # PySpark 4.1.3 stack above. This job installs a hash-locked mirror of that runtime's libraries
  # (requirements-dataproc-3.0.in says where the versions come from) and runs the pipeline tests under it. It is the only
  # Python 3.12 coverage: training, scoring and reporting run on 3.14 only.
  pipeline-dataproc-runtime:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
        with:
          persist-credentials: false

      - uses: actions/setup-java@de7274f081f381c8f8158605e0321c36c376e2e6  # v6.0.1
        with:
          distribution: "temurin"
          java-version: "21"

      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97  # v7.0.0
        with:
          python-version: "3.12"
          cache: "pip"
          cache-dependency-path: requirements-dataproc-3.0.txt

      - name: Install the runtime 3.0 dependency set
        run: |
          pip install -e .
          pip install --require-hashes -r requirements-dataproc-3.0.txt
          pip check

      - name: Test the pipeline
        env:
          # Turns on test_runtime_dependencies.py's environment check (Python, Spark, library minors, Java 21), so a
          # green run proves this job ran the intended stack, not merely that some stack passed.
          READMISSION_RISK_EXPECT_DATAPROC_RUNTIME: "3.0"
        run: pytest tests/pipeline
```

There is no `if:` anywhere, which `test_ci_audits_workflows_with_zizmor_online_and_blocking` forbids. The checkout does
not persist credentials, and every `uses:` is SHA-pinned, so the existing `test_docs.py` workflow tests keep passing
without changes.

### 3. `tests/pipeline/test_runtime_dependencies.py`

Module-level constants (`ROOT = Path(__file__).resolve().parents[2]`, `SRC = ROOT / "src"`):

```python
# PRD FR3: the Spark job may import only the stdlib, readmission_risk.pipeline, and libraries Dataproc runtime 3.0
# bundles. Keys are dotted import prefixes (matched as `m == key or m.startswith(key + ".")`), values the distribution
# name that must be hash-pinned in requirements-dataproc-3.0.txt, so the pipeline-dataproc-runtime CI job tests it.
# Widening: confirm the package is on the runtime-3.0 page, add it here AND to requirements-dataproc-3.0.in, recompile
# (e.g. a future {"google.cloud.storage": "google-cloud-storage"}; never a bare "google").
RUNTIME_BUNDLED_IMPORTS_ALLOWED: dict[str, str] = {"pyspark": "pyspark", "pandas": "pandas", "numpy": "numpy", "pyarrow": "pyarrow"}
# The code that ships to Dataproc: the package root (executed on import), the whole pipeline subpackage (recursive),
# and its CLI entry point.
SPARK_JOB_SOURCES = [
    SRC / "readmission_risk" / "__init__.py",
    *sorted((SRC / "readmission_risk" / "pipeline").rglob("*.py")),
    ROOT / "scripts" / "run_big_join.py",
]
# Runtime 3.0.14 per the page cited in requirements-dataproc-3.0.in, checked 2026-10-11.
RUNTIME_3_0 = {"python": (3, 12), "java_major": 21, "pyspark": "4.0.1", "pandas": "2.3", "pyarrow": "19.0", "numpy": "2.1"}
LOCKFILE = ROOT / "requirements-dataproc-3.0.txt"
DEV_LOCKFILE = ROOT / "requirements-dev.txt"
```

Pure helpers, defined in the test module:

```python
def source_package(path: Path) -> tuple[str, ...] | None:
    """The package a file under src/ belongs to: src/readmission_risk/pipeline/big_join.py -> ("readmission_risk",
    "pipeline"); src/readmission_risk/__init__.py -> ("readmission_risk",). None for a file outside src/ (scripts)."""
    # path.relative_to(SRC).with_suffix("").parts[:-1], or None if path is not under SRC

def forbidden_imports(source: str, *, filename: str, package: tuple[str, ...] | None) -> list[str]:
    """Return 'filename:lineno: <module as written>' for every import the Spark job may not ship with."""
```

`forbidden_imports` walks `ast.walk(ast.parse(source))`, so nested imports (inside functions, `if` blocks) are covered.
Rules, applied per `ast.Import` alias (module `alias.name`) and per `ast.ImportFrom`:

- **Absolute** (`Import`, or `ImportFrom` with `level == 0`, module `m = node.module`). Allowed iff one of:
  - `m.split(".")[0] in sys.stdlib_module_names`;
  - `m == k or m.startswith(k + ".")` for some key `k` of `RUNTIME_BUNDLED_IMPORTS_ALLOWED`;
  - `m == "readmission_risk.pipeline" or m.startswith("readmission_risk.pipeline.")`.

  Anything else is forbidden, including `from readmission_risk import pipeline`: its module is `readmission_risk`.
  Write `from readmission_risk.pipeline import …` instead.
- **Relative** (`level >= 1`). Forbidden if `package is None` or `level - 1 > len(package) - 1`. Otherwise the target is
  `package[: len(package) - (level - 1)] + tuple((node.module or "").split(".") if node.module else ())`, and the import
  is allowed iff `target[:2] == ("readmission_risk", "pipeline")`.

  So `from .schemas import X` and `from . import schemas` inside `pipeline/` are allowed, and so is `from ..x import y`
  inside a future `pipeline/sub/`. `from ..models import split` inside `pipeline/` (target
  `("readmission_risk", "models")`) is forbidden. Every relative import in `readmission_risk/__init__.py` resolves to
  `("readmission_risk", …)` outside `pipeline`, so it is forbidden unless it names `.pipeline…`.
- The display text is `"." * level + (node.module or "")` for `ImportFrom` and `alias.name` for `Import`.

`sys.stdlib_module_names` belongs to the running interpreter. Under the 3.12 job it therefore also catches a 3.13+-only
stdlib module, which is a deliberate bonus. Dynamic imports (`importlib.import_module`, `__import__`) are not detected
(see Failure modes).

Tests:

| Test | Asserts |
|---|---|
| `test_spark_job_imports_only_stdlib_the_pipeline_and_runtime_bundled_packages` | each file in `SPARK_JOB_SOURCES` exists; `forbidden_imports(path.read_text(), filename=<repo-relative>, package=source_package(path))` concatenated over all files is `[]` |
| `test_forbidden_imports_flags_what_cannot_ship` (parametrized; `package=("readmission_risk", "pipeline")` unless stated) | flagged: `import mlflow`; `from sklearn.base import X`; `def f():\n    import typer`; `import google.cloud.storage`; `from readmission_risk.models import split`; `import readmission_risk.scoring`; `from readmission_risk import pipeline`; `from ..models import split`; `from .schemas import X` with `package=None`; `from . import x` with `package=("readmission_risk",)` |
| `test_forbidden_imports_allows_what_can_ship` (parametrized, same default package) | not flagged: `import json`; `from __future__ import annotations`; `import pyspark.sql.functions as F`; `from pyspark.sql import Window`; `import pandas as pd`; `from readmission_risk.pipeline.schemas import TABLE_SCHEMAS`; `import readmission_risk.pipeline.big_join`; `from .schemas import X`; `from . import schemas`; `from ..x import y` with `package=("readmission_risk", "pipeline", "sub")` |
| `test_source_package_maps_files_to_their_package` | the two `src/` examples in `source_package`'s docstring, plus `ROOT/"scripts"/"run_big_join.py"` → `None` |
| `test_every_allowed_runtime_import_is_hash_pinned_in_the_runtime_lockfile` | for each distribution in `RUNTIME_BUNDLED_IMPORTS_ALLOWED.values()`, `LOCKFILE` has a line starting `<dist>==` whose next line, stripped, starts with `--hash=sha256:` |
| `test_runtime_lockfile_mirrors_runtime_3_0` | header contains `autogenerated by pip-compile with Python 3.12`; pins `pyspark==4.0.1` exactly; the pandas/pyarrow/numpy pins start with `<RUNTIME_3_0 minor>.`; no line starts with `mlflow==`, `scikit-learn==` or `xgboost==` |
| `test_runtime_lockfile_pins_the_dev_lockfiles_pytest` | the `pytest==X` line in `LOCKFILE` equals the one in `DEV_LOCKFILE` |
| `test_environment_is_dataproc_runtime_3_0_when_requested(spark)` | `v = os.environ.get("READMISSION_RISK_EXPECT_DATAPROC_RUNTIME", "")`. Empty → `pytest.skip`. Any value other than `"3.0"` → `pytest.fail("only '3.0' is supported …")`, so a typo cannot silently skip. `"3.0"` → builds a `mismatches: list[str]` from **every** check: Python major.minor, `pyspark.__version__ == "4.0.1"`, pandas/pyarrow/numpy minors, and the Java major from `int(spark.sparkContext._jvm.java.lang.System.getProperty("java.version").split(".")[0])`. Then `assert mismatches == []`, so a failure lists all mismatches regardless of order. `_jvm` is a private PySpark API that works in classic local mode (the fixture's); accepted and commented as fragile |

The lockfile and import tests read files only, so they run in `lint-and-test`, in the runtime job and locally.

### 4. `Makefile`

**Compatibility:** the Makefile must work under GNU Make 3.81 (macOS `/usr/bin/make`, verified on this machine) and
4.3 (ubuntu-24.04). So: no `define NAME =` (the `=` form is 3.82+), no `.ONESHELL`, no `$(file …)`, no `!=`, no
`.RECIPEPREFIX`. Recipes are POSIX `sh`. `--no-print-directory` exists in both.

Variables, all overridable (`make score RUN_DATE=20240115`):

```make
PYTHON           ?= python3.14
PYTHON312        ?= python3.12
VENV             ?= .venv
RUNTIME_VENV     ?= .venv-dataproc
LOCK_VENV        ?= .venv-lock312
GOLD_DIR         ?= data/gold/full_run_2b
REFERENCE_DATE   ?= 20260916
TEST_START_DATE  ?= 20230101
EXPERIMENT       ?= readmission-risk-v1
RUN_DATE         ?= 20250630
TRAIN_EXPERIMENT ?=
TRAIN_ARGS       ?=
SCORE_ARGS       ?=
BIN := $(VENV)/bin
```

The shared macros are exact text: single-line `define`s with no `=`, called with comma-separated arguments.

```make
# $(call check_python,<venv dir>,<major>,<minor>,<variable to override>)
define check_python
$(1)/bin/python -c 'import sys; sys.exit(sys.version_info[:2] != ($(2), $(3)))' || { echo "ERROR: $(1) is not Python $(2).$(3); delete it, or override $(4)" >&2; exit 1; }
endef

# $(call fresh_venv,<dir>,<python>,<variable>): the Makefile owns .venv-* directories and recreates them from scratch, so
# a stale venv never survives. The case guard admits only a plain .venv-* name in the repository root: the first arm
# rejects any '/' or '..' (a shell `*` also matches '/', so `.venv-x/../../y` would otherwise pass), keeping overrides
# such as RUNTIME_VENV=/ or RUNTIME_VENV=.venv-x/../.. away from rm -rf.
define fresh_venv
case "$(1)" in */*|*..*) echo "ERROR: $(3) must name a .venv-* directory in the repository root (it is deleted and recreated)" >&2; exit 1;; .venv-*) ;; *) echo "ERROR: $(3) must name a .venv-* directory in the repository root (it is deleted and recreated)" >&2; exit 1;; esac; rm -rf "$(1)" && $(2) -m venv "$(1)"
endef
```

Every target is listed in `.PHONY`, `.DEFAULT_GOAL := help`, and each target line ends with `## <description>`.
`help` is `@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'`. Recipes
call the venvs' binaries by path, so no activated venv is needed.

| Target | Recipe lines, in order |
|---|---|
| `setup` | `test -d $(VENV) \|\| $(PYTHON) -m venv $(VENV)`; `@$(call check_python,$(VENV),3,14,VENV)`; `$(BIN)/pip install -e .`; `$(BIN)/pip install --require-hashes -r requirements.txt -r requirements-linux-extras.txt`; `$(BIN)/pip install --require-hashes -r requirements-dev.txt`; `@echo "Done. Run 'make hooks' to install the git pre-commit hook."`. The developer's `.venv` is never deleted |
| `hooks` | `$(BIN)/pre-commit install --allow-missing-config`. The hook script in `.git/hooks/` is shared by every branch and worktree, and `main` has no `.pre-commit-config.yaml` until PR #4 merges; without the flag every commit on such a branch would be refused ("No .pre-commit-config.yaml file was found"). With it, those commits pass with a skip notice |
| `lint` | exactly two lines: `$(BIN)/ruff check .` and `$(BIN)/zizmor --offline .` |
| `test` | `$(BIN)/pytest` |
| `pre-commit` | `$(BIN)/pre-commit run --all-files` |
| `train` | `@test -n "$(TRAIN_EXPERIMENT)" \|\| { echo "ERROR: set TRAIN_EXPERIMENT=<a new experiment name>; training into an experiment that already holds runs makes the scorer's --model ambiguous (docs/scoring.md)" >&2; exit 1; }`; `@test "$(TRAIN_EXPERIMENT)" != "$(EXPERIMENT)" \|\| { echo "ERROR: TRAIN_EXPERIMENT must differ from the scoring experiment $(EXPERIMENT)" >&2; exit 1; }`; `$(BIN)/python scripts/train_models.py --gold-dir $(GOLD_DIR) --reference-date $(REFERENCE_DATE) --test-start-date $(TEST_START_DATE) --experiment-name $(TRAIN_EXPERIMENT) $(TRAIN_ARGS)` |
| `score` | `$(BIN)/python scripts/score_discharges.py --gold-dir $(GOLD_DIR) --experiment-name $(EXPERIMENT) --run-date $(RUN_DATE) $(SCORE_ARGS)` |
| `setup-runtime` | `@$(call fresh_venv,$(RUNTIME_VENV),$(PYTHON312),RUNTIME_VENV)`; `@$(call check_python,$(RUNTIME_VENV),3,12,PYTHON312)`; `$(RUNTIME_VENV)/bin/pip install -e .`; `$(RUNTIME_VENV)/bin/pip install --require-hashes -r requirements-dataproc-3.0.txt`; `$(RUNTIME_VENV)/bin/pip check` |
| `test-runtime` | `@test -x $(RUNTIME_VENV)/bin/pytest \|\| { echo "ERROR: run 'make setup-runtime' first" >&2; exit 1; }`; `@echo "PySpark uses JAVA_HOME=$${JAVA_HOME:-<unset: the java on PATH>}; Spark 4.0 supports JDK 17 or 21 only."`; `$(RUNTIME_VENV)/bin/pytest tests/pipeline`. It does **not** set `READMISSION_RISK_EXPECT_DATAPROC_RUNTIME`, because a laptop's JDK need not be 21 |
| `lock-dev` | `@$(call fresh_venv,$(LOCK_VENV),$(PYTHON312),LOCK_VENV)`; `@$(call check_python,$(LOCK_VENV),3,12,PYTHON312)`; `@$(LOCK_VENV)/bin/python -c 'import sys; print("pip-compile interpreter:", sys.version.split()[0], sys.executable)'`; `$(LOCK_VENV)/bin/pip install --require-hashes -r requirements-dev.txt`; `$(LOCK_VENV)/bin/pip-compile --allow-unsafe --generate-hashes --output-file=requirements-dev.txt requirements-dev.in` |
| `lock-runtime` | the first four `lock-dev` lines; then `$(LOCK_VENV)/bin/pip-compile --upgrade --generate-hashes --output-file=requirements-dataproc-3.0.txt requirements-dataproc-3.0.in`. `--upgrade` is deliberate: this set mirrors "the newest patch of each bundled minor at compile time", so every run re-resolves instead of keeping the previous pins; the `.in` constraints (exact pyspark and pytest, minor wildcards) bound what can move |

Notes:

- **Exit codes.** Make itself exits 2 whenever a recipe line fails, whatever code the line returned. Recipes `exit 1`,
  and the tests assert a non-zero return code plus the stderr text, never a specific code.
- **pip-compile keeps existing pins** of an existing output file unless `--upgrade` is passed (only `lock-runtime` passes it). So `lock-dev` adds only
  pre-commit and its dependencies, and leaves the header unchanged. A real `lock-dev` recompile still records
  `--no-index` (measured by the round-4 critic; the earlier probe header lacked it only because of how it was
  invoked). The runtime lockfile's header likewise records `--no-index` and omits `--upgrade`, so **the header is not
  the recipe**: the Makefile target is.
- **Which 3.12 compiles the lockfiles.** The lock venv installs pip-tools from the hash-pinned `requirements-dev.txt`.
  On this machine `python3.12` resolves to Homebrew's 3.12.7, not pyenv's 3.12.11. The interpreter line printed by
  `lock-*` makes the choice visible, and the lockfile header records only "Python 3.12", as it does today. **This
  slice compiles with pyenv's 3.12.11**, to match the probe and the existing lockfiles:
  `make lock-dev lock-runtime PYTHON312=$HOME/.pyenv/versions/3.12.11/bin/python`. Any 3.12.x is acceptable later.
- **Bootstrap order for this slice:** `lock-dev`, then `lock-runtime`, then `setup` (picks up pre-commit), then
  `hooks`.

### 5. `.pre-commit-config.yaml` (exact content)

```yaml
# Local hooks only. Each runs the tool from the MAIN checkout's .venv (created by `make setup`), found through git's
# common directory, so the hooks also work in linked worktrees (e.g. .claude/worktrees/*), which share the hooks but
# have no .venv of their own. The version is the hash-pinned one in requirements-dev.txt: no second, unhashed version
# source. pre-commit does not put any venv on PATH, hence the explicit path. With no .venv in the main checkout the hook
# fails ("not found", exit 127) and blocks the commit. zizmor runs offline here; CI (ci.yml) runs ruff and online zizmor
# again, so `git commit --no-verify` skips nothing CI does not catch.
repos:
  - repo: local
    hooks:
      - id: ruff
        name: ruff check
        entry: sh -c 'exec "$(git rev-parse --git-common-dir)/../.venv/bin/ruff" check --force-exclude "$@"' --
        language: unsupported
        types_or: [python, pyi, jupyter]
      - id: zizmor
        name: zizmor (offline)
        entry: sh -c 'exec "$(git rev-parse --git-common-dir)/../.venv/bin/zizmor" --offline .' --
        language: unsupported
        files: ^(\.github/|\.pre-commit-config\.yaml$)
        pass_filenames: false
```

How this behaves:

- **Finding the venv.** pre-commit runs hooks from the worktree root. `git rev-parse --git-common-dir` prints `.git`
  in the main checkout and an absolute `<main>/.git` in a linked worktree, so `<that>/../.venv` is the main checkout's
  venv in both cases. A worktree on another branch therefore uses the main checkout's tool versions; that is accepted
  and noted in the comment.
- **ruff.** ruff receives the staged `.py`, `.pyi` and `.ipynb` files (pre-commit appends them after `--`, so they
  become `"$@"`). `--force-exclude` makes ruff honour its exclusions even for explicitly passed paths, and per-file
  ignores and `known-third-party = ["mlflow"]` apply as in CI.
- **Limits, stated in docs/setup.md.** The hook lints only staged files. A commit that changes only `pyproject.toml`
  (for example ruff's own config) re-lints nothing. `make lint` is the full-repo equivalent of CI's lint.
- **zizmor** runs over the whole repo whenever any staged path is under `.github/`, or `.pre-commit-config.yaml` itself (zizmor audits it too).

### 6. `tests/test_tooling.py`

The `make` invocation is fixed:

```python
# Every variable the Makefile reads, plus make's own propagation variables: a pytest started by `make test X=y` (or a
# shell exporting VENV=...) must not change what these tests see.
MAKEFILE_VARIABLES = ("PYTHON", "PYTHON312", "VENV", "RUNTIME_VENV", "LOCK_VENV", "GOLD_DIR", "REFERENCE_DATE",
                      "TEST_START_DATE", "EXPERIMENT", "RUN_DATE", "TRAIN_EXPERIMENT", "TRAIN_ARGS", "SCORE_ARGS")
SCRUBBED_ENV = ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "GNUMAKEFLAGS", "MAKEFILES", *MAKEFILE_VARIABLES)
needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="needs make")

def run_make(*args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_ENV}
    return subprocess.run(["make", "--no-print-directory", *args], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
```

`make -n` prints every recipe line after variable and macro expansion, including the `@` lines. Assertions are
"contains" on the stdout, except the exact two-line lint check.

| Test | Needs make | Asserts |
|---|---|---|
| `test_makefile_declares_the_agreed_targets` | no | targets parsed from the `.PHONY:` line(s) == `{help, setup, hooks, lint, test, pre-commit, train, score, setup-runtime, test-runtime, lock-dev, lock-runtime}`; each has a `^<target>:.*## ` line |
| `test_makefile_avoids_features_gnu_make_3_81_lacks` | no | no line matches `^define \S+ *=`, and none contains `.ONESHELL`, `$(file `, ` != ` as an assignment (`^\S+ *!=`) or `.RECIPEPREFIX` |
| `test_make_help_lists_every_target` | yes | `run_make("help")` returns 0, and stdout names each target |
| `test_make_lint_runs_ruff_and_offline_zizmor_from_the_venv` | yes | non-empty stdout lines of `run_make("-n", "lint")` == `[".venv/bin/ruff check .", ".venv/bin/zizmor --offline ."]` |
| `test_make_score_passes_the_reference_defaults` | yes | `-n score` contains `.venv/bin/python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --experiment-name readmission-risk-v1 --run-date 20250630`; `-n score RUN_DATE=20240115 SCORE_ARGS=--show-observed-outcomes` contains `--run-date 20240115 --show-observed-outcomes` |
| `test_make_train_refuses_a_missing_or_reference_experiment_name` | yes | real runs (not `-n`) with `VENV=<tmp_path>/novenv`: no `TRAIN_EXPERIMENT` → non-zero and `set TRAIN_EXPERIMENT` on stderr; `TRAIN_EXPERIMENT=readmission-risk-v1` → non-zero and `must differ` on stderr. `-n train TRAIN_EXPERIMENT=x TRAIN_ARGS=--seed=7` contains `--experiment-name x --seed=7` |
| `test_make_hooks_tolerates_branches_without_a_config` | yes | `-n hooks` contains `.venv/bin/pre-commit install --allow-missing-config` |
| `test_make_test_runtime_runs_only_the_pipeline_tests_in_the_runtime_venv` | yes | `-n test-runtime` contains `.venv-dataproc/bin/pytest tests/pipeline` |
| `test_make_runtime_and_lock_venvs_are_recreated_and_guarded` | yes | `-n setup-runtime` contains `rm -rf ".venv-dataproc" && python3.12 -m venv ".venv-dataproc"`; real runs of `make setup-runtime` with `RUNTIME_VENV=<tmp_path>` and with `RUNTIME_VENV=.venv-x/../<relative path to tmp_path>` (computed with `os.path.relpath(tmp_path, ROOT)`) each exit non-zero with `must name a .venv-* directory`, and the sentinel file in `tmp_path` still exists; `RUNTIME_VENV=.venv-a..b` is refused too |
| `test_make_lock_targets_compile_under_python_3_12` | yes | `-n lock-runtime` contains `python3.12 -m venv ".venv-lock312"` and `.venv-lock312/bin/pip-compile --upgrade --generate-hashes --output-file=requirements-dataproc-3.0.txt requirements-dataproc-3.0.in`; `-n lock-dev` contains `.venv-lock312/bin/pip-compile --allow-unsafe --generate-hashes --output-file=requirements-dev.txt requirements-dev.in` |
| `test_pre_commit_config_is_local_hooks_from_the_main_checkouts_venv_only` | no | the comment-free, non-blank, `rstrip`ped lines equal a pinned `EXPECTED_PRE_COMMIT` list, in the style of `EXPECTED_DEPENDABOT` in `test_docs.py` |
| `test_pre_commit_is_a_hash_pinned_dev_dependency` | no | `pre-commit` is a line of `requirements-dev.in`; `requirements-dev.txt` has a `pre-commit==` line followed by a `--hash=sha256:` line |
| `test_dev_and_main_lockfiles_agree_on_shared_pins` | no | parse every `name==version` line (name normalised per PEP 503: lowercase, runs of `-_.` → `-`) from `requirements.txt` and `requirements-dev.txt`; for every name pinned in both, the versions are equal. Today that is click 8.5.0, packaging 26.3, pygments 2.21.0, and after this slice pyyaml 6.0.3, all equal (checked 2026-10-11). Reason: both are installed into the same `.venv` by separate `pip install` calls, so a divergence would silently replace the main lockfile's pin. The runtime lockfile is never co-installed and is excluded |
| `test_pre_commit_config_validates` | no | skipped if `importlib.util.find_spec("pre_commit") is None`; `[sys.executable, "-m", "pre_commit", "validate-config", ".pre-commit-config.yaml"]` (cwd=ROOT) exits 0 and its combined output has no `deprecat` (case-insensitive) |

The sentinel detail for the guard test: create `tmp_path / "sentinel"`, then call
`run_make("setup-runtime", f"RUNTIME_VENV={value}")` for each refused value, and assert that each call fails with the
message and that the sentinel still exists. The guard was verified under Make 3.81 in the scratchpad: `.venv-ok` is
admitted; `.venv-x/../../y`, `/tmp/z`, `.venv-a..b` and `x` are refused.

### 7. `.gitignore` and `tests/test_docs.py`

- `.gitignore`: add `.venv-*/` under "Virtual environments", with the comment
  `# Extra venvs the Makefile creates and recreates (.venv-dataproc, .venv-lock312)`.
- `MUST_BE_IGNORED`: add `".venv-dataproc/bin/python"` and `".venv-lock312/bin/python"`.
- `MUST_NOT_BE_IGNORED`: add `"Makefile"`, `".pre-commit-config.yaml"`, `"requirements-dataproc-3.0.txt"` and
  `"requirements-dataproc-3.0.in"`.
- Neither tool scans the extra venvs: pytest's default `norecursedirs` includes `.*`, and ruff honours `.gitignore`,
  whose new rule lands in the same change.

### 8. Documentation wording (content, not final prose)

**README.md**
- Line 3 badge: `[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](.python-version)`.
- The "Supply chain" bullet, found by its text `6. **Supply chain:**`: replace "a Python 3.12 + 3.14 matrix" with "Python 3.14 tests plus a Dataproc-runtime job (Python 3.12, PySpark 4.0.1, Java 21) for the pipeline tests, and pre-commit hooks (ruff, zizmor)".
- Layout block: add lines for `Makefile` (common tasks: setup, lint, test, train, score, runtime env, lockfiles), `.pre-commit-config.yaml` (ruff + zizmor hooks) and `requirements-dataproc-3.0.txt` (hash-locked Dataproc runtime 3.0 mirror, CI only).
- Layout block, the `.github/workflows/` line: "CI: ruff + pytest, a Dataproc-runtime pipeline job, and a zizmor workflow audit; actions pinned to commit SHAs".

**docs/setup.md**
- Line 7: CI tests 3.14. The pipeline subpackage is also CI-tested on 3.12 with PySpark 4.0.1, the stack of the Dataproc runtime the scale-up targets. The rest of the package is not tested on 3.12, although `requires-python` still admits it.
- Two new **H2** sections, inserted in this order after `## Verify the code works` and before `## Running the actual pipeline (Slice 1: Synthea generation)`:
  - `## Makefile and pre-commit` covers:
    - the target table;
    - the assumption that the venv lives at `.venv`;
    - that `make setup` does not install the hook and `make hooks` does;
    - that `pre-commit install` fails without a `.git` directory or when `core.hooksPath` is set;
    - that the hook lints only staged files;
    - that the hooks in a linked worktree use the main checkout's `.venv`, but the Makefile does not: its `VENV` default is the relative `.venv`, so in a worktree run `make lint VENV="$(git rev-parse --git-common-dir)/../.venv"` (or create a `.venv` there with `make setup`);
    - that `make hooks` installs with `--allow-missing-config`, so branches without `.pre-commit-config.yaml` (such as `main` before PR #4 merges) commit with a skip notice;
    - that GNU Make 3.81 is sufficient.
  - `## Testing against the Dataproc runtime` covers:
    - `make setup-runtime && make test-runtime`;
    - that a `python3.12` is needed (override `PYTHON312=`);
    - that the local JDK must be 17 or 21 (Spark 4.0's supported versions; `JAVA_HOME` decides; JDK 25 is not supported);
    - that Java 21 and the exact versions are asserted only in CI;
    - that `make lock-runtime` refreshes the set when the runtime page changes;
    - that `.venv-*` directories are deleted and recreated by these targets.

**docs/scoring.md**
- Line 16: append "This 3.12 recipe is not exercised by CI since 2026-10-11; CI tests only the pipeline subpackage on 3.12."

**pyproject.toml**
- The `requires-python` comment becomes "developed and CI-tested on 3.14; 3.12 kept because the pipeline subpackage runs on Dataproc runtime 3.0 (Python 3.12) and is CI-tested there (job pipeline-dataproc-runtime)".

**tests/scoring/helpers.py**
- Line 379: the comment edit listed under Surfaces touched.

**CLAUDE.md** (edits at the cited lines)
- 61, the Stack table: "CI tests **3.14**, plus `tests/pipeline` on the Dataproc-runtime-3.0 mirror (3.12 + PySpark 4.0.1)".
- 65, the Python version note:
  - the matrix sentence is replaced by the runtime job;
  - "CI does not test PySpark 4.0.1 … belongs to the cloud phase" becomes "resolved in v2 slice 1 (design note path)".
- 88, the CI bullet: "Since 2026-10-09 `lint-and-test` is a `python-version` matrix…" is replaced by the three-job description.
- 99, v2 "Versions": "(not yet built)" becomes "(landed in slice 1; FR3 enforced by `tests/pipeline/test_runtime_dependencies.py`, zip build in slice 5)".
- "Carried" bullet: "Makefile + pre-commit (slice 1, done)".
- 112, "Python version:":
  - "CI also tests 3.12" becomes "CI tests 3.12 only for `tests/pipeline`";
  - "after any recompile, both CI legs re-verify them" becomes "CI installs `requirements.txt`/`requirements-dev.txt` only on 3.14 now; `make lock-*` installs `requirements-dev.txt` on 3.12".
- Build & test: add lines for the `make` targets, the pre-commit hook and the runtime set.
- Build & test: update the test count (576 → the measured number).
- Build & test, "Dependencies": keep the caveat "do NOT run the `--no-index` command printed in the lockfile headers literally" and say it applies to all three lockfiles, the new runtime one included; the `make lock-dev` / `make lock-runtime` targets are the real recipes (the runtime header does not record `--upgrade`). Mention `requirements-dataproc-3.0.txt` there too.
- CI bullet (line 88), the cache-key sentence: add "and on `requirements-dataproc-3.0.txt` in `pipeline-dataproc-runtime`".
- Build & test, "Train models": `--gold-dir data/gold/full_run` becomes `--gold-dir data/gold/full_run_2b`. That is the gold table the `readmission-risk-v1` runs, `docs/setup.md` and the Makefile default use; a run trained on the pre-2b `full_run` is refused by the scorer's fingerprint guard. Mention `make train TRAIN_EXPERIMENT=<name>` as the equivalent.

**PRD** (`notes/prds/v2-…-2026-10-09.md`)
- FR2 (line 193): amend its last sentence, "Any recompile of the main lockfile is verified by both Python legs". Keep
  it struck through (`~~…~~`) and follow it with: "Superseded in slice 1 by owner decision (2026-10-11): the 3.12
  full-suite leg was replaced by the {3.12, pyspark 4.0.1} `tests/pipeline` job, so a main-lockfile recompile is
  verified on 3.14 only."
- The decision-table row at line 109 (Strategy B, "a 3.12 + 3.14 CI matrix"): append "Slice 1 (2026-10-11): the matrix
  became a 3.14 full suite plus the {3.12, pyspark 4.0.1} `tests/pipeline` job; the 3.12 full-suite leg was removed by
  owner decision."
- Append "Done in slice 1 (`notes/eg-new-feature/v2-slice1-tooling-and-dataproc-runtime-leg-2026-10-11.md`)" to:
  - FR2;
  - FR3, adding "zip build → slice 5";
  - the FR0.1 Makefile/pre-commit item, adding "tf-validate → slice 3; hooks target added";
  - the slice-1 line.

## UX flow

1. Developer setup. `make setup` creates `.venv` (or reuses it after the 3.14 check) and installs the three sets.
   `make hooks` installs the git hook. On each commit, ruff runs on the staged Python files and notebooks, and
   zizmor runs offline when `.github/` or `.pre-commit-config.yaml` itself changes (zizmor audits it too). `make lint test` reproduces CI's `lint-and-test` locally, with zizmor
   offline.
2. Developer, cloud-runtime check. `make setup-runtime && make test-runtime` recreates `.venv-dataproc` and runs
   `tests/pipeline` under {3.12, pyspark 4.0.1, pandas 2.3.3, pyarrow 19.0.1, numpy 2.1.3}.
3. CI on PR #4 and on pushes to `main` runs three jobs:
   - `lint-and-test`: the 3.14 full suite;
   - `pipeline-dataproc-runtime`: `tests/pipeline` on the 3.12 runtime mirror, with the environment asserted;
   - `audit-workflows`.
4. When the runtime moves (a new 3.0.x changes a bundled minor), edit the `.in` file and its source/date comment and
   `RUNTIME_3_0`, then run `make lock-runtime` and `make test-runtime`.

## Data handling

No data, label, split, feature, seed or model artifact is touched, and no train/test split code changes, so there is
no new leakage surface.

The only behaviour-relevant change is coverage. The pipeline code is now tested against Spark 4.0.1, and the rest of
the package is no longer tested on 3.12. That narrowing is the owner's explicit decision, and the docs state it.

`make train` requires an explicit `TRAIN_EXPERIMENT` and refuses the scoring experiment `$(EXPERIMENT)`
(`readmission-risk-v1` by default). It does **not** check whether some other named experiment already holds runs.
That remains the developer's responsibility, as documented in `docs/scoring.md`.

Reproducibility: all three dependency sets are hash-locked and compiled under Python 3.12, and the Makefile records
how each lockfile is produced.

## Failure modes

| Situation | Behaviour |
|---|---|
| `python3.12` not on PATH | `make setup-runtime` / `lock-*` fail at `python3.12 -m venv` ("command not found"); override with `PYTHON312=/path`; docs say so |
| `.venv` exists but is another Python | `make setup` stops with a message naming `VENV` (protects guard 14's major.minor rule) |
| `RUNTIME_VENV`/`LOCK_VENV` overridden to a non-`.venv-*` path | refused before `rm -rf` (tested) |
| A stale `.venv-dataproc` / `.venv-lock312` from an earlier attempt | deleted and recreated by `setup-runtime` / `lock-*`, so no stale package survives |
| No `.venv` in the main checkout | each hook fails with "not found" (exit 127) and blocks the commit; `make setup` fixes it |
| Commit from a linked worktree | hooks use the main checkout's `.venv` (accepted: possibly another branch's tool versions) |
| Commit on a branch without `.pre-commit-config.yaml` (e.g. `main` before PR #4 merges) | the shared hook skips with a notice (`--allow-missing-config`), so the commit is not blocked |
| `make lint`/`test`/`pre-commit` from a linked worktree with no `.venv` of its own | "No such file" for `.venv/bin/...`; documented workaround `VENV="$(git rev-parse --git-common-dir)/../.venv"`; the Makefile deliberately keeps the simple relative default |
| `make hooks` outside a git checkout or with `core.hooksPath` set | `pre-commit install` errors; `make setup` is unaffected because hook installation is a separate target |
| `git commit --no-verify`, or a commit touching only `pyproject.toml` | hooks skipped or nothing to lint; CI still runs full ruff and online zizmor; `make lint` is the local full check |
| zizmor offline passes, online fails (new advisory) | the hook is green and CI's `audit-workflows` is red; by design |
| A future pipeline import of `google.cloud.storage` / `gcsfs` (slice 5) | the FR3 test fails until a dotted-prefix → distribution entry is added and the distribution is pinned in the runtime lockfile |
| Dynamic import (`importlib.import_module("mlflow")`) | not detected by the AST check; named limitation; none exists today |
| Runtime 3.0.15 changes a bundled minor | not detected automatically; the mirror goes stale until the page is re-read (named follow-up) |
| CI runner's 3.12 patch differs from Dataproc's | accepted: Dataproc states only "3.12"; major.minor is asserted |
| The runtime job runs the wrong stack | `test_environment_is_dataproc_runtime_3_0_when_requested` fails, listing every mismatch |
| Typo in `READMISSION_RISK_EXPECT_DATAPROC_RUNTIME` | fails ("only '3.0' is supported") instead of skipping |
| Local JDK 25 for `test-runtime` | unsupported by Spark 4.0; `test-runtime` prints the `JAVA_HOME` in use and docs say 17 or 21 |
| `pip-compile` in `lock-dev` bumps an existing pin | caught by V4's diff review |
| Linux-only transitive dependency missed by macOS pip-compile (the `requirements-linux-extras.txt` precedent) | checked for the distributions this slice adds: the only platform marker is `colorama; sys_platform == "win32"` (from pytest), so nothing is Linux-only; re-checked by V8 (scoped to the added distributions); residual risk caught by V6 |
| `requirements.txt`/`requirements-dev.txt` (compiled on 3.12) no longer installed on 3.12 in CI | accepted: they are used on 3.14 (CI) and, for the dev set, on 3.12 by `make lock-*` only |
| Spark driver `BindException` in a restricted sandbox | environmental; `SPARK_LOCAL_IP=127.0.0.1` works around it |

## Verification criteria

- **V1** `ruff check .` and `pytest` (full suite, 3.14) pass. The count is 576 plus the new tests; the measured number
  goes into CLAUDE.md.
- **V2** `make lint` and `make test` pass locally, and `make test SCORE_ARGS=x` also passes, which proves the
  environment scrub. `make lint` is the check that covers the slice's new, still untracked files.
  `make pre-commit` (`pre-commit run --all-files`, tracked files only) passing proves that the hook configuration runs
  cleanly over the repository.

- **V3** `make setup-runtime && make test-runtime` passes on JDK 17: `tests/pipeline` all green, including the new
  file, and the environment test skipped.
- **V4** The `requirements-dev.txt` diff changes no existing `name==version` pin. It adds exactly cfgv 3.5.0,
  distlib 0.4.3, filelock 4.1.0, identify 2.6.20, nodeenv 1.11.0, platformdirs 4.12.4, pre-commit 4.6.2,
  python-discovery 1.6.2, pyyaml 6.0.3 and virtualenv 21.14.6 (or newer, if released since; any difference is
  reported). Only `# via` annotations of existing entries may change (measured: `packaging` gains `virtualenv`), and
  the header is unchanged. `requirements-dataproc-3.0.txt` pins the probe's versions listed in "Why" (or newer
  patches of the same minors; any difference is reported).
- **V5** Negative checks. None of them may create a branch, move any ref, or leave a commit reachable from any ref in
  the real repository. Step 3's throwaway commit lives only on a removed detached worktree's HEAD, so it is
  unreachable and gets garbage-collected.
  1. **Main-checkout hook path.** Write `ruff_v5_tmp.py` containing `import os` at the repo root, run
     `.venv/bin/pre-commit run ruff --files ruff_v5_tmp.py`, and expect a non-zero exit with `F401` in the output.
     Then delete the file. No staging or commit is involved.
  2. **Worktree hook path, a real commit attempt.**
     - Run `git worktree add --detach <scratchpad>/wt HEAD`, so no branch is created.
     - Copy `.pre-commit-config.yaml` into it: the slice is uncommitted, so HEAD has no config, and
       `--allow-missing-config` would otherwise skip the hook.
     - Create `ruff_v5_tmp.py` (`import os`) there and run `git add .pre-commit-config.yaml ruff_v5_tmp.py`.
     - Run `git commit -m tmp` and expect a non-zero exit, `F401` in the output, and `git -C <wt> rev-parse HEAD` still
       equal to the original HEAD.
     - Clean up with `git worktree remove --force <scratchpad>/wt`, then `git worktree prune`.
     - Confirm with `git worktree list` (only the main checkout remains) and `git branch` (unchanged). The hook must
       have been installed by `make hooks` first.
  3. **Missing-config path.**
     - Run `git worktree add --detach <scratchpad>/wt2 HEAD`, with no config copied in.
     - Create and `git add` a clean scratch file (`x = 1`), then run `git commit -m tmp`.
     - Expect exit 0 and pre-commit 4.6.2's notice "`.pre-commit-config.yaml` config file not found. Skipping
       `pre-commit`." (`hook_impl.py:56`; the "No .pre-commit-config.yaml file was found" text is the error printed
       without the flag).
     - Remove the worktree with `git worktree remove --force` and `git worktree prune`. The commit existed only on that
       detached HEAD and is discarded with the worktree.
  4. **FR3 bites.** Add `import mlflow` to `big_join.py`, confirm the FR3 test fails, then revert.
  5. **Environment check bites.** Run `READMISSION_RISK_EXPECT_DATAPROC_RUNTIME=3.0 make test-runtime` on the local
     JDK 17 and expect exactly one listed mismatch, the Java one.

- **V6** CI on PR #4 shows three green jobs, and `pipeline-dataproc-runtime`'s log shows the environment test **passed,
  not skipped**. **This needs a push to `origin/v2`, an outward-facing action that requires the owner's explicit
  go-ahead; until then V6 is reported as pending.**
- **V7** `tests/test_docs.py` passes with the new layout-block paths and `.gitignore` entries.
- **V8** An `importlib.metadata` scan of the requirements of the distributions **this slice adds**: every package
  pinned in `requirements-dataproc-3.0.txt` (scanned in `.venv-dataproc`), plus pre-commit and the dependencies the
  `requirements-dev.txt` diff adds (scanned in `.venv`). It finds no non-extra requirement with a `platform_system`,
  `sys_platform` or `platform_machine` marker other than pytest's `colorama; sys_platform == "win32"`. Existing
  distributions are out of scope: their platform markers (`xgboost → nvidia-nccl-cu13` Linux, covered by
  `requirements-linux-extras.txt`; mlflow's gunicorn/waitress; docker's pywin32; SQLAlchemy's greenlet, pinned in
  `requirements.in`; typer's colorama) are the main lockfile's existing concern and are unchanged.

## Out-of-scope follow-ups

- The `tf-validate` target (slice 3).
- The `--py-files` zip build and submission (slice 5), which will reuse `SPARK_JOB_SOURCES`.
- A watch on the runtime-3.0 page, so bundled-version drift turns something red.
- A CI check that each lockfile matches its `.in` (an existing gap, now three lockfiles).
- A `make lock` target for the main `requirements.txt`, including the Linux-extras re-check.
- Moving `lint-and-test` to Java 21.
- A Spark 4.0.1 leg beyond `tests/pipeline`. Not needed: only the Big Join runs on Dataproc.
- Retiring the `docs/scoring.md` 3.12 recipe once the `readmission-risk-2b` history runs are no longer worth
  documenting.
- A `make train` check that the named experiment holds no runs. It needs MLflow access, so it belongs in
  `train_models.py` itself.

## Revision log

**Round 1 → 2** (design check 2026-10-11; critic 16 gaps, readiness 5 questions):

**Critic gaps**

| # | Gap | Resolution |
|---|---|---|
| C1 | macro syntax breaks on Make 3.81 | §4: exact single-line `define` with no `=`; `$(2)`/`$(3)` major/minor; a compatibility rule plus a test |
| C2 | test_tooling inherits parent make state | §6: `run_make` scrubs `MAKEFLAGS`/`MFLAGS`/`MAKELEVEL`/`MAKEOVERRIDES`/`GNUMAKEFLAGS`/`MAKEFILES` and every Makefile variable; `--no-print-directory`; cwd=ROOT; V2 adds `make test SCORE_ARGS=x` |
| C3 | hooks break in worktrees | §5: the entry resolves `.venv` through `git rev-parse --git-common-dir`; V5 adds the worktree check |
| C4 | stale extra venvs | `fresh_venv` deletes and recreates them, guarded by a `.venv-*` name check (tested); scanning is rebutted in §7 (pytest `norecursedirs` `.*`; ruff honours `.gitignore`) |
| C5 | which 3.12 compiles | `lock-*` prints the interpreter; this slice uses pyenv 3.12.11 explicitly (§4 Notes) |
| C6 | "JDK 17+" wrong | "17 or 21"; `test-runtime` prints `JAVA_HOME`; JDK 25 is a failure-mode row |
| C7 | train guard over-claimed | the guard also refuses `$(EXPERIMENT)`; the Data handling claim is narrowed; `TRAIN_ARGS` added; exit-code note added; a follow-up is named |
| C8 | import vs distribution names | the allowlist is now a dotted prefix → distribution dict; `google.cloud.storage` example; flagged-case test |
| C9 | `SPARK_JOB_SOURCES` gaps | adds `readmission_risk/__init__.py` and uses `rglob`; package-aware relative-import resolution; `from readmission_risk import pipeline` stated as forbidden |
| C10 | stale 3.12 claims | `tests/scoring/helpers.py:379` plus CLAUDE.md lines 61/65/88/99/112 are listed; the "both legs re-verify" residual is stated |
| C11 | Linux-only dependencies | marker scan done (only win32 colorama); V8; residual stated |
| C12 | unpinned pytest | pinned to the dev lockfile's 9.1.1; a test keeps them equal |
| C13 | hook lints fewer files than CI | `jupyter` added; the staged-only and config-only limits are documented |
| C14 | skip too broad | per-test `needs_make` column |
| C15 | assertion order / `_jvm` | mismatches are collected then asserted; `_jvm` noted as fragile |
| C16 | `pre-commit install` failure in setup | separate `hooks` target; failure-mode row |

**Readiness questions**

| # | Question | Resolution |
|---|---|---|
| R1 | the `check_python` macro | answered in §4, the C1 change |
| R2 | how the tests invoke make | answered in §6, the C2 change |
| R3 | dev-lockfile header change | (corrected in round 4) the header does not change; V4 asserts unchanged pins, allowing only `# via` changes |
| R4 | two more CLAUDE.md spots | lines 61 and 112 are listed explicitly in §8 |
| R5 | setup.md placement | two H2s after "Verify the code works", order given |

**Round 2 → 3.** Readiness returned `implementation ready`; the critic found 5 gaps:

| # | Gap | Resolution |
|---|---|---|
| C2.1 | the shared hook would block commits on `main` (no config until PR #4 merges) | `make hooks` uses `pre-commit install --allow-missing-config` (flag verified in 4.6.2); test and failure-mode row |
| C2.2 | `fresh_venv` guard escapable via `.venv-x/../..` | first `case` arm rejects `*/*` and `*..*`; verified under Make 3.81; test cases added |
| C2.3 | `lock-runtime` would keep stale patches | `lock-runtime` passes `--upgrade` (the mirror's definition is "newest patch at compile time"); the `.in` comment, test and notes updated; `lock-dev` stays without it |
| C2.4 | Makefile not worktree-aware | documented limitation and `VENV=` workaround in setup.md and the failure-mode table; the hooks stay worktree-aware |
| C2.5 | CLAUDE.md "Train models" uses the pre-2b `full_run` | §8 now fixes it to `full_run_2b` |

**Round 3 → 4.** Readiness returned `implementation ready`; the critic found 6 gaps:

| # | Gap | Resolution |
|---|---|---|
| C3.1 | V8 unpassable over the whole `.venv` (existing markers) | V8 is scoped to the distributions this slice adds; the existing markers are listed as out of scope; the failure-mode row is amended |
| C3.2 | the V5 worktree check would skip (no config at HEAD) and create a branch | V5 is rewritten: `--detach`, the config copied in, `F401` asserted, HEAD unchanged, explicit cleanup and confirmation; the main-checkout path uses `pre-commit run --files` (no commit); a separate missing-config check |
| C3.3 | PRD FR2 sentence and line-109 row become false | §8 amends both explicitly |
| C3.4 | no rule that the dev and main lockfiles agree on shared pins | new `test_dev_and_main_lockfiles_agree_on_shared_pins`; today all shared pins are equal |
| C3.5 | `make pre-commit` does not see untracked new files | V2 reworded: `make lint` covers them; `make pre-commit` proves the configuration over tracked files |
| C3.6 | CLAUDE.md `--no-index` caveat and cache-key sentence | both listed in §8 |

**Round 4 (cap reached).** Readiness returned `implementation ready`; the critic found 3 gaps, all measured factual
corrections, not design changes. The owner chose "Override and proceed anyway" at the 3-revision cap (2026-10-11).
The corrections below were applied before implementation and are not re-reviewed:

| # | Gap | Resolution |
|---|---|---|
| C4.1 | `--no-index` stays in the headers; the runtime header omits `--upgrade` | §4 Notes, §8 CLAUDE.md edit (the caveat covers all three lockfiles) and R3 corrected; "the header is not the recipe" stated |
| C4.2 | V4 unpassable literally | V4 asserts no `name==version` change, the exact added set, and allows only `# via` changes |
| C4.3 | V5.3 expected the wrong message | expects "`.pre-commit-config.yaml` config file not found. Skipping `pre-commit`." |
