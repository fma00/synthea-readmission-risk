# Ops bundle: submitter skips the usage wait for never-run batches, permission-failure hint, launcher prints positive-control OK (2026-10-12)

Design doc for `/eg-new-feature`. Branch `v2-ops-polish` off `v2` (at `050cf50`, PR #12 merged), one PR into `v2`. No
GCP call is made by this work; all behaviour is exercised through `tests/ops/fake_gcloud.FakeGcloud`. No data, model,
split or Terraform change.

Shorthand: **D1** usage-wait skip, **D2** positive-control OK line, **D3** permission-failure hint, **K** carry-overs from
the ci-hygiene bundle (docs only).

## Why

Group D of the 2026-10-12 TODO triage (owner-confirmed order: CI hygiene → L9 → **ops** → infra cleanup → slice 6).
Source: slice-5 note `notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md`, "Out-of-scope
follow-ups" → "Added by 5b" items (5), (6), (7); CLAUDE.md "TODO (open, slice 5 follow-ups)" item (6). Evidence from the
5b live run (`data/pilot/pilot_20k/`, gitignored, read 2026-10-12):

- **D1:** batch `bigjoin-pilot-20k-202610121318` was created 13:18:14Z and ended `FAILED` 13:18:19Z (5 s) with
  `stateHistory = [{"state": "PENDING", …}]` (never RUNNING) and no `runtimeInfo.approximateUsage`. The submitter's loop
  at `src/readmission_risk/ops/dataproc_batch.py:237-241` then polls for usage for `USAGE_WAIT_SECONDS = 600` that will
  never appear. The successful batch `…1337` has `stateHistory` states `["PENDING", "RUNNING"]` (the current state,
  `SUCCEEDED`, is not in the history) and has usage.
- **D3:** the failed batch's `stateMessage` was: `Permission validation failed for operation e5cc8693-…: Service Account
  'dataproc-runtime@synthea-readmission-risk.iam.gserviceaccount.com' is missing required permissions:
  [dataprocrm.nodePools.update].` The second attempt (`…1332`, after the role fix) failed with the same message because
  IAM had not propagated (~8 min). `docs/setup.md:323` already documents the wait; the operator gets no hint at the
  moment of failure.
- **D2:** `generation_vm.launch` prints nothing when `positive_control` (`generation_vm.py:264-298`) passes; in 5b the
  pass had to be inferred from the launcher proceeding (`live-checks.md:127`).
- **K:** the ci-hygiene note's V9 is still "pending" although PR #12's CI ran (run 38302549542, all four jobs green);
  CLAUDE.md's Linux skip list (line 118) misses `tests/reporting/test_style.py:82`'s font skip seen in that run.

## Scope

In:
1. D1 in `ops/dataproc_batch.py`: a pure `never_ran(describe) -> bool`; `submit` skips the usage wait when it is true and
   reports the observation ("stateHistory never reached RUNNING") rather than asserting the batch never ran.
2. D3 in `ops/dataproc_batch.py`: a pure `permission_hint(state_message) -> str | None`; on a failed batch whose
   message matches, `submit` echoes the hint after the failure line.
3. D2 in `ops/generation_vm.py`: `launch` echoes one `POSITIVE CONTROL OK: …` line when `positive_control` returns `[]`.
4. `tests/ops/fake_gcloud.py`: `describe` emits `stateHistory`; a new switch `batch_state_message`.
5. Tests in `tests/ops/test_dataproc_batch.py` and `tests/ops/test_generation_vm.py`.
6. Docs: `docs/setup.md:323` paragraph, slice-5 note follow-ups (5)(6)(7) marked done, CLAUDE.md slice-5 TODO (6)
   items struck, K (ci-hygiene note V9, CLAUDE.md line 118 Linux skip list).

Out: any change to exit codes, preflight, submit argv, polling cadence or `USAGE_WAIT_SECONDS`; automatic retry or
waiting on IAM propagation (the hint tells the operator; resubmitting stays a human decision); detecting recent IAM
changes via audit logs; the infra cleanup (group E); anything live.

## Surfaces touched

| File | Change |
|---|---|
| `src/readmission_risk/ops/dataproc_batch.py` | new `never_ran`, `permission_hint`, `NEVER_RAN_STATES`, `PERMISSION_FAILURE_MARKER`, `PERMISSION_HINT`; `submit` lines 237-241 (usage wait) and 261-271 (reporting) |
| `src/readmission_risk/ops/generation_vm.py` | `launch`, the `else:` branch after `positive_control` (line 400); new `positive_control_ok_message` |
| `tests/ops/fake_gcloud.py` | `batches` entries gain `history`; `describe` emits `stateHistory`; new field `batch_state_message: str | None = None` |
| `tests/ops/test_dataproc_batch.py` | new tests (below) |
| `tests/ops/test_generation_vm.py` | `test_done_marker_then_delete_then_proof` asserts the OK line; new negative assertion in `test_positive_control_mismatch_deletes_and_exits_2` |
| `docs/setup.md:323` | append, at the end of the paragraph: "Since 2026-10-12 the submitter (`make bigjoin-batch`) returns as soon as a batch ends FAILED or CANCELLED without ever reaching RUNNING, instead of waiting 10 minutes for usage figures that such a batch never gets, and prints this propagation hint when the failure message contains \"Permission validation failed\"; the generation launcher prints `POSITIVE CONTROL OK: …` when its positive control passes." |
| `notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md` | "Added by 5b" (5) and (6) suffixed " **Done 2026-10-12** (`notes/eg-new-feature/ops-submitter-launcher-polish-2026-10-12.md`)"; (7) suffixed " **Partly done 2026-10-12** (same note): a hint on permission pre-check failures; no automatic wait or retry" |
| `notes/eg-new-feature/ci-hygiene-lockfiles-2026-10-12.md` | Results line "- **V9** CI: pending (the PR into `v2`)." replaced by: "- **V9** CI on PR #12 (run 38302549542, head 570ac07, merged as 050cf50): all four jobs green. `pipeline-dataproc-runtime`: 318 passed (the environment check ran and asserted pyspark 4.0.2). `lint-and-test`: 1225 passed, 4 skipped; the `-rs` list shows \"lock-main venv guard: macOS only …\" and \"terraform-fmt hook: needs terraform on PATH\" (the runner has no Terraform) and does not show \"lock-main OS guard: off-macOS only\" (so it ran); the other two skips are the Dataproc-environment check and `tests/reporting/test_style.py:82` (light font face, not listed in CLAUDE.md before; added by the ops bundle)." |
| `CLAUDE.md` | line 118: the lead-in "(1,229 tests since 2026-10-12, ci-hygiene: 1,227 passed + 2 skipped on macOS in about 3 minutes;" becomes "(<N> tests since 2026-10-12, ops bundle: <P> passed + <S> skipped on macOS in about 3 minutes;" with N, P, S from the final local `pytest -rs` summary (counted, not estimated), and the Linux skip list gains, after "the terraform-fmt flag test", the words ", and `tests/reporting/test_style.py`'s font check when the machine has a light font face (CI's ubuntu-24.04 runner: 1,225 passed, 4 skipped on PR #12)"; line 130 TODO (6): "the submitter's 10-minute usage wait after a pre-run failure" → "~~the submitter's 10-minute usage wait after a pre-run failure~~ (done 2026-10-12, ops bundle)"; "an explicit positive-control message" → "~~an explicit positive-control message~~ (done 2026-10-12, ops bundle)"; "the ~8-minute IAM propagation wait after a role change" → "the ~8-minute IAM propagation wait after a role change (partly done 2026-10-12: the submitter prints a hint on a permission pre-check failure; no automatic wait or retry)" (not struck) |

## Interfaces

### D1 — `never_ran`

```python
def never_ran(describe: dict) -> bool:
    """True when the batch ended FAILED or CANCELLED and Dataproc never moved it to RUNNING: it failed or was cancelled in
    validation/PENDING, so no usage will ever be computed (5b: batch …1318, stateHistory PENDING only, FAILED after 5 s).
    False when the history is missing or malformed: then the usage wait runs as before (the conservative default)."""
```
Rules, exactly:
- `describe.get("state") not in NEVER_RAN_STATES` → `False`, where `NEVER_RAN_STATES = ("FAILED", "CANCELLED")` (a new
  module constant). A `SUCCEEDED` batch certainly ran, so it never skips the usage wait, even with a lagging history.
- `history = describe.get("stateHistory")`; if `history` is not a non-empty `list` → `False`.
- If any element is not a `dict`, or has no `state` key whose value is a `str` → `False` (malformed).
- Otherwise → `True` iff no element has `element["state"] == "RUNNING"`.

`submit` change (replacing lines 237-241):
```python
        skipped_wait = never_ran(describe)
        if skipped_wait:
            ctx.echo(f"Batch {batch_id} ended {describe.get('state')}; stateHistory never reached RUNNING: skipping the usage wait")
        waited = 0.0
        while not skipped_wait and not (describe.get("runtimeInfo") or {}).get("approximateUsage") and waited < USAGE_WAIT_SECONDS:
            ...unchanged body...
```
Reporting (line 261), in this order of precedence:
1. `usage.dcu_hours is not None` → `f"{usage.dcu_hours:.3f}"` (usage present always wins, even when `skipped_wait`);
2. elif `skipped_wait` → `"none (stateHistory never reached RUNNING; if it did run, re-run gcloud dataproc batches describe later)"`;
3. else → the existing `"not yet populated (re-run gcloud dataproc batches describe later)"`.
The batch record JSON gains one key, `"never_ran": skipped_wait` (bool, the `never_ran(describe)` result for the
terminal describe), after `"usage"`.

Failure block (lines 266-271): when `usage.state != "SUCCEEDED"`:
1. echo `f"FAILED: {describe.get('stateMessage')}"` (unchanged first line);
2. echo the existing partial-gold cleanup line (unchanged text), always, also when `skipped_wait`: it is harmless when
   the prefix is empty, and it protects the case where `stateHistory` lagged behind a batch that did run;
3. `hint = permission_hint(describe.get("stateMessage"))`; if `hint` is not `None`: echo `hint`;
4. return `EXIT_FAILED` (unchanged).

Today items 1 and 2 are one `ctx.echo` call with an embedded newline; the implementation splits it into two calls with
the same text (`"FAILED: …"` and `"  if gs://…/ holds partial output, the owner deletes it: gcloud storage rm -r …/"`), so
the existing `test_failed_batch_exit_2_prints_cleanup` (which joins `echoed` with newlines) still passes unchanged.

### D3 — `permission_hint`

```python
PERMISSION_FAILURE_MARKER = "Permission validation failed"  # Dataproc's pre-check message prefix (5b, 2026-10-12)
PERMISSION_HINT = (
    "HINT: Dataproc's permission pre-check refused the batch before it ran (no cost). If an IAM role or binding for "
    f"{RUNTIME_SA} changed in the last ~8 minutes (e.g. the dataprocRuntimeWorker custom role), it may not have propagated "
    "yet: wait and resubmit. Otherwise grant the listed permissions in infra/iam.tf. See docs/setup.md, 'Lessons from the "
    "first live run'."
)

def permission_hint(state_message: object) -> str | None:
    """PERMISSION_HINT when Dataproc's stateMessage reports a failed permission pre-check, else None."""
    return PERMISSION_HINT if isinstance(state_message, str) and PERMISSION_FAILURE_MARKER in state_message else None
```
Case-sensitive substring match on the exact prefix Dataproc produced in 5b. A message that does not contain it (or is
`None`/not a string) gives no hint; the failure path is otherwise unchanged.

### D2 — positive-control OK line

```python
def positive_control_ok_message(name: str, max_run_seconds: int) -> str:
    return (
        f"POSITIVE CONTROL OK: {name} is labelled purpose=synthea-generation, runs the pinned startup script, is SPOT with "
        f"termination action DELETE and maxRunDuration {max_run_seconds}s, has no external IP and runs as {GENERATOR_SA}; "
        "its boot disk is labelled; both label filters list exactly this instance and disk"
    )
```
In `launch`, the `else:` branch after `if problems:` (line 400) first does
`ctx.echo(positive_control_ok_message(name, max_run_seconds))`, then the existing `wait_for_marker` call. The sentence
lists exactly the checks `positive_control` makes (lines 266-297), in its order.

### FakeGcloud changes (`tests/ops/fake_gcloud.py`)

- `submit`: the batch entry becomes `{"state": "PENDING", "states": list(self.batch_states), "history": ["PENDING"]}`.
- `describe`: before popping the next state, remember `previous = batch["state"]`; after popping, if the state changed
  and `previous` is not terminal and `previous != batch["history"][-1]`, append `previous` to `batch["history"]`. Emit
  `"stateHistory": [{"state": s, "stateStartTime": "2026-10-13T10:00:00.000Z"} for s in batch["history"]]`. (Matches the
  real shape: past states only, the current one excluded; e.g. states `["RUNNING", "SUCCEEDED"]` → history
  `["PENDING", "RUNNING"]` at SUCCEEDED; states `["FAILED"]` → history `["PENDING"]`.)
- New field `batch_state_message: str | None = None`: when not `None`, it replaces `"simulated"` as the `stateMessage` of
  a non-SUCCEEDED describe.
- Usage stays emitted for any terminal state when `batch_usage` is not `None` (existing behaviour). Tests of the
  never-ran path set `batch_usage = None` to mirror reality.

## Tests

`tests/ops/test_dataproc_batch.py`:
- `test_never_ran` parametrized over `(describe, expected)`:
  `{"state": "FAILED", "stateHistory": [{"state": "PENDING"}]}` → True;
  `{"state": "CANCELLED", "stateHistory": [{"state": "PENDING"}]}` → True;
  `{"state": "FAILED", "stateHistory": [{"state": "PENDING"}, {"state": "RUNNING"}]}` → False;
  `{"state": "SUCCEEDED", "stateHistory": [{"state": "PENDING"}, {"state": "RUNNING"}]}` → False;
  `{"state": "SUCCEEDED", "stateHistory": [{"state": "PENDING"}]}` → False (SUCCEEDED always ran);
  `{"state": "RUNNING", "stateHistory": [{"state": "PENDING"}]}` → False (not terminal);
  `{"state": "FAILED"}` → False (no history); `{"state": "FAILED", "stateHistory": []}` → False;
  `{"state": "FAILED", "stateHistory": "PENDING"}` → False; `{"state": "FAILED", "stateHistory": ["PENDING"]}` → False;
  `{"state": "FAILED", "stateHistory": [{}]}` → False; `{"state": "FAILED", "stateHistory": [{"state": None}]}` → False.
- `test_never_ran_batch_skips_usage_wait(env)`: `fake.batch_states = ["FAILED"]`, `fake.batch_usage = None`,
  `fake.batch_state_message = "Permission validation failed for operation x: Service Account 'dataproc-runtime@…' is missing required permissions: [dataprocrm.nodePools.update]."`;
  a recording `ctx.sleep`; assert exit `EXIT_FAILED`; the describe call count is exactly 1
  (`len(fake.ran("gcloud", "dataproc", "batches", "describe")) == 1`); `ctx.sleep` was never called; the text contains
  "stateHistory never reached RUNNING: skipping the usage wait", "DCU-hours=none (stateHistory never reached RUNNING", "holds partial
  output" (the cleanup line is always printed) and `db.PERMISSION_HINT`; the batch record has `"never_ran": true`.
- `test_usage_present_wins_over_never_ran(env)`: `batch_states = ["FAILED"]`, default `batch_usage` (present): exit
  `EXIT_FAILED`; describe count 1; text contains "stateHistory never reached RUNNING: skipping the usage wait" (the
  observation is still true and still printed) and "DCU-hours=2.000", and not "DCU-hours=none"; record `"never_ran": true`.
- `test_failed_after_running_still_waits_for_usage(env)`: `batch_states = ["RUNNING", "FAILED"]`, `batch_usage = None`;
  assert the describe count is 2 (polling) + 20 (usage wait: 600 / 30), the cleanup line is printed, no permission hint,
  record `"never_ran": false`.
- `test_permission_hint`: `permission_hint` of the 5b message (verbatim, from Why) → `PERMISSION_HINT`; of `"simulated"`,
  `""`, `None`, `42` → `None`; of `"permission validation failed"` (lower case) → `None`.
- Existing tests unchanged; `test_batch_record_and_usage_printed` additionally asserts `record["never_ran"] is False`.

`tests/ops/test_generation_vm.py`:
- `test_done_marker_then_delete_then_proof`: additionally assert
  `gv.positive_control_ok_message(gv.instance_name("pilot_20k", env["ctx"].now()), 7200) in text(env)` (the fake's
  template `synthea-generator-pilot-7200s` gives 7200; `instance_name` is the function `launch` uses, `generation_vm.py:215`).
- `test_positive_control_mismatch_deletes_and_exits_2`: additionally assert `"POSITIVE CONTROL OK" not in text(env)`.

`tests/ops/test_fake_gcloud.py` is not created; the fake's history is exercised through the submit tests above and one
direct test in `test_dataproc_batch.py`: `test_fake_state_history_matches_real_shape` — submit then describe twice with
`batch_states = ["RUNNING", "SUCCEEDED"]` → the second describe's `stateHistory` states are `["PENDING", "RUNNING"]`.

## UX flow

Operator runs `make bigjoin-batch RUN=…`. If Dataproc refuses the batch in validation, the submitter now returns within
one poll (about 30 s) instead of ~10 minutes and prints, in this order: "… stateHistory never reached RUNNING: skipping
the usage wait", the batch line with "DCU-hours=none (stateHistory never reached RUNNING; …)", the failure message, the
partial-gold cleanup line and, for a permission pre-check failure, the propagation hint. A generation launch prints `POSITIVE CONTROL OK: …` before it starts waiting
for `_DONE`.

## Data handling

None: operations CLIs only. No randomness, split, label or artifact is touched. The batch record JSON (gitignored
`data/pilot/<run>/`) gains a `never_ran` boolean; nothing reads that file programmatically.

## Failure modes

- `stateHistory` absent/malformed in a future API version → `never_ran` is `False` → the old 10-minute wait (safe).
- `stateHistory` well-formed but lagging (a terminal describe for a batch that did run, without RUNNING yet; not observed,
  only two real samples exist) → the wait is skipped and the report says "stateHistory never reached RUNNING; if it did
  run, re-run gcloud dataproc batches describe later": the usage is recoverable by hand, the wording states the
  observation rather than a conclusion, and the partial-gold cleanup line is still printed. Accepted.
- A batch that ran briefly then failed → history contains RUNNING → the wait runs (usage may still appear) → unchanged.
- Dataproc rewords the permission message → no hint; the failure line still prints the full message (degrades to today).
- `CANCELLED` while PENDING (e.g. the operator cancelled) → treated as never ran (no usage is computed for it).
- KeyboardInterrupt / GcloudError paths unchanged.

## Verification criteria

1. New and extended tests pass; `pytest tests/ops` and the full suite green; `ruff check .` clean.
2. Replaying the real 5b failed describe: `never_ran(json.load(open("data/pilot/pilot_20k/batch_bigjoin-pilot-20k-202610121318.json"))["describe"])`
   is `True`, and for `…1337` is `False`; `permission_hint` of …1318's `stateMessage` is `PERMISSION_HINT` (manual check,
   recorded in Results; the files are gitignored, so not a committed test).
3. Docs: every doc row of "Surfaces touched" applied with its given text; CLAUDE.md line 118's count equals the final
   `pytest` summary; `tests/test_docs.py` green.

## Out-of-scope follow-ups

- Automatic IAM-propagation wait or retry in the submitter.
- Reading audit logs to detect a recent role change.
- Pre-existing, unchanged: a Ctrl-C during the usage wait, after the batch is already terminal, prints "the batch keeps
  running" and writes no batch record (5b's `…1332` has no record for this reason). This change shortens that window for
  never-run batches but does not fix it.
- The remaining slice-5 follow-ups (group E infra cleanup, slice-6 items).

## Revision log

Revision 1 (after round 1; comprehension passed, critic 4, readiness 2). Critic → 1 a lagging `stateHistory`: cleanup
line always printed, the report states the observation and how to recover usage, failure mode listed. 2 precedence:
usage present wins, then never-ran, then not-yet-populated; new test. 3 `docs/setup.md` sentence given verbatim. 4
CLAUDE.md count updated from the final run. Readiness → 1 = critic 3; 2 = critic 4 plus the skip-entry wording.

Revision 2 (after round 2; readiness READY, critic 5). 1 the status line states the observation ("stateHistory never
reached RUNNING: skipping the usage wait") and is printed whenever the wait is skipped, also when usage is present
(asserted). 2 `never_ran` requires every element to carry a `str` `state` (else False); `[{}]` and `[{"state": None}]`
cases added. 3 docs sentence says "contains". 4 V9 and CLAUDE.md line-118 lead-in texts given verbatim. 5 follow-up (7)
marked partly done, not struck.

Revision 3 (after round 3; readiness READY, critic 2). 1 `never_ran` limited to FAILED/CANCELLED (`NEVER_RAN_STATES`): a
SUCCEEDED batch always waits for usage; SUCCEEDED + PENDING-only case added; docs sentence aligned. 2 UX flow aligned
with the Interfaces wording.

Round 4: critic `design ready`, readiness `implementation ready` (gate closed without override). Its non-blocking notes
were applied: UX-flow output order, and the pre-existing Ctrl-C-during-usage-wait gap listed as a follow-up.

## Results (implementation, 2026-10-12)

Branch `v2-ops-polish`, laptop (macOS arm64, Python 3.14.7).

- **V1** `pytest tests/ops`: 139 passed (122 before; 17 new: 12 `test_never_ran` cases, `test_permission_hint`,
  `test_fake_state_history_matches_real_shape`, `test_never_ran_batch_skips_usage_wait`,
  `test_usage_present_wins_over_never_ran`, `test_failed_after_running_still_waits_for_usage`; plus new assertions in
  three existing tests). All 122 existing ops tests passed unchanged before the new ones were added. Full `pytest -rs`:
  **1244 passed, 2 skipped** (Dataproc-environment check; "lock-main OS guard: off-macOS only"), 174 s. `ruff check .` clean.
- **V2** replay of the real 5b records: `…1318` → `never_ran` True, `permission_hint` = `PERMISSION_HINT`; `…1337` →
  `never_ran` False, no hint.
- **V3** docs: every "Surfaces touched" doc row applied with its given text; CLAUDE.md line 118 = 1,246 tests (1,244 + 2).
