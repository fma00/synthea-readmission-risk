# Generation launcher: diagnose a failed VM and hint at missing permissions (2026-10-13)

Design doc for `/eg-new-feature`. Branch `v2-ops-polish` (HEAD `07060b5`), same PR as the ops work. Laptop CLI only
(`make generate-vm`); every GCP call goes through the `ops.gcloud` Runner, faked in tests. No data, model, split or
Terraform change. No live launch (it costs money); the two facts the design rests on were measured read-only (Why).

## Why

Follow-up of `notes/eg-new-feature/ops-launcher-iam-wait-2026-10-13.md` ("a launcher-side hint when the VM fails in a way
that looks like missing permissions"); the owner asked for it on 2026-10-13 and chose scope **B**: read `_FAILED` on a
failed run, and read the VM's startup-script output from Cloud Logging when no marker appeared (owner declined scope C,
early detection during the wait).

How a generation VM fails today (`infra/generation/startup.sh`, `ops/generation_vm.py`):
- On any error the startup script writes the last 200 log lines as `_FAILED` under the raw prefix (`startup.sh:21-48`)
  and idles. The launcher's `wait_for_marker` (`generation_vm.py:327-340`) sees `_FAILED` and `launch` prints only
  `FAILED: generation failed: see gs://…/_FAILED` (`generation_vm.py:449`). It never reads the file.
- If the failing permission is the raw-bucket write, the `_FAILED` upload fails too (`|| true`), the VM idles until
  `max_run_duration` deletes it, and the launcher reports `vanished` or `timeout` (`generation_vm.py:450-451`) with no
  diagnosis at all.
- The script's output also goes to Cloud Logging through the guest agent (`startup.sh:9-11`; the generator SA holds
  `roles/logging.logWriter`), so it outlives the VM.

Facts measured 2026-10-13 (owner-approved, read-only):
1. **403 wording**, from `gcloud storage ls gs://synthea-readmission-risk-raw/` impersonating `scorer@…` (no access):
   `ERROR: (gcloud.storage.ls) [scorer@synthea-readmission-risk.iam.gserviceaccount.com] does not have permission to
   access b instance [synthea-readmission-risk-raw] (or it may not exist): scorer@synthea-readmission-risk.iam.gserviceaccount.com
   does not have storage.objects.list access to the Google Cloud Storage bucket. Permission 'storage.objects.list' denied
   on resource '//storage.googleapis.com/projects/_/buckets/synthea-readmission-risk-raw' (or it may not exist). …`
   It contains no "HTTPError 403"; the reliable anchors name the permission: `does not have <perm> access` and
   `Permission '<perm>' denied`. **Assumed, not measured:** the wording of `gcloud storage cp` (the VM's downloads need
   `storage.objects.get`, its uploads `storage.objects.create`) on the VM's own, unpinned gcloud. Cloud Storage's
   server message carries the same two anchors, so the patterns are expected to match it too; the tests include a
   plausible `cp` form for that reason, and a non-matching wording degrades to "evidence lines, no hint".
2. **Startup-script log entries** of the 5b VM (2026-10-12): `logName` =
   `projects/synthea-readmission-risk/logs/google_metadata_script_runner`; the VM name is the top-level label
   `labels.instance_name` (e.g. `synthea-gen-pilot-20k-202610121245`); `resource.type` = `gce_instance`; each script
   line is `jsonPayload.message` = `Metadata key("startup-script"), command("/bin/bash"): <line>`; `timestamp` is RFC 3339
   with nanoseconds.

## Scope

In:
1. Pure functions in `ops/generation_vm.py`: `missing_permissions`, `permission_hint`, `vm_log_argv`, `vm_log_lines`.
2. `diagnose_failure(ctx, run, name, state, since)`: never raises; for `failed` reads `_FAILED`, for `vanished` and
   `timeout` reads the VM's Cloud Logging lines; prints the last lines found and, when they name denied permissions, the
   hint.
3. `launch` calls it in the `state != "done"` branch, after the existing `FAILED: …` line, before teardown.
4. `tests/ops/fake_gcloud.py`: no change needed (`storage cat` and `logging read` already exist); tests queue responses.
5. Tests, docs (`docs/setup.md`, module and script docstrings untouched except where named), the IAM-wait note's
   follow-up marked done, CLAUDE.md test count.

Out: early detection during the wait (scope C, declined); changing the startup script; the positive-control failure
path (a different failure, already reported in detail); a hint for non-permission failures.

## Surfaces touched

| File | Change |
|---|---|
| `src/readmission_risk/ops/generation_vm.py` | new constants and four pure functions, `diagnose_failure`, one call in `launch` (lines 447-454 region) |
| `tests/ops/test_generation_vm.py` | new tests |
| `docs/setup.md:302`, `:323` | one sentence appended; one phrase extended (texts in Verification 3) |
| `src/readmission_risk/ops/iam_propagation.py:103` | one docstring phrase (Verification 3) |
| `notes/eg-new-feature/ops-launcher-iam-wait-2026-10-13.md` | follow-up bullet "A launcher-side hint when the VM fails in a way that looks like missing permissions." suffixed " **Done 2026-10-13** (`notes/eg-new-feature/ops-launcher-permission-hint-2026-10-13.md`)" |
| `CLAUDE.md` line 118 | test count from the final run |

## Interfaces (all in `ops/generation_vm.py`)

```python
SCRIPT_RUNNER_LOG = f"projects/{PROJECT}/logs/google_metadata_script_runner"
STARTUP_LINE_PREFIX = 'Metadata key("startup-script"), command("/bin/bash"): '
# Both forms name the denied permission (measured 2026-10-13, gcloud storage as a service account without access)
_DENIED_PERMISSION = re.compile(r"Permission '([A-Za-z0-9_.]+)' denied")
_LACKS_PERMISSION = re.compile(r"does not have ([a-z][A-Za-z0-9_]*\.[A-Za-z0-9_.]+) access")
DIAGNOSIS_TAIL_LINES = 15
# The log query starts this long before the laptop's creation time: laptop and Cloud Logging clocks can differ, and the
# instance name (unique per launch minute; P3 refuses leftover VMs) already confines the query to this VM.
VM_LOG_MARGIN = timedelta(minutes=10)
```
(`timedelta` added to the module's existing `from datetime import UTC, datetime` import.)

```python
def missing_permissions(text: str) -> list[str]:
    """Sorted, de-duplicated IAM permission names that `text` reports as denied (both message forms)."""
    return sorted(set(_DENIED_PERMISSION.findall(text)) | set(_LACKS_PERMISSION.findall(text)))


def permission_hint(permissions: list[str], run: str) -> str:  # `run` added in pre-commit review round 1
    return (
        f"HINT: the VM ({GENERATOR_SA}) was denied {', '.join(permissions)}. If an IAM role or binding changed in the last "
        "minutes, it may not have propagated yet (the launcher only waits for changes the audit log already shows): wait, "
        f"delete the partial raw prefix (gcloud storage rm -r {raw_prefix(run)}/) and rerun. Otherwise grant the permission "
        "to the synthea-generator service account in infra/iam.tf. See docs/setup.md, 'Lessons from the first live run'."
    )


def vm_log_argv(name: str, since: datetime) -> list[str]:
    query = (
        f'logName="{SCRIPT_RUNNER_LOG}" AND labels.instance_name="{name}" '
        f'AND timestamp>="{since.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}" '
        'AND (jsonPayload.message:"denied" OR jsonPayload.message:"does not have" OR jsonPayload.message:"startup.sh FAILED")'
    )
    return ["gcloud", "logging", "read", query, "--limit=200", "--order=asc"]


def vm_log_lines(entries: object) -> list[str]:
    """The startup-script lines in `gcloud logging read --format=json` output, in the given order: each entry's
    jsonPayload.message, with STARTUP_LINE_PREFIX removed when present (kept as is otherwise: the VM image, hence the
    guest agent's prefix format, is not pinned, and the query already narrowed the entries to this VM and to matching
    terms); entries that are not dicts, have no dict jsonPayload or no str message are skipped. Non-list input -> []."""
```

```python
def diagnose_failure(ctx: LaunchContext, run: str, name: str, state: str, since: datetime) -> None:
    """After a failed run: show the evidence and, when it names denied permissions, the hint. Never raises for its own
    reads: GcloudError/ValueError become one 'could not read …' line, and a KeyboardInterrupt during the diagnosis is
    caught and reported, so the run's exit code stays 2 (failed) and teardown proceeds."""
```
Behaviour, exactly:
- `source` is assigned FIRST, before any read (the error handler formats it): `state == "failed"`: `source = "_FAILED"`,
  then `text = run_ok(ctx.run, ["gcloud", "storage", "cat", f"{raw_prefix(run)}/_FAILED"])`.
- `state in ("vanished", "timeout")`: `source` (below) first, then `lines = vm_log_lines(run_json(ctx.run, vm_log_argv(name, since - VM_LOG_MARGIN)))`, `text =
  "\n".join(lines)`; `source = "the VM's startup-script log in Cloud Logging (lines mentioning a denial or a startup.sh
  failure)"`. The query returns only lines matching `denied`, `does not have` or `startup.sh FAILED`, oldest first.
- any other `state`: return without output.
- On `GcloudError` or `ValueError` while reading: `ctx.echo(f"(could not read {source} for a diagnosis: {str(exc).splitlines()[-1]})")`
  and return.
- `except KeyboardInterrupt`: `ctx.echo("(diagnosis interrupted)")` and return.
- Non-empty lines means `line.strip() != ""`.
- If there are no non-empty lines: for `_FAILED`, `ctx.echo("(no lines in _FAILED)")`; for the log,
  `ctx.echo("(no denial or startup.sh failure lines in the VM's startup-script log; the VM may have been preempted, or still been working at the timeout)")`;
  then return.
- `_FAILED`: `ctx.echo("Last lines of _FAILED:")`, then the LAST `DIAGNOSIS_TAIL_LINES` non-empty lines (the log tail ends
  at the failure). Log: `ctx.echo(f"First lines of {source}:")`, then the FIRST `DIAGNOSIS_TAIL_LINES` non-empty lines (the
  earliest denial is the cause). Each echoed as `"  " + line`.
- Then `perms = missing_permissions(text)` over the whole text read; if `perms`: `ctx.echo(permission_hint(perms, run))`.

`launch` change (the `if state != "done":` branch, `generation_vm.py:447-454`): after both existing statements
`ctx.echo(f"FAILED: {messages[state]}")` and `outcome = EXIT_FAILED`, add `diagnose_failure(ctx, options.run, name, state, created_at)`, where `created_at = ctx.now()` is taken immediately
before the `run_ok(ctx.run, create_argv(...))` call (line 439) and stored in a local. `since` therefore bounds the log
query to this VM's life (the name is also unique per launch minute).

## Tests (`tests/ops/test_generation_vm.py`)

Constants: `DENIED_TEXT` = ADAPTED from the measured message in Why, fact 1 (not itself measured): `scorer@` replaced by
`synthea-generator@`, the permission `storage.objects.create`, the bucket raw: concretely
`"ERROR: (gcloud.storage.cp) [synthea-generator@synthea-readmission-risk.iam.gserviceaccount.com] does not have permission to access b instance [synthea-readmission-risk-raw] (or it may not exist): synthea-generator@synthea-readmission-risk.iam.gserviceaccount.com does not have storage.objects.create access to the Google Cloud Storage bucket. Permission 'storage.objects.create' denied on resource '//storage.googleapis.com/projects/_/buckets/synthea-readmission-risk-raw' (or it may not exist)."`.
A helper `vm_log_entry(line: str) -> dict` returns
`{"timestamp": "2026-10-13T09:10:00.123456789Z", "labels": {"instance_name": "x"}, "jsonPayload": {"message": gv.STARTUP_LINE_PREFIX + line}}`.

Pure-function tests:
- `test_missing_permissions`: `DENIED_TEXT` → `["storage.objects.create"]`; the plausible (assumed) `cp` form
  `"ERROR: (gcloud.storage.cp) HTTPError 403: synthea-generator@synthea-readmission-risk.iam.gserviceaccount.com does not have storage.objects.get access to the Google Cloud Storage object. Permission 'storage.objects.get' denied on resource (or it may not exist)."`
  → `["storage.objects.get"]`; a text with both `Permission 'storage.objects.get' denied` and
  `does not have storage.buckets.get access` → `["storage.buckets.get", "storage.objects.get"]`; `"generation failed: OOM"` → `[]`;
  `"does not have permission to access b instance"` alone → `[]` (no dotted permission).
- `test_vm_log_argv`: for `name = "synthea-gen-pilot-20k-202610130905"`, `since = datetime(2026, 10, 13, 9, 5, tzinfo=UTC)` the argv equals
  `["gcloud", "logging", "read", 'logName="projects/synthea-readmission-risk/logs/google_metadata_script_runner" AND labels.instance_name="synthea-gen-pilot-20k-202610130905" AND timestamp>="2026-10-13T09:05:00Z" AND (jsonPayload.message:"denied" OR jsonPayload.message:"does not have" OR jsonPayload.message:"startup.sh FAILED")', "--limit=200", "--order=asc"]`.
- `test_vm_log_lines`: `[vm_log_entry("a"), {"jsonPayload": {"message": "No shutdown scripts to run"}}, "x", {"jsonPayload": "y"}, {"jsonPayload": {"message": 7}}, vm_log_entry("b")]` → `["a", "No shutdown scripts to run", "b"]`; `None` → `[]`.
- `test_unprefixed_log_line_still_hints(env)`: `on_create = None`; responses `[[], [{"jsonPayload": {"message": "startup-script: " + DENIED_TEXT}}]]`
  (a different prefix); the hint is in text and "(no denial" is not.

Launcher tests (the fixture's `now` is fixed at 09:05; the IAM wait consumes the first `logging read` response, so failure
tests queue `[]` first):
- `test_failed_marker_shows_tail_and_permission_hint(env)`: `on_create` writes `_FAILED` = `"line 1\n" + DENIED_TEXT + "\n"`;
  exit `EXIT_FAILED`; text contains "Last lines of _FAILED:", `"  " + DENIED_TEXT`, and `gv.permission_hint(["storage.objects.create"], RUN)`;
  the VM is still torn down (`fake.instances == {}`).
- `test_failed_marker_without_permission_error_has_no_hint(env)`: `_FAILED` = `"java.lang.OutOfMemoryError\n"`; text contains
  "Last lines of _FAILED:" and "  java.lang.OutOfMemoryError", and not "HINT: the VM".
- `test_failed_marker_shows_only_the_last_lines(env)`: `_FAILED` = 30 lines `"l00"` … `"l29"`; the text contains `"  l29"`
  and `"  l15"` and not `"  l14"`.
- `test_timeout_reads_vm_log_and_hints(env)`: `on_create = None`; `fake.logging_responses = [[], [vm_log_entry("startup.sh FAILED at …: line 97: gcloud storage cp …"), vm_log_entry(DENIED_TEXT)]]`;
  and a MOVING clock: `env["ctx"].now = lambda: datetime(2026, 10, 13, 9, 5, tzinfo=UTC) + timedelta(seconds=env["ctx"].monotonic())`
  (the fixture's `sleep` advances `monotonic`, so diagnosis time is ~2 h after creation); exit `EXIT_FAILED`; "timed out"
  and "First lines of the VM's startup-script log in Cloud Logging" and the hint in text; the second `logging read` call's
  query contains `labels.instance_name="synthea-gen-pilot-20k-202610130905"` and `timestamp>="2026-10-13T08:55:00Z"`
  (creation time minus `VM_LOG_MARGIN`; a diagnosis-time `since` would read about 11:00 and fail this assertion).
- `test_log_diagnosis_prints_first_matches(env)`: `on_create = None`; responses `[[], [vm_log_entry(f"m{i:02d}") for i in range(20)]]`;
  "  m00" and "  m14" in text, "  m15" not in text.
- `test_ctrl_c_during_diagnosis_keeps_exit_2(env)`: `on_create` writes `_FAILED` = `"x"`; monkeypatch the fake's `storage
  cat` path by setting `fake.fail_on = ()` and wrapping `env["ctx"].run` so that a call whose argv starts with `["gcloud",
  "storage", "cat"]` raises `KeyboardInterrupt` and every other call goes to the fake; exit `EXIT_FAILED`; "(diagnosis
  interrupted)" in text; `fake.instances == {}` (teardown ran).
- `test_vanished_reads_vm_log(env)`: as the existing vanished test (preempting `sleep`), with `logging_responses = [[], [vm_log_entry(DENIED_TEXT)]]`;
  exit `EXIT_FAILED`; "preempted or max-run-duration" and the hint in text.
- `test_unreadable_diagnosis_is_reported_not_raised(env)`: `on_create` writes `_FAILED` = `"x"`; `fake.fail_on = ("gcloud storage cat",)`;
  exit `EXIT_FAILED` (not a crash); "(could not read _FAILED for a diagnosis: ERROR: simulated failure)" in text; teardown
  ran (`fake.instances == {}`).
- `test_empty_vm_log_says_so(env)`: `on_create = None`; responses `[[], []]`; "(no denial or startup.sh failure lines in the VM's startup-script log" in text.
- Existing `test_failed_marker_exit_2_and_proof_runs`, `test_timeout_exit_2_deletes`, `test_vanished_without_marker_exit_2`
  stay unchanged and green (their `_FAILED`/log reads return "log tail" / `[]`).

## UX flow

`make generate-vm` → … → the VM fails → `FAILED: <reason>` (unchanged) → `Last lines of _FAILED:` (or of the Cloud
Logging startup log) with up to 15 lines → `HINT: the VM (synthea-generator@…) was denied storage.objects.create. …` when
the evidence names denied permissions → teardown and proof as before; exit 2.

## Data handling

None: operations CLI only; it reads text the VM already wrote. No secrets: `_FAILED` and the script log are the
operator's own run output.

## Failure modes

- `_FAILED` or the log unreadable (permissions, Logging API, network) → one "could not read" line; exit code and
  teardown unchanged.
- Laptop clock skew against Cloud Logging's timestamps: absorbed by `VM_LOG_MARGIN` (10 min); the instance-name filter
  keeps other VMs out.
- Log ingestion lag: lines from the last seconds before the outcome may not be readable yet. Accepted: the failure
  normally happened long before a timeout or vanish is detected (minutes to hours).
- A permission error that the script survived (a `|| true` command) also shows up in the log scan and triggers the hint.
  Accepted: the hint says "was denied", which stays true; the evidence lines are printed next to it.
- More than 200 matching log lines: only the first 200 in time order are read (`--order=asc`). The first denial is
  what matters.
- Wording drift by Google: no match → evidence lines without hint (degrades to "here is what failed").

## Verification criteria

1. New tests pass; `pytest tests/ops`, the full `pytest` and `ruff check .` green.
2. **Live, read-only, owner-confirmed (V-live):** with `CLOUDSDK_CORE_PROJECT=synthea-readmission-risk`, run the shipped
   `vm_log_argv("synthea-gen-pilot-20k-202610121245", datetime(2026, 10, 12, 12, 0, tzinfo=UTC))` query with ONE term added
   inside its OR-group, ` OR jsonPayload.message:"startup.sh done"` (the 5b run succeeded, so no shipped term matches);
   it must exit 0 (the shipped filter syntax, the three-way OR group and `--order=asc` accepted), and `vm_log_lines` of the
   result must contain a line starting `startup.sh done at 2026-10-12T12:51:52Z`. Recorded in Results.
3. Docs: `docs/setup.md:302` (launcher paragraph) gains at its end: "When the VM fails, the launcher prints the last
   lines of `_FAILED` (or, when no marker appeared, the first lines of the VM's startup-script log in Cloud Logging that
   mention a denial or a startup.sh failure) and, when they name a denied permission, a hint about IAM propagation and
   `infra/iam.tf`."; in `docs/setup.md:323`, "for the launcher a miss means a VM that boots and fails" becomes "for the
   launcher a miss means a VM that boots and fails; the launcher then names the denied permission when the VM's output
   names one (if the VM cannot even write `_FAILED`, only after `max_run_duration` has ended it)"; in
   `src/readmission_risk/ops/iam_propagation.py:103` (the `wait_for_iam_propagation` docstring) "(the Big Join
   submitter's failure-path hint still applies; the launcher has none)" becomes "(the Big Join submitter prints a hint
   when Dataproc's permission pre-check failed; the generation launcher when the VM's failure output names a denied
   permission)"; CLAUDE.md line 118 lead-in "(<N> tests since
   2026-10-13, launcher permission hint: <P> passed + <S> skipped on macOS in about 3 minutes;" from the final `pytest -rs`;
   `tests/test_docs.py` green.

## Out-of-scope follow-ups

- Early detection (scope C): poll the VM's log during the wait so a VM that cannot write `_FAILED` is deleted within
  minutes instead of idling until `max_run_duration` (cost).

## Revision log

Revision 1 (after round 1; comprehension passed, readiness READY, critic 6). 1 the call comes after `outcome =
EXIT_FAILED` and the diagnosis catches KeyboardInterrupt, so exit 2 survives (new test). 2 the log source is labelled as
matching lines, the FIRST 15 are printed, and the empty case says what it means (new test). 3 a moving clock in the timeout
test pins `since` to creation time. 4 V-live runs the shipped filter plus one OR term. 5 `iam_propagation.py` docstring and
`docs/setup.md:323` updated. 6 the never-raises scope is explicit.

Revision 2 (after round 2; readiness READY, critic 3). 1 `vm_log_lines` strips the guest-agent prefix only when present
(the image is unpinned), new test with a different prefix. 2 the setup.md and docstring texts state when the hint appears
and that a VM unable to write `_FAILED` is diagnosed only after `max_run_duration`. 3 `source` is assigned before the read.

Revision 3 (after round 3; readiness READY, critic 2). 1 the log query starts `VM_LOG_MARGIN` (10 min) before creation
(clock skew); the timeout test asserts 08:55. 2 the `iam_propagation.py` docstring states each CLI's hint condition.

Revision 4 (after round 4; readiness READY, critic 1; owner chose "fix it and implement" at the 3-revision cap). The
`gcloud storage cp` wording on the VM is stated as assumed, `DENIED_TEXT` as adapted, and a plausible `HTTPError 403` cp
form is added to `test_missing_permissions`.

## Results (implementation, 2026-10-13)

Design gate: the critic did not formally close (rounds 1-4: 6, 3, 2, 1 gaps); readiness closed in rounds 1-4; the owner
chose "fix it and implement" at the 3-revision cap after round 4 (revision 4 applied).

- **V1** `tests/ops/test_generation_vm.py`: 55 passed (13 new: `test_missing_permissions`, `test_vm_log_argv`,
  `test_vm_log_lines`, and 10 launcher tests); the 171 existing ops tests passed unchanged with the diagnosis wired in
  before the new tests were added. Full `pytest -rs`: **1289 passed, 2 skipped** (Dataproc-environment check; "lock-main
  OS guard: off-macOS only"). `ruff check .` clean.
- **V2 (V-live)**, owner-confirmed, read-only, 2026-10-13, `CLOUDSDK_CORE_PROJECT=synthea-readmission-risk`: the shipped
  `vm_log_argv("synthea-gen-pilot-20k-202610121245", 2026-10-12T12:00Z)` query with ` OR jsonPayload.message:"startup.sh done"`
  added inside its OR group exited 0 and `vm_log_lines` returned exactly one line, `startup.sh done at 2026-10-12T12:51:52Z`.
  **Pass.** Side result: the successful 5b run's log has no line matching `denied` or `does not have` (no false positive
  on a healthy run).
- **V3** docs: `docs/setup.md:302` and `:323`, `iam_propagation.py:103`, the IAM-wait note's follow-up; CLAUDE.md line 118
  = 1,291 tests (1,289 + 2); `tests/test_docs.py` green.
- **Pre-commit review round 1** (2 findings): (1) fixed: the hint's "wait and rerun" would be refused by preflight P4
  (the failed run left files under the raw prefix); `permission_hint(permissions, run)` now includes
  `gcloud storage rm -r <raw prefix>/`, asserted in `test_failed_marker_shows_tail_and_permission_hint`. (2) rebutted:
  "not a bug: the untracked design note is staged at commit time (`git add -A`, as for every commit on this branch);
  nothing is committed yet."

