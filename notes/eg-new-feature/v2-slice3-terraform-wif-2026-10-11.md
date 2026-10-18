# v2 slice 3: Terraform + Workload Identity Federation (FR8–FR9)

**Status:** implemented and applied 2026-10-11 (design revision 7; the design-check rounds are in "Revision log" at the end, the implementation, review and live-check outcomes in "Results"; one deviation: the bundle upload uses the JSON API, not `gcloud storage cp`, see Results > V4). Open: V-post after v2 merges. Branch: a feature branch off `v2`, merged into `v2` by PR (ruleset `require-pr-main-and-v2`, id 24876876, blocks direct pushes to `v2`). The earlier, paused slice-3a drafts (untracked) no longer exist; this doc starts fresh.

## Why

PRD `notes/prds/v2-mlops-keyless-scheduled-scale-up-2026-10-09.md`, FR8–FR9 and "Implementation hints" item 3: "bootstrap script; `infra/` directory; one `workflow_dispatch` smoke job that only lists a bucket. Also: `terraform import` the existing CHF 20 budget and create the carried CHF 5 budget." Every later v2 slice (BigQuery scoring in 4, the `gs://` Big Join in 5, Dataproc in 6, the scheduled tick in 7) needs buckets, service accounts and a keyless CI identity that exist and are reproducible from code. This slice also lands the carried items: the CHF 5 early-warning budget (OQ4), the `make tf-validate` target (FR0.1, deferred from slice 1), and the bucket/IAM half of FR7 (bundles cannot be overwritten).

## Owner decisions taken in Step 0 (2026-10-11)

| # | Question | Decision |
|---|---|---|
| D1 | GCS bundle reader/writer code and the `champion.json` repoint | **Slice 4.** This slice adds no Python dependency. It uploads both existing v1 bundles with `gcloud`, impersonating the writer service account, as the live proof of FR7's controls. `deploy/champion.json` keeps its local `bundle_uri` until slice 4's reader exists (repointing now would break `make score`). |
| D2 | Service accounts created now | **Two:** `gha-smoke` and `bundle-writer`. Scorer, monitor, Big Join submitter, Dataproc runtime and trainer arrive with their jobs (slices 4–7), when their exact roles are known. |
| D3 | WIF verification given the `refs/heads/main` condition | **After the merge of v2 into main.** The condition is never weakened. Pre-merge: laptop IAM checks plus static tests. Post-merge: one dispatch on `main` (must succeed) and one on another branch (must be refused), recorded as an open checklist item (see "Verification", V-post). Consistent with the owner's 2026-10-11 decision to run the WIF proof after v2 merges. |
| D4 | Dependabot for Terraform | **Yes:** a `terraform` ecosystem entry for `/infra`, weekly, 7-day cooldown. |

## Scope

**In:**
1. `infra/`: one Terraform root module (GCS backend), with the files listed under "Surfaces touched".
2. `infra/bootstrap.sh`: creates the state bucket with a temporary local backend, then migrates state into it.
3. Resources: 6 enabled APIs, 4 buckets, 2 service accounts and their IAM, 1 WIF pool + 1 OIDC provider, 2 budgets (CHF 20 imported, CHF 5 created).
4. `.github/workflows/smoke.yml`: `workflow_dispatch` only; lists the bundle bucket through WIF + impersonation of `gha-smoke`.
5. CI: a new `terraform-validate` job in `ci.yml` (`fmt -check`, `init -backend=false -lockfile=readonly`, `validate`); never `plan`/`apply`, no cloud credentials.
6. Makefile `tf-validate` target (same three commands as CI).
7. Dependabot `terraform` entry.
8. Static tests (`tests/test_infra.py`, plus edits in `tests/test_docs.py` and `tests/test_tooling.py`).
9. A one-off upload of the two v1 bundles to `gs://synthea-readmission-risk-bundles/` (D1), and live IAM checks.
10. Docs: `docs/setup.md` gains two sections directly after "Cloud project and cost guard": "Infrastructure (Terraform)" (prerequisites, tfvars, bootstrap, plan/apply, lock-file recipe, bundle upload, live checks V1–V8, V-post) and "Teardown checklist"; the existing section's intro ("Nothing runs in the cloud yet … later adopted by Terraform") is rewritten to say the budget is now imported by Terraform. CLAUDE.md and PRD updates as specified in the Revision log (Readiness 9 row), including the stale "no branch protection or ruleset" sentence.
11. The `store.py` refusal message and `docs/scoring.md` "Fresh clone" note move their slice reference from 3 to 4.

**Out (explicit):**
- Any Python GCS code; `google-cloud-storage` (slice 4).
- BigQuery datasets and tables (slice 4); the dashboard SA and its custom query quota (slice 8).
- Service accounts other than the two above.
- `terraform apply`/`plan` in CI; any service-account key anywhere.
- Removing `roles/editor` from the default compute service account (flagged under Failure modes; decided in the Dataproc slice).
- A pre-commit `terraform fmt` hook (it would require Terraform for every commit; CI and `make tf-validate` cover it).
- Bucket Lock / Object Retention Lock (rejected in the PRD: irreversible, blocks teardown).
- A Pub/Sub billing hard stop (declined in OQ4).

## Facts this design relies on (verified 2026-10-11 unless marked)

- GitHub repository `fma00/synthea-readmission-risk`: `id` **1380503419**, `owner.id` **270004823** (`gh api repos/fma00/synthea-readmission-risk`). Public values; committed as variable defaults.
- GCP project `synthea-readmission-risk`, number **271458030145**, no organization parent. Owner: the operator's user account. Enabled APIs include `storage`, `billingbudgets`, `compute`, `dataproc`, `bigquery`; **not** enabled: `iam`, `iamcredentials`, `sts`, `cloudresourcemanager`.
- No buckets and no user-created service accounts exist. The default compute SA `271458030145-compute@developer.gserviceaccount.com` holds `roles/editor`.
- Existing budget: `billingAccounts/<BILLING_ACCOUNT_ID>/budgets/<EXISTING_BUDGET_ID>` (both ids live only in the gitignored `terraform.tfvars`; see "Redaction rule"), display name `synthea-readmission-risk monthly`, `CHF` units `20`, filter `projects/271458030145`, `MONTH`, `INCLUDE_ALL_CREDITS`, thresholds 0.5/0.9/1.0 `CURRENT_SPEND` and 1.0 `FORECASTED_SPEND`, empty `notificationsRule` (default emails to billing admins).
- GitHub's OIDC token carries `event_name`, `workflow_ref`, `ref`, `repository_id`, `repository_owner_id` (GitHub docs, "OpenID Connect reference"). Google: "Your attribute mappings can use any of the claims in the GitHub Actions OIDC token" and "You must have an attribute mapping for all attribute condition fields"; `google.subject` is at most 127 characters (Google docs, "Configure WIF with deployment pipelines"). This closes the PRD NHV item "whether `event_name` / `workflow_ref` can be mapped" **on documentation**; the live match is proven only by V-post.
- This repository issues **immutable subject claims**: `gh api repos/fma00/synthea-readmission-risk/actions/oidc/customization/sub` returns `use_default: true`, `use_immutable_subject: true`, prefix `repo:fma00@270004823/synthea-readmission-risk@1380503419` (GitHub's default for new repositories since 2026; changelog "Immutable subject claims for GitHub Actions OIDC tokens", 2026-04-23). Per that changelog only `sub` changes, not `ref` or `workflow_ref` (documentation; V-post is the live proof). Neither the condition nor any binding in this design uses `sub`.
- Terraform's `gcs` backend "supports state locking" and Object Versioning is "highly recommended" (HashiCorp docs). Closes the NHV item "the GCS backend's native state locking".
- Versions: Terraform **1.16.5** (released 2026-10-02); `hashicorp/google` provider **8.5.0** (2026-09-29; 8.6.0 is 5 days old, inside the project's 7-day cooldown). SHA256 of `terraform_1.16.5_linux_amd64.zip`: `2bc2fcfff033265c9e02ca0351f01794eb122f62a9b2a49a3294b9e49eaab5e4` (from `releases.hashicorp.com/terraform/1.16.5/terraform_1.16.5_SHA256SUMS`).
- `google-github-actions/auth` **v3.0.0** = commit `7c6bc770dae815cd3e89ee6cdf493a5fab2cc093` (lightweight tag).
- The `ubuntu-24.04` runner image ships Google Cloud CLI 587.0.0 (runner-images `Ubuntu2404-Readme.md`), so no `setup-gcloud` action is needed.
- The operator's Mac is `arm64` (`darwin_arm64` lock-file hashes needed alongside `linux_amd64`).
- Local bundles: `bundles/75a1448729ef561c596f5e850f5bedce50ed63355ca2de38dc724c635d24eef4/` (logistic_regression, the champion) and `bundles/30009d198070f66db1a0c35b033f11de9e08d88292330e0e71265553c4645d2a/` (xgboost_calibrated); each holds `manifest.json`, `model.pkl`, `split_assignment.csv`.

**Operator prerequisites (not code; listed in docs/setup.md; Terraform must be installed before implementation starts, because `make tf-validate` and the lock-file recipe need it):** Terraform 1.16.5 installed (`brew install hashicorp/tap/terraform`, then check `terraform version`); `gcloud auth login` (the gcloud CLI calls in `bootstrap.sh`, the upload and the live checks use it); `gcloud auth application-default login` and `gcloud auth application-default set-quota-project synthea-readmission-risk` (today the ADC token cannot be minted and its quota project is `project-0-490621`; the budget API needs the quota project); the operator keeps Billing Account Administrator (or Costs Manager) on the billing account, as when the CHF 20 budget was created.

## Surfaces touched

New:
- `infra/versions.tf`, `infra/providers.tf`, `infra/variables.tf`, `infra/apis.tf`, `infra/storage.tf`, `infra/iam.tf`, `infra/wif.tf`, `infra/budgets.tf`, `infra/outputs.tf`
- `infra/.terraform.lock.hcl` (committed; `linux_amd64` + `darwin_arm64`)
- `infra/terraform.tfvars.example` (committed); `infra/terraform.tfvars` (gitignored)
- `infra/bootstrap.sh` (executable)
- `.github/workflows/smoke.yml`
- `tests/test_infra.py` (at the `tests/` root, like `test_docs.py` and `test_tooling.py`: it tests configuration, not a package)

Edited:
- `.github/workflows/ci.yml` (new job `terraform-validate`)
- `.github/dependabot.yml` (terraform entry)
- `.gitignore` (Terraform entries)
- `Makefile` (`tf-validate`, `TERRAFORM` variable)
- `tests/test_docs.py` (`EXPECTED_DEPENDABOT`, renamed Dependabot test, `MUST_BE_IGNORED`, `MUST_NOT_BE_IGNORED`)
- `tests/test_tooling.py` (`EXPECTED_TARGETS` + one dry-run test)
- "slice 3" → "slice 4" (text only): `src/readmission_risk/bundles/store.py` lines 2, 28 and 90 (module docstring, the `gs://` refusal, the `write_bundle` docstring), `tests/bundles/test_store.py:135`, the `tests/bundles/test_champion.py:53` comment, and the `.gitignore` comment above `/bundles/` (`docs/scoring.md:50` is reworded, see below)
- Stale prose: `.github/dependabot.yml` header ("github-actions only" → actions and Terraform), `tests/test_docs.py` module docstring lines 17-19 ("Dependabot covers github-actions only" → "github-actions and terraform"), `.github/workflows/ci.yml:9` comment (adds "and validates the Terraform config"), `docs/setup.md:116` ("Nothing runs in the cloud yet" → the infrastructure exists from slice 3; the budget is now Terraform-managed)
- `README.md` "Repository layout" block (lines 181-183): add `infra/` ("Terraform: buckets, service accounts, WIF, budgets; applied from a laptop, validated in CI"), extend the workflows line with "a dispatch-only keyless GCP smoke job", the dependabot line with "and the Terraform provider", and the Makefile line with `tf-validate`. The roadmap ("Cloud (not started)", line 25 and 133) is **not** changed: nothing runs in the cloud until slice 4, and the README's v2 framing is rewritten in slice 8
- `docs/setup.md`: besides Scope item 10, the "Makefile and pre-commit" target table gains a `make tf-validate` row ("Check `infra/` like CI's `terraform-validate` job; needs Terraform 1.16.x").
- `docs/scoring.md:50` is reworded, not just renumbered: "**Fresh clone:** bundles are gitignored, and the copies uploaded in v2 slice 3 live in a private bucket, so the committed champion names a bundle that only the author can read or reproduce (`make export` from the author's MLflow store). Anyone else trains, exports and scores their own: …" (the rest of the sentence unchanged).
- `CLAUDE.md`, exhaustively (every sentence this slice makes false): (1) the ruleset sentence (Revision log, Readiness 9); (2) "Since 2026-10-11 (v2 slice 1) CI has three jobs" → four, adding "`terraform-validate` (Terraform 1.16.5 from a SHA256-verified zip; `fmt -check`, `init -backend=false -lockfile=readonly`, `validate`; no credentials)"; (3) the Dependabot sentence → "github-actions and the Terraform provider (`/infra`)"; (4) "`tf-validate` target deferred to slice 3" in the v2 bullets → "landed in slice 3"; (5) the Make targets list in "Build & test commands" gains `tf-validate`, and a new "Infrastructure" bullet there gives `infra/bootstrap.sh`, `terraform -chdir=infra plan/apply` (laptop only, owner-confirmed) and points to docs/setup.md > "Infrastructure (Terraform)"; (6) the test count "738 tests" → the new collected count, with the same 1 skip; (7) in the slice-2 bullet, "(GCS: slice 3)" → "(uploaded to the private bundle bucket in slice 3; read from GCS in slice 4)" and "reproducible only from the owner's local MLflow store until slice 3" → "reproducible only by the owner (local MLflow store, or the private bucket)"; (8) a "Landed in slice 3" note on the "Orchestration and identity" bullet (buckets, two SAs, WIF pool/provider with the numeric + event_name condition, budgets; V-post pending). Nothing else in CLAUDE.md changes.
- The PRD (status lines, per the Revision log, Readiness 9)
- Memory (outside the repo): `project_wif_proof_deferred_branch_ruleset.md` is rewritten to "slice 3 landed: infra applied, both bundles uploaded; V-post still pending after the v2 merge; ruleset unchanged" (name/description updated, `MEMORY.md` line updated); `project_gcp_setup.md` gains "Terraform in `infra/` manages buckets (tfstate/raw/gold/bundles), gha-smoke + bundle-writer, WIF pool `github` / provider `github-actions`, the CHF 5 budget, and the imported CHF 20 budget; apply from the laptop only".

## Interfaces

### `infra/versions.tf`

```hcl
terraform {
  required_version = ">= 1.16.5, < 1.17.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.5.0"
    }
  }
  # Backends cannot read variables, so the name is literal. bootstrap.sh creates this bucket first.
  backend "gcs" {
    bucket = "synthea-readmission-risk-tfstate"
    prefix = "infra"
  }
}
```

### `infra/providers.tf`

```hcl
provider "google" {
  project = var.project_id
  region  = var.region
  # User ADC needs a quota project for the Billing Budget API; without it budget calls fail with a quota-project error.
  billing_project       = var.project_id
  user_project_override = true
  default_labels = {
    repo       = "synthea-readmission-risk"
    managed-by = "terraform"
  }
}
```

### `infra/variables.tf`

| Variable | Type | Default | Committed value? |
|---|---|---|---|
| `project_id` | string | `"synthea-readmission-risk"` | yes |
| `project_number` | string | `"271458030145"` | yes |
| `region` | string | `"us-central1"`; `validation` requires exactly `us-central1` | yes |
| `github_repository` | string | `"fma00/synthea-readmission-risk"` | yes |
| `github_repository_id` | string | `"1380503419"`; `validation`: `can(regex("^[0-9]+$", ...))` | yes |
| `github_repository_owner_id` | string | `"270004823"`; same validation | yes |
| `billing_account` | string | none (required) | no: `terraform.tfvars` only |
| `existing_budget_id` | string | none (required) | no: `terraform.tfvars` only |
| `operator_member` | string | none (required); `validation`: starts with `user:` | no: `terraform.tfvars` only |

`terraform.tfvars.example` carries the three required variables with placeholders (`billing_account = "XXXXXX-XXXXXX-XXXXXX"`, `existing_budget_id = "<id from gcloud billing budgets list>"`, `operator_member = "user:you@example.com"`). The billing account id is kept out of the repo for the same reason `docs/setup.md` already writes `<BILLING_ACCOUNT_ID>`; the operator's email is kept out because it is personal data in a public repo.

### `infra/apis.tf`

`google_project_service` for `iam.googleapis.com`, `iamcredentials.googleapis.com`, `sts.googleapis.com`, `cloudresourcemanager.googleapis.com`, `storage.googleapis.com`, `billingbudgets.googleapis.com`, each `disable_on_destroy = false` (disabling an API on destroy can break resources outside this config and costs nothing to leave on). Expressed as one `for_each` over a `toset([...])` named `local.services`. Every non-API resource (buckets, service accounts, IAM members, pool, provider, budgets) carries `depends_on = [google_project_service.enabled]`.

### `infra/storage.tf`

All four: `location = "US-CENTRAL1"` (from `upper(var.region)`), `storage_class = "STANDARD"`, `uniform_bucket_level_access = true`, `public_access_prevention = "enforced"`, `labels = { purpose = <see table> }` (plus the provider's default labels).

| Resource | Name | Versioning | Lifecycle | Soft delete | `force_destroy` | `prevent_destroy` |
|---|---|---|---|---|---|---|
| `google_storage_bucket.tfstate` | `synthea-readmission-risk-tfstate` | on | delete when `num_newer_versions = 10` and `with_state = "ARCHIVED"` | default (7 days) | false | **true** |
| `google_storage_bucket.raw` | `synthea-readmission-risk-raw` | off | delete when `age = 90` (OQ5; the whole bucket is "raw/") | `retention_duration_seconds = 0` (no billed copies of deleted raw data) | true (regenerable, deterministic per seed) | false |
| `google_storage_bucket.gold` | `synthea-readmission-risk-gold` | off | none | default | true (rebuilt from raw) | false |
| `google_storage_bucket.bundles` | `synthea-readmission-risk-bundles` | on (protects against an owner/editor overwrite, which IAM cannot prevent) | none | default | false | **true** |

`purpose` labels: `terraform-state`, `raw-synthea`, `gold`, `model-bundles`.

### `infra/iam.tf`

```hcl
resource "google_service_account" "gha_smoke" {
  account_id   = "gha-smoke"
  display_name = "GitHub Actions smoke job (lists the bundle bucket)"
}

resource "google_service_account" "bundle_writer" {
  account_id   = "bundle-writer"
  display_name = "Model bundle writer (create-only)"
}
```

| Binding resource | Target | Role | Member |
|---|---|---|---|
| `google_storage_bucket_iam_member.smoke_lists_bundles` | bundles bucket | `roles/storage.legacyBucketReader` (`storage.buckets.get` + `storage.objects.list`; no `objects.get`, so the smoke job cannot download a pickle) | `serviceAccount:gha-smoke@…` |
| `google_storage_bucket_iam_member.writer_creates_bundles` | bundles bucket | `roles/storage.objectCreator` | `serviceAccount:bundle-writer@…` |
| `google_service_account_iam_member.smoke_wif` | `gha_smoke` SA | `roles/iam.workloadIdentityUser` | `local.smoke_principal_set` |
| `google_service_account_iam_member.operator_impersonates_writer` | `bundle_writer` SA | `roles/iam.serviceAccountTokenCreator` | `var.operator_member` |

```hcl
locals {
  # The exported `name` (projects/<number>/locations/global/workloadIdentityPools/github), not a hand-built string.
  pool_name           = google_iam_workload_identity_pool.github.name
  smoke_workflow_ref  = "${var.github_repository}/.github/workflows/smoke.yml@refs/heads/main"
  smoke_principal_set = "principalSet://iam.googleapis.com/${local.pool_name}/attribute.workflow_ref/${local.smoke_workflow_ref}"
}
```

Bucket-level legacy roles remain grantable under uniform bucket-level access (only the legacy *object* roles are not); if the API refuses `legacyBucketReader` at apply, stop and ask the owner (fallback: a project custom role with exactly `storage.buckets.get` + `storage.objects.list`). HCL forms (fixed, so the static tests can match them): bucket members use `bucket = google_storage_bucket.bundles.name` and `member = "serviceAccount:${google_service_account.<sa>.email}"`; SA members use `service_account_id = google_service_account.<sa>.name` and `member = local.smoke_principal_set` or `member = var.operator_member`; each argument on its own line. Only `*_iam_member` (additive) resources are used: never `_iam_policy` or `_iam_binding`, which would replace bindings Terraform does not own (the owner's, the service agents'). Why `objectCreator` blocks overwrite: replacing an existing object needs `storage.objects.delete`, which the role lacks (live check V5 proves this, closing the NHV item "`storage.objectCreator` lacking overwrite"). Why the operator gets Token Creator on the writer: `roles/owner` does not let a user mint tokens for a service account, and uploads must run *as* the writer so that the create-only rule applies to them; the owner's own identity could overwrite.

Why each SA binding is keyed to `workflow_ref` and not to the whole repository: any workflow file on `main` would otherwise be able to impersonate every service account; with this binding `gha-smoke` is usable only by `smoke.yml` on `main`. Later slices bind their own SA to their own workflow file the same way. If the repository is renamed, `workflow_ref` changes and the binding stops matching (fails closed; the numeric condition still holds).

### `infra/wif.tf`

```hcl
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github_actions" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions"
  display_name                       = "GitHub Actions OIDC"
  attribute_mapping = {
    "google.subject"                = "assertion.sub"
    "attribute.repository_id"       = "assertion.repository_id"
    "attribute.repository_owner_id" = "assertion.repository_owner_id"
    "attribute.ref"                 = "assertion.ref"
    "attribute.event_name"          = "assertion.event_name"
    "attribute.workflow_ref"        = "assertion.workflow_ref"
  }
  attribute_condition = join(" && ", [
    "assertion.repository_id == '${var.github_repository_id}'",
    "assertion.repository_owner_id == '${var.github_repository_owner_id}'",
    "assertion.ref == 'refs/heads/main'",
    "assertion.event_name in ['workflow_dispatch', 'schedule']",
  ])
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}
```

No `allowed_audiences`: the default audience is the provider's resource name, which `google-github-actions/auth` requests by default. `google.subject` = `repo:fma00@270004823/synthea-readmission-risk@1380503419:ref:refs/heads/main` (76 characters, under the 127 limit; this is the value that appears in STS/IAM audit logs when debugging V-post).

### `infra/budgets.tf`

```hcl
import {
  to = google_billing_budget.monthly
  id = "billingAccounts/${var.billing_account}/budgets/${var.existing_budget_id}"
}
```

The `import` block stays in `budgets.tf` after the first apply: while the budget is in state it is a no-op, and it documents that the budget pre-dates Terraform. Teardown deletes it before touching the budget (see "Teardown checklist", step 1), so a `state rm` is never followed by a re-import.

`google_billing_budget.monthly` reproduces the existing budget field for field (values under "Facts"), `budget_filter.projects = ["projects/${var.project_number}"]`, with `lifecycle { prevent_destroy = true }`. Acceptance: after the import, `terraform plan` shows **no update** for `monthly`. If the provider reports a purely representational diff (for example an empty `all_updates_rule` block), the config is aligned to the imported state until the plan is clean; an apply that would change amount, thresholds, filter or notifications is never run.

`google_billing_budget.early_warning`: display name `synthea-readmission-risk early warning`, `CHF` units `5`; `budget_filter` identical to `monthly`'s (`projects = ["projects/${var.project_number}"]`, `calendar_period = "MONTH"`, `credit_types_treatment = "INCLUDE_ALL_CREDITS"`); thresholds 1.0 `CURRENT_SPEND` and 1.0 `FORECASTED_SPEND`; no `all_updates_rule` block, so notification is the default email to billing-account admins and users, the same accepted behaviour as `monthly` (no Pub/Sub, no monitoring channels); no `prevent_destroy` (it is Terraform-born and goes with the rest at teardown).

### `infra/outputs.tf`

`workload_identity_provider` (`google_iam_workload_identity_pool_provider.github_actions.name`, the value `smoke.yml` hard-codes), `smoke_service_account_email`, `bundle_writer_service_account_email`, `bucket_names` (map with keys `tfstate`, `raw`, `gold`, `bundles` → the bucket names). No sensitive outputs.

### `infra/bootstrap.sh`

`set -euo pipefail`; `cd "$(dirname "$0")"`. Constants: `PROJECT=synthea-readmission-risk`, `STATE_BUCKET=synthea-readmission-risk-tfstate`, `STATE_OBJECT=gs://$STATE_BUCKET/infra/default.tfstate` (the GCS backend's object for prefix `infra`, workspace `default`). A `trap` removes `bootstrap_override.tf` on every exit. Never `-auto-approve`.

**Preflight** (any failure → message, exit 1, nothing changed):
1. `terraform version` reports 1.16.x; `terraform.tfvars` exists and `terraform fmt -check terraform.tfvars` passes; `gcloud` is on `PATH`.
2. Both credentials work: `gcloud auth print-access-token >/dev/null` (the gcloud CLI login) and `gcloud auth application-default print-access-token >/dev/null` (ADC, which Terraform uses). They are separate logins; either can be broken while the other works.
3. `gcloud services enable iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com cloudresourcemanager.googleapis.com storage.googleapis.com billingbudgets.googleapis.com --project=$PROJECT` (idempotent). Reason: `google_project_service` in provider 8.5.0 reads each *newly* enabled service back through the Cloud Resource Manager API with no retry for "API not enabled", and CRM is disabled today, so enabling it in the same Terraform batch would likely fail the first apply. Enabled beforehand, Terraform adopts all six as already-enabled. This changes the live project: the owner confirms the bootstrap run before it starts.

**Probe** (a function used for the object and the bucket): runs `gcloud storage objects describe "$STATE_OBJECT"` / `gcloud storage buckets describe "gs://$STATE_BUCKET"`, capturing stderr. Result `present` on exit 0; `absent` only when the exit is non-zero **and** stderr contains `404` or `not found`; anything else (permission, auth, network) → print stderr and exit 1. Unknown is never treated as absent.

**Decision table** (one row applies; evaluated in this order):

| # | State object | Bucket | Local `terraform.tfstate` | Action |
|---|---|---|---|---|
| A | present | (present) | any | Already bootstrapped: `terraform init -input=false -reconfigure` (GCS backend); if a local `terraform.tfstate` exists, print a warning naming it (not deleted: it may be the only record of a half-finished run); exit 0. |
| B | absent | present | exists | Resume: **migrate**. |
| C | absent | present | none | Refuse, exit 1: bucket without state anywhere. Print the manual fix (under a hand-written local override: `terraform import google_storage_bucket.tfstate synthea-readmission-risk-tfstate`, then re-run). |
| D | absent | absent | any | **create** (an existing local state from a failed earlier run is reused: Terraform plans only what is missing), then **migrate**. |

**create:** write `bootstrap_override.tf` = `terraform { backend "local" {} }`; `terraform init -input=false -reconfigure`; `terraform apply -target=google_project_service.enabled -target=google_storage_bucket.tfstate` (interactive approval). If the apply fails, exit 1 with the local state kept; the re-run lands in D again (bucket still absent) or B (bucket created, later step failed).

**migrate:** re-probe the state object immediately before migrating and refuse unless it is `absent` (so `-force-copy` can only ever write into an empty backend, never over a real remote state); ensure the override exists and run `terraform init -input=false -reconfigure` (so `.terraform/` records the local backend as current); remove the override; `terraform init -input=false -migrate-state -force-copy`; check that `terraform state list` (now against GCS) contains `google_storage_bucket.tfstate`; only then delete the local `terraform.tfstate` and `terraform.tfstate.backup`. If the check fails, keep the local files and exit 1.

The full `terraform plan`/`apply` afterwards is a manual operator step (documented), each apply confirmed by the owner in chat before it runs.

### `.github/workflows/smoke.yml`

```yaml
name: GCP smoke

# Keyless proof of the WIF path (v2 slice 3): exchanges this job's GitHub OIDC token for a short-lived token of the
# gha-smoke service account and lists the bundle bucket. Dispatch only; the WIF condition accepts only refs/heads/main.
on:
  workflow_dispatch:

permissions: {}

jobs:
  list-bundles:
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    permissions:
      contents: read
      id-token: write
    steps:
      # The auth action writes its credentials file into $GITHUB_WORKSPACE and its README requires checkout first.
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
        with:
          persist-credentials: false
      - uses: google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093  # v3.0.0
        with:
          workload_identity_provider: projects/271458030145/locations/global/workloadIdentityPools/github/providers/github-actions
          service_account: gha-smoke@synthea-readmission-risk.iam.gserviceaccount.com
      - name: List the bundle bucket
        run: gcloud storage ls gs://synthea-readmission-risk-bundles/
```

Checkout runs first, although the job uses no repository content: `google-github-actions/auth`'s README (v3.0.0, Prerequisites and `create_credentials_file`) requires it, because the credentials file goes into `$GITHUB_WORKSPACE`; without it later steps may run unauthenticated, which would only surface at V-post and look like a WIF failure. The pin is the same `actions/checkout` SHA as `ci.yml`. The project number and SA email are public identifiers and committed.

### `ci.yml` job `terraform-validate`

Placed last in `ci.yml`, after `audit-workflows`, with a short comment above it like the other jobs. Job keys: `runs-on: ubuntu-24.04` only (no `timeout-minutes`, no job-level `permissions`: it inherits the workflow's top-level `contents: read`, like the existing jobs).


Steps: checkout (`persist-credentials: false`); install Terraform 1.16.5 by `curl -fsSLo` of `https://releases.hashicorp.com/terraform/1.16.5/terraform_1.16.5_linux_amd64.zip`, `echo "<sha256>  terraform.zip" | sha256sum -c -`, `unzip` into `$RUNNER_TEMP/terraform-bin`, add that directory to `$GITHUB_PATH`; then `terraform -chdir=infra fmt -check -recursive -diff`, `terraform -chdir=infra init -backend=false -input=false -lockfile=readonly`, `terraform -chdir=infra validate`. The version and hash are workflow-level `env:` values (`TERRAFORM_VERSION`, `TERRAFORM_SHA256`) so the run steps only reference env vars (zizmor template-injection clean). Not `hashicorp/setup-terraform`: a pinned SHA256 of the binary is strictly stronger than trusting an action's download path, and it adds no new action. The cost: Dependabot does not bump the CLI; a version bump is a manual two-line edit (version + hash) plus `required_version`, enforced consistent by a test.

### Makefile

```make
TERRAFORM ?= terraform
tf-validate: ## Check infra/ like CI's terraform-validate job: fmt -check, init without backend, validate
	$(TERRAFORM) -chdir=infra fmt -check -recursive -diff
	$(TERRAFORM) -chdir=infra init -backend=false -input=false -lockfile=readonly
	$(TERRAFORM) -chdir=infra validate
```

`fmt -check -recursive` also checks `*.tfvars`, so locally it covers the operator's gitignored `infra/terraform.tfvars`, which CI never sees. Accepted and documented (docs/setup.md, next to the tfvars step): run `terraform -chdir=infra fmt` after editing `terraform.tfvars`; the bootstrap preflight runs `terraform fmt -check terraform.tfvars` and refuses with that hint. Added to `.PHONY`, and `TERRAFORM` is added to `MAKEFILE_VARIABLES` in `tests/test_tooling.py` (so it is scrubbed from the test environment like every other Makefile variable). `init -backend=false` writes `infra/.terraform/` (gitignored) and never touches GCS.

### `.github/dependabot.yml`

Header comment rewritten to say it covers the SHA-pinned actions and the Terraform provider (pip stays excluded, same reason as today). Appended (deliberately no `groups:` block: there is one provider, so grouping changes nothing):
```yaml
  - package-ecosystem: "terraform"
    directory: "/infra"
    schedule:
      interval: "weekly"
    cooldown:
      default-days: 7
```

### `.gitignore`

Appended block: `infra/.terraform/`, `*.tfstate`, `*.tfstate.*`, `*.tfvars`, `*.tfvars.json`, `!*.tfvars.example`, `*_override.tf`, `*_override.tf.json`, `crash.log`, `.terraform.tfstate.lock.info`. (`*.tfvars` also covers `*.auto.tfvars` and `terraform.tfvars`.) The comment above `/bundles/` changes from "local until v2 slice 3 moves them to GCS" to "local; slice 3 uploads copies to the bundle bucket, slice 4 reads them from there".

### Provider lock file

Generated once on the laptop, before the first commit of `infra/`, and on every provider bump:
```sh
terraform -chdir=infra init -backend=false -input=false
terraform -chdir=infra providers lock -platform=linux_amd64 -platform=darwin_arm64
```
The file then holds one `provider "registry.terraform.io/hashicorp/google"` block with `version = "8.5.0"`, `constraints = "8.5.0"` and at least two `h1:` hashes (one per locked platform) plus the registry's `zh:` hashes. CI's `-lockfile=readonly` refuses to change it. The recipe goes into docs/setup.md.

### gcloud context rule

The operator's gcloud default project is deliberately `project-0-490621` (with `billing/quota_project = CURRENT_PROJECT`), which has neither `iam` nor `iamcredentials` enabled; it is **not** changed. Instead:
1. `bootstrap.sh` and every command block in docs/setup.md (bootstrap, upload, live checks, V-post, teardown) start with `export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk`, so project-scoped commands and the user-credential quota project (`CURRENT_PROJECT`) both resolve to this project. Commands that take one also pass `--project=synthea-readmission-risk` explicitly (V7, V8's `workload-identity-pools providers describe`, teardown steps 7–8, `undelete`).
2. Impersonated commands (the token probe, the uploads, V5) are additionally prefixed with `CLOUDSDK_BILLING_QUOTA_PROJECT=LEGACY`, so gcloud sends no `X-Goog-User-Project` header: the user-credential mint is billed the legacy way, and the impersonated storage calls need no `serviceusage.services.use` on `bundle-writer` (whose only roles stay as in the IAM table and V8).
3. The bootstrap preflight checks that `gcloud projects describe synthea-readmission-risk` reports `ACTIVE` with the gcloud login (implementation review: a `gcloud config get project` check could not fail under the export).
4. Failure mode: if an impersonated call still fails with "has not been used in project …" or "serviceusage", that is a context error, not propagation: stop and ask the owner; never grant `serviceusage` roles to `bundle-writer` silently.

## Flow

Operator (laptop), once:
1. Prerequisites (Terraform, ADC + quota project).
2. `cp infra/terraform.tfvars.example infra/terraform.tfvars`, fill in the three values.
3. `infra/bootstrap.sh` → state bucket exists; state is in GCS. **Owner confirms in chat before the targeted apply.**
4. `terraform -chdir=infra plan` → acceptance figure **"Plan: 1 to import, 12 to add, 0 to change, 0 to destroy"** (the APIs and the state bucket are already in state from the bootstrap; the 12 are 3 buckets, 2 service accounts, 4 IAM members, the pool, the provider and the CHF 5 budget; the import is `monthly` with no update). Any other figure: stop and investigate before applying. **Owner confirms before apply.** `terraform apply`.
5. `terraform plan` again → "No changes".
6. **IAM propagation rule** (new bindings can take minutes to apply): wait 5 minutes after the apply, then probe the impersonation alone, `CLOUDSDK_BILLING_QUOTA_PROJECT=LEGACY gcloud auth print-access-token --impersonate-service-account="$W" >/dev/null` (context rule above), retrying every 60 s for up to 10 minutes. A failure here is a **token-mint** failure (gcloud names `iam.serviceAccounts.getAccessToken`): propagation of the Token Creator grant, or V8 will show why. Then upload; a Cloud Storage 403 on the **first** upload (message names `storage.objects.create`) is retried up to 3 times at 60 s before it counts as a role problem under Failure modes. V5's negative checks run only after V4 has fully succeeded, which proves both grants have propagated, so a 403/412 there is meaningful.
7. Upload both v1 bundles as `bundle-writer` (per file, `manifest.json` last, so a reader never sees a manifest without its files):
   ```sh
   export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk
   W=bundle-writer@synthea-readmission-risk.iam.gserviceaccount.com
   for id in 75a1448729ef561c596f5e850f5bedce50ed63355ca2de38dc724c635d24eef4 30009d198070f66db1a0c35b033f11de9e08d88292330e0e71265553c4645d2a; do
     for f in model.pkl split_assignment.csv manifest.json; do
       CLOUDSDK_BILLING_QUOTA_PROJECT=LEGACY gcloud storage cp "bundles/$id/$f" "gs://synthea-readmission-risk-bundles/$id/$f" --if-generation-match=0 --impersonate-service-account="$W"
     done
   done
   ```
   **Owner confirms before the upload** (it publishes the model pickles to a private bucket).
8. Live checks V1–V8.

CI, every PR/push to main: `terraform-validate` (no credentials). After v2 merges: V-post by hand.

## Data handling

No training/scoring data changes; no split, label, feature or seed is touched, so there is no leakage surface. The bundles are uploaded byte-identically; `manifest.json`'s sha256 (pinned in `deploy/champion.json`) is checked after upload (V6). The buckets hold synthetic data only (S5: no PHI). Public access prevention is enforced on every bucket. Reproducibility: Terraform and provider versions are pinned, the provider lock file carries hashes, and state is versioned.

### Redaction rule

Anything recorded in a committed file (this note's "Results", docs, CLAUDE.md, PR text) replaces the billing account id with `<BILLING_ACCOUNT_ID>`, the budget id with `<EXISTING_BUDGET_ID>`, and the operator's email with `<OPERATOR>`. `terraform plan` output is summarised ("N to add, 0 to change, 0 to destroy; 1 to import"), never pasted. Public identifiers stay: project id and number, repository and owner ids, bucket and service-account names.

## Failure modes

| Failure | Behaviour / handling |
|---|---|
| A bucket name is already taken globally | Apply fails on that bucket; nothing is half-made silently. Fix: rename (all four share the prefix) in a reviewed commit; never a random suffix. |
| ADC missing or wrong quota project | Plan fails on budget or API calls with a quota-project error; docs/setup.md prerequisite lists both commands. |
| The budget import plan shows a real change | Do not apply; align config; if impossible, stop and ask the owner (the budget pre-dates Terraform). |
| `principalSet` value containing `/` and `@` rejected by IAM | Apply fails at `smoke_wif` (visible pre-merge). Stop and ask the owner. The candidate fallback is binding on `attribute.repository_id` (repository-wide scope for this one account), documented as a deviation; binding on the workflow `name:` is not acceptable (names are not unique). |
| The binding is accepted but never matches (attribute value mismatch) | Only visible in V-post. The `auth` step stays green (without `token_format` it only writes an external-account credentials file; no exchange happens there); the **`List the bundle bucket`** step fails inside gcloud. A provider-condition refusal shows an STS error (`sts.googleapis.com`, `unauthorized_client` / rejected by the attribute condition); a binding mismatch shows an IAM `iam.serviceAccounts.getAccessToken` PERMISSION_DENIED naming `gha-smoke`. Debug from that stderr and the STS/IAM audit logs (subject = the 76-character immutable `sub`); fix in Terraform. |
| V5 shows an overwrite **succeeding** as `bundle-writer` (no 403 without the precondition) | FR7's first control is void. Stop and report to the owner before anything else; do not repoint anything. The uploaded bundles stay (their bytes are verified by V6, and versioning keeps any overwritten generation), the overwritten generation is inspected with `gcloud storage ls --all-versions`, and the cause (an extra grant found by V8, or a role-semantics change) decides the fix. |
| `gcloud storage cp --if-generation-match=0` needs a permission `objectCreator` lacks (e.g. a pre-flight `get`) | V4 fails. Do not widen the role silently; stop and report. Options: a raw JSON-API upload with `curl` and the impersonated token, or (owner decision) `objectViewer` on the writer too, which still does not grant delete. |
| Someone with project Owner/Editor (the operator, or the default compute SA via `roles/editor`) overwrites a bundle | IAM cannot prevent this; versioning on the bundle bucket keeps the old generation. Recorded as a known limit of FR7, and the `roles/editor` grant is flagged for the Dataproc slice. |
| `bootstrap.sh` interrupted after the targeted apply | Re-run takes branch B (no state object in GCS, local `terraform.tfstate` present) and resumes at **migrate**. Interrupted during migrate after the copy: branch A (state object exists), and the leftover local state file is reported, not deleted, by the A branch (it prints a warning naming it). |
| `terraform destroy` run naively | Fails on the three `prevent_destroy` resources before destroying anything; the teardown checklist is the supported path. |
| Drift (a console or gcloud change outside Terraform) | Not detected automatically: CI has no credentials. Accepted gap: every later slice that touches `infra/` starts with a laptop `terraform plan` that must show only its own changes; V8 checks the IAM policies directly once, here. |
| WIF pool deleted and re-created with the same id | Fails for 30 days (soft delete). Teardown checklist: undelete, or pick a new id. |
| CI runner cannot reach the registry during `init` | Job fails red (signal, not a merge gate; `main` has a ruleset requiring PRs but no required checks). |

## Verification

**Automated (pytest, `tests/test_infra.py`):** static, line/regex based like `test_docs.py`; no new dependency, no HCL parser.
- `test_wif_condition_pins_numeric_repo_and_owner_ids_main_and_event_names`: `wif.tf` contains the line `attribute_condition = join(" && ", [` (whitespace-collapsed) and, inside that list (up to its closing `])`), exactly the four condition fragments verbatim (with the `${var...}` interpolations) and nothing else; no `||` appears anywhere in `wif.tf`; `variables.tf` defaults are `"1380503419"` and `"270004823"`; for every `assertion.X` token in the `attribute_condition` (regex `assertion\.([a-z_]+)`), the mapping contains `"attribute.X" = "assertion.X"` after collapsing runs of whitespace to one space (`terraform fmt` aligns the `=` signs).
- `test_smoke_sa_binding_is_keyed_to_the_smoke_workflow_on_main`: `iam.tf` defines (whitespace-collapsed, verbatim) `smoke_workflow_ref = "${var.github_repository}/.github/workflows/smoke.yml@refs/heads/main"`, `smoke_principal_set = "principalSet://iam.googleapis.com/${local.pool_name}/attribute.workflow_ref/${local.smoke_workflow_ref}"` and `pool_name = google_iam_workload_identity_pool.github.name`; `.github/workflows/smoke.yml` exists; the string `attribute.repository` does not appear in any `member` of `infra/*.tf` (a repository-wide binding is an owner-approved deviation, never a silent edit).
- `test_smoke_workflow_matches_the_terraform_values`: `smoke.yml`'s `workload_identity_provider` equals `projects/<project_number default>/locations/global/workloadIdentityPools/<workload_identity_pool_id>/providers/<workload_identity_pool_provider_id>` read from `variables.tf` and `wif.tf`; its `service_account` equals `<gha_smoke account_id from iam.tf>@<project_id default>.iam.gserviceaccount.com`; the bucket in its `gcloud storage ls` equals the `bundles` bucket name in `storage.tf`.
- `test_smoke_account_can_only_list`: parse every `resource "google_*_iam_member" "<name>" { … }` block in `infra/*.tf` (regex over the block text, arguments one per line); among blocks whose `member` references `google_service_account.gha_smoke.email`, the only `role` is `roles/storage.legacyBucketReader`, on `google_storage_bucket.bundles.name`; the only block whose `service_account_id` is `google_service_account.gha_smoke.name` has role `roles/iam.workloadIdentityUser` and member `local.smoke_principal_set`.
- `test_no_service_account_keys_in_infra_or_workflows`: no `google_service_account_key` in `infra/*.tf`; no `credentials_json` in any workflow.
- `test_buckets_enforce_uniform_access_and_public_access_prevention`: each of the 4 `google_storage_bucket` blocks has `uniform_bucket_level_access = true` and `public_access_prevention = "enforced"`; exactly 4 bucket resources.
- `test_raw_bucket_deletes_after_90_days` and `test_state_and_bundle_buckets_and_monthly_budget_prevent_destroy`.
- `test_iam_is_additive_only`: no `_iam_policy` or `_iam_binding` resources in `infra/`.
- `test_bundle_writer_is_create_only`: with the same block parser, the only block whose `member` references `google_service_account.bundle_writer.email` has role `roles/storage.objectCreator` on `google_storage_bucket.bundles.name`; the only block whose `service_account_id` is `google_service_account.bundle_writer.name` has role `roles/iam.serviceAccountTokenCreator` and member `var.operator_member`.
- `test_terraform_versions_agree`: CI `TERRAFORM_VERSION` = the lower bound of `required_version`; the provider version is exact; `.terraform.lock.hcl` has exactly one provider block, for `registry.terraform.io/hashicorp/google`, with the same `version`, and at least two `h1:` hashes (platform coverage itself is enforced by CI's `-lockfile=readonly` init on linux and by the documented recipe; the `h1:` values are opaque, so a test cannot name their platforms).
- `test_id_token_write_only_at_job_level_in_dispatch_or_schedule_workflows`: for every workflow file, `id-token: write` never appears in a top-level `permissions:` block, and any file containing it has only `workflow_dispatch`/`schedule` triggers; `smoke.yml` has `permissions: {}` at top level, only the `workflow_dispatch` trigger, and its one job's permission set is exactly `{contents: read, id-token: write}`.
- `test_ci_runs_terraform_validate_without_credentials`: the `terraform-validate` job runs `fmt -check`, `init -backend=false ... -lockfile=readonly`, `validate`, verifies the zip with `sha256sum -c`; no non-comment line of the job matches `\bterraform\b.*\b(plan|apply)\b`, and the job has no `id-token`.
- `test_bootstrap_script_never_auto_approves`: `bootstrap.sh` has no `-auto-approve` and uses `set -euo pipefail`.

Edited tests: `tests/test_docs.py` `EXPECTED_DEPENDABOT` gains the six terraform lines (`package-ecosystem`, `directory`, `schedule:`, `interval`, `cooldown:`, `default-days`), exactly as in Interfaces; the test is renamed `test_dependabot_config_is_weekly_with_cooldown_for_actions_and_terraform`; `MUST_BE_IGNORED` gains `infra/terraform.tfvars`, `infra/prod.auto.tfvars`, `infra/terraform.tfvars.json`, `infra/terraform.tfstate`, `infra/terraform.tfstate.backup`, `infra/.terraform/providers/x`, `infra/bootstrap_override.tf`; `MUST_NOT_BE_IGNORED` gains `infra/.terraform.lock.hcl`, `infra/terraform.tfvars.example`, `infra/storage.tf`, `infra/bootstrap.sh`. `tests/test_tooling.py`: `EXPECTED_TARGETS` gains `tf-validate`; `MAKEFILE_VARIABLES` gains `TERRAFORM`; new `test_make_tf_validate_runs_fmt_init_without_backend_and_validate` asserts that `make -n tf-validate` prints exactly these three lines: `terraform -chdir=infra fmt -check -recursive -diff`, `terraform -chdir=infra init -backend=false -input=false -lockfile=readonly`, `terraform -chdir=infra validate`. `tests/bundles/test_store.py:135` expects "slice 4".

**Tool gates:** `ruff check .`, `pytest` (baseline 738 collected = 737 passed + 1 skipped; after this slice 738 + N collected, all passed except the same 1 skip, where N is the number of new test cases including parametrized `.gitignore` cases), `zizmor --offline .` locally and online in CI with zero findings, `make tf-validate` locally, CI green on all four jobs.

**Live checks (operator laptop, after apply; results recorded in the design note's "Results" section):**
- **V1** `terraform plan` → "No changes."
- **V2** `gcloud billing budgets list --billing-account=… --billing-project=synthea-readmission-risk` → among budgets whose `budgetFilter.projects` contains `projects/271458030145` (the billing account may hold others; they are ignored), exactly two: CHF 20 unchanged (same thresholds) and CHF 5. A third budget for this project fails the check.
- **V3** `gcloud storage buckets describe` each bucket → `US-CENTRAL1`, PAP `enforced`, uniform access on, versioning and lifecycle as in the table.
- **V4** The upload in Flow step 7 succeeds as `bundle-writer` (6 objects).
- **V5** As `bundle-writer`, for `gs://synthea-readmission-risk-bundles/75a1448729ef561c596f5e850f5bedce50ed63355ca2de38dc724c635d24eef4/manifest.json`: record its `generation` and the sha256 of its bytes (owner: `gcloud storage objects describe … --format='value(generation)'`, `gcloud storage cat … | shasum -a 256`); then (a) re-upload with `--if-generation-match=0` → refused with **412 or 403** (GCS may check `storage.objects.delete` before the precondition; either refusal is acceptable); (b) re-upload without a precondition → refused with **403**; (c) `gcloud storage ls` on the bucket → 403 (no list). After (a) and (b), the generation and sha256 are **unchanged** and `gcloud storage ls --all-versions` (owner) shows a single generation: that, not the status code, is the proof that the object was not overwritten. Any success, or a changed generation, triggers the Failure-modes row "V5 shows an overwrite succeeding".
- **V6** As the owner: `gcloud storage cat gs://…-bundles/75a14…/manifest.json | shasum -a 256` equals `deploy/champion.json`'s `sha256`; the same for all 6 objects against the local files.
- **V7** `gcloud iam service-accounts keys list --iam-account=<each SA> --managed-by=user` → empty (M2).
- **V8** Actual policies, not just Terraform's view (additive members cannot see out-of-band grants): `gcloud iam service-accounts get-iam-policy` for `gha-smoke` → exactly one binding, `roles/iam.workloadIdentityUser` → the smoke `principalSet`; for `bundle-writer` → exactly one binding, `roles/iam.serviceAccountTokenCreator` → `<OPERATOR>`; `gcloud storage buckets get-iam-policy gs://synthea-readmission-risk-bundles` → the project-convenience legacy bindings (`projectOwner`/`projectEditor`/`projectViewer`) plus exactly `legacyBucketReader` → gha-smoke and `objectCreator` → bundle-writer. `gcloud iam workload-identity-pools providers describe github-actions --workload-identity-pool=github --location=global --project=synthea-readmission-risk --format='value(attributeCondition)'` → exactly the literal `assertion.repository_id == '1380503419' && assertion.repository_owner_id == '270004823' && assertion.ref == 'refs/heads/main' && assertion.event_name in ['workflow_dispatch', 'schedule']` (compared to this literal, not to `wif.tf`, so a wrong config cannot vouch for itself); the same check prints `attributeMapping` and it must equal the six mappings in `wif.tf`.

**V-post (open item, after v2 merges into main; owner decision D3):** `gh workflow run smoke.yml --ref main` → green, and the log lists the two bundle prefixes. Then a negative proof: on a throwaway branch containing `smoke.yml`, `gh workflow run smoke.yml --ref <branch>` → the `auth` step succeeds (it only writes the credentials file; `smoke.yml` deliberately sets no `token_format`, so no token is minted or exposed as a step output) and the **`List the bundle bucket`** step fails; its stderr must show the **STS** refusal by the attribute condition (`sts.googleapis.com`, `unauthorized_client` / rejected by the attribute condition), not an IAM `getAccessToken` denial and not a context or network error. Any other failure does not count as the negative proof: record it and investigate. On the positive run, the same step must print the two bundle prefixes. Both run URLs are recorded in this note and CLAUDE.md. Until then, the WIF match is documented, not proven.

What V-post does **not** prove, stated so it is not over-claimed: (a) that the `workflow_ref` binding refuses a *different* workflow file on `main` (that needs a second id-token workflow on `main`; it is first exercised when slice 4's scorer workflow, bound to its own SA, cannot impersonate `gha-smoke` and vice versa); (b) the `event_name` clause (every workflow that can reach the token exchange is dispatch-only, so no negative case exists; it is defence in depth for later workflows). Both rest on Google's documented semantics plus V8's policy check.

**Named, not taken: an earlier proof.** A PR into `main` containing only `smoke.yml` would allow V-post before v2 merges without weakening the condition. It is not taken: on 2026-10-11 the owner decided that the WIF proof runs after v2 merges and that `main` stays the v1.1 tree until then. Consequence, accepted: slices 4–7 build on a WIF path that is documented but not live-proven; slice 4's design must keep its first scheduled/dispatched run after the merge too, or revisit this.

## Teardown checklist (goes into docs/setup.md)

Order matters: `prevent_destroy` makes `terraform destroy` refuse while any of the three guarded resources is still in state, and the bundle bucket's IAM members must go before the bucket.

1. **Release the CHF 20 budget** (it pre-dates Terraform and stays as the standing guard while the billing account exists): delete the `import` block and the `google_billing_budget.monthly` resource block from `budgets.tf` in the working copy, then `terraform -chdir=infra state rm google_billing_budget.monthly`.
2. **Keep what you want from the bundle bucket:** copy any bundle out (`gcloud storage cp --recursive gs://synthea-readmission-risk-bundles/<id> <local dir>`).
3. **Remove the bundle bucket's IAM members, then the bucket:** `terraform -chdir=infra destroy -target=google_storage_bucket_iam_member.smoke_lists_bundles -target=google_storage_bucket_iam_member.writer_creates_bundles`; `gcloud storage rm --recursive --all-versions "gs://synthea-readmission-risk-bundles/**"`; `terraform -chdir=infra state rm google_storage_bucket.bundles`; `gcloud storage buckets delete gs://synthea-readmission-risk-bundles`.
4. **Release the state bucket from state:** `terraform -chdir=infra state pull > final.tfstate` (a local record; gitignored by `*.tfstate`), then `terraform -chdir=infra state rm google_storage_bucket.tfstate`.
5. **Destroy the rest:** `terraform -chdir=infra destroy` (raw and gold are `force_destroy`; the CHF 5 budget, both service accounts, all IAM members, the pool and the provider go; APIs stay enabled by `disable_on_destroy = false`). The final state is written to the state bucket, which still exists.
6. **Delete the state bucket last:** `gcloud storage rm --recursive --all-versions "gs://synthea-readmission-risk-tfstate/**"`, then `gcloud storage buckets delete gs://synthea-readmission-risk-tfstate`. The `infra/` working copy is unusable afterwards until a new bootstrap.
7. **Check for leftovers:** `gcloud storage buckets list`, `gcloud dataproc batches list --region=us-central1`, `gcloud compute instances list`, `bq ls` (later slices), `gcloud iam service-accounts list`.
8. The WIF pool stays soft-deleted for 30 days; re-creating `github` in that window needs `gcloud iam workload-identity-pools undelete github --location=global`.
9. Ultimate stop: `gcloud billing projects unlink synthea-readmission-risk`, or delete the project.

## Results

Recorded under the redaction rule. Implementation 2026-10-11 on branch `v2-slice3-terraform-wif` (off `v2`).

**Design check.** Round 1 (comprehension, critic, readiness) and rounds 2–7 (critic; readiness closed in round 5). The three-revision cap was reached after round 4; the owner chose further rounds twice, then "proceed to implementation" after round 7 with the critic's single remaining gap (which workflow step fails in the negative WIF proof) fixed in revision 7 but not re-reviewed.

**Pre-commit review.** 4 rounds, ending in "no findings" (the same-method stop condition tripped after rounds 2 and 3; the owner chose "keep working" both times). 16 findings fixed (bootstrap migration safety, override refusal, `grep -q` under pipefail, resumable and fail-fast uploads, V1–V8/V-post in docs, CLAUDE.md, and six test-strength gaps); one rebutted (memory files deferred until after the live apply, now done). Mutation checks: 12 deliberate breakages of the config, workflows and `bootstrap.sh`, each caught by `tests/test_infra.py`.

**Tool gates.** `ruff check .` clean; `zizmor --offline .` no findings (7 suppressed below the default persona: 3 pre-existing, 4 pedantic notes on `smoke.yml`); `make tf-validate` passes; `pytest` 767 passed, 1 skipped (768 collected; the PySpark tests need `SPARK_LOCAL_IP=127.0.0.1` in the agent sandbox, an environmental limit).

**Bootstrap.** `infra/bootstrap.sh` (branch D): 6 APIs enabled by gcloud and adopted by Terraform, state bucket created ("7 added"), state migrated to `gs://synthea-readmission-risk-tfstate/infra/default.tfstate`, local copy removed after the read-back checks. Re-run: branch A, "Already bootstrapped", no local state file.

**Plan and apply.** `Plan: 1 to import, 12 to add, 0 to change, 0 to destroy` (the acceptance figure); applied from the saved plan: `1 imported, 12 added, 0 changed, 0 destroyed`. IAM accepted the `attribute.workflow_ref/...smoke.yml@refs/heads/main` principalSet (`/` and `@` in the value) and `roles/storage.legacyBucketReader` under uniform access; neither fallback was needed.

**Live checks.**
- V1 `terraform plan`: "No changes."
- V2: exactly two budgets for `projects/271458030145`: CHF 20 (0.5/0.9/1.0 actual, 1.0 forecast, MONTH, INCLUDE_ALL_CREDITS; unchanged by the import) and CHF 5 (1.0 actual, 1.0 forecast).
- V3: all four buckets US-CENTRAL1, PAP enforced, uniform access; versioning on for tfstate and bundles; raw: delete at age 90, soft delete 0; tfstate: delete noncurrent when 10 newer versions exist; labels as designed (plus Google's `goog-terraform-provisioned`).
- V4: 6 objects uploaded as `bundle-writer`, manifests last. **Deviation:** `gcloud storage cp` failed with 403 on `storage.objects.get` (it reads the destination first), the failure-mode row "needs a permission `objectCreator` lacks". Owner decision: option (a), upload through the JSON API with a short-lived impersonated token and `ifGenerationMatch=0`; the role stays create-only. docs/setup.md now documents that loop. (On the failed first attempt, an output-filtering pipe added for the run, not in the documented block, hid the failures from `set -e`, so all six attempts ran; none wrote anything.)
- V5: as `bundle-writer`, re-upload with `ifGenerationMatch=0` → 412; without the precondition → 403 (same bytes and different bytes); list → 403. Generation and sha256 unchanged; one generation. Closes the PRD NHV item "`storage.objectCreator` lacking overwrite" with a live measurement.
- V6: all 6 objects' sha256 equal the local files; the remote logistic-regression `manifest.json` sha256 equals `deploy/champion.json`'s pin (`b61388f0…`).
- V7: no user-managed keys on `gha-smoke` or `bundle-writer`.
- V8: `gha-smoke` has exactly one binding (workloadIdentityUser → the smoke.yml principalSet); `bundle-writer` exactly one (serviceAccountTokenCreator → `<OPERATOR>`); the bundle bucket has the project legacy bindings plus exactly `legacyBucketReader` → gha-smoke and `objectCreator` → bundle-writer; the provider's condition equals the expected literal, the six mappings are as designed, issuer GitHub, no audiences, state ACTIVE.

**Open:** V-post (after v2 merges into `main`).

## Out-of-scope follow-ups

- Slice 4: Python GCS bundle store (`google-cloud-storage`, `ifGenerationMatch=0` writer, reader), `champion.json` → `gs://`, scorer SA bound to its workflow.
- Slices 4–8: BigQuery datasets/tables, monitor / Big Join submitter / Dataproc runtime / trainer / dashboard SAs, the dashboard custom query quota.
- Dataproc slice: decide on removing `roles/editor` from the default compute SA (it can currently overwrite bundles).
- A pre-commit `terraform fmt` hook.
- A lockfile-drift CI check (PRD NFR, still open).
- V-post (see Verification).
- Drift detection beyond the per-slice laptop `plan` (e.g. a scheduled keyless `plan` job with a read-only SA).

## Revision log

**Round 1 (2026-10-11).** Comprehension: unclear on the bootstrap idempotency check. Critic: 16 gaps. Readiness: 12 questions. All addressed in revision 1:

| Gap | Resolution |
|---|---|
| Critic 1, Readiness 1: teardown order (tfstate `prevent_destroy` before destroy; bundle IAM members after the bucket) | Teardown rewritten: state-rm the budget and the state bucket, targeted destroy of the bundle IAM members before deleting the bucket, then `destroy`, state bucket last. |
| Critic 2, Readiness 1: the `import` block's lifecycle | Kept after the first apply (no-op, provenance); teardown step 1 deletes it with the resource block before `state rm`. |
| Critic 3, Readiness 2, Comprehension: bootstrap idempotency contradiction | Four-branch decision table keyed on the GCS state object, the local state file and the bucket; `-reconfigure` before migrate. |
| Critic 4: smoke.yml not tied to Terraform values | `test_smoke_workflow_matches_the_terraform_values`. |
| Critic 5: hand-built pool name | `google_iam_workload_identity_pool.github.name`. |
| Critic 6: live checks blind to out-of-band bindings | V8 reads the actual policies and the provider condition. |
| Critic 7: smoke SA could read pickles | `roles/storage.legacyBucketReader`; test `test_smoke_account_can_only_list`; custom-role fallback named. |
| Critic 8: billing/personal data in recorded output; budget id committed | Redaction rule; the budget id is removed from the Facts section. |
| Critic 9, Readiness 3: missed "slice 3" references | `store.py:90`, `.gitignore` comment, `docs/setup.md:116` added to Surfaces. |
| Critic 10, Readiness 7, 12: stale prose (Dependabot header, test_docs docstring, ci.yml comment, README layout) | All listed under Surfaces with the new wording; README roadmap deliberately unchanged (reason given). Terraform Dependabot entry has no `groups:` on purpose. |
| Critic 11, Readiness 4: `TERRAFORM` not scrubbed in tests | Added to `MAKEFILE_VARIABLES`; the dry-run test's three expected lines are spelled out. |
| Critic 12, Readiness 6: lock-file recipe; Terraform not installed | "Provider lock file" section with the recipe and the test's exact checks; Terraform install moved before implementation. |
| Critic 13: only one tfvars name ignored | `*.tfvars`, `*.tfvars.json`, `!*.tfvars.example`; three more `MUST_BE_IGNORED` cases. |
| Critic 14: ambiguous WIF mapping test | The rule is spelled out (`assertion.X` → `"attribute.X" = "assertion.X"`). |
| Critic 15: thin negative proofs; earlier-proof option | What V-post does not prove is stated; the smoke-only-PR option is named and declined with the owner's 2026-10-11 reason; the consequence for slices 4–7 is stated. |
| Critic 16: drift, `gcloud auth login`, CHF 5 notifications | Drift: accepted gap + per-slice `plan` + follow-up; `gcloud auth login` added to prerequisites; CHF 5 notification behaviour stated. |
| Readiness 5: CHF 5 budget fields | Spelled out (filter fields, thresholds, no `all_updates_rule`). |
| Readiness 8: docs/setup.md placement | See Scope item 10, now: two new sections directly after "Cloud project and cost guard" ("Infrastructure (Terraform)": prerequisites, tfvars, bootstrap, plan/apply, lock-file recipe, bundle upload, live checks, V-post; "Teardown checklist"), and that section's intro rewritten to say the budget is now imported by Terraform. |
| Readiness 9: CLAUDE.md / PRD text | See Scope item 10, now: CLAUDE.md's "`main` has no branch protection or ruleset" becomes "`main` and `v2` have ruleset `require-pr-main-and-v2` (id 24876876, since 2026-10-11): a PR is required, zero approvals, no bypass actors, no required status checks, so a red job is still a signal, not a merge gate"; the v2 architecture bullets get a "Landed in slice 3" note (like slice 2's) and the Dependabot sentence adds Terraform. PRD: an FR8/FR9 "Slice 3 (2026-10-11)" status note like FR4–FR7's, implementation-hints item 3 marked done with the V-post caveat, FR0.1's CHF 5 line marked done, and two NHV items annotated (event_name/workflow_ref mappable per docs; GCS backend locking per docs; objectCreator-overwrite per V5). |
| Readiness 10: `bucket_names` keys | `tfstate`, `raw`, `gold`, `bundles`. |
| Readiness 11: pytest baseline | 738 collected = 737 passed + 1 skipped. |

**Round 2 (2026-10-11).** Critic: 3 gaps; readiness: 3 questions + 4 tightenings. All addressed in revision 2:

| Gap | Resolution |
|---|---|
| Critic 1: partial `create` failure loops in branch B | The decision table now keys on the bucket too: bucket absent → D (re-run create with the existing local state), bucket present → B. |
| Critic 2: any gcloud error read as "absent"; `-force-copy` could overwrite remote state | Preflight checks both logins; the probe distinguishes 404 from other errors (other → exit 1); migrate re-probes and refuses unless the remote object is absent. |
| Critic 3: CRM disabled breaks the provider's read-back of newly enabled services | Preflight enables the six APIs with gcloud (owner-confirmed) so Terraform adopts them. |
| Readiness 1: Dependabot line count | Six lines, named. |
| Readiness 2: memory update | Two memory files and their new content named. |
| Readiness 3: plan acceptance figure | "1 to import, 12 to add, 0 to change, 0 to destroy". |
| Readiness tightenings: one-line HCL; whitespace in the mapping test; `depends_on` scope; no Results section | Multi-line HCL; whitespace-collapsed match; `depends_on` on every non-API resource; Results placeholder added. |

**Round 3 (2026-10-11).** Critic: 4 gaps; readiness: 2 questions. All addressed in revision 3:

| Gap | Resolution |
|---|---|
| Critic 1: `auth` needs a checkout first | `smoke.yml` gains a SHA-pinned checkout with `persist-credentials: false`; the reason is stated. |
| Critic 2: no rule if V5's overwrite succeeds | New failure-mode row: stop, report, keep the bundles, inspect generations, fix by cause. |
| Critic 3, Readiness 1–2: CLAUDE.md / docs lists not exhaustive | Surfaces now list every CLAUDE.md sentence this slice makes false (8 items, "nothing else changes") and the docs/setup.md target-table row. |
| Critic 4: `docs/scoring.md:50` false implication | Reworded (private bucket; only the author can read the champion bundle). |

**Round 4 (2026-10-11).** Critic: 4 gaps; readiness: 5 questions. Three-revision cap reached; owner chose "apply fixes, one more round". Revision 4:

| Gap | Resolution |
|---|---|
| Critic 1: wrong `sub` fact (immutable subject) | Facts and the WIF section corrected (76-character subject, source recorded). |
| Critic 2, Readiness 1: smoke job lacks `contents: read` | Added at job level; the test pins the set to exactly `{contents: read, id-token: write}`. |
| Critic 3: IAM propagation vs. the stop rules | Flow step 6: wait + impersonation probe + bounded retry, token-mint vs. storage 403 distinguished; V5 only after V4 succeeded. |
| Critic 4: local `fmt -check` covers the gitignored tfvars | Accepted and documented; bootstrap preflight checks the tfvars' formatting. |
| Readiness 2: plan/apply match in the CI test | Regex on non-comment lines. |
| Readiness 3: job placement and keys | Last job, `runs-on` only, inherits top-level permissions. |
| Readiness 4: how tests attribute IAM blocks | Fixed HCL forms; block-parser rules spelled out. |
| Readiness 5: V2 on a billing account with other budgets | Filter by this project; exactly two. |

**Round 5 (2026-10-11).** Readiness: implementation ready. Critic: 1 gap. Revision 5:

| Gap | Resolution |
|---|---|
| Critic 1: no gcloud project context; the laptop's default project/quota project is `project-0-490621` | "gcloud context rule": `CLOUDSDK_CORE_PROJECT` exported everywhere, explicit `--project` where accepted, `CLOUDSDK_BILLING_QUOTA_PROJECT=LEGACY` on impersonated calls (no `serviceusage` grant), preflight check, and a stop rule for context errors. |
| Readiness notes: step reference, tfvars example alignment | V4 now references Flow step 7; `terraform.tfvars.example` is written `terraform fmt`-clean. |

**Round 6, critic only (2026-10-11; owner's choice after round 5).** Critic: 2 gaps. Revision 6:

| Gap | Resolution |
|---|---|
| Critic 1: the `&&` conjunction and the principal-set definition are not pinned; V8 compared live config to `wif.tf` | The WIF test asserts the `join(" && ", [` line, exactly four fragments and no `||`; the binding test asserts the `smoke_principal_set`, `smoke_workflow_ref` and `pool_name` definitions verbatim and bans `attribute.repository` in members; V8 compares the live condition and mapping with literals. |
| Critic 2: V5 hard-coded 412 | 412 or 403 accepted for the precondition case; the proof is an unchanged generation and sha256 and a single generation in `ls --all-versions`. |

**Round 7, critic only (2026-10-11; owner's choice after round 6).** Critic: 1 gap. Revision 7:

| Gap | Resolution |
|---|---|
| Critic 1: without `token_format` the exchange happens in the gcloud step, not in `auth`; the negative proof was not specific | Kept `auth` without `token_format` (no token output). V-post and the failure-mode row now name the gcloud step and the exact error class: STS refusal for the condition, IAM `getAccessToken` denial for a binding mismatch; any other failure does not count as the negative proof. |

## V-post (2026-10-18, after v2 merged into `main` as PR #4)

- **Positive:** [run 39156510316](https://github.com/fma00/synthea-readmission-risk/actions/runs/39156510316), `gh workflow run smoke.yml --ref main`: green; the listing shows five prefixes (the two schema-1 and two schema-2 v1 bundles, and the slice-7 champion `569fdab6…`).
- **Negative:** [run 39156545023](https://github.com/fma00/synthea-readmission-risk/actions/runs/39156545023), the same dispatch from the throwaway branch `vpost-negative-check` (a copy of `main`, deleted afterwards): refused by STS with `{"error":"unauthorized_client","error_description":"The given credential is rejected by the attribute condition."}`. This is the attribute-condition refusal (not IAM, not network), so the `refs/heads/main` clause is proven live.
- **Deviation from the expected location:** the refusal surfaced in the `auth` step, not in "List the bundle bucket". `google-github-actions/auth` v3.0.0 performs the STS exchange itself even without `token_format`; Revision 7's Critic-1 resolution assumed otherwise. Only the location changes; the proof is the same. The comments in `smoke.yml` and `score.yml` and the runbook in `docs/setup.md` are corrected.

