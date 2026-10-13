# Ops follow-ups: pre-submit IAM-propagation wait from the audit log; Ctrl-C after a finished batch still writes its record (2026-10-12)

Design doc for `/eg-new-feature`. Same branch and PR as the ops bundle (`v2-ops-polish`, commit `ab7d9f3`,
`notes/eg-new-feature/ops-submitter-launcher-polish-2026-10-12.md`). Laptop CLI only (`make bigjoin-batch`); every GCP
call goes through the `ops.gcloud` Runner and is faked in tests. No data, model, split or Terraform change.

Shorthand: **F12** the pre-submit IAM check (follow-ups 1 "automatic IAM-propagation wait" and 2 "detect a recent role
change from audit logs"); **F3** the Ctrl-C record fix.

## Why

The three out-of-scope follow-ups of the ops bundle note; the owner asked to address them before PR merge (2026-10-12).
Owner decisions (2026-10-12): F12 is a **pre-submit audit-log check with no automatic resubmission**; when it finds a
recent change it **waits automatically**; full design-doc + goldfish process.

Evidence, read-only audit-log query on project `synthea-readmission-risk` (owner-approved, 2026-10-12), exactly:
`logName="projects/synthea-readmission-risk/logs/cloudaudit.googleapis.com%2Factivity" AND timestamp>="2026-10-12T12:00:00Z"
AND timestamp<="2026-10-12T15:00:00Z" AND (protoPayload.methodName:"Role" OR protoPayload.methodName:"IamPolicy" OR
protoPayload.methodName:"IAMPolicy" OR protoPayload.methodName:"setIamPermissions")`, `--limit=50`. The `:` operator
is a substring match, so this covers every method in `IAM_CHANGE_METHODS` (all Create/Update/Delete/UndeleteRole,
both SetIamPolicy spellings, `storage.setIamPermissions`); it returned 7 entries, fewer than the limit, so the window is
complete:

| timestamp (Z) | methodName | principal | what |
|---|---|---|---|
| 12:21:22 | `SetIamPolicy` (cloudresourcemanager, project) | owner | REMOVE `roles/dataproc.worker` from `dataproc-runtime` |
| 12:21:29 | `google.iam.admin.v1.CreateRole` | owner | `roles/dataprocRuntimeWorker` |
| 12:21:33 | `SetIamPolicy` (project) | owner | ADD the custom role to `dataproc-runtime` |
| 12:38:32 / 12:40:33 | `google.iam.admin.v1.SetIAMPolicy` (service account) | owner | add/remove TokenCreator for the owner |
| 13:31:54 | `google.iam.admin.v1.UpdateRole` | owner | `dataprocRuntimeWorker` (+ `dataprocrm.nodePools.update`) |
| 13:37:29 | `SetIamPolicy` (project) | `service-agent-manager@system.gserviceaccount.com` | ADD `roles/dataprocrm.nodeServiceAgent` to Dataproc's node service agent, one second after batch `…1337` was created |

Consequences: batch `…1332` (submitted ~13:32, <1 min after the 13:31:54 update) failed validation; `…1337` (13:37:28,
~5.6 min after) succeeded, so propagation took <5.6 min that time and the documented 8-minute rule is conservative.
Google-managed principals (the `@system.gserviceaccount.com` service-agent manager, per-service agents
`service-<n>@gcp-sa-….iam.gserviceaccount.com`, the Google APIs agent `<n>@cloudservices.gserviceaccount.com`) change IAM
as a side effect of our own batches, so they must be ignored or the check would delay the next submission for nothing. Every human/Terraform change appears under the
operator's principal, so matching on the **method name and a non-system principal** suffices; no binding parsing.

**F3** (pre-existing, `dataproc_batch.py` `submit`, the `except KeyboardInterrupt` branch): a Ctrl-C during the usage
wait, i.e. after the batch is already terminal, prints "the batch keeps running … cancel it" (false) and writes no
batch record; 5b's batch `…1332` has no record for this reason.

## Scope

In:
1. F12 in `src/readmission_risk/ops/dataproc_batch.py`: constants, `IamChange`, `iam_changes_argv`,
   `parse_iam_changes`, `wait_for_iam_propagation`; called by `submit` just before the batch is submitted; a dry run
   only prints that it would check.
2. F3 in `submit`: the post-submit tail (record + report + failure lines) moves into a helper used by both the normal
   path and the interrupt path when the batch is already terminal.
3. `tests/ops/fake_gcloud.py`: `gcloud logging read` support.
4. Tests in `tests/ops/test_dataproc_batch.py`.
5. Docs: module docstring (exit codes), `docs/setup.md` (lessons paragraph), CLAUDE.md TODO (6), the ops-bundle
   note's follow-up list.

Out: automatic resubmission after a failure; the generation launcher (`generation_vm.py`; its SA and failure modes
differ); BigQuery dataset IAM methods (the runtime SA does not use BigQuery); any infra/role change (granting
`logging.viewer` is not needed: the operator is project owner).

## Surfaces touched

| File | Change |
|---|---|
| `src/readmission_risk/ops/dataproc_batch.py` | new constants and functions (Interfaces); `submit` restructured as described; module docstring |
| `tests/ops/fake_gcloud.py` | new field `logging_responses`; `("logging", "read")` handler |
| `tests/ops/test_dataproc_batch.py` | new tests; `test_dry_run_makes_no_runner_call` gains one assertion |
| `docs/setup.md:323` | one sentence appended (text below) |
| `docs/setup.md:311` | "Ctrl-C leaves the batch running (bounded by its ttl); cancel it with `gcloud dataproc batches cancel <id> --region=us-central1`." → "Ctrl-C while the batch runs leaves it running (bounded by its ttl); cancel it with `gcloud dataproc batches cancel <id> --region=us-central1`. Ctrl-C during the pre-submit IAM wait submits nothing; Ctrl-C (or a gcloud error) during the usage wait of a finished batch still writes its record." |
| `scripts/submit_big_join_batch.py:3-6` docstring | "submits with maxExecutors=7; waits;" → "waits for IAM propagation if a role or binding changed in the last 8 minutes (audit log); submits with maxExecutors=7; waits;", and "Exit codes: 0 succeeded, 1 refused, 2 failed, 130 interrupted." → "Exit codes: 0 succeeded, 1 refused, 2 failed, 130 interrupted (see ops/dataproc_batch.py)." |
| `CLAUDE.md` line 130 | TODO (6) IAM item text replaced (text below); line 118 test count from the final run |
| `notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md` "Added by 5b" (7) | "**Partly done 2026-10-12** (same note): a hint on permission pre-check failures; no automatic wait or retry" → "**Done 2026-10-12** (`notes/eg-new-feature/ops-submitter-launcher-polish-2026-10-12.md`, `notes/eg-new-feature/ops-iam-propagation-and-interrupt-2026-10-12.md`): a failure hint plus a pre-submit audit-log check that waits; no automatic resubmission" |
| `notes/eg-new-feature/ops-submitter-launcher-polish-2026-10-12.md` | "Out-of-scope follow-ups": the first three bullets (automatic IAM-propagation wait or retry; reading audit logs to detect a recent role change; the pre-existing Ctrl-C-during-usage-wait record gap) suffixed " **Done 2026-10-12** (`notes/eg-new-feature/ops-iam-propagation-and-interrupt-2026-10-12.md`)"; the fourth bullet ("The remaining slice-5 follow-ups …") is unchanged |

## Interfaces

### F12 constants (in `dataproc_batch.py`, after `PERMISSION_HINT`)

```python
from datetime import UTC, datetime, timedelta  # UTC and timedelta added to the existing datetime import
from math import ceil

IAM_PROPAGATION_SECONDS = 480  # the ~8-minute rule (docs/setup.md); 5b measured < 5.6 min once
MAX_IAM_WAITS = 3  # waits for successive changes before refusing
AUDIT_LOG = f"projects/{PROJECT}/logs/cloudaudit.googleapis.com%2Factivity"  # Admin Activity, always on
IAM_CHANGE_METHODS = (  # methodName values that change who may do what (5b audit log, 2026-10-12, plus bucket IAM)
    "SetIamPolicy",  # project policy (cloudresourcemanager)
    "google.iam.admin.v1.SetIAMPolicy",  # a service account's policy (actAs, token creation)
    "google.iam.admin.v1.CreateRole",
    "google.iam.admin.v1.UpdateRole",
    "google.iam.admin.v1.DeleteRole",
    "google.iam.admin.v1.UndeleteRole",
    "storage.setIamPermissions",  # bucket policy: dataproc-runtime's grants are bucket-level
)
# Google-managed principals change IAM as a side effect of our own batches (5b: service-agent-manager@system... at 13:37:29)
GOOGLE_MANAGED_PRINCIPAL = re.compile(
    r".*@system\.gserviceaccount\.com"  # e.g. service-agent-manager@system.gserviceaccount.com
    r"|service-[0-9]+@gcp-sa-[a-z0-9-]+\.iam\.gserviceaccount\.com"  # per-service agents
    r"|[0-9]+@cloudservices\.gserviceaccount\.com"  # the Google APIs service agent
)
# (`import re` added to the module imports)
```

### `IamChange` and `parse_iam_changes`

```python
@dataclass(frozen=True)
class IamChange:
    timestamp: datetime  # UTC, aware
    method: str
    principal: str  # "" when the entry has none
    resource: str  # protoPayload.resourceName, "" when absent


def parse_iam_changes(entries: object, *, after: datetime) -> list[IamChange]:
    """IAM changes strictly after `after`, by non-system principals, oldest first. `entries` is `gcloud logging read
    --format=json` output (a list; run_json returns None for empty output, treated as no entries)."""
```
Rules, exactly:
- `entries` that is not a `list` → `[]`.
- For each element: skip unless it is a `dict` whose `protoPayload` is a `dict`.
- `method = protoPayload.get("methodName")`; skip unless `method in IAM_CHANGE_METHODS`.
- `auth = protoPayload.get("authenticationInfo")`; `principal = auth.get("principalEmail") if isinstance(auth, dict) else None`;
  if `principal` is not a `str`, use `""`. Skip if `GOOGLE_MANAGED_PRINCIPAL.fullmatch(principal)`. (A missing or
  non-string principal becomes `""` and is kept, conservatively.)
- `raw = element.get("timestamp")`; skip the element if `raw` is not a `str` (checked BEFORE parsing: `_parse_time`
  of a non-str raises AttributeError); `timestamp = _parse_time(raw)` (the existing helper; it truncates nanoseconds to
  microseconds on Python 3.12 and 3.14, checked 2026-10-12); skip the element if parsing raises
  `ValueError`/`TypeError`, or the result is `None`, or the result is naive (`timestamp.tzinfo is None`, e.g. a value
  without `Z` or an offset); skip unless `timestamp > after`.
- `resource = protoPayload.get("resourceName")` if it is a `str`, else `""`.
- Return the kept `IamChange`s sorted by `timestamp` ascending.

### `iam_changes_argv`

```python
def iam_changes_argv(since: datetime) -> list[str]:
    methods = " OR ".join(f'"{m}"' for m in IAM_CHANGE_METHODS)
    query = (
        f'logName="{AUDIT_LOG}" AND timestamp>="{since.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}" '
        f"AND protoPayload.methodName=({methods})"
    )
    return ["gcloud", "logging", "read", query, "--limit=100"]
```
(`run_json` appends `--format=json`; `UTC` is imported from `datetime`.) The gcloud filter only narrows the query;
`parse_iam_changes` is the source of truth.

### `wait_for_iam_propagation`

```python
def wait_for_iam_propagation(ctx: LaunchContext) -> float:
    """Before submitting: if a non-system principal changed IAM within IAM_PROPAGATION_SECONDS, wait until that much time
    has passed since the latest change, then look again for newer changes. Refused after MAX_IAM_WAITS waits that each
    found newer changes. An unreadable audit log is a warning, not a refusal (the failure-path hint still applies).
    Returns the total seconds slept (0.0 when it did not wait)."""
```
Algorithm, exactly:
```
cutoff = ctx.now() - timedelta(seconds=IAM_PROPAGATION_SECONDS)
waits = 0
slept = 0.0
while True:
    try:
        entries = run_json(ctx.run, iam_changes_argv(cutoff))
    except (GcloudError, ValueError) as exc:  # ValueError: json.JSONDecodeError on non-JSON stdout
        ctx.echo("WARNING: could not read the Admin Activity audit log for recent IAM changes; submitting without the "
                 f"propagation check (it needs roles/logging.viewer and the Logging API): {str(exc).splitlines()[-1]}")
        return slept
    changes = parse_iam_changes(entries, after=cutoff)
    if not changes:
        return slept
    if waits == MAX_IAM_WAITS:
        raise Refused(f"IAM kept changing across {MAX_IAM_WAITS} propagation waits (latest: {latest line}); resubmit once IAM is stable")
    latest = changes[-1]
    ctx.echo(f"IAM changed {len(changes)} time(s) since {cutoff:%H:%M:%S}Z; latest: {latest.method} on "
             f"{latest.resource or '<unknown resource>'} by {latest.principal or '<unknown principal>'} at {latest.timestamp:%H:%M:%S}Z")
    remaining = (latest.timestamp + timedelta(seconds=IAM_PROPAGATION_SECONDS) - ctx.now()).total_seconds()
    if remaining > 0:
        ctx.echo(f"Waiting {ceil(remaining)} s for IAM propagation (Ctrl-C aborts; nothing has been submitted yet)")
        ctx.sleep(remaining)
        slept += remaining
    waits += 1
    cutoff = latest.timestamp
```
The Refused message's `{latest line}` is `f"{changes[-1].method} by {changes[-1].principal or '<unknown principal>'} at {changes[-1].timestamp:%H:%M:%S}Z"`.
`ctx.now()` returns an aware UTC datetime (`LaunchContext.now`, `generation_vm.py:172`).

### `submit` changes (`dataproc_batch.py:239-312` at `ab7d9f3`)

Order in the first `try` block, after the existing `upload_artifact` calls:
```python
        upload_artifact(ctx.run, zip_)
        upload_artifact(ctx.run, main)
        try:
            waited = wait_for_iam_propagation(ctx)
        except KeyboardInterrupt:
            ctx.echo("INTERRUPTED during the IAM propagation wait: no batch was submitted (the uploaded artifacts are reused on the next run)")
            return EXIT_INTERRUPTED
        if waited > 0:
            # the wait can last ~24 min: re-check active batches, VM, quota, gold prefix, AND that the code is unchanged
            _, rechecked = preflight(options, ctx, remote=True)
            if (rechecked.head_commit, rechecked.prereg_commit) != (record.head_commit, record.prereg_commit):
                raise Refused(
                    f"HEAD or the prereg commit changed during the IAM wait (was {record.head_commit[:12]}/"
                    f"{record.prereg_commit[:12]}, now {rechecked.head_commit[:12]}/{rechecked.prereg_commit[:12]}); "
                    "the zip and provenance were built from the old commit: rerun to build from the new one"
                )
    except (Refused, GcloudError) as exc:
        ctx.echo(f"REFUSED: {exc}")
        return EXIT_REFUSED
```
Dry run (inside the existing `if options.dry_run:` branch, before `return EXIT_OK`):
`ctx.echo(f"would check the Admin Activity audit log for IAM changes in the last {IAM_PROPAGATION_SECONDS // 60} minutes, then submit")`.
(No runner call; `test_dry_run_makes_no_runner_call` keeps asserting `fake.calls == []`.)

### F3 — the tail helper and the interrupt path

New private helper (body = lines 279-312 at `ab7d9f3`, from `usage = parse_batch_usage(describe)` to the final
`return EXIT_OK`, i.e. everything after the usage-wait loop, moved verbatim apart from the one marked change):
```python
def _finish(ctx, options, batch_id, argv, record, main, zip_, describe, skipped_wait, *, interrupted: bool) -> int:
    """Write the batch record, print the summary and, for a non-SUCCEEDED batch, the failure, cleanup and hint lines.
    Returns EXIT_OK for SUCCEEDED, else EXIT_FAILED."""
```
Changes inside it: the record dict gains `"interrupted": interrupted` right after `"never_ran"`; nothing else.

Every `describe = run_json(ctx.run, describe_argv)` in `submit` (the polling loop and the usage-wait loop) becomes
`describe = _describe(ctx, describe_argv)`, a new private helper:
```python
def _describe(ctx: LaunchContext, argv: list[str]) -> dict:
    found = run_json(ctx.run, argv)  # GcloudError on a non-zero exit, ValueError (JSONDecodeError) on non-JSON stdout
    if not isinstance(found, dict):
        raise ValueError(f"gcloud dataproc batches describe returned {type(found).__name__}, not a JSON object")
    return found
```
Because the assignment only happens when `_describe` returns, `describe` keeps the last good (possibly terminal) value
when a later read fails, which is what the terminal check in the handler below relies on. A `ValueError` before the
batch is terminal takes the existing FAILED path (exit 2), like a `GcloudError` there.

`submit` after this change:
```python
    describe_argv = [...]
    describe: dict | None = None
    skipped_wait = False
    try:
        run_ok(ctx.run, argv)
        ...polling loop, skip decision and usage wait unchanged...
    except KeyboardInterrupt:
        if describe is not None and describe.get("state") in TERMINAL_STATES:
            code = _finish(ctx, options, batch_id, argv, record, main, zip_, describe, skipped_wait, interrupted=True)
            ctx.echo(f"INTERRUPTED during the usage wait: batch {batch_id} had already ended {describe['state']}; its record "
                     "was written with the usage known so far (re-run gcloud dataproc batches describe later for DCU-hours)")
            return code if code == EXIT_FAILED else EXIT_INTERRUPTED
        ctx.echo(...existing "the batch keeps running ... cancel it" line, unchanged...)
        return EXIT_INTERRUPTED
    except (GcloudError, ValueError) as exc:  # ValueError: a describe that is not a JSON object (see below)
        if describe is not None and describe.get("state") in TERMINAL_STATES:
            ctx.echo(f"WARNING: describe failed during the usage wait of finished batch {batch_id}; recording what is known: "
                     f"{str(exc).splitlines()[-1]}")
            return _finish(ctx, options, batch_id, argv, record, main, zip_, describe, skipped_wait, interrupted=False)
        ctx.echo(f"FAILED: {exc}")  # unchanged: submit or polling failed
        return EXIT_FAILED
    return _finish(ctx, options, batch_id, argv, record, main, zip_, describe, skipped_wait, interrupted=False)
```
Exit-code precedence follows the launcher's documented order (2 > 130 > 0): an interrupted wait after a FAILED or
CANCELLED batch returns 2; after a SUCCEEDED batch, 130. The module docstring's exit-code sentence becomes: "Exit codes:
0 succeeded; 1 refused (including IAM still changing after 3 propagation waits); 2 batch failed or cancelled (also when
the usage wait was interrupted after such a batch); 130 interrupted (during the IAM propagation wait: nothing submitted; while
polling: the batch keeps running, bounded by its ttl; during the usage wait after a SUCCEEDED batch: record written)."

### FakeGcloud (`tests/ops/fake_gcloud.py`)

- New field `logging_responses: list = field(default_factory=list)`: each `gcloud logging read` call pops the first
  element and returns it as JSON; when the list is empty it returns `[]`.
- The existing `fail_on` switch makes it fail: `fail_on=("gcloud logging read",)`.
- New field `describe_failures_after: int | None = None`: when set, each batch entry keeps a counter
  `batch["describes"]` (initialised to 0 in the `submit` handler, incremented at the start of the `describe` handler);
  when `describe_failures_after is not None and batch["describes"] > describe_failures_after`, the handler returns
  `subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr="ERROR: simulated failure")` before popping a
  state, so a terminal describe can be followed by a failing one.
- New field `describe_failure_output: str | None = None`: when set, the failing describe (per `describe_failures_after`)
  instead returns exit 0 with that text as stdout (e.g. `"not json"` or `""`), simulating malformed output.
- Handler, placed right after the existing `storage` branches (so after the generic `fail_on` check at the top of
  `__call__`, which therefore still wins): `if args[1:3] == ["logging", "read"]: return _ok(self.logging_responses.pop(0) if self.logging_responses else [])`
  (`head` is a 3-tuple and cannot be compared with a 2-tuple; the `storage` branches use the same `args[1:3]` form). A
  `str` element is returned verbatim by `_ok` (used to simulate non-JSON output); any other element is JSON-encoded.

## Tests (`tests/ops/test_dataproc_batch.py`)

Helper: `def audit_entry(method, at, principal="operator@example.com", resource="projects/synthea-readmission-risk/roles/dataprocRuntimeWorker") -> dict`
returns `{"timestamp": at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "123Z", "protoPayload": {"methodName": method,
"authenticationInfo": {"principalEmail": principal}, "resourceName": resource}}` (nanosecond-style suffix on purpose).
`NOW = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)` (the `env` fixture's clock).

- `test_iam_changes_argv`: for `since = datetime(2026, 10, 12, 13, 24, tzinfo=UTC)` the argv equals
  `["gcloud", "logging", "read", 'logName="projects/synthea-readmission-risk/logs/cloudaudit.googleapis.com%2Factivity" AND timestamp>="2026-10-12T13:24:00Z" AND protoPayload.methodName=("SetIamPolicy" OR "google.iam.admin.v1.SetIAMPolicy" OR "google.iam.admin.v1.CreateRole" OR "google.iam.admin.v1.UpdateRole" OR "google.iam.admin.v1.DeleteRole" OR "google.iam.admin.v1.UndeleteRole" OR "storage.setIamPermissions")', "--limit=100"]`.
- `test_parse_iam_changes`: from `after = NOW - 480 s`, entries: UpdateRole at NOW-60s (kept); SetIamPolicy at NOW-30s by
  `service-agent-manager@system.gserviceaccount.com` (dropped); `storage.setIamPermissions` at NOW-120s (kept);
  `"google.iam.admin.v1.GetRole"` at NOW-10s (dropped: not a change method); UpdateRole at NOW-600s (dropped: before
  `after`); an entry with no `authenticationInfo` at NOW-90s, method SetIamPolicy (kept, principal ""); an entry with
  `"timestamp": "garbage"` and method UpdateRole (dropped by the timestamp rule, not the method rule); an UpdateRole whose
  timestamp equals `after` exactly, written with the nanosecond suffix (`(after).strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"`)
  (dropped: not strictly after); a non-dict element `"x"` (dropped); an UpdateRole with a naive timestamp
  `"2026-10-13T09:59:30"` (dropped); an UpdateRole at NOW-50s with `"authenticationInfo": "x"` (kept, principal "");
  an UpdateRole at NOW-40s with `"principalEmail": 7` (kept, principal ""); a SetIamPolicy at NOW-20s by
  `service-271458030145@gcp-sa-dataprocrmnode.iam.gserviceaccount.com` (dropped); a SetIamPolicy at NOW-15s by
  `271458030145@cloudservices.gserviceaccount.com` (dropped). Expected: five changes in order NOW-120s,
  NOW-90s, NOW-60s, NOW-50s, NOW-40s (timestamps compared at microsecond precision). Also `parse_iam_changes(None, after=...) == []` and
  `parse_iam_changes({"a": 1}, after=...) == []`.
- `test_submit_waits_for_recent_iam_change(env)`: `fake.batch_states = ["SUCCEEDED"]` (no polling sleep); `entry =
  audit_entry("google.iam.admin.v1.UpdateRole", NOW - 60 s)`; `fake.logging_responses = [[entry], [entry]]` (the second
  read returns the same change again, as real gcloud does, because the re-check's filter `timestamp>=` is rounded down to
  the second; the loop must stop because that entry is not strictly after the cutoff);
  `slept = []`, `env["ctx"].sleep = slept.append`; exit `EXIT_OK`; `slept == [420.0]`; two `gcloud logging read` calls,
  the second's filter contains the first change's timestamp at second precision (`timestamp>="2026-10-13T09:59:00Z"`);
  the index of the last `logging read` call in `fake.calls` is lower than the index of the `batches submit` call; text
  contains "google.iam.admin.v1.UpdateRole on projects/synthea-readmission-risk/roles/dataprocRuntimeWorker by operator@example.com at 09:59:00Z"
  and "Waiting 420 s for IAM propagation".
- `test_system_principal_change_does_not_wait(env)`: `fake.batch_states = ["SUCCEEDED"]` (no polling sleep); one
  response holding a SetIamPolicy at NOW-30s by `service-agent-manager@system.gserviceaccount.com`; `slept = []` as
  above; exit `EXIT_OK`; `slept == []`; "IAM changed" not in text.
- `test_iam_keeps_changing_refuses(env)`: four responses, each one UpdateRole 1 s later than the previous (NOW-60s,
  NOW-59s, NOW-58s, NOW-57s); `fake.batch_states = ["SUCCEEDED"]`; exit `EXIT_REFUSED`; exactly 3 sleeps; 4 logging
  reads; no `batches submit` call; text contains "IAM kept changing across 3 propagation waits".
- `test_audit_log_unreadable_warns_and_submits(env)`: `fake.fail_on = ("gcloud logging read",)`; exit `EXIT_OK`; one
  `batches submit` call; text contains "WARNING: could not read the Admin Activity audit log" and "ERROR: simulated
  failure" (the fake's stderr, i.e. GcloudError's last line, so the real reason is shown).
- `test_audit_log_non_json_warns_and_submits(env)`: `fake.logging_responses = ["not json"]`; exit `EXIT_OK`; one
  `batches submit` call; text contains "WARNING: could not read the Admin Activity audit log".
- `test_ctrl_c_during_iam_wait_submits_nothing(env)`: one recent change; `ctx.sleep` raises `KeyboardInterrupt`; exit
  `EXIT_INTERRUPTED`; no `batches submit` call; text contains "INTERRUPTED during the IAM propagation wait: no batch was submitted".
- `test_preflight_rechecked_after_iam_wait(env)`: responses `[[e], [e]]` with `e` = UpdateRole at NOW-60s; `fake.batch_states =
  ["SUCCEEDED"]`; `ctx.sleep` is a function that, when called, adds `{"state": "RUNNING", "states": []}` under key
  `"other"` to `fake.batches` (another batch became active during the wait); exit `EXIT_REFUSED`; text contains
  "another Dataproc batch is active"; no `batches submit` call.
- `test_commit_change_during_iam_wait_refuses(env)`: responses `[[e], [e]]`; `fake.batch_states = ["SUCCEEDED"]`;
  monkeypatch `db.check_prereg_committed` with a function returning `RECORD` on its first call and
  `dataclasses.replace(RECORD, head_commit="e" * 40)` on later calls; exit `EXIT_REFUSED`; no `batches submit` call; text
  contains "HEAD or the prereg commit changed during the IAM wait".
- `test_no_wait_no_second_preflight(env)`: default (no changes): exactly one `gcloud dataproc batches list` call
  (preflight's active-batch check runs once).
- `test_dry_run_makes_no_runner_call` (existing): additionally assert "would check the Admin Activity audit log" in text.
- `test_ctrl_c_during_usage_wait_after_succeeded_writes_record(env)`: `fake.batch_states = ["SUCCEEDED"]`,
  `fake.batch_usage = None` (so the usage wait runs: history is `["PENDING"]` but SUCCEEDED never skips); `ctx.sleep`
  raises `KeyboardInterrupt` on its first call (the first usage-wait sleep); exit `EXIT_INTERRUPTED`; the record file
  exists with `"interrupted": true` and `"never_ran": false`; text contains "had already ended SUCCEEDED" and not
  "keeps running".
- `test_ctrl_c_during_usage_wait_after_failed_returns_2(env)`: `fake.batch_states = ["RUNNING", "FAILED"]`,
  `fake.batch_usage = None`; `ctx.sleep` succeeds once (the polling sleep) and raises on the second call (first
  usage-wait sleep); exit `EXIT_FAILED`; record `"interrupted": true`; text contains "holds partial output" and "had
  already ended FAILED".
- `test_keyboard_interrupt_exit_130_prints_cancel` (existing, interrupt while RUNNING) stays unchanged and green.
- `test_malformed_describe_during_usage_wait_still_writes_record(env)`, parametrized over `"not json"` and `""`:
  `fake.batch_states = ["SUCCEEDED"]`, `fake.batch_usage = None`, `fake.describe_failures_after = 1`,
  `fake.describe_failure_output = <param>`; exit `EXIT_OK`; record written; text contains "WARNING: describe failed
  during the usage wait".
- `test_describe_error_during_usage_wait_still_writes_record(env)`: `fake.batch_states = ["SUCCEEDED"]`,
  `fake.batch_usage = None`, `fake.describe_failures_after = 1` (the second describe, the first usage-wait read, fails);
  exit `EXIT_OK` (the batch succeeded); record file exists with `"interrupted": false`; text contains "WARNING: describe
  failed during the usage wait" and "ERROR: simulated failure".
- `test_batch_record_and_usage_printed` (existing): additionally `record["interrupted"] is False`.

## UX flow

`make bigjoin-batch RUN=…` → preflight → uploads → **audit-log check**: no recent human IAM change → submit at once;
a change within 8 minutes → one line naming it, "Waiting N s …", then a re-check, then submit; IAM still changing after
3 waits → REFUSED (exit 1); audit log unreadable → WARNING, submit. Ctrl-C during that wait → nothing submitted, exit
130. After submission, a Ctrl-C or a gcloud error during the usage wait of a finished batch now writes its record (and says
so) instead of losing it.

## Data handling

None: operations CLI only. The gitignored batch record gains `interrupted` (bool). No randomness.

## Failure modes

- Audit-log entries lag the change by seconds to a minute; a change made immediately before submission can be missed.
  The failure-path hint (ops bundle) still covers that. Accepted, stated in docs.
- The operator lacks `logging.viewer` / the Logging API is disabled / gcloud prints non-JSON → WARNING (with gcloud's
  last stderr line, or the JSON error) and submit (degrades to today).
- The wait outlasts the preflight: after any non-zero wait the full remote preflight runs again (another batch, a
  generation VM, quota, a non-empty gold prefix, the prereg/P1 commit checks; a refusal then exits 1), and the submitter
  also refuses if HEAD or the prereg commit changed during the wait, because the zip, the argv provenance and the batch
  record were built from the pre-wait commit (P1: the launched code is exactly HEAD). Rerunning rebuilds everything. `batch_id` keeps the pre-wait
  minute (it stays unique; accepted).
- A Ctrl-C during preflight, the zip build or the uploads behaves as before (unhandled, a traceback; nothing submitted).
  Only the new wait has its own handler.
- Laptop clock skew: the wait is computed from the laptop clock against server timestamps; a fast clock shortens the
  wait (remaining ≤ 0 → no sleep), a slow one lengthens it. Accepted; the check is a heuristic.
- An unrelated human IAM change (e.g. the scorer's role) also triggers a wait of up to 8 minutes. Accepted:
  conservative, and the printed line names what triggered it.
- More than 100 matching entries in 8 minutes: only the first 100 (gcloud orders newest first by default) are parsed;
  the newest is among them, which is what the wait depends on.

## Verification criteria

1. New and extended tests pass; full `pytest` green; `ruff check .` clean.
2. **Live, read-only, owner-confirmed (V-live):** with `CLOUDSDK_CORE_PROJECT=synthea-readmission-risk` exported (the
   operator's default gcloud project is deliberately another one; `ops.gcloud.default_runner` sets it too), run `gcloud logging read` with `iam_changes_argv(datetime(2026, 10, 12, 13, 24, tzinfo=UTC))`
   whose query string (argv[3]) has ` AND timestamp<"2026-10-12T13:40:00Z"` appended for this check only (an upper bound,
   so later IAM activity cannot push the target entries out of the 100-entry page), plus `--format=json`; it must parse without a filter-syntax error, and `parse_iam_changes` of
   the result with `after=2026-10-12T13:24:00Z` must return the 13:31:54 `UpdateRole` and must not return the 13:37:29
   system-principal `SetIamPolicy`. Per the evidence query (whose substring filter is a superset of `IAM_CHANGE_METHODS` and returned all its 7 entries),
13:31:54 and 13:37:29 are the only matching entries in 13:24-13:40, so the parsed result must be exactly
[the 13:31:54 UpdateRole]. If the live read returns any further entry in that window, that is a discrepancy: record it
verbatim in Results and stop for the owner's decision before merging. Recorded in Results.
3. Docs: `docs/setup.md:323` appended: "Before submitting, the submitter reads the Admin Activity audit log and, if a
   non-Google principal changed IAM in the last 8 minutes, waits until 8 minutes have passed since the latest change
   (Ctrl-C submits nothing); changes by Google-managed principals (`…@system.gserviceaccount.com`,
   `service-<n>@gcp-sa-….iam.gserviceaccount.com`, `<n>@cloudservices.gserviceaccount.com`) are ignored, and an
   unreadable log is a warning; after a wait the preflight checks run again." CLAUDE.md line 130's "the ~8-minute IAM propagation wait after a role change (partly done
   2026-10-12: the submitter prints a hint on a permission pre-check failure; no automatic wait or retry)" becomes
   "~~the ~8-minute IAM propagation wait after a role change~~ (done 2026-10-12: a failure hint plus a pre-submit
   audit-log check that waits; no automatic resubmission)". CLAUDE.md line 118's lead-in becomes "(<N> tests since 2026-10-12, ops
   follow-ups: <P> passed + <S> skipped on macOS in about 3 minutes;" with N, P, S from the final `pytest -rs` run.
   `tests/test_docs.py` green.

## Out-of-scope follow-ups

- The same pre-submit check for the generation launcher (`synthea-generator` SA), if ever needed. **Done 2026-10-13** (`notes/eg-new-feature/ops-launcher-iam-wait-2026-10-13.md`)
- Automatic resubmission after a permission failure (owner declined, 2026-10-12).

## Revision log

Revision 1 (after round 1; comprehension passed, critic 6, readiness 3). Critic → 1 test principal is
`operator@example.com`, never the owner's address. 2 `parse_iam_changes` total: naive timestamps skipped, non-dict
`authenticationInfo` / non-str principal / non-str resource become ""; cases added. 3 V-live gets an upper time bound.
4 the Ctrl-C handler wraps only the wait, and its message names the wait. 5 after a non-zero wait the remote preflight
runs again (two tests); stale `batch_id` minute accepted. 6 the two other Google service-agent forms are filtered and
named in docs. Readiness → 1 the wait test sets SUCCEEDED; 2 line references corrected to `ab7d9f3`; 3 one marked change.

Revision 2 (after round 2; readiness READY, critic 6). 1 the wait tests' second read repeats the change (as real gcloud
does, the filter being rounded to the second), and the parse test has an entry exactly at the cutoff: a `>=` slip now
fails tests. 2 the garbage-timestamp case has a change method. 3 the warning shows gcloud's last stderr line. 4 the
slice-5 note's item (7) is updated. 5 a gcloud error during the usage wait of a finished batch also writes the record
(new fake switch, new test). 6 V-live exports the project.

Revision 3 (after round 3; readiness READY, critic 3). 1 the fake's handler compares `args[1:3]`, placed after
`fail_on`; the `describe_failures_after` counter specified. 2 `docs/setup.md:311` and the script docstring updated. 3
non-JSON audit-log output is a warning too (`ValueError` caught; new test). Readiness nits: `UTC` import, str check
before parsing, duplicate rule removed.

Round 4: readiness READY, critic 1 (V-live's "exactly two entries" not shown for the full method list). Rebutted in
substance and fixed in the doc: the evidence query's exact filter is now quoted; its substring matches cover every
method in `IAM_CHANGE_METHODS` and it returned fewer entries than its limit. V-live's pass criterion is exact and a
discrepancy stops for the owner. (Revision 4, owner chose "fix it + one more round" at the 3-revision cap.)

Round 5: readiness READY, critic 1 (the post-wait preflight could approve a newer commit while the zip, argv provenance
and record came from the old one). Revision 5: refuse when `head_commit` or `prereg_commit` changed during the wait;
new test.

Round 6: readiness READY, critic 2. Revision 6: 1 every describe goes through `_describe` (non-object output is a
`ValueError`, caught with `GcloudError`; the last good describe is kept), so a malformed usage-wait read of a finished
batch also writes the record (fake switch `describe_failure_output`, parametrized test). 2 the ops-bundle note's three
bullets named explicitly, the fourth unchanged.

Design gate: owner override after round 6 (readiness READY in rounds 2-6; the critic never formally closed; every
critic finding through round 6 is addressed in revisions 1-6).

## Results (implementation, 2026-10-12/13)

Branch `v2-ops-polish`, laptop (macOS arm64, Python 3.14.7).

- **V1** `pytest tests/ops`: 155 passed (139 before; 16 new incl. one parametrized pair). All existing ops tests passed
  unchanged once the fake understood `gcloud logging read` (9 failed before that, as expected: every real submit now
  reads the audit log). Mutation check: replacing the strict `timestamp > after` with `>=` makes
  `test_parse_iam_changes` and `test_submit_waits_for_recent_iam_change` fail (file restored). Full `pytest -rs`:
  **1260 passed, 2 skipped** (Dataproc-environment check; "lock-main OS guard: off-macOS only"), 176 s. `ruff check .`
  clean (one deliberate `noqa: TRY004`: `_describe` raises ValueError so malformed output is handled with JSON errors).
- **V2 (V-live)**, owner-confirmed, read-only, 2026-10-13, `CLOUDSDK_CORE_PROJECT=synthea-readmission-risk`: the exact
  `iam_changes_argv(2026-10-12T13:24Z)` query plus `AND timestamp<"2026-10-12T13:40:00Z"` exited 0 (filter syntax
  accepted) and returned exactly two raw entries: 13:37:29.754825Z `SetIamPolicy` by
  `service-agent-manager@system.gserviceaccount.com` and 13:31:54.026045548Z `google.iam.admin.v1.UpdateRole` by the
  owner. `parse_iam_changes(…, after=13:24Z)` returned exactly `[UpdateRole at 13:31:54.026045Z on
  projects/synthea-readmission-risk/roles/dataprocRuntimeWorker]`. **Pass**, no discrepancy.
- **V3** docs: every "Surfaces touched" doc row applied; CLAUDE.md line 118 = 1,262 tests (1,260 + 2).
- **Pre-commit review round 1** (4 findings): (1) audit-log ingestion lag: behaviour kept (accepted in Failure modes;
  the ops-bundle failure hint covers a miss), docs sentence now states the limitation and advises waiting a minute after
  `terraform apply`; (2) a describe error before the batch is terminal now says "FAILED to monitor batch …; it may
  still be running" with the describe and cancel commands (exit 2), instead of a bare FAILED; (3) the record gains
  `usage_wait_error`; (4) the post-wait re-check runs inside the same Ctrl-C handler. Two new tests; ops tests 157.
- **Pre-commit review round 2** (2 findings, both fixed): the exit-2 contract (module and script docstrings) now names
  "monitoring failed after submission, the batch may still be running"; counts re-measured after round 1: full
  `pytest -rs` **1262 passed, 2 skipped** (1,264 tests); CLAUDE.md line 118 updated (supersedes V1/V3's 1,260/1,262).
