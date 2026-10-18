# Infra cleanup: remove roles/editor from the default compute SA; destroy the unused Dataproc staging bucket (2026-10-13)

Design doc for `/eg-new-feature`. Branch `v2-infra-cleanup`, created from `v2-ops-polish` at `acba7a6`; its own PR into
`v2` after the ops PR (owner decision 2026-10-13). Two **live, owner-confirmed** Terraform changes, applied from the
laptop (CLAUDE.md: Terraform is applied from the laptop only, each apply confirmed by the owner; never in CI). No data,
model, split or scoring change.

Shorthand: **E1** the editor removal, **E2** the staging-bucket removal.

## Why

Group E of the 2026-10-12 TODO triage. Sources: CLAUDE.md "TODO (open, slice 5 follow-ups)" item (4) "remove
`roles/editor` from the default compute service account (owner decision, non-additive; no workload uses that account)"
and the "removing the now-unused `dataproc-staging` bucket and its two grants" part of item (6); slice-5 note
`notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md` "Out-of-scope follow-ups" ("Removing
`roles/editor` from the default compute SA", line 1287; "Added by 5b" (3), lines 1296-1297).

- **E1:** the default Compute Engine service account holds project `roles/editor` (Google grants it when the Compute Engine API is
  enabled). No workload of this project runs as it, so the grant is pure attack surface: any future resource that
  falls back to the default SA would silently get project-wide write access.
- **E2:** the bucket `synthea-readmission-risk-dataproc-staging` (`infra/storage.tf:123-149`) and its two grants to
  `dataproc-runtime` (`infra/iam.tf:308-322`: `objectAdmin`, `legacyBucketReader`) became unused when PR #9 stopped
  passing `--staging-bucket` (Dataproc runtime 3.0 rejects it). The constant `STAGING_BUCKET`
  (`src/readmission_risk/ops/generation_vm.py:53`) is used only by a test that checks constants against Terraform.

Read-only live checks, 2026-10-13 (owner-approved, `CLOUDSDK_CORE_PROJECT=synthea-readmission-risk`):
1. The default compute SA is `271458030145-compute@developer.gserviceaccount.com`; its only project-level role is
   `roles/editor` (`gcloud projects get-iam-policy … --flatten=bindings --filter=bindings.members:serviceAccount:<sa>`).
2. No compute instance exists (`gcloud compute instances list` printed nothing). All three Dataproc batches ever run
   (`…1318`, `…1332`, `…1337`, 2026-10-12) ran as `dataproc-runtime@…` (`environmentConfig.executionConfig.serviceAccount`).
2b. No service that runs as the default compute SA when none is named is enabled: `gcloud services list --enabled`
   shows none of run, cloudfunctions, cloudbuild, appengine, container, composer, dataflow, notebooks, aiplatform,
   workflows, batch or eventarc (compute and dataproc are covered by check 2; dataform is enabled as a BigQuery default
   and unused).
2c. The default compute SA made no Admin Activity call (admin writes) in the last 30 days (`gcloud logging read` on
   `cloudaudit.googleapis.com%2Factivity` with `protoPayload.authenticationInfo.principalEmail` = the SA,
   `--freshness=30d`: no entries). This does NOT show data-plane use (object reads/writes, BigQuery queries land in Data
   Access logs, which are off); the evidence for "unused" is checks 2 and 2b (nothing that could run as it exists or is
   enabled), and 2c only adds "no admin writes". Last-authentication activity (Policy Analyzer) would need enabling a new
   API, which this cleanup does not do.
3. The staging bucket is empty: `gcloud storage du -s gs://synthea-readmission-risk-dataproc-staging` = 0. The
   successful batch `…1337` ran without it.
4. The deleted bucket's own comment (`storage.tf:123`) gave its reason: "Named explicitly: otherwise Dataproc creates its
   own unmanaged per-location buckets." That no longer applies: the 5b batch submission was refused with "3.0 runtime
   version does not support configuring Dataproc staging bucket via the staging_bucket field" (the error, quoted at
   `tests/ops/test_dataproc_batch.py:88-89`), and Google's runtime 3.0 page
   (docs.cloud.google.com/managed-spark/docs/concepts/versions/spark-runtime-3.0, read 2026-10-12 and recorded in the
   gitignored `data/pilot/pilot_20k/live-checks.md:149-150`) says "3.0+ runtimes don't use Cloud Storage staging buckets.";
   and `gcloud storage buckets list --project=synthea-readmission-risk` (2026-10-13) shows exactly the six managed buckets
   (artifacts, bundles, dataproc-staging, gold, raw, tfstate): no auto-created `dataproc-staging-*`/`dataproc-temp-*`
   bucket after three runtime-3.0 batches.

## Scope

In:
1. **E1** in Terraform (owner decision: a Terraform resource, not a one-off gcloud): a `google_project_iam_member_remove`
   for `roles/editor` and the default compute SA, whose email comes from the `google_compute_default_service_account`
   data source (so no `-compute@developer.gserviceaccount.com` literal appears in `infra/`, keeping
   `tests/test_infra.py::test_no_resource_uses_default_compute_sa` meaningful).
2. **E2** in Terraform: delete `google_storage_bucket.dataproc_staging`, `google_storage_bucket_iam_member.dataproc_uses_staging`,
   `google_storage_bucket_iam_member.dataproc_lists_staging`, and the `dataproc_staging` key of `output.bucket_names`.
3. Code: delete `STAGING_BUCKET` from `generation_vm.py` and from `test_constants_match_terraform`.
4. Tests: `tests/test_infra.py` bucket list updated; one new test for E1.
5. The live run (plan → owner confirmation → apply → post-checks), recorded in this note's Results.
6. Docs: the comment at `infra/iam.tf:147-149`, `docs/setup.md:341` (teardown step 7), CLAUDE.md lines 103 and 130,
   the slice-5 note's two follow-ups.

Out: any other IAM change (the other default service agents are Google-managed and needed); removing other project roles
from other principals; the Compute Engine API's default-SA creation itself (cannot be disabled after the fact; the
org-policy `iam.automaticIamGrantsForDefaultServiceAccounts` applies only to new projects and needs an organization).

## Surfaces touched

| File | Change |
|---|---|
| `infra/iam.tf` | header sentence (lines 1-4); new `data "google_compute_default_service_account" "default" {}` and `resource "google_project_iam_member_remove" "default_compute_no_editor"` (in the slice-5 section, after the comment at lines 147-149, whose text changes); delete the two `dataproc_*_staging` members (lines 308-322, plus one of the surrounding blank lines) |
| `infra/storage.tf` | replace `google_storage_bucket.dataproc_staging` and its comment line (lines 123-149) by the three-line rationale comment given in Interfaces |
| `infra/outputs.tf` | delete the line `dataproc_staging = google_storage_bucket.dataproc_staging.name` in `bucket_names` |
| `src/readmission_risk/ops/generation_vm.py:53` | delete `STAGING_BUCKET = …` |
| `tests/ops/test_generation_vm.py:321` | remove `gv.STAGING_BUCKET, ` from the tuple |
| `tests/test_infra.py` | docstring line 10; `test_iam_is_additive_only` (lines 189-192); bucket list (line 206); member count (around line 456); two new tests |
| `docs/setup.md` | resource table (lines 145-153): one new row; teardown step 5 (line 339) and step 7 (line 341) wording |
| `CLAUDE.md` | line 101 (slice-3 bullet), line 103 (slice-5 paragraph), line 130 (TODO items), one temporary branch-rule line |
| `notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md` | lines 1287 and 1296-1297 suffixed done |

## Interfaces

### Terraform (`infra/iam.tf`)

The rationale survives in `infra/storage.tf`: the deleted block is replaced by the comment
```hcl
# No Dataproc staging bucket: runtime 3.0 rejects the staging_bucket field, and Google's runtime 3.0 page says "3.0+
# runtimes don't use Cloud Storage staging buckets." The named bucket created in slice 5 was removed on 2026-10-13
# (notes/eg-new-feature/infra-cleanup-editor-and-staging-2026-10-13.md); no auto-created staging bucket existed then.
```
(It contains neither "dataproc_staging" nor "dataproc-staging", so `test_no_staging_bucket_left` still holds.)

The comment at lines 147-149 becomes:
```hcl
# Neither workload uses the default compute service account. Its project roles/editor (granted by Google when the Compute
# Engine API is enabled) is removed below (owner decision 2026-10-13, notes/eg-new-feature/infra-cleanup-editor-and-staging-2026-10-13.md).
# legacyBucketReader gives storage.buckets.get + objects.list, which object-level roles and object-name conditions cannot
# grant and which gcloud and the Hadoop GCS connector may call.
```
Immediately after it:
```hcl
# No depends_on on purpose: a data source with depends_on is read at apply time whenever the dependency has pending
# changes (any future API added to apis.tf), which would make the email unknown and show this removal as a replace. The
# Compute API is enabled on this project, and bootstrap.sh applies the APIs first (-target) on a fresh project.
# Deleting or disabling the default compute SA requires removing this data source and the resource below first.
data "google_compute_default_service_account" "default" {
  project = var.project_id
}

# The one non-additive IAM resource in this config (see the header): removes the binding if present and on every later
# apply if it reappears; destroying this resource does NOT re-add it.
resource "google_project_iam_member_remove" "default_compute_no_editor" {
  project = var.project_id
  role    = "roles/editor"
  member  = "serviceAccount:${data.google_compute_default_service_account.default.email}"

  depends_on = [google_project_service.enabled]
}
```
The header of `infra/iam.tf` (lines 1-4) gains, after "*_iam_policy or *_iam_binding would replace bindings this config
does not own.", the sentence: "One deliberate exception: `google_project_iam_member_remove.default_compute_no_editor`
removes Google's project roles/editor grant from the default compute service account (2026-10-13)."

The behaviour comment states the provider's documented semantics for `google_project_iam_member_remove` (hashicorp/google
8.x); the plan output (Verification 3) is the check that 8.5.0 behaves so on this project.

`terraform fmt` decides alignment; `make tf-validate` must pass.

### Tests (`tests/test_infra.py`)

- `test_buckets_enforce_uniform_access_and_public_access_prevention`: the expected list becomes
  `["artifacts", "bundles", "gold", "raw", "tfstate"]`.
- New `test_default_compute_sa_editor_is_removed`:
  ```python
  def test_default_compute_sa_editor_is_removed():
      remove = _collapse(_resource("google_project_iam_member_remove", "default_compute_no_editor"))
      assert 'role = "roles/editor"' in remove
      assert 'member = "serviceAccount:${data.google_compute_default_service_account.default.email}"' in remove
      assert 'data "google_compute_default_service_account" "default"' in _all_tf()
  ```
  (`_resource` and `_collapse` are the module's existing helpers, `tests/test_infra.py:37-57`; `_resource` matches
  `resource` blocks only, so the `data` block is checked as text in `_all_tf()`.)
- `test_no_resource_uses_default_compute_sa` stays unchanged and must still pass (no literal email).
- `test_every_resource_depends_on_enabled_apis` (`tests/test_infra.py:420-424`) stays unchanged; the new resource
  carries the `depends_on` it requires (the data block is not a `resource` block, so the test does not look at it).
- `test_iam_is_additive_only` (`tests/test_infra.py:189-192`): its member count `28` becomes `26`, the comment becomes
  `# 12 (slices 3-4) + 14 (slice 5: synthea-generator 6, dataproc-runtime 8; the 2 staging grants removed 2026-10-13)`,
  and it gains
  `assert [(t, n) for t, n, _ in _resources(_all_tf()) if t.endswith("_iam_member_remove")] == [("google_project_iam_member_remove", "default_compute_no_editor")]`
  (exactly one removal, so a second one must be a deliberate test change).
- `test_gold_object_admin_is_conditioned_on_v2_prefix` (around `tests/test_infra.py:456`): `len(_members_of("dataproc_runtime")) == 10` becomes `== 8`.
- The `tests/test_infra.py` module docstring (line 10) "bundle-writer can only create objects; IAM is additive only;"
  becomes "bundle-writer can only create objects; IAM is additive only, except one pinned removal of the default compute
  SA's roles/editor;".
- New `test_no_staging_bucket_left`: `"dataproc_staging" not in _all_tf()` and `"dataproc-staging" not in _all_tf()`.

### Live procedure (owner-confirmed at each step)

All commands with `export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk`, from the repo root, on `v2-infra-cleanup`
with the code changes in place:
1. `make tf-validate` (offline; fmt, init without backend, validate).
2. `terraform -chdir=infra init` (real backend), then `terraform -chdir=infra plan -out=<session scratchpad>/cleanup.tfplan`
   (the session's scratchpad directory; never committed).
   **Expected plan, exactly:** `1 to add, 0 to change, 3 to destroy`: add
   `google_project_iam_member_remove.default_compute_no_editor`; destroy `google_storage_bucket.dataproc_staging`,
   `google_storage_bucket_iam_member.dataproc_uses_staging`, `google_storage_bucket_iam_member.dataproc_lists_staging`;
   the `bucket_names` output loses `dataproc_staging`; the data source is read during plan. The removal of the binding
   itself is not a plan line: `google_project_iam_member_remove` acts when it is created. Anything else (another change,
   a replace, a "known after apply" member) stops the run for the owner.
2c. `gcloud storage buckets list --project=synthea-readmission-risk --format='value(name)'` must print exactly the six
   buckets of Why, check 4 (else stop: Dataproc or something else created a bucket since).
2a. Save the whole project policy: `gcloud projects get-iam-policy synthea-readmission-risk --format=json >
   <session scratchpad>/policy-before.json`.
2b. Immediately before the apply: `gcloud storage du -s gs://synthea-readmission-risk-dataproc-staging` must print 0
   (else stop: something wrote to the bucket since the 2026-10-13 check).
3. Owner confirms; `terraform -chdir=infra apply <session scratchpad>/cleanup.tfplan`.
4. Post-checks (read-only):
   a. `gcloud projects get-iam-policy synthea-readmission-risk --flatten=bindings --filter=bindings.members:serviceAccount:271458030145-compute@developer.gserviceaccount.com --format='value(bindings.role)'` prints nothing.
   b. `gcloud storage buckets describe gs://synthea-readmission-risk-dataproc-staging` fails with a not-found error.
   c. `dataproc-runtime`'s other bucket grants are intact: `gcloud storage buckets get-iam-policy gs://synthea-readmission-risk-artifacts --format=json`
      still lists `serviceAccount:dataproc-runtime@…` under `roles/storage.legacyBucketReader`, and the raw bucket's
      policy still lists it.
   d. `terraform -chdir=infra plan` afterwards shows `No changes`.
   e. Collateral check: `gcloud projects get-iam-policy synthea-readmission-risk --format=json >
      <session scratchpad>/policy-after.json`, then compare the two files' `bindings` as sets of (role, member,
      condition-as-JSON-or-None) triples (a short `python3 -c` over both JSON files, ignoring `etag` and `version`): the
      only difference must be the removal of (`roles/editor`,
      `serviceAccount:271458030145-compute@developer.gserviceaccount.com`, None); no triple added. The bucket grants are
      bucket policies, not in the project policy, and are covered by 4b/4c. On any other difference: stop, and (owner
      decision) reverse ONLY the unexpected triples with `gcloud projects add-iam-policy-binding` /
      `remove-iam-policy-binding` (with `--condition` when the triple has one); never `set-iam-policy` from the
      pre-apply snapshot (its etag is stale, and it would re-add editor).
5. No Dataproc batch or VM is launched to prove E2 (both cost money); the evidence is that batch `…1337` succeeded
   without the bucket (Why, check 3), and E2 removes only the bucket and its own grants.

### Branch rule until this PR merges

The apply runs from `v2-infra-cleanup` before its PR merges (it waits for the ops PR). Until it is merged into `v2`,
**no Terraform plan/apply from any other branch** (`v2`, `v2-ops-polish`, or anything cut from them): their config still
defines the staging bucket and grants and lacks the removal, so an apply would recreate an empty bucket and its two
grants and destroy `default_compute_no_editor` from state (which does NOT re-add editor). If it happens anyway: re-apply
from `v2-infra-cleanup`. The rule is recorded as a temporary TODO line in CLAUDE.md (Build & test commands) and removed
when the PR merges.

### Rollback

- E1: `gcloud projects add-iam-policy-binding synthea-readmission-risk --member=serviceAccount:271458030145-compute@developer.gserviceaccount.com --role=roles/editor`,
  and remove the Terraform resource (otherwise the next apply removes the grant again).
- E2 (without touching E1): restore only the E2 pieces from the parent commit: the `google_storage_bucket.dataproc_staging`
  block in `storage.tf`, the two `dataproc_*_staging` members in `iam.tf`, the `dataproc_staging` output key, the
  `STAGING_BUCKET` constant and its test use, and the E2 test changes (bucket list, counts 26→28 and 8→10, drop
  `test_no_staging_bucket_left`); then plan, expecting exactly `3 to add, 0 to change, 0 to destroy`. Never `git revert`
  the whole commit: it also contains E1, and reverting it would destroy `default_compute_no_editor` from state and drop
  the E1 tests. The bucket was empty, so nothing is lost.

## UX flow

The owner reviews the plan (1 add, 3 destroy), confirms, applies, runs the post-checks 4a-4e (after the pre-apply
steps 2a, 2b and 2c). Nothing changes for
`make generate-vm` or `make bigjoin-batch`.

## Data handling

None: the staging bucket is empty (checked); no project data, model or bundle is touched.

## Failure modes

- Something does use the default SA after all (not found by the checks): it loses editor; symptom: a 403 for that
  workload; rollback E1 above. Mitigated by checks 1, 2, 2b and 2c.
- The plan differs from the expected one (provider semantics, drift): stop before apply.
- A bucket that is not empty at apply time (something wrote to it since the check): `force_destroy = true` on the
  existing resource deletes the objects; acceptable because no workload uses the bucket (re-check with `du` right before
  the apply, step 2).
- The data source is read on every plan, apply and `terraform destroy`; if the default compute SA is ever deleted or
  disabled, all of them fail. So deleting or disabling that SA (a possible later hardening) must first remove
  `default_compute_no_editor` and the data source from `infra/`; this is stated in the `infra/iam.tf` comment above the
  data source ("Deleting or disabling the default compute SA requires removing this data source and the resource below
  first.") and in the teardown note.
- `google_compute_default_service_account` fails if the Compute API is disabled: it is enabled on this project, and a
  fresh bootstrap enables the APIs first (`infra/bootstrap.sh`, `-target`); the data source deliberately has no
  `depends_on` (it would defer the read and make later plans show a replace).

## Verification criteria

1. `ruff check .`, the full `pytest` (including the new and changed `test_infra.py` tests and `test_generation_vm.py`)
   and `make tf-validate` green.
2. A re-check of the staging bucket's size (= 0) immediately before the apply, recorded in Results.
3. The live plan matches the expected plan exactly; the owner confirms; apply succeeds; post-checks 4a-4e pass (all five
   are gates; 2a-2c are pre-apply gates). Results (a section added at the end of this note) record, per step, the command and an excerpt: 2c the bucket list, 2a the
   snapshot's file name and binding count, the plan's summary line and its four resource lines, 2b the `du` output, the
   apply's summary line, each of 4a-4d's output (empty output stated as "printed nothing"), and 4e's list of removed and
   added triples.
4. Docs: `infra/iam.tf` comment as above; `docs/setup.md:341` "the artifacts and Dataproc staging buckets are
   `force_destroy` and go there too" becomes "the artifacts bucket is `force_destroy` and goes there too (the Dataproc
   staging bucket was removed on 2026-10-13)"; `docs/setup.md:339` (teardown step 5) gains after "all IAM members … go":
   " (destroying `default_compute_no_editor` does not restore the default compute SA's roles/editor; to delete or disable
   that SA later, first remove `default_compute_no_editor` and its data source from `infra/`, because every plan and
   destroy reads it)"; the resource table gains, as its LAST row (after "Budgets"), the row "| Default compute service account (2026-10-13) | Its project `roles/editor`, granted by Google when the Compute Engine API
   is enabled, is removed (`google_project_iam_member_remove`); no workload runs as it |"; CLAUDE.md line 103
   "`artifacts` and `dataproc-staging` buckets" becomes "`artifacts` and `dataproc-staging` buckets (the staging bucket
   removed 2026-10-13)" and, in the same sentence, "16 additive bindings" becomes "16 additive bindings, 14 since the
   staging grants were removed on 2026-10-13"; CLAUDE.md line 101's "Flagged, not fixed: the default compute service
   account holds project `roles/editor` (decide in the Dataproc slice)." becomes "The default compute service account's
   project `roles/editor` was removed on 2026-10-13 (`google_project_iam_member_remove`,
   `notes/eg-new-feature/infra-cleanup-editor-and-staging-2026-10-13.md`)."; and CLAUDE.md "Build & test commands" gains,
   immediately after the bullet that starts "- Infrastructure (v2 slice 3):", the temporary line "- **TEMPORARY (until the v2-infra-cleanup PR merges into v2): run Terraform plan/apply only from
   `v2-infra-cleanup`** (other branches still define the staging bucket and lack the editor removal; see the
   infra-cleanup note's branch rule)."; CLAUDE.md line 130 "(4) remove
   `roles/editor` from the default compute service account (owner decision, non-additive; no workload uses that account);"
   becomes "(4) ~~remove `roles/editor` from the default compute service account~~ (done 2026-10-13, Terraform
   `google_project_iam_member_remove`);" and "removing the now-unused `dataproc-staging` bucket and its two grants," becomes
   "~~removing the now-unused `dataproc-staging` bucket and its two grants~~ (done 2026-10-13),"; the slice-5 note's line
   1287 and its "Added by 5b" (3) suffixed " **Done 2026-10-13** (`notes/eg-new-feature/infra-cleanup-editor-and-staging-2026-10-13.md`)";
   `tests/test_docs.py` green; CLAUDE.md line 118 test count from the final run.

## Out-of-scope follow-ups

- None new.

## Revision log

Revision 1 (after round 1; comprehension passed, critic 7, readiness 8). The removal resource carries the required
`depends_on`; the data source deliberately does not (deferred reads). Member counts 28→26 and 10→8 updated; the additive-only
rule states its one exception in the iam.tf header, the test docstring and a test pinning exactly one `_iam_member_remove`.
CLAUDE.md's binding count updated. The `du` re-check is step 2b. Plan path = the session scratchpad; the Results format is
given; the plan's exactness explained (the removal is not a plan line; "known after apply" stops the run). docs/setup.md
gains a resource-table row and a teardown note.

Revision 2 (after round 2; readiness READY, critic 6). 1 CLAUDE.md:101's "flagged" sentence updated. 2 evidence widened
and measured read-only (2b: no fallback-capable service enabled; 2c: no Admin Activity call by the SA in 30 days). 3
project-policy before/after diff as post-check 4e. 4 branch rule until merge (+ temporary CLAUDE.md line). 5 the data
source's dependency on the SA's existence stated (failure modes, iam.tf comment). 6 "granted when the Compute Engine API is
enabled", not at project creation.

Revision 3 (after round 3; critic 3, readiness 2). 4e compares (role, member, condition) triples and, on a mismatch,
reverses only the unexpected triples (never `set-iam-policy` from the stale snapshot). The gates are 2a, 2b, 4a-4e
everywhere, 4e recorded. 2c restated as "no admin writes", with batch and eventarc added to 2b's list (absent in the
2026-10-13 enabled list). The teardown note's SA-deletion sentence and the positions of the new table row and CLAUDE.md
line are given.

Revision 4 (after round 4; readiness READY, critic 2; owner chose "fix both + one more round" at the 3-revision cap). 1
the staging bucket's original reason re-checked (Why, check 4: Google's runtime-3.0 statement + a live bucket list showing
no auto-created buckets), a surviving `storage.tf` comment keeps the rationale, and the bucket list is pre-apply gate 2c.
2 the E2 rollback restores only E2's pieces (never a whole-commit revert, which would undo E1).

Round 5: readiness READY, critic 1 (a quotation attributed to the wrong source). Revision 5: the batch error and the
runtime-3.0 page sentence are each quoted with their real source (the page sentence is Google's, from the 3.0 page read
2026-10-12, recorded in live-checks.md:149-150); the surviving storage.tf comment attributes it to the page.

## Results (2026-10-14)

Design gate: owner override after round 5 (readiness READY in rounds 2, 4, 5; the critic's findings narrowed to one
misattributed quotation, fixed in revision 5). Live steps run by Claude on the laptop with
`CLOUDSDK_CORE_PROJECT=synthea-readmission-risk`, each owner-confirmed (read-only part, then the apply).

- **Offline:** `ruff check .` clean; `tests/test_infra.py` + `tests/ops/test_generation_vm.py` 88 passed; `make
  tf-validate`: fmt clean after re-aligning `outputs.tf`, `terraform validate` "Success! The configuration is valid."
- **Plan:** `Plan: 1 to add, 0 to change, 3 to destroy.` Resource lines: `google_project_iam_member_remove.default_compute_no_editor
  will be created` (member resolved at plan time to `serviceAccount:271458030145-compute@developer.gserviceaccount.com`,
  role `roles/editor`); `google_storage_bucket.dataproc_staging`, `google_storage_bucket_iam_member.dataproc_lists_staging`,
  `google_storage_bucket_iam_member.dataproc_uses_staging` will be destroyed; output `dataproc_staging` removed. Matches
  the expected plan exactly.
- **2c** bucket list: the six managed buckets (artifacts, bundles, dataproc-staging, gold, raw, tfstate). **2a** snapshot
  `policy-before.json` in the session scratchpad: 9 bindings, 9 members. **2b** `du -s` of the staging bucket: 0.
- **Apply:** `Apply complete! Resources: 1 added, 0 changed, 3 destroyed.` (the removal "Creation complete after 2s").
- **4a** `get-iam-policy` filtered to the default compute SA: printed nothing. **4b** `buckets describe` of the staging
  bucket: `not found: 404`. **4c** `dataproc-runtime` on artifacts: legacyBucketReader, objectViewer; raw: legacyBucketReader,
  objectViewer; gold: legacyBucketReader, objectAdmin (the v2/-conditioned grant), objectViewer. **4d** `terraform plan
  -detailed-exitcode`: exit 0, "No changes. Your infrastructure matches the configuration." **4e** removed
  `[('roles/editor', 'serviceAccount:271458030145-compute@developer.gserviceaccount.com', None)]`, added `[]`; 9 → 8
  (role, member, condition) triples.
- The temporary CLAUDE.md branch rule stays until the v2-infra-cleanup PR merges into v2.
- **Pre-commit review round 1** (1 finding, fixed): the live change happened on 2026-10-14, not 2026-10-13 (the design
  and pre-checks date); every "removed/done" date for it in CLAUDE.md, docs/setup.md, infra/iam.tf, infra/storage.tf,
  tests/test_infra.py and the slice-5 note now says 2026-10-14; decision and pre-check dates stay 2026-10-13.
