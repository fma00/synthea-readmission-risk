# CI hygiene bundle: lockfile-drift check, 3.14 lockfiles, Spark test heap, Dataproc mirror 4.0.2, terraform-fmt hook, doc corrections (2026-10-12)

Design doc for `/eg-new-feature`. Branch: a feature branch off `v2` (`v2-ci-hygiene`), one PR into `v2` (ruleset
`require-pr-main-and-v2` requires a PR). No GCP, no live step, no model or data change.

Shorthand: **C1** drift check, **C2** Spark test heap, **C3** mirror bump, **C4** terraform-fmt hook, **C5** doc
corrections, **C6** recompile the main and dev lockfiles under Python 3.14. "Pair" = a `.in` file and the lockfile
pip-compile builds from it. "Lockfile" = a pip-compile output: every package with an exact `==` version, one or more
`--hash=sha256:` lines, and `# via` comments naming what pulled it in.

## Why

The CLAUDE.md TODO list, triaged with the owner on 2026-10-12. The owner chose this bundle first because it is offline,
cheap, and makes CI trustworthy before slice 6 (the 200k run, the next expensive step). Items and sources:

- **C1** "a CI check that `requirements.txt` still matches `requirements.in`" — open since v1 (CLAUDE.md, Build & test
  commands, TODO); v2 PRD NFR Reproducibility: "A lockfile-drift CI check is added, because the library-version guard
  would otherwise refuse a scheduled run after drift" (guard 8, `scoring/bundle_guard.py:66-70`, compares scikit-learn,
  xgboost, mlflow, pandas, numpy against the bundle's manifest).
  **What C1 does and does not cover (round-2 finding):** C1 proves each lockfile is the current pip-compile output of
  its `.in` file. It does NOT protect against the guard-8 failure the PRD names: a properly recompiled bump of one of
  the five guarded libraries passes C1 and is then refused by guard 8 on the next scheduled run. An offline check of
  that needs the champion's library versions in a tracked file; today `deploy/champion.json` holds only the bundle URI
  and sha256, and `bundles/` is gitignored. That check is a named follow-up ("champion-compatibility check"), and the
  closeout records the PRD NFR (line 317) as **partly** met (C5, Verification 10), not as done.
- **C2** slice-5 note, "Added by 5b" (4): `pipeline-dataproc-runtime` hit `java.lang.OutOfMemoryError: Java heap space`
  once. Evidence (run 38253244020, attempt 1, job 114824614958): `tests/pipeline/test_big_join.py::test_big_join_local_str_paths_equal_path_objects`
  failed at broadcast 1786 / stage 2106 after 316 tests had passed in the one session-scoped SparkSession
  (`tests/conftest.py:6-10`). Measured 2026-10-12: that session's driver heap is the default **1.0 GiB**
  (`Runtime.maxMemory()`); `make_spark_session(conf={"spark.driver.memory": "2g"})` gives **2.0 GiB** (honoured because
  PySpark passes builder conf to spark-submit before the JVM starts).
- **C3** slice-5 note, "Added by 5b" (2): Dataproc runtime 3.0 ran **Spark 4.0.2** in the 5b batch (`spark_version`
  4.0.2 recorded in the gold metadata; Results line "PASS L6 … (spark 4.0.2)"), while the CI mirror pins pyspark 4.0.1.
  Google's runtime-3.0 page, re-checked 2026-10-12, still lists 3.0.14 = Spark 4.0.1 (it lags the live runtime).
  pyspark 4.0.2 is on PyPI (uploaded 2026-02-05). Owner decision 2026-10-12: **bump the mirror to 4.0.2.**
- **C4** CLAUDE.md TODO "add a pre-commit hook for `terraform fmt -check` on `infra/`". Owner decision 2026-10-12:
  **skip with a visible notice when `terraform` is not on PATH** (CI's `terraform-validate` remains the gate).
- **C5** CLAUDE.md slice-5 follow-up (5) and slice-5 note "CLAUDE.md / PRD corrections": `generation.py`'s comment names
  Synthea v4.0.0; the PRD says "deterministic per seed".
- **C6** Owner request 2026-10-12: compile `requirements.txt` and `requirements-dev.txt` under **Python 3.14** (the
  interpreter that installs them), not 3.12. The Dataproc mirror stays on 3.12 (it describes a 3.12 runtime).

## Scope

In:
1. C1: a new offline test module `tests/test_lockfiles.py` checking all three pairs plus the hand-maintained
   `requirements-linux-extras.txt`.
2. C6: Makefile `lock-main` (new) and `lock-dev` (changed) compile under 3.14, macOS only; both lockfiles recompiled once.
3. C2: the session `spark` fixture gets a 2 GiB driver heap (nothing else; see the C2 measurement); a test asserts it.
4. C3: `requirements-dataproc-3.0.in` → `pyspark==4.0.2`, `make lock-runtime`, `RUNTIME_3_0["pyspark"]` → `"4.0.2"`.
5. C4: a fourth local pre-commit hook `terraform-fmt`.
6. C5: comment/doc corrections (no behaviour change).
7. Fix the existing `_pins` regex blind spot (pins with extras, e.g. `google-api-core[grpc]==2.42.0`, are skipped) in
   `tests/test_tooling.py:67-70` and `tests/pipeline/test_runtime_dependencies.py:94-96`.
8. Docs: every row of "Documentation surfaces (exact changes)" below, plus CLAUDE.md (Verification 10).

Out (explicitly):
- A CI job that re-runs pip-compile (owner chose offline-only; see Data handling for what that cannot catch).
- Any version change in `requirements.txt` / `requirements-dev.txt` (C6 keeps every pin; no `--upgrade`).
- Changing `requires-python` (stays `>=3.12,<3.15`: the package is still installed on 3.12 by the Dataproc job).
- Upgrading pandas/numpy (the standing pandas-3 TODO), the Linux extras' content, Dataproc runtime sub-version recording
  (slice 6, FR14), and every slice-6 / infra item from the triage.

## Surfaces touched

| File | Change |
|---|---|
| `tests/test_lockfiles.py` | **new** (C1) |
| `tests/test_tooling.py` | `EXPECTED_TARGETS` += `lock-main`; `MAKEFILE_VARIABLES` += `LOCK314_VENV`; `EXPECTED_PRE_COMMIT` += the C4 hook lines; `test_make_lock_targets_compile_under_python_3_12` replaced (below); `test_pre_commit_config_is_local_hooks_from_the_main_checkouts_venv_only` renamed `test_pre_commit_config_is_exactly_the_expected_local_hooks` (its body unchanged; the terraform hook does not run from `.venv`); new C4 tests; `_pins` regex fix |
| `tests/pipeline/test_runtime_dependencies.py` | `RUNTIME_3_0["pyspark"]` → `"4.0.2"`, comment line 48; `_pins` regex fix |
| `tests/pipeline/test_spark_session.py` | new test for the fixture heap (C2) |
| `tests/conftest.py` | `spark` fixture passes `conf=TEST_SPARK_CONF` (C2) |
| `Makefile` | `LOCK314_VENV ?= .venv-lock314`; new `lock-main`; `lock-dev` moves to 3.14; both macOS-guarded; `.PHONY`; comment above the lock targets; `hooks` description |
| `requirements.txt`, `requirements-dev.txt` | recompiled under 3.14 (C6) |
| `requirements-dataproc-3.0.in`, `requirements-dataproc-3.0.txt` | C3 |
| `.pre-commit-config.yaml` | C4 hook; header comment (lines 1-6) |
| `.gitignore` line 16 comment | name `.venv-lock314` too |
| `src/readmission_risk/pipeline/generation.py:20-21` | C5 comment |
| `README.md`, `docs/setup.md`, `docs/scoring.md`, `ci.yml` comments, v2 PRD, slice-5 note, CLAUDE.md | "Documentation surfaces (exact changes)" |

## Interfaces

### C1 — `tests/test_lockfiles.py`

Module docstring: sits at the `tests/` root (like `test_tooling.py`) because it tests repository files; needs no JVM,
no data, no network; imports only stdlib, `pytest` and `packaging` (hash-pinned in `requirements-dev.txt` as a pytest
dependency). Runs in CI's `lint-and-test` job (full suite on 3.14), NOT in `pipeline-dataproc-runtime` (that job runs only
`tests/pipeline`). The docstring also states the blind spot (Data handling).

```python
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
# major.minor of .python-version (e.g. "3.14.7" -> "3.14"): the main and dev lockfiles follow the dev interpreter.
DEV_PYTHON = ".".join((ROOT / ".python-version").read_text().strip().split(".")[:2])

@dataclass(frozen=True)
class LockPair:
    in_file: str      # e.g. "requirements.in"
    lock_file: str    # e.g. "requirements.txt"
    python: str       # the interpreter pip-compile must have run under (header line 2)
    command: str      # the pip-compile invocation pip-compile records (header line 5, after "#    ")
    recipe: str       # the make target that recompiles it, named in failure messages

LOCK_PAIRS = (
    LockPair("requirements.in", "requirements.txt", DEV_PYTHON,
             "pip-compile --generate-hashes --no-index --output-file=requirements.txt requirements.in", "make lock-main"),
    LockPair("requirements-dev.in", "requirements-dev.txt", DEV_PYTHON,
             "pip-compile --allow-unsafe --generate-hashes --no-index --output-file=requirements-dev.txt requirements-dev.in",
             "make lock-dev"),
    LockPair("requirements-dataproc-3.0.in", "requirements-dataproc-3.0.txt", "3.12",
             "pip-compile --generate-hashes --no-index --output-file=requirements-dataproc-3.0.txt requirements-dataproc-3.0.in",
             "make lock-runtime"),
)
LINUX_EXTRAS = "requirements-linux-extras.txt"

@dataclass(frozen=True)
class Pin:
    version: str
    parents: frozenset[str]   # raw `# via` entries: "-r requirements.in", "google-api-core", "google-api-core[grpc]"
    n_hashes: int

def parse_lockfile(text: str) -> dict[str, Pin]: ...
def parse_in_file(text: str) -> list[Requirement]: ...
def drift_problems(in_text: str, lock_text: str, *, pair: LockPair) -> list[str]: ...
```

The header's `--no-index` is an artefact of how pip-compile records itself (CLAUDE.md: never run that command
literally); D2 only compares the recorded text. It catches a recompile with different flags, e.g. `requirements-dev.txt`
without `--allow-unsafe`, which would silently drop the pip/setuptools pins.

`parse_lockfile` (line-based; keys are `canonicalize_name(name)`, extras stripped):
- A pin line fully matches `^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;\\=][^\s;\\]*)\s*(?:;[^\\]*)?(?:\\)?\s*$` (extras allowed and ignored; the version
  cannot start with `=`, so `foo===1.0` is not a pin; after the version only an optional `; marker`, an optional
  trailing `\` and whitespace may follow). Checked 2026-10-12: it matches all 148 pin lines of the four current files and
  rejects every case in `test_parse_lockfile_rejects_unrecognised_lines`. It starts a new current entry with no parents and 0 hashes. A second pin of the same
  canonical name raises `ValueError(f"duplicate pin: {name}")`.
- A stripped line starting `--hash=sha256:` increments the current entry's `n_hashes` (outside an entry:
  `ValueError(f"hash line outside a pin: {line}")`). Any other stripped line starting `-` (e.g. `--index-url`,
  `--extra-index-url`, `-e`) raises `ValueError(f"unsupported lockfile option line: {line}")` wherever it appears (this
  project uses none; making them an error keeps the parser honest).
- Any other non-blank line whose stripped form does not start with `#` and that is not a pin at column 0 (the pin regex
  is anchored at `^`, so an indented pin does not match) raises `ValueError(f"unrecognised lockfile line: {line}")`:
  e.g. `foo>=1.0`, `foo===1.0`, an indented `  alpha==1.0`, a direct-URL pin `name @ https://…`, stray text. This keeps
  a following hash line from being counted under the previous pin.
- Comments while an entry is current: `# via X` (single-line form) adds `X`; a bare `# via` opens a list and each
  following `#   X` line adds `X`; any other comment line closes the list without ending the entry (pip-compile writes
  the unsafe-packages section header as a comment). Blank lines are ignored. Comments before the first pin (the header)
  are ignored here.

`parse_in_file`: strip everything from the first `#`; skip blank lines; a line starting with `-` (e.g. `-r`, `-c`)
raises `ValueError(f"unsupported .in directive: {line}")` (none exist today); every other line is `Requirement(line)`.

`drift_problems` returns strings, empty when clean. `<name>` is always `canonicalize_name(...)` of the name (so `Typer`
and `typer` both print `typer`); `<in>` is `pair.in_file`. `lines = lock_text.split("\n")`; a missing line counts as `""`.
Messages, exact, in this order:

| Rule | Check | Message |
|---|---|---|
| D1 interpreter | `lines[1] == f"# This file is autogenerated by pip-compile with Python {pair.python}"` | `f"D1 header line 2 is {found!r}, expected {expected!r}"` |
| D2 flags | `lines[4] == f"#    {pair.command}"` | `f"D2 header line 5 is {found!r}, expected {expected!r}"` |
| D3 pinned | for each `.in` requirement, in file order: name is a key | `f"D3 {name}: in {in} but not pinned"` |
| D3 satisfied | `r.specifier.contains(pin.version, prereleases=True)` (unpinned entries such as `pytest` always pass) | `f"D3 {name}: pinned {version} does not satisfy {r.specifier}"` |
| D3 marker | `r.marker is None` | `f"D3 {name}: environment markers are not supported in {in}"` |
| D4 direct set | `direct = {n for n, p in pins.items() if f"-r {in}" in p.parents}` vs the `.in` names; one message per name in the symmetric difference, sorted by name | in `.in` only: `f"D4 {name}: in {in} but not marked '# via -r {in}'"`; in lock only: `f"D4 {name}: marked '# via -r {in}' but not in {in}"` |
| D5 has parent | per pin in file order: `p.parents` non-empty | `f"D5 {name}: no '# via' annotation"` |
| D6 parent pinned | per pin, per parent sorted: a parent not starting `-r ` has its name part (`^[A-Za-z0-9_.\-]+`) canonicalized and present as a key | `f"D6 {name}: '# via' parent {parent!r} is not pinned"` |
| D7 hashed | per pin: `n_hashes >= 1` | `f"D7 {name}: no --hash line"` |

D3 per requirement: the marker check first (a requirement with a marker gets only the marker message); otherwise the
pinned check; only a pinned requirement gets the satisfied check. D5-D7 are evaluated pin by pin in lockfile order, D5
then D6 then D7 for each pin.

Tests in the module:
- `test_lock_pair_has_no_drift[<lock_file>]` — parametrized over `LOCK_PAIRS` (ids = lock file names); reads both
  files from `ROOT`; asserts `drift_problems(...) == []` with the message
  `"\n".join(problems) + f"\nfix: edit {pair.in_file} if needed, then run {pair.recipe} (on macOS, see docs/setup.md)"`.
- `test_linux_extras_are_marked_hashed_and_not_in_the_main_lockfile` — `parse_lockfile` on the extras file (no D-rules:
  it is hand-written and has no `# via`); every raw line of the file that matches the pin regex (re-scanned from the
  text; `Pin` stores no marker) contains `; platform_system == "Linux"`; every pin has
  `n_hashes >= 1`; no pin's name is a key of the parsed `requirements.txt`. The last assertion's message:
  `f"{names} pinned in both {LINUX_EXTRAS} and requirements.txt; was requirements.txt compiled on Linux? (make lock-main must run on macOS)"`.
- Unit tests on synthetic text. The fixture (a module constant) is a lockfile for pair
  `LockPair("x.in", "x.txt", "3.14", "pip-compile --generate-hashes --no-index --output-file=x.txt x.in", "make x")`:
  ```
  #
  # This file is autogenerated by pip-compile with Python 3.14
  # by the following command:
  #
  #    pip-compile --generate-hashes --no-index --output-file=x.txt x.in
  #
  alpha==1.0 \
      --hash=sha256:aa
      # via -r x.in
  beta==2.0 \
      --hash=sha256:bb
      # via alpha
  ```
  and `x.in` = `alpha==1.0\n`. Each test changes one thing and asserts the returned list equals exactly the listed
  message(s):
  - `test_clean_fixture_has_no_problems` → `[]`
  - `test_d1_wrong_python_header` (line 2 says 3.12) → `["D1 header line 2 is '# This file is autogenerated by pip-compile with Python 3.12', expected '# This file is autogenerated by pip-compile with Python 3.14'"]`
  - `test_d1_d2_truncated_lockfile` (lock text `"#\n"`) → D1 and D2 with `found=''`, then `"D3 alpha: in x.in but not pinned"`, `"D4 alpha: in x.in but not marked '# via -r x.in'"`
  - `test_d2_flags_changed` (line 5 drops `--generate-hashes`) → one D2 message
  - `test_d3_in_pin_bumped_without_recompile` (`x.in` = `alpha==1.1`) → `["D3 alpha: pinned 1.0 does not satisfy ==1.1"]`
  - `test_d3_and_d4_requirement_added_to_in` (`x.in` += `Gamma`) → `["D3 gamma: in x.in but not pinned", "D4 gamma: in x.in but not marked '# via -r x.in'"]`
  - `test_d4_requirement_removed_from_in` (`x.in` empty) → `["D4 alpha: marked '# via -r x.in' but not in x.in"]`
  - `test_d3_marker_rejected` (`x.in` = `alpha==1.0 ; sys_platform == "linux"`) → `['D3 alpha: environment markers are not supported in x.in']`
  - `test_d5_pin_without_via` (beta's via line removed) → `["D5 beta: no '# via' annotation"]`
  - `test_d6_parent_missing` (beta via `delta`) → `["D6 beta: '# via' parent 'delta' is not pinned"]`
  - `test_d7_pin_without_hash` (beta's hash line removed) → `["D7 beta: no --hash line"]`
  - `test_parse_lockfile_rejects_duplicate_pins`, `test_parse_lockfile_rejects_option_lines` (`--index-url https://x`),
    `test_parse_in_file_rejects_directives` (`-r other.in`) → `pytest.raises(ValueError, match=...)` with the messages above
  - `test_parse_lockfile_rejects_unrecognised_lines`, parametrized over `foo>=1.0`, `foo===1.0`, `  alpha==1.0`,
    `name @ https://example.invalid/x.whl`, `stray text`, `alpha==1.0 junk` (each appended to the fixture) → `ValueError` matching
    `unrecognised lockfile line`
  - `test_parse_lockfile_reads_extras_and_multiline_via`: the clean fixture with these lines appended
    ```
    google-api-core[grpc]==2.42.0 \
        --hash=sha256:cc
        # via
        #   -r x.in
        #   alpha
        #   beta
    grpcio==1.0 \
        --hash=sha256:dd
        # via google-api-core[grpc]
    ```
    and `x.in` = `alpha==1.0\ngoogle-api-core[grpc]==2.42.0\n`: `parse_lockfile(...)["google-api-core"]` equals
    `Pin("2.42.0", frozenset({"-r x.in", "alpha", "beta"}), 1)`, and `drift_problems(...)` on the whole text returns `[]`
    (in particular no D6 message for grpcio's `google-api-core[grpc]` parent)
  - `test_wildcard_specifier_is_satisfied` (`x.in` = `alpha==1.*`) → `[]`; with alpha pinned `2.0` →
    `["D3 alpha: pinned 2.0 does not satisfy ==1.*"]`

Pre-implementation check (done 2026-10-12, prototype in the session scratchpad, not committed): rules D3-D7 report zero
problems on all three current lockfiles (109 / 24 / 14 pins, 13 / 5 / 5 direct) once the extras-aware pin regex is used;
the recorded commands on line 5 equal the three `command` strings above. D1 fails on the two main lockfiles until C6
recompiles them (by design: C1 and C6 land together, in that order within one commit or C6 first).

### `_pins` fix (existing helpers)

`tests/test_tooling.py:67-70` and `tests/pipeline/test_runtime_dependencies.py:94-96` change their regex to
`^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;\\=][^\s;\\]*)`, keying by the bare name (extras dropped): `test_tooling`'s stays
PEP 503-normalised, `test_runtime_dependencies`' stays "as written" (its docstring updated: "by the name as written,
extras dropped"). Effect: `test_dev_and_main_lockfiles_agree_on_shared_pins` now also sees `google-api-core` and
`google-auth`; the dev lockfile pins neither, so its result is unchanged. Neither helper is replaced by
`parse_lockfile` (they live in packages run under different CI jobs; one regex line each is the smaller change).

### C6 — Makefile

One new variable line, inserted after the existing `LOCK_VENV        ?= .venv-lock312` (which stays unchanged and is
used by `lock-runtime` only). No inline comment: GNU Make would keep the spaces before an inline `#` in the value.
```make
LOCK314_VENV     ?= .venv-lock314
```

```make
# requirements.txt and requirements-dev.txt are compiled under Python 3.14, the interpreter that installs them (since
# 2026-10-12), and on macOS: pip-compile resolves environment markers for the host only, and requirements-linux-extras.txt
# holds what a macOS compile cannot see (on Linux, nvidia-nccl-cu13 would land in requirements.txt unmarked).
# requirements-dataproc-3.0.txt is compiled under 3.12, the Dataproc runtime's interpreter. The header records how
# pip-compile was invoked, not this recipe: run these targets rather than the header's command. tests/test_lockfiles.py
# checks the result.
lock-main: ## Recompile requirements.txt under Python 3.14 on macOS, keeping existing pins
	@test "$$(uname -s)" = Darwin || { echo "ERROR: run lock-main on macOS (see the comment above lock-main in the Makefile)" >&2; exit 1; }
	@$(call fresh_venv,$(LOCK314_VENV),$(PYTHON),LOCK314_VENV)
	@$(call check_python,$(LOCK314_VENV),3,14,PYTHON)
	@$(LOCK314_VENV)/bin/python -c 'import sys; print("pip-compile interpreter:", sys.version.split()[0], sys.executable)'
	$(LOCK314_VENV)/bin/pip install --require-hashes -r requirements-dev.txt
	$(LOCK314_VENV)/bin/pip-compile --generate-hashes --output-file=requirements.txt requirements.in
	@echo "requirements-linux-extras.txt is hand-maintained: if a Linux-only dependency changed, update it (docs/setup.md)."

lock-dev: ## Recompile requirements-dev.txt under Python 3.14 on macOS, keeping existing pins
	@test "$$(uname -s)" = Darwin || { echo "ERROR: run lock-dev on macOS (see the comment above lock-main in the Makefile)" >&2; exit 1; }
	@$(call fresh_venv,$(LOCK314_VENV),$(PYTHON),LOCK314_VENV)
	@$(call check_python,$(LOCK314_VENV),3,14,PYTHON)
	@$(LOCK314_VENV)/bin/python -c 'import sys; print("pip-compile interpreter:", sys.version.split()[0], sys.executable)'
	$(LOCK314_VENV)/bin/pip install --require-hashes -r requirements-dev.txt
	$(LOCK314_VENV)/bin/pip-compile --allow-unsafe --generate-hashes --output-file=requirements-dev.txt requirements-dev.in
```

`lock-main` goes before `lock-dev` in the Makefile, both replacing the current lock-dev block (lines 103-110; the
existing comment at 103-104 is replaced by the one above). `.PHONY` gains `lock-main`. `lock-runtime` is unchanged
(3.12, `--upgrade`, no OS guard: the mirror has no platform-conditional dependency). It keeps installing
`requirements-dev.txt` into the 3.12 venv to get pip-compile. The pip-compile flags of `lock-main` / `lock-dev` are
exactly the ones recorded in the current headers, so the headers change only in "Python 3.14" (D2 checks this).

**Python 3.12 install path (measured 2026-10-12, scratchpad copies):** the 3.14-compiled `requirements.txt` +
`requirements-linux-extras.txt` + `requirements-dev.txt` install with `--require-hashes` on Python 3.12.11 (Homebrew,
macOS arm64), `pip install -e .` included, and `pip check` is clean. So `docs/scoring.md`'s 3.12 recipe and
`make lock-runtime` keep working today. This is a property of today's dependency set, not a guarantee: a future
recompile can drop a dependency that only Python < 3.13 needs (the recompile already removes three such `# via`
entries for `typing-extensions`, which stays pinned through other parents). `docs/scoring.md`'s 3.12 paragraph gains:
"Since 2026-10-12 the lockfiles are compiled under Python 3.14; installing them on 3.12 was verified on that date, not
continuously. If pip reports a missing hash or `pip check` fails, the 3.14 compile dropped a dependency that only 3.12
needs." `requires-python = ">=3.12,<3.15"` stays: it describes the package (installed on 3.12 by the Dataproc job), and
`pyproject.toml` declares no dependencies.

`tests/test_tooling.py`: replace `test_make_lock_targets_compile_under_python_3_12` with
`test_make_lock_targets_compile_under_the_intended_python`, asserting in the `make -n` output:
- `lock-runtime`: contains `python3.12 -m venv ".venv-lock312"` and the existing `--upgrade` pip-compile line; does not contain `uname`;
- `lock-dev`: contains `test "$(uname -s)" = Darwin`, `python3.14 -m venv ".venv-lock314"` and `.venv-lock314/bin/pip-compile --allow-unsafe --generate-hashes --output-file=requirements-dev.txt requirements-dev.in`;
- `lock-main`: contains `test "$(uname -s)" = Darwin`, `python3.14 -m venv ".venv-lock314"` and `.venv-lock314/bin/pip-compile --generate-hashes --output-file=requirements.txt requirements.in`, and does NOT contain `--upgrade`.
Add a separate test `test_make_lock_main_venv_is_guarded(tmp_path)` with
`@pytest.mark.skipif(sys.platform != "darwin", reason="lock-main venv guard: macOS only (the OS guard refuses first elsewhere)")` (on Linux the OS guard refuses first), repeating the existing
test's three refusal values and sentinel check for `make lock-main LOCK314_VENV=<value>`; the existing
`test_make_runtime_and_lock_venvs_are_recreated_and_guarded` is unchanged; add `test_make_lock_main_refuses_off_macos`, with
`@pytest.mark.skipif(sys.platform == "darwin", reason="lock-main OS guard: off-macOS only")`, asserting a non-zero exit and
"run lock-main on macOS" in stderr (it runs in CI's Linux `lint-and-test`).

Expected recompile result (measured 2026-10-12 in a scratchpad copy): identical package set, identical versions, **zero
hash-line changes**; `requirements.txt` differs only in the header line 2 and in `typing-extensions`' `# via` list losing
`aiohttp`, `aiosignal`, `starlette`; `requirements-dev.txt` differs only in header line 2. Any other diff at
implementation time stops the work and is reported to the owner.

### C2 — test SparkSession heap

Measured 2026-10-12 (laptop, Python 3.14.7, pyspark 4.1.3, a full `pytest tests/pipeline` = 316 tests at the default
1 GiB heap, via a scratchpad pytest plugin; not committed):

| Setting | max heap | peak in use between tests | retained after `System.gc()` at the end |
|---|---|---|---|
| defaults | 1024 MiB | 910 MiB | 200 MiB |
| `spark.sql.ui.retainedExecutions=50`, `spark.ui.enabled=false` | 1024 MiB | 911 MiB | 175 MiB |

So the session does not accumulate much (≈200 MiB live after GC; the UI-retention cap saves ≈25 MiB) and the
retention settings are **not** adopted. The heap routinely fills to ~90% between collections; the CI OOM (Python 3.12,
Spark 4.0.1, a Linux runner) is consistent with a transient peak in a heavy test (the failing test runs `build_big_join`
twice) not fitting in 1 GiB. Doubling the heap addresses that directly.

`tests/conftest.py`:
```python
# One JVM serves the whole test session. Its default 1 GiB driver heap ran out once in CI (run 38253244020, a Big Join
# test after 316 pipeline tests). Measured 2026-10-12: ~200 MiB stays live after GC, but the heap fills to ~900 MiB
# between collections, so the failure was a transient peak, not a leak (notes/eg-new-feature/ci-hygiene-lockfiles-2026-10-12.md).
# spark.driver.memory only takes effect if this fixture is the first code in the pytest process to start the JVM:
# a test that calls make_spark_session() itself before this fixture runs would start a 1 GiB JVM that every later
# session reuses.
TEST_SPARK_CONF = {"spark.driver.memory": "2g"}

@pytest.fixture(scope="session")
def spark():
    session = make_spark_session("test", conf=TEST_SPARK_CONF)
    yield session
    session.stop()
```
No change to `make_spark_session` / `builder_settings` (production defaults unchanged; `builder_settings` already
accepts and sorts arbitrary conf and only rejects a non-UTC time zone). Only `tests/conftest.py:8` starts the JVM in the
pytest process today (`tests/pipeline/tz_agreement_probe.py` runs in a subprocess; `test_big_join.py:1434` reuses the
fixture's session).

`tests/pipeline/test_spark_session.py` gains:
```python
def test_session_fixture_has_a_two_gib_driver_heap(spark):
    from tests.conftest import TEST_SPARK_CONF

    max_heap = spark.sparkContext._jvm.java.lang.Runtime.getRuntime().maxMemory()
    assert max_heap >= 1.75 * 2**30, (
        f"driver heap is {max_heap / 2**30:.2f} GiB, not 2 GiB: was the JVM started before the session fixture "
        "(a direct make_spark_session() call in a test)? See tests/conftest.py"
    )
    assert spark.sparkContext.getConf().get("spark.driver.memory") == TEST_SPARK_CONF["spark.driver.memory"]
```
(`_jvm` is private PySpark API, already used the same way at `test_runtime_dependencies.py:195`.)

### C3 — mirror bump

All three changes go in ONE commit (the lockfile, its `.in` and the asserted constant must agree at every commit):
- `requirements-dataproc-3.0.in`: `pyspark==4.0.2`; its comment lines 4-7 become:
  ```
  # Library minors: Google's runtime-3.0 page, https://docs.cloud.google.com/dataproc-serverless/docs/concepts/versions/spark-runtime-3.0,
  # checked 2026-10-12: latest listed sub-version 3.0.14 (2026-08-25) = Python 3.12, Java 21, pandas 2.3, pyarrow 19.0,
  # numpy 2.1 (listed by minor only, so the patch is the newest at compile time; `make lock-runtime` passes --upgrade).
  # Spark patch: what the live runtime ran, because the page lags. The page lists Spark 4.0.1; the 2026-10-12 pilot batch
  # ran Spark 4.0.2 (gold metadata spark_version; notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md, L6).
  ```
  and line 8 ("# Refresh by hand when that page changes; nothing alerts on it (see the slice-1 design note's
  follow-ups).") becomes "# Refresh by hand when the page changes a library minor, or when a batch's gold metadata
  records a new Spark patch; nothing alerts on either (see the slice-1 design note's follow-ups)."
- `make lock-runtime` (Python 3.12, `--upgrade`): pandas/pyarrow/numpy/pytest/py4j may move to newer patches within
  their pins. The resulting versions of pyspark, py4j, pandas, pyarrow, numpy and pytest are recorded in this note's
  Results (the `--upgrade` outcome depends on the date). pytest must stay equal to the dev lockfile's (existing test
  `test_runtime_lockfile_pins_the_dev_lockfiles_pytest`); if `--upgrade` moves it, the `.in`'s exact `pytest==` pin
  already holds it, so it cannot.
- `tests/pipeline/test_runtime_dependencies.py:48-49`: `RUNTIME_3_0["pyspark"] = "4.0.2"`; comment line 48 becomes
  `# Runtime 3.0 as run on 2026-10-12 (Spark 4.0.2 measured; library minors from Google's page): requirements-dataproc-3.0.in.`
- CI: besides the comment edits, `ci.yml`'s two pytest steps gain `-rs` (`run: pytest -rs` at line 64 and
  `run: pytest -rs tests/pipeline` at line 99), so every CI log lists each skipped test with its reason (no test
  asserts these commands; checked). The `pip` cache key already includes `requirements-dataproc-3.0.txt`.

### C4 — pre-commit hook

Appended to `.pre-commit-config.yaml` (and to `EXPECTED_PRE_COMMIT` verbatim, as the last six lines). The notice has
no `": "` (a colon followed by a space ends a YAML plain scalar; round 1 found the first draft invalid):
```yaml
      - id: terraform-fmt
        name: terraform fmt -check (infra/)
        entry: sh -c 'command -v terraform >/dev/null 2>&1 || { echo "terraform not on PATH, skipped (CI terraform-validate checks formatting)"; exit 0; }; exec terraform fmt -check -diff "$@"' --
        language: unsupported
        files: ^infra/.*\.tf$
        verbose: true
```
Verified 2026-10-12 on a scratch copy of the config with this block appended: `pre-commit validate-config` passes;
`yaml.safe_load` + `shlex.split(entry)` gives `["sh", "-c", <script>, "--"]`; `<script>` with `PATH` = an empty
directory → exit 0 and the notice; with a misformatted `.tf` → exit 3; with `infra/apis.tf infra/iam.tf` → exit 0
(Terraform 1.16.5).
- Passes the staged `.tf` filenames (`pass_filenames` default true); `terraform fmt` accepts several file targets.
  Deliberately only `.tf`: the gitignored `infra/terraform.tfvars` (CI never sees it), the tracked
  `infra/terraform.tfvars.example`, `infra/.terraform.lock.hcl` and the shell scripts are outside this hook; CI's
  `fmt -recursive` covers `.tf` files only in the checkout, so hook and CI check the same set.
- `verbose: true` is required: pre-commit hides a passing hook's output, so without it the skip notice would be invisible.
- It does not rewrite files (`-check`), consistent with the ruff hook (check, not fix).
- Uses `terraform` from PATH, so unlike ruff/zizmor it is not version-pinned locally; CI pins 1.16.5 and remains the gate.
- `.pre-commit-config.yaml`'s header comment (lines 1-6) is rewritten to say: ruff and zizmor run from the main
  checkout's `.venv` (hash-pinned in requirements-dev.txt); terraform-fmt runs `terraform` from PATH, unpinned, and
  skips with a notice when it is missing, because CI's `terraform-validate` checks formatting with a pinned version.

Tests (`tests/test_tooling.py`), with a helper `_terraform_fmt_script() -> str` that loads `PRE_COMMIT_CONFIG` with
`yaml.safe_load` (`pytest.importorskip("yaml")`; PyYAML is a pre-commit dependency in `requirements-dev.txt`), finds the
hook with `id == "terraform-fmt"`, `shlex.split`s its `entry`, asserts the result has the shape `["sh", "-c", script, "--"]`
and returns `script`:
- `EXPECTED_PRE_COMMIT` gains the six YAML lines above, verbatim; existing `test_pre_commit_config_validates` covers schema validity.
- `test_terraform_fmt_hook_skips_without_terraform(tmp_path)`: `subprocess.run(["/bin/sh", "-c", script, "--", "infra/x.tf"], env={"PATH": str(tmp_path)}, cwd=ROOT, capture_output=True, text=True)`
  (`tmp_path` is empty, so `terraform` is not found; `command` and `echo` are shell builtins) → exit 0 and stdout equals
  the notice plus newline. Runs everywhere, including CI.
- `test_terraform_fmt_hook_flags_unformatted_file(tmp_path)`: `@pytest.mark.skipif(shutil.which("terraform") is None,
  reason="terraform-fmt hook: needs terraform on PATH")` — so it
  is **skipped in CI's `lint-and-test`** (no Terraform there) and runs on the owner's laptop; the only CI-automated proof
  is the skip path. Inherits `os.environ` (the same `terraform` that `which` found), `cwd=ROOT`; writes
  `resource "x" "y" {\na=1\n}\n` to `tmp_path / "bad.tf"`, runs `["/bin/sh", "-c", script, "--", str(tmp_path / "bad.tf")]` → exit 3;
  and with `"infra/versions.tf"` → exit 0.

### C5 — corrections (text only)

- `src/readmission_risk/pipeline/generation.py:20-21` becomes: "# Synthea CLI/config facts below were verified against
  the v4.0.0 release tag's source and synthea.properties, not assumed. The jar scripts/setup_synthea.sh downloads from
  the v4.0.0 release reports build v3.4.0-18-ga07a65555 (pinned as synthea_build_version in prereg.yaml); the flags used
  here were exercised against that jar in the slice-5 pilot."
- v2 PRD line 315 "Synthea generation is deterministic per seed." → append " **Corrected 2026-10-12 (slice 5):** content
  is deterministic only with `-s`, `-cs`, `-e` and a pinned JVM time zone; row order varies." Line 227 "deterministic per
  seed" → "content-deterministic given seed, clinician seed, end date and JVM time zone, except row order and, across
  machines, 45 encounter-less QALY/DALY/QOLS rows of observations.csv; the gold fingerprint is the reproducible unit
  (slice 5)".
- v2 PRD line 317 (the lockfile-drift NFR) → append " **Partly met 2026-10-12**
  (`notes/eg-new-feature/ci-hygiene-lockfiles-2026-10-12.md`): `tests/test_lockfiles.py` proves each lockfile matches
  its `.in`; a champion-compatibility check for guard 8 is still open."

### Documentation surfaces (exact changes)

| Surface | Change |
|---|---|
| `README.md:38` | "Dataproc-runtime job (Python 3.12, PySpark 4.0.1, Java 21)" → "(Python 3.12, PySpark 4.0.2, Java 21)"; "local pre-commit hooks (ruff, zizmor)" → "(ruff, zizmor, terraform fmt)"; add after "hash-locked": ", with a drift test against its `.in` file" |
| `Makefile` `hooks` target description | "(ruff, offline zizmor)" → "(ruff, offline zizmor, terraform fmt)" |
| `docs/setup.md:7` | "with PySpark 4.0.1" → "with PySpark 4.0.2" |
| `docs/setup.md:50` | row split into `make lock-main`, `make lock-dev` → "Recompile `requirements.txt` / `requirements-dev.txt` under Python 3.14, on macOS (keeps existing pins)" and `make lock-runtime` → "Recompile `requirements-dataproc-3.0.txt` under Python 3.12 (re-resolves to the newest allowed patches)" |
| `docs/setup.md:53` | insert, immediately before "Things to know:", the sentence: "A third hook runs `terraform fmt -check` on staged `infra/*.tf` files with the `terraform` on PATH, and skips with a notice when there is none (CI's `terraform-validate` job is the gate)." |
| `docs/setup.md:62` | "Python 3.12, Spark 4.0.1 and Java 21" → "Python 3.12, Spark 4.0.x (4.0.2 ran on 2026-10-12; Google's page still lists 4.0.1) and Java 21"; "names the source page and date" → "names its sources and dates" |
| `docs/setup.md:71` | rewritten: "The library minors follow Google's runtime page; the Spark patch follows what the live runtime ran (the page lags), as recorded in a batch's gold metadata (`spark_version`). When either changes, edit `requirements-dataproc-3.0.in` and `RUNTIME_3_0` in `tests/pipeline/test_runtime_dependencies.py`, run `make lock-runtime` and `make test-runtime`, and commit the `.in` file, the lockfile and the test together (any one of them alone fails `tests/test_lockfiles.py` or `test_runtime_lockfile_mirrors_runtime_3_0`). `make lock-runtime` re-resolves to the newest allowed patches. The command recorded in a lockfile's header is not the recipe, so use the Makefile targets: `make lock-main` and `make lock-dev` keep existing pins (Python 3.14, macOS only), `make lock-runtime` passes `--upgrade` (Python 3.12). `tests/test_lockfiles.py` fails when a lockfile no longer matches its `.in` file. The `.venv-*` directories are deleted and recreated by these targets." |
| `docs/scoring.md` 3.12 paragraph (line 19) | insert the caveat sentence from C6 immediately before "This 3.12 recipe is not exercised by CI since 2026-10-11;" (the colon introducing the code block stays at the end of that sentence) |
| `.github/workflows/ci.yml:64, 99` | `run: pytest` → `run: pytest -rs`; `run: pytest tests/pipeline` → `run: pytest -rs tests/pipeline` |
| `.github/workflows/ci.yml:26` | "Python 3.12 / Spark 4.0.1, the" → "Python 3.12 / Spark 4.0.x, the" |
| `.github/workflows/ci.yml:66` | "(Python 3.12, Spark 4.0.1, Java 21)" → "(Python 3.12, Spark 4.0.x, Java 21; the mirror pins the 4.0.2 the live runtime ran)" |
| `tests/pipeline/test_big_join.py:1322` docstring | "Spark 4.0.1" → "Spark 4.0.2" |
| `.gitignore:16` comment | "(.venv-dataproc, .venv-lock312)" → "(.venv-dataproc, .venv-lock312, .venv-lock314)" |
| v2 PRD lines 227, 315, 317 | C5 |
| slice-5 note "Out-of-scope follow-ups", "Added by 5b" | (2) and (4) suffixed "**Done 2026-10-12** (`notes/eg-new-feature/ci-hygiene-lockfiles-2026-10-12.md`)"; the "CLAUDE.md / PRD corrections" bullet's generation.py and determinism parts likewise |
| CLAUDE.md | Verification 10 |

## UX flow

Developer edits a `.in` file → forgets to recompile → `pytest` (locally and in CI `lint-and-test`) fails in
`test_lock_pair_has_no_drift[requirements.txt]` with the D-messages and the `make lock-main` recipe. Developer runs
`make lock-main` on macOS (needs `python3.14`) → test passes. A committer staging `infra/*.tf` without Terraform sees the
skip notice; with Terraform an unformatted file blocks the commit with a diff.

## Data handling

No patient data, model, split or label is touched. Reproducibility effects:
- C6 changes no installed version in `.venv`, CI or the bundles' recorded library versions (zero hash changes measured),
  so guard 8 and guard 14 outcomes are unchanged; the champion bundle stays valid. C6 does not touch the Dataproc mirror.
- C3 changes the CI test mirror only; nothing is installed on Dataproc (FR3). Fingerprint agreement of Spark 4.0.2
  (Dataproc), local 4.1.3 and Python was already measured live in 5b.
- C1's blind spot (stated in the module docstring): it proves, structurally, that each lockfile is pip-compile's output
  for the current `.in` with the expected interpreter and flags. It cannot detect a transitive version that a fresh
  resolve would pick differently, nor a hand-edited version or hash of a transitive pin that keeps the graph consistent
  (`--require-hashes` installs catch hashes that do not match PyPI's files; nothing catches a consistent hand edit). A
  networked re-resolve job was considered and declined by the owner (2026-10-12).

## Failure modes

- `.in` with a directive or a marker → D3 message or `ValueError` naming the line; never silently skipped.
- Lockfile with a duplicate pin, a stray hash line or an option line → `ValueError` naming it (test error, not pass).
- Lockfile compiled on Linux → extras test fails naming the double-pinned packages and asking "compiled on Linux?";
  `make lock-main` / `lock-dev` refuse to run off macOS in the first place.
- `requirements-dev.txt` recompiled without `--allow-unsafe` (or any flag change) → D2.
- `python3.14` / `python3.12` missing for `make lock-*` → the existing venv-creation / `check_python` error naming the
  override variable.
- A future 3.14 compile drops a 3.12-only dependency → the 3.12 scoring recipe (docs/scoring.md) and `make lock-runtime`
  (which installs the dev lockfile on 3.12) fail with pip's "hashes are required"/missing-requirement error; documented
  in docs/scoring.md (C6), not tested in CI (no CI job installs these lockfiles on 3.12).
- pip-compile 7.x prints a `--strip-extras` deprecation warning; harmless, not acted on (changing flags changes headers
  and D2's expected commands).
- Terraform absent → hook passes with the notice; a local Terraform version different from CI's 1.16.5 could format
  differently; CI stays authoritative.
- C2: if the OOM recurs at 2 GiB, the follow-up is a per-package session or a heap measurement on the CI runner, not a
  further raise. 2 GiB fits CI runners (16 GB RAM) and the laptop.
- C3: if `make lock-runtime` resolves pandas/pyarrow/numpy outside 2.3/19.0/2.1 → existing
  `test_runtime_lockfile_mirrors_runtime_3_0` fails.
- C2 ordering: a future test that starts the JVM before the fixture → `test_session_fixture_has_a_two_gib_driver_heap`
  fails with the message naming that cause.

## Verification criteria

1. `pytest tests/test_lockfiles.py` green; each unit test asserts its exact message list. Before C6's recompile, D1
   fails on the two main lockfiles (shows the test reads the real files; observed during implementation, reported).
2. Mutation check on real files (manual, reported in Results, not committed): `typer==0.27.2` → `0.27.3` in
   `requirements.in` → exactly `D3 typer: pinned 0.27.2 does not satisfy ==0.27.3`; deleting the `cloudpickle` line →
   exactly `D4 cloudpickle: marked '# via -r requirements.in' but not in requirements.in`; both reverted.
3. `make lock-main` and `make lock-dev` on macOS under 3.14; `git diff` of the two lockfiles equals the measured
   expectation (line 2 of each + the three `typing-extensions` `# via` lines), zero hash changes.
4. Python 3.12 install path re-run on the committed lockfiles: fresh 3.12 venv, `pip install -e .`, the
   `--require-hashes` installs of `requirements.txt` + extras and of `requirements-dev.txt`, `pip check` clean (result in Results).
5. `make lock-runtime` under 3.12; `requirements-dataproc-3.0.txt` pins `pyspark==4.0.2`; the six resolved versions
   recorded in Results; `make setup-runtime && make test-runtime` green locally (JDK 21 from `tools/jdk/mac/`, or 17).
6. Full `pytest` and `ruff check .` green in `.venv` (3.14); the `make -n` tests green under macOS make 3.81.
7. `test_session_fixture_has_a_two_gib_driver_heap` green; full `pytest tests/pipeline` green; the scratchpad heap probe
   re-run once at 2 GiB with the result recorded in Results (peak and retained, for the record; no threshold).
8. Pre-commit: `make pre-commit` green; staging a deliberately misformatted scratch `.tf` under `infra/` blocks a commit
   attempt (file removed afterwards, nothing committed); `pytest -rA tests/test_tooling.py -k "terraform_fmt_hook or
   lock_main"` on the laptop shows `PASSED tests/test_tooling.py::test_terraform_fmt_hook_flags_unformatted_file` and
   `PASSED tests/test_tooling.py::test_make_lock_main_venv_is_guarded` (`-rA` prints passed tests by node id) and one
   `SKIPPED` line with the reason "lock-main OS guard: off-macOS only" (output in Results).
9. CI on the PR into `v2`: all four jobs green; `pipeline-dataproc-runtime` asserts pyspark 4.0.2;
   in `lint-and-test`'s log. pytest's `-rs` summary prints `SKIPPED [n] <file>:<line>: <reason>`, not test names, so the
   check is by the three unique reason strings above: "lock-main venv guard: macOS only …" appears; "lock-main OS guard:
   off-macOS only" does NOT appear (the test was collected, not skipped and not failed, so it ran and passed);
   "terraform-fmt hook: needs terraform on PATH" appears if the runner image has no `terraform`, and is absent (the test
   ran) if it has one — either is acceptable and the observed case is recorded in Results. Before relying on this, grep
   the repo to confirm each reason string is used by exactly one test. The PR says the C2 flake was seen once in about a
   dozen runs, so a green run is consistent with, not proof of, the fix.
10. Docs: `tests/test_docs.py` green; every row of "Documentation surfaces" applied; CLAUDE.md updated: the
    lockfile-drift TODO and the terraform-fmt TODO marked done with this note's path; slice-5 follow-up (5) and the
    mirror/OOM parts of (6) marked done; the Build & test commands' first two bullets say main+dev lockfiles compile under
    3.14 on macOS via `make lock-main` / `make lock-dev` and the Dataproc mirror under 3.12 via `make lock-runtime`
    (pyspark 4.0.2), and the "`requirements.txt` by the command below" wording names `make lock-main`; the Make-targets
    bullet lists `lock-main`; current-state mentions of the mirror's "pyspark 4.0.1" (lines 61, 115, and line 99's
    "{3.12, `pyspark==4.0.1`}") become 4.0.2; line 65's present-tense "(pyspark 4.0.1, pandas 2.3, pyarrow 19.0, numpy
    2.1)" becomes "(pyspark 4.0.1, 4.0.2 since 2026-10-12, pandas 2.3, pyarrow 19.0, numpy 2.1)"; line 99's runtime mention "(Python 3.12 / Spark 4.0.1; …" becomes
    "(Python 3.12 / Spark 4.0.x, 4.0.2 as run on 2026-10-12; …"; line 103's "not the 4.0.1 the CI mirror pins" becomes
    "which the CI mirror pins since 2026-10-12"; the lockfile-drift TODO is rewritten, not deleted: "Done 2026-10-12:
    `tests/test_lockfiles.py` (each lockfile is pip-compile's output for its `.in`). Still open: a champion-compatibility
    check (guard 8 refuses the champion after a properly recompiled bump of scikit-learn/xgboost/mlflow/pandas/numpy;
    needs the champion's library versions in a tracked file)"; dated historical records (e.g. the 2026-10-09 Python-version note's account of what was decided then) are left as written.

## Out-of-scope follow-ups

- Champion-compatibility check: fail CI when `requirements.txt` pins a guarded library (scikit-learn, xgboost, mlflow,
  pandas, numpy) at a version different from the champion bundle's manifest. Needs those versions in a tracked file
  (e.g. added to `deploy/champion.json`, a slice-2 format change). Until then the v2 PRD NFR's guard-8 rationale is only
  partly addressed.

- A networked re-resolve check (pip-compile dry run) if C1's structural check proves insufficient.
- A CI job installing the main+dev lockfiles on 3.12, if the documented 3.12 scoring recipe is to stay supported.
- Splitting the session SparkSession (per package) or measuring heap on the CI runner if the OOM recurs at 2 GiB.
- Recording the Dataproc runtime sub-version and Spark version per batch (slice 6, FR14) so the next mirror bump has a
  run-recorded source rather than a note.
- A check that `requirements-linux-extras.txt` matches xgboost's actual Linux dependency (still manual).
- Pinning Terraform for the pre-commit hook (it uses whatever is on PATH).
- Everything else from the 2026-10-12 triage: L9, ops bundle (D1-D3), infra cleanup (E1-E2), slice-6 items, V-post/L-post, pandas 3.

## Revision log

Revision 1 (after round 1; comprehension passed, critic 9 gaps, readiness 8 questions). Critic → 1 hook YAML invalid
(": " in the notice): notice reworded, validated with pre-commit, tests read the entry via `yaml.safe_load` + `shlex.split`.
2 3.12 install path: measured OK (C6), documented caveat + failure mode + verification 4; `requires-python` kept with reason.
3 platform dependence: `uname` guard on lock-main/lock-dev, extras-test message, tests both ways. 4 C2 hypothesis:
measured; the retention hypothesis was wrong (≈25 MiB), settings dropped, comment rewritten from the measurement.
5 JVM-start assumption stated in conftest and in the heap test's failure message. 6 every hooks/refresh/4.0.1 surface
given exact wording ("Documentation surfaces"). 7 D1 Python derived from `.python-version` for main/dev; new D2 checks
the recorded pip-compile command; option lines are errors. 8 CI skips the flag test (stated); `.tf`-only scope stated.
9 C3 in one commit; resolved versions recorded. Readiness → 1 = critic 1; 2-6 every message given verbatim with
canonical names and line-missing rule (table); 7 `_pins` keying specified; 8 flag test env/cwd specified.

Revision 2 (after round 2; critic 3, readiness 5). Critic → 1 C1 does not cover guard-8 compatibility: stated in Why,
CLAUDE.md TODO rewritten as partly done, champion-compatibility check named as a follow-up. 2 unrecognised lockfile
lines raise + parametrized test. 3 extras marker check re-scans raw lines. Readiness → 1 CLAUDE.md lines 99 and 103
wording given; 2 multi-line-via fixture and expected `Pin` given; 3 separate darwin-only test; 4 insertion points in
docs/setup.md and docs/scoring.md; 5 D3 per-requirement order.

Revision 3 (after round 3; readiness READY, critic 1). Critic → 1 `foo===1.0` matched the pin regex (version `=1.0`):
the pin regex is now full-line and the version cannot start with `=`; validated on all 148 real pin lines and the six
rejection cases (added `alpha==1.0 junk`); the `_pins` helpers get the same version group (they never raise).

Revision 4 (after round 4; readiness READY, critic 4; owner chose "fix the 4 + one more round"). 1 CI/laptop proof
that the platform-conditional tests ran: `pytest -rA` locally, skipped-count delta of exactly 2 in CI. 2 PRD line 317
annotated "partly met". 3 PRD line 227 reworded to the measured reproducibility (row order, 45 cross-machine rows, gold
is the reproducible unit). 4 `requirements-dataproc-3.0.in` line 8 reworded. Citations corrected (`_pins` lines, `_jvm` line).


Revision 5 (after round 5; readiness 1 question, critic 2; owner's extra round). Readiness → 1 Makefile variable
snippet had inline comments (GNU Make would keep the spaces in the value): now a plain line, `LOCK_VENV` untouched.
Critic → 1 CI proof by count delta had no reference run and no reading for a delta of 1: replaced by `pytest -rs` in
both CI pytest steps and a name-based check of the skip list (Terraform present or absent both handled). 2 CLAUDE.md
line 65's present-tense mirror description added to Verification 10.

Revision 6 (after round 6; readiness READY, critic 3; owner's extra round). Critic → 1 pytest's skip summary prints
reasons, not names (critic verified with pytest 9.1.1): the three platform/tool skips get unique reason strings and CI
is checked by reason; the laptop check uses `-rA`'s PASSED node ids. 2 the docs/setup.md refresh recipe commits `.in`,
lockfile and test together. 3 `test_pre_commit_config_is_local_hooks_from_the_main_checkouts_venv_only` renamed.

## Results (implementation, 2026-10-12)

Design gate: owner override after round 6 (readiness closed in rounds 3, 4 and 6; the critic never formally closed). All
numbers below were measured on the owner's laptop (macOS arm64, Python 3.14.7) on branch `v2-ci-hygiene`.

- **V1** before C6, `tests/test_lockfiles.py`: exactly 2 failures, D1 on `requirements.txt` and `requirements-dev.txt`
  ("… with Python 3.12', expected '… with Python 3.14'"); the Dataproc pair and all 25 rule tests passed.
- **V2** mutations of the real `requirements.in` (in memory): `typer==0.27.3` → `D3 typer: pinned 0.27.2 does not
  satisfy ==0.27.3`; `cloudpickle` line removed → `D4 cloudpickle: marked '# via -r requirements.in' but not in
  requirements.in`. Exactly the specified messages.
- **V3** `make lock-main` + `make lock-dev` (3.14): diff = line 2 of each file + the three `typing-extensions` `# via`
  lines (`aiohttp`, `aiosignal`, `starlette`); **zero version or hash changes**, as predicted.
- **V4** fresh Python 3.12 (Homebrew) venv: `pip install -e .`, `--require-hashes` installs of `requirements.txt` +
  extras and of `requirements-dev.txt`, `pip check` clean.
- **V5** `make lock-runtime` (interpreter 3.12.7): pyspark 4.0.2, py4j 0.10.9.9, pandas 2.3.3, pyarrow 19.0.1,
  numpy 2.1.3, pytest 9.1.1; only pyspark changed. `make setup-runtime && make test-runtime` (`.venv-dataproc`:
  Python 3.12.7, pyspark 4.0.2, pandas 2.3.3, pyarrow 19.0.1, numpy 2.1.3; JDK 17.0.18 Homebrew, which the doc allows
  besides 21): `tests/pipeline` **317 passed, 1 skipped** (the Dataproc-environment check), 79 s. Java 21 is asserted in CI.
- **V6** `ruff check .` clean; `zizmor --offline .` no findings; full `pytest -rs`: **1227 passed, 2 skipped**
  (Dataproc-environment check; "lock-main OS guard: off-macOS only"), 172 s.
- **V7** `tests/pipeline` at 2 GiB with the heap probe: 317 passed; max 2048 MiB, peak in use between tests 1722 MiB,
  retained after GC 193 MiB (1 GiB run: 910 / 200 MiB). Live state is unchanged; the JVM just collects later.
- **V8** `pre-commit run --all-files`: ruff, zizmor, terraform-fmt passed. A misformatted `infra/zz_scratch_bad.tf`
  run through `pre-commit run terraform-fmt --files …` failed with exit 3 and printed the diff (file removed; a real
  commit attempt was replaced by this to avoid any chance of a stray commit). `pytest -rA -k "terraform_fmt_hook or
  lock_main"`: PASSED `test_make_lock_main_venv_is_guarded`, `test_terraform_fmt_hook_skips_without_terraform`,
  `test_terraform_fmt_hook_flags_unformatted_file`; SKIPPED "lock-main OS guard: off-macOS only". Each of the three skip
  reasons is used by exactly one test (git grep).
- **V9** CI on PR #12 (run 38302549542, head 570ac07, merged as 050cf50): all four jobs green. `pipeline-dataproc-runtime`: 318 passed (the environment check ran and asserted pyspark 4.0.2). `lint-and-test`: 1225 passed, 4 skipped; the `-rs` list shows "lock-main venv guard: macOS only …" and "terraform-fmt hook: needs terraform on PATH" (the runner has no Terraform) and does not show "lock-main OS guard: off-macOS only" (so it ran); the other two skips are the Dataproc-environment check and `tests/reporting/test_style.py:82` (light font face, not listed in CLAUDE.md before; added by the ops bundle).
- **V10** docs: all "Documentation surfaces" rows and the CLAUDE.md lines applied; `tests/test_docs.py` passes in V6.
