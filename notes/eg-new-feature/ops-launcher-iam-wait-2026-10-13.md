# Generation launcher: the same pre-submit IAM-propagation wait, via a shared ops module (2026-10-13)

Design doc for `/eg-new-feature`. Branch `v2-ops-polish` (HEAD `a0fd5bb`), same PR as the ops bundle and its follow-ups.
Laptop CLIs only; every GCP call goes through the `ops.gcloud` Runner, faked in tests. No data, model, split or Terraform
change. No live run (the launcher costs money; the audit-log filter itself was verified live on 2026-10-13, see
`notes/eg-new-feature/ops-iam-propagation-and-interrupt-2026-10-12.md`, Results V2).

## Why

Out-of-scope follow-up of `notes/eg-new-feature/ops-iam-propagation-and-interrupt-2026-10-12.md` ("The same pre-submit
check for the generation launcher"); the owner asked for it on 2026-10-13, through the full design process, same PR.

`make generate-vm` creates a spot VM that runs as `synthea-generator` and writes to the raw bucket through bucket-level
grants. If an IAM role or binding that VM depends on changed minutes earlier and has not propagated, the failure shows
up only inside the VM (the startup script cannot write, so `_FAILED`, a vanished VM or a timeout) after the VM has
booted and been billed, which is costlier than the Big Join's free validation failure. The Big Join submitter already
waits for propagation before submitting (`ops/dataproc_batch.py::wait_for_iam_propagation`, commit `a0fd5bb`); the
launcher should do the same before creating the VM.

The code cannot simply be imported: `ops/dataproc_batch.py` already imports `ops/generation_vm.py` (`Refused`,
`LaunchContext`, constants), so `generation_vm` importing `dataproc_batch` would be circular. The IAM-wait code moves to
a new module that imports neither.

## Scope

In:
1. New module `src/readmission_risk/ops/iam_propagation.py` holding the IAM-wait code, moved from `dataproc_batch.py`.
2. `_parse_time` moves from `dataproc_batch.py` to `ops/gcloud.py` as `parse_gcloud_time` (both modules need it).
3. `dataproc_batch.py` imports from the new module; its behaviour is unchanged except two message wordings (below).
4. `generation_vm.launch` calls the wait after the uploads and template lookup, before `gcloud compute instances
   create`; after a non-zero wait it re-runs both preflights and refuses if HEAD or the prereg commit moved.
5. Tests: a new `tests/ops/test_iam_propagation.py`; moved unit tests; new launcher tests; `audit_entry` moves to
   `tests/ops/fake_gcloud.py`.
6. Docs: module and script docstrings, `docs/setup.md`, the IAM note's follow-up list, CLAUDE.md test count.

Out: a launcher-side failure hint (the VM's IAM failure has no Dataproc-style message to match); waiting inside the
VM; automatic retry; any change to the filter, thresholds or the Big Join's behaviour beyond the two wordings.

## Surfaces touched

| File | Change |
|---|---|
| `src/readmission_risk/ops/iam_propagation.py` | **new** (Interfaces) |
| `src/readmission_risk/ops/gcloud.py` | new `parse_gcloud_time` (moved `_parse_time`) |
| `src/readmission_risk/ops/dataproc_batch.py` | remove the moved definitions (lines 89-106: `IAM_PROPAGATION_SECONDS` through the closing `)` of `GOOGLE_MANAGED_PRINCIPAL`; lines 126-129: `_parse_time`; lines 167-253: from `@dataclass(frozen=True)` above `class IamChange` through `cutoff = latest.timestamp`) and now-unused imports; import from `iam_propagation` and `gcloud`; `submit`'s outer `except` adds `IamStillChanging`; module docstring unchanged |
| `src/readmission_risk/ops/generation_vm.py` | `launch` (lines 374-399 region), new `recheck_after_wait`, module docstring exit codes |
| `scripts/launch_generation.py` | docstring |
| `tests/ops/fake_gcloud.py` | new public helper `audit_entry` (moved from `test_dataproc_batch.py`) |
| `tests/ops/test_iam_propagation.py` | **new** |
| `tests/ops/test_dataproc_batch.py` | `test_iam_changes_argv`, `test_parse_iam_changes` move to the new test file; local `audit_entry` replaced by the import |
| `tests/ops/test_generation_vm.py` | new tests; dry-run test gains one assertion |
| `docs/setup.md` | line 302 (launcher paragraph) and line 323 (lessons sentence) |
| `notes/eg-new-feature/ops-iam-propagation-and-interrupt-2026-10-12.md` | follow-up bullet "The same pre-submit check for the generation launcher …" suffixed " **Done 2026-10-13** (`notes/eg-new-feature/ops-launcher-iam-wait-2026-10-13.md`)" |
| `CLAUDE.md` line 118 | test count from the final run |

## Interfaces

### `ops/gcloud.py`

```python
def parse_gcloud_time(text: str | None) -> datetime | None:
    """gcloud's RFC 3339 timestamps ("...Z", up to nanoseconds; Python 3.11+ fromisoformat truncates to microseconds)."""
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))
```
Body identical to `dataproc_batch._parse_time` (lines 126-129 at `a0fd5bb`; line 130 is blank). `from datetime import datetime` is added
(stdlib; `tests/ops/test_gcloud.py::test_gcloud_module_is_stdlib_plus_gcp_only` keeps passing).

### `ops/iam_propagation.py`

Module docstring: "The pre-submit IAM-propagation wait shared by the Big Join submitter and the generation launcher:
reads the Admin Activity audit log and waits until IAM changes by non-Google principals are ~8 minutes old. Imports
neither CLI module (they import it). Design: notes/eg-new-feature/ops-iam-propagation-and-interrupt-2026-10-12.md and
notes/eg-new-feature/ops-launcher-iam-wait-2026-10-13.md."

Imports: `re`; `from collections.abc import Callable`; `from dataclasses import dataclass`;
`from datetime import UTC, datetime, timedelta`; `from math import ceil`; `from typing import Protocol`;
`from .gcloud import PROJECT, GcloudError, Runner, parse_gcloud_time, run_json`.

Moved **verbatim** from `dataproc_batch.py` at `a0fd5bb` (same names, same values, same bodies): `IAM_PROPAGATION_SECONDS`,
`MAX_IAM_WAITS`, `AUDIT_LOG`, `IAM_CHANGE_METHODS`, `GOOGLE_MANAGED_PRINCIPAL`, `IamChange`, `parse_iam_changes` (its
`_parse_time(raw)` call and its comment "before parsing: _parse_time of a non-str raises AttributeError" both say
`parse_gcloud_time` instead), `iam_changes_argv`, `wait_for_iam_propagation` with exactly these six changes:

1. Signature: `def wait_for_iam_propagation(ctx: IamWaitContext) -> float:` where
   ```python
   class IamWaitContext(Protocol):
       """What the wait needs from a CLI's context (generation_vm.LaunchContext satisfies it)."""
       run: Runner | None
       now: Callable[[], datetime]
       sleep: Callable[[float], None]
       echo: Callable[[str], None]
   ```
2. It raises the new `class IamStillChanging(Exception): """IAM kept changing across MAX_IAM_WAITS waits (callers exit 1, refused)."""`
   instead of `Refused` (same message text).
3. The warning's "submitting without the propagation check" becomes "continuing without the propagation check".
4. The waiting line's "(Ctrl-C aborts; nothing has been submitted yet)" becomes "(Ctrl-C aborts; nothing has been started yet)".
5. The docstring's first words "Before submitting:" become "Before a CLI starts paid work (a Dataproc batch, a generation
   VM):", and "(the failure-path hint still applies)" becomes "(the Big Join submitter's failure-path hint still applies;
   the launcher has none)".
6. The refusal message's "resubmit once IAM is stable" becomes "rerun once IAM is stable".

### `dataproc_batch.py` after the move

- Imports: `from .iam_propagation import IAM_PROPAGATION_SECONDS, IamStillChanging, wait_for_iam_propagation` and
  `parse_gcloud_time` added to the existing `from .gcloud import (...)`; `parse_batch_usage` calls `parse_gcloud_time`.
  Imports no longer used after the removal are removed: exactly those ruff F401 reports (expected: `re`, `ceil`,
  `timedelta`, `UTC`, and `datetime` if no annotation still uses it).
- `submit`'s outer handler becomes `except (Refused, GcloudError, IamStillChanging) as exc:` (echo `REFUSED: {exc}`,
  exit 1, unchanged).
- Nothing else changes. `recheck_after_wait`, `_describe`, `_finish` stay in `dataproc_batch.py`.

### `generation_vm.py`

Import: `from .iam_propagation import IAM_PROPAGATION_SECONDS, IamStillChanging, wait_for_iam_propagation`.

New function (after `preflight_remote`):
```python
def recheck_after_wait(options: LaunchOptions, ctx: LaunchContext, record: PreregRecord) -> None:
    """The IAM wait can last ~24 min: re-run both preflights (P1/P1b/P2/P5 locally; P3/P4/P6 remotely) and refuse if HEAD or
    the prereg commit moved, because the uploaded artifacts and the VM metadata were built before the wait."""
    _, rechecked = preflight_local(options, ctx)
    preflight_remote(options, ctx)
    if (rechecked.head_commit, rechecked.prereg_commit) != (record.head_commit, record.prereg_commit):
        raise Refused(
            f"HEAD or the prereg commit changed during the IAM wait (was {record.head_commit[:12]}/"
            f"{record.prereg_commit[:12]}, now {rechecked.head_commit[:12]}/{rechecked.prereg_commit[:12]}); "
            "the artifacts and VM metadata were built from the old commit: rerun to build from the new one"
        )
```

`launch` changes (first `try` block, lines 374-399 at `a0fd5bb`):
- Dry run, after the existing "would run:" echo and before `return EXIT_OK`:
  `ctx.echo(f"would check the Admin Activity audit log for IAM changes in the last {IAM_PROPAGATION_SECONDS // 60} minutes, then create the VM")`.
- After `template, max_run_seconds = resolve_template(ctx.run, options.run)`:
  ```python
        try:
            if wait_for_iam_propagation(ctx) > 0:
                recheck_after_wait(options, ctx, record)
        except KeyboardInterrupt:
            ctx.echo(
                "INTERRUPTED during the IAM propagation wait or its re-check: no VM was created (the uploaded artifacts "
                "are reused on the next run)"
            )
            return EXIT_INTERRUPTED
    except (Refused, GcloudError, IamStillChanging) as exc:
        ctx.echo(f"REFUSED: {exc}")
        return EXIT_REFUSED
  ```
  The wait runs before `started = ctx.monotonic()` and before the `try/finally` that tears the VM down, so a refusal or
  Ctrl-C there creates nothing and runs no teardown, and the wait is not counted in the reported VM time.
- Module docstring: "130 interrupted." becomes "130 interrupted (during the IAM propagation wait or its re-check: no VM
  was created; after creation: the VM is torn down and proven gone)." and "1 refused (preflight)" becomes "1 refused
  (preflight, or IAM still changing after 3 propagation waits)".

### `scripts/launch_generation.py` docstring

"uploads the pinned artifacts, creates the VM from the Terraform template," → "uploads the pinned artifacts, waits for IAM
propagation if a role or binding changed in the last 8 minutes (audit log), creates the VM from the Terraform template,";
and "130 interrupted." → "130 interrupted (during the IAM wait: no VM created; see ops/generation_vm.py).".

### Tests

`tests/ops/fake_gcloud.py` gains (moved from `test_dataproc_batch.py`, same body):
```python
AUDIT_ROLE = "projects/synthea-readmission-risk/roles/dataprocRuntimeWorker"


def audit_entry(method: str, at: datetime, principal: str = "operator@example.com", resource: str = AUDIT_ROLE) -> dict:
```
(`from datetime import datetime` added to the fake's imports.) `test_dataproc_batch.py` imports both
(`from tests.ops.fake_gcloud import AUDIT_ROLE, FakeGcloud, audit_entry`), defines `ROLE = AUDIT_ROLE` in place of its
literal (so the existing assertions keep their name), and keeps `NOW` and `_seconds` (still used by its submit tests).

`tests/ops/test_iam_propagation.py` (new):
- Module-level helpers, defined in this file: `NOW = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)` (the moved parse test's
  naive-timestamp case "2026-10-13T09:59:30" relies on this exact value), `ROLE = AUDIT_ROLE` (imported from
  `tests.ops.fake_gcloud`, with `audit_entry` and `FakeGcloud`), `def _seconds(n: int) -> timedelta: return timedelta(seconds=n)`.
- `test_iam_changes_argv` and `test_parse_iam_changes` moved verbatim from `test_dataproc_batch.py`, with `db.` replaced
  by `iam.` (`from readmission_risk.ops import iam_propagation as iam`).
- `test_module_imports_neither_cli_module`: parse `src/readmission_risk/ops/iam_propagation.py` with `ast`; no `Import`
  or `ImportFrom` names `generation_vm` or `dataproc_batch` (relative `from .generation_vm` included: check
  `node.module` and alias names).
- `test_wait_returns_zero_without_changes`: a `types.SimpleNamespace(run=FakeGcloud(), now=lambda: NOW, sleep=..., echo=...)`
  context; `iam.wait_for_iam_propagation(ctx) == 0.0`; one `logging read` call.
- `test_wait_raises_iam_still_changing`: four responses, each one UpdateRole 1 s newer (NOW-60 … NOW-57 s);
  `pytest.raises(iam.IamStillChanging, match="IAM kept changing across 3 propagation waits")`; three sleeps.

`tests/ops/test_generation_vm.py` (fixture clock `NOW_G = datetime(2026, 10, 13, 9, 5, tzinfo=UTC)`; `e` =
`audit_entry("google.iam.admin.v1.UpdateRole", NOW_G - timedelta(seconds=60))`):
- `test_launch_waits_for_recent_iam_change_before_create(env)`: `fake.logging_responses = [[e], [e]]`; wrap the
  fixture's sleep to also record (`slept.append(s); original(s)`); exit `EXIT_OK`; `slept[0] == 420.0`; the index of the
  last `gcloud logging read` call in `fake.calls` is lower than the index of the `gcloud compute instances create` call;
  "Waiting 420 s for IAM propagation" in text.
- `test_ctrl_c_during_iam_wait_creates_no_vm(env)`: one response `[e]`; `ctx.sleep` raises `KeyboardInterrupt`; exit
  `EXIT_INTERRUPTED`; no `compute instances create` and no `compute instances delete` call; text contains
  "INTERRUPTED during the IAM propagation wait or its re-check: no VM was created".
- `test_iam_keeps_changing_refuses_launch(env)`: four responses, 1 s newer each; exit `EXIT_REFUSED`; no `instances
  create` call; "IAM kept changing across 3 propagation waits" in text.
- `test_commit_change_during_iam_wait_refuses_launch(env, monkeypatch)`: responses `[[e], [e]]`; monkeypatch
  `gv.check_prereg_committed` to return `RECORD` on the first call and `dataclasses.replace(RECORD, head_commit="e" * 40)`
  on later calls; exit `EXIT_REFUSED`; no `instances create` call; "HEAD or the prereg commit changed during the IAM wait"
  in text.
- `test_preflight_runs_once_without_a_wait(env, monkeypatch)`: wrap `gv.preflight_remote` with a counter that calls the
  real function; default `logging_responses` (no change) → exit `EXIT_OK`, counter == 1.
- `test_preflights_rerun_after_a_wait(env, monkeypatch)`: the same counter; `fake.logging_responses = [[e], [e]]` →
  exit `EXIT_OK`, counter == 2.
- `test_recheck_refuses_when_a_batch_started_during_the_wait(env)`: `fake.logging_responses = [[e], [e]]`; a sleep
  function that, on its first call, adds `{"state": "RUNNING", "states": []}` under key `"other"` to `fake.batches`
  (and advances the fixture clock as the original does); exit `EXIT_REFUSED`; "a Dataproc batch is active" in text; no
  `compute instances create` call.
- `test_ctrl_c_during_recheck_creates_no_vm(env, monkeypatch)`: `[[e], [e]]`; monkeypatch `gv.recheck_after_wait` with
  a function raising `KeyboardInterrupt`; exit `EXIT_INTERRUPTED`; no `instances create` call; "INTERRUPTED during the
  IAM propagation wait or its re-check: no VM was created" in text.
- `test_audit_log_unreadable_warns_and_launches(env)`: `fake.fail_on = ("gcloud logging read",)`; exit `EXIT_OK`; one
  `instances create` call; "WARNING: could not read the Admin Activity audit log" in text.
- `test_dry_run_makes_no_runner_call` (existing): additionally assert "would check the Admin Activity audit log" in text.

`tests/ops/test_dataproc_batch.py` keeps every submit-level IAM test unchanged (they now exercise the moved code through
`db.submit`); their assertions on the warning and waiting lines use substrings that the wording changes keep
("WARNING: could not read the Admin Activity audit log", "Waiting 420 s for IAM propagation").

## UX flow

`make generate-vm RUN=… JDK_TARBALL=…` → local preflight → remote preflight → uploads → template → **audit-log check**
(no recent human IAM change: continue at once; a change within 8 minutes: one line naming it, "Waiting N s …", a
re-check, then both preflights again) → VM created → positive control → wait for `_DONE` → teardown proof. Ctrl-C during
the wait: nothing created, exit 130. The Big Join submitter behaves as before.

## Data handling

None: operations CLIs only.

## Failure modes

Same as the submitter's (IAM note, Failure modes): ingestion lag can hide a change made seconds before the launch (docs
advise waiting a minute after `terraform apply`); unreadable log → warning and continue; Google-managed principals
ignored; laptop clock skew; an unrelated human IAM change also causes a wait of up to 8 minutes. Launcher-specific: a
miss is costlier (the VM boots and fails), and there is no failure hint to fall back on; accepted.

## Verification criteria

1. All new and moved tests pass; `pytest tests/ops`, the full `pytest` and `ruff check .` green.
2. The new module compared with the lines removed from `dataproc_batch.py` (manual side-by-side, recorded in Results)
   shows only: the six listed changes, the `parse_gcloud_time` call and comment, the new `IamWaitContext` and
   `IamStillChanging` classes, and the module docstring/imports.
3. Docs: the rows above applied; in `docs/setup.md:302` (the launcher paragraph starting "It uploads the jar, JDK,
   …") the words "…/sha256/<hex>/` (create-only), creates the VM," become "…/sha256/<hex>/` (create-only), waits for IAM
   propagation like the Big Join submitter below if IAM changed in the last 8 minutes, creates the VM,"; `docs/setup.md:323`'s sentence
   "Before submitting, the submitter reads the Admin Activity audit log" becomes "Before submitting (and the generation
   launcher before creating its VM), the submitter reads the Admin Activity audit log"; in the same sentence "(Ctrl-C
   submits nothing)" becomes "(Ctrl-C there starts nothing)", "can be missed" stays, "wait a minute before `make
   bigjoin-batch` (the failure hint still covers a miss)" becomes "wait a minute before `make bigjoin-batch` or `make
   generate-vm` (for the Big Join the failure hint still covers a miss; for the launcher a miss means a VM that boots and
   fails)", and "after a wait the preflight checks run again" stays. `docs/setup.md:302` additionally gains, at the end of
   that paragraph, the sentence "Ctrl-C during that IAM wait (or its re-check) creates no VM; later, the VM is deleted
   and proven gone as usual." CLAUDE.md line 118 lead-in
   "(<N> tests since 2026-10-13, launcher IAM wait: <P> passed + <S> skipped on macOS in about 3 minutes;" from the final
   `pytest -rs`; `tests/test_docs.py` green.

## Out-of-scope follow-ups

- A launcher-side hint when the VM fails in a way that looks like missing permissions.

## Revision log

Revision 1 (after round 1; comprehension passed, readiness READY, critic 4). 1 submitter-only wording in the shared
function (docstring, refusal) and in the docs sentence neutralised; criterion 2 lists every change. 2 test helpers
specified (`NOW`, `ROLE`, `_seconds`; one `AUDIT_ROLE` constant in the fake); the moved comment and the possibly unused
`datetime` import covered. 3 launcher tests where the re-check refuses and where Ctrl-C hits the re-check. 4 setup.md and
the script docstring say a Ctrl-C during the wait creates no VM.

Round 2: critic `design ready`, readiness `implementation ready` (gate closed without override). The critic's
non-blocking note that CLAUDE.md TODO (6) still lists the ops items as open was checked and is wrong: all three are
struck through with "done" notes.

## Results (implementation, 2026-10-13)

Branch `v2-ops-polish` on top of `a0fd5bb`, laptop (macOS arm64, Python 3.14.7). No live run (none needed: the filter
was verified live in the IAM note's V2; the launcher costs money).

- **V1** `pytest tests/ops`: 169 passed. Before the tests moved, the 155 submit-level tests passed unchanged through the
  shared module (only the two moved unit tests failed, as expected, since their names left `dataproc_batch`). New:
  `tests/ops/test_iam_propagation.py` (2 moved + 3 new), 9 launcher tests in `test_generation_vm.py`, one assertion added
  to its dry-run test. Full `pytest -rs`: **1274 passed, 2 skipped** (Dataproc-environment check; "lock-main OS guard:
  off-macOS only"), 172 s. `ruff check .` clean; the unused imports removed from `dataproc_batch.py` were exactly `re`,
  `UTC`, `datetime`, `timedelta`, `ceil` (plus the resulting import re-sort).
- **V2** side-by-side diff (blank lines ignored) of `dataproc_batch.py` lines 89-106 and 167-253 at `a0fd5bb` against
  `iam_propagation.py`: only the two new classes (`IamStillChanging`, `IamWaitContext`), `parse_gcloud_time` in the call
  and its comment, and the six listed changes (signature, docstring ×2, "continuing", "started", `IamStillChanging`,
  "rerun"). Nothing else changed.
- **V3** docs applied (`docs/setup.md` lines 302 and 323, the script docstring, the IAM note's follow-up); CLAUDE.md line
  118 = 1,276 tests (1,274 + 2); `tests/test_docs.py` green in V1.
- **Pre-commit review round 1** (1 finding, fixed): the instance template was resolved before the wait and never again,
  so a `terraform apply` that replaced it during the wait (the usual source of the IAM change) would create from a
  deleted or stale template. `resolve_template` now runs after the wait (still inside the refusal `try`, so a missing or
  ambiguous template exits 1). New test `test_template_is_resolved_after_the_wait`.
- **Pre-commit review round 2** (1 low-severity finding on the round-1 lines; the loop's stop rule tripped, owner chose
  "keep working"): a missing or ambiguous template was refused only after the IAM wait. `resolve_template` now also runs
  before the wait as a fail-fast check (result discarded); the after-wait result feeds the create call. New test
  `test_missing_template_is_refused_before_any_iam_wait`.
- **Pre-commit review round 3**: no findings. Final gate: `ruff check .` clean; full `pytest -rs` **1276 passed, 2
  skipped** (1,278 tests; supersedes V1's 1,274), CLAUDE.md line 118 updated; ops tests 171.
