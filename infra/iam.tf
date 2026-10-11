# Service accounts: gha-smoke and bundle-writer (v2 slice 3), scorer (v2 slice 4). Later slices add one per job (monitor,
# Big Join submitter, Dataproc runtime, trainer), each bound to its own workflow file like gha-smoke below. Only additive *_iam_member resources:
# *_iam_policy or *_iam_binding would replace bindings this config does not own.

resource "google_service_account" "gha_smoke" {
  account_id   = "gha-smoke"
  display_name = "GitHub Actions smoke job (lists the bundle bucket)"

  depends_on = [google_project_service.enabled]
}

resource "google_service_account" "bundle_writer" {
  account_id   = "bundle-writer"
  display_name = "Model bundle writer (create-only)"

  depends_on = [google_project_service.enabled]
}

resource "google_service_account" "scorer" {
  account_id   = "scorer"
  display_name = "Batch scorer: reads bundles and gold, writes predictions and scoring_runs"

  depends_on = [google_project_service.enabled]
}

locals {
  # The exported `name` (projects/<number>/locations/global/workloadIdentityPools/github), not a hand-built string.
  pool_name           = google_iam_workload_identity_pool.github.name
  smoke_workflow_ref  = "${var.github_repository}/.github/workflows/smoke.yml@refs/heads/main"
  smoke_principal_set = "principalSet://iam.googleapis.com/${local.pool_name}/attribute.workflow_ref/${local.smoke_workflow_ref}"
  score_workflow_ref  = "${var.github_repository}/.github/workflows/score.yml@refs/heads/main"
  score_principal_set = "principalSet://iam.googleapis.com/${local.pool_name}/attribute.workflow_ref/${local.score_workflow_ref}"
}

# List only: storage.buckets.get + storage.objects.list, no objects.get, so the smoke job cannot download a pickle.
resource "google_storage_bucket_iam_member" "smoke_lists_bundles" {
  bucket = google_storage_bucket.bundles.name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.gha_smoke.email}"

  depends_on = [google_project_service.enabled]
}

# Create only (PRD FR7): replacing an existing object needs storage.objects.delete, which this role lacks.
resource "google_storage_bucket_iam_member" "writer_creates_bundles" {
  bucket = google_storage_bucket.bundles.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.bundle_writer.email}"

  depends_on = [google_project_service.enabled]
}

# Keyed to the workflow file, not the repository: any other workflow on main cannot impersonate gha-smoke. The WIF
# provider's condition (wif.tf) additionally pins the numeric repository/owner ids, refs/heads/main and the event.
resource "google_service_account_iam_member" "smoke_wif" {
  service_account_id = google_service_account.gha_smoke.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.smoke_principal_set

  depends_on = [google_project_service.enabled]
}

# roles/owner cannot mint tokens for a service account. Uploads run as bundle-writer so the create-only rule applies to
# them; the owner's own identity could overwrite.
resource "google_service_account_iam_member" "operator_impersonates_writer" {
  service_account_id = google_service_account.bundle_writer.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = var.operator_member

  depends_on = [google_project_service.enabled]
}

# ---------------------------------------------------------------- scorer (v2 slice 4)
# Reads bundles and gold (objectViewer: objects.get + list; unlike gha-smoke it must download the pickle), writes
# serving.predictions/scoring_runs through the no-delete custom role (bigquery.tf), owns the scratch dataset, runs jobs.

resource "google_storage_bucket_iam_member" "scorer_reads_bundles" {
  bucket = google_storage_bucket.bundles.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.scorer.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "scorer_reads_gold" {
  bucket = google_storage_bucket.gold.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.scorer.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_table_iam_member" "scorer_writes_predictions" {
  dataset_id = google_bigquery_table.predictions.dataset_id
  table_id   = google_bigquery_table.predictions.table_id
  role       = google_project_iam_custom_role.scorer_table_writer.id
  member     = "serviceAccount:${google_service_account.scorer.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_table_iam_member" "scorer_writes_scoring_runs" {
  dataset_id = google_bigquery_table.scoring_runs.dataset_id
  table_id   = google_bigquery_table.scoring_runs.table_id
  role       = google_project_iam_custom_role.scorer_table_writer.id
  member     = "serviceAccount:${google_service_account.scorer.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset_iam_member" "scorer_edits_scratch" {
  dataset_id = google_bigquery_dataset.scratch.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.scorer.email}"

  depends_on = [google_project_service.enabled]
}

# Jobs exist only at project level; this role creates jobs and grants no data access by itself.
resource "google_project_iam_member" "scorer_runs_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.scorer.email}"

  depends_on = [google_project_service.enabled]
}

# Keyed to score.yml on main, like gha-smoke to smoke.yml; the provider's condition (wif.tf) also applies.
resource "google_service_account_iam_member" "scorer_wif" {
  service_account_id = google_service_account.scorer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.score_principal_set

  depends_on = [google_project_service.enabled]
}

# The laptop runs (D1) impersonate the scorer, so they exercise exactly its access.
resource "google_service_account_iam_member" "operator_impersonates_scorer" {
  service_account_id = google_service_account.scorer.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = var.operator_member

  depends_on = [google_project_service.enabled]
}
