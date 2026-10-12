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

# ---------------------------------------------------------------- v2 slice 5: generation VM and Dataproc runtime
# Neither workload uses the default compute service account (which holds project roles/editor; removing that grant is an
# owner decision, not made here). legacyBucketReader gives storage.buckets.get + objects.list, which object-level roles and
# object-name conditions cannot grant and which gcloud and the Hadoop GCS connector may call.

resource "google_service_account" "synthea_generator" {
  account_id   = "synthea-generator"
  display_name = "Synthea generation VM: reads artifacts, creates raw objects (never deletes)"

  depends_on = [google_project_service.enabled]
}

resource "google_service_account" "dataproc_runtime" {
  account_id   = "dataproc-runtime"
  display_name = "Dataproc Serverless Big Join: reads raw and artifacts, writes gold under v2/ only"

  depends_on = [google_project_service.enabled]
}

# Create-only on raw: no storage.objects.delete, so a finished run's objects cannot be replaced (and composite uploads are off).
resource "google_storage_bucket_iam_member" "generator_creates_raw" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.synthea_generator.email}"

  depends_on = [google_project_service.enabled]
}

# gcloud storage cp reads object metadata (slice 3's finding).
resource "google_storage_bucket_iam_member" "generator_reads_raw" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.synthea_generator.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "generator_lists_raw" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.synthea_generator.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "generator_reads_artifacts" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.synthea_generator.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "generator_lists_artifacts" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.synthea_generator.email}"

  depends_on = [google_project_service.enabled]
}

# The startup script's output in Cloud Logging.
resource "google_project_iam_member" "generator_writes_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.synthea_generator.email}"

  depends_on = [google_project_service.enabled]
}

# roles/dataproc.worker (the documented role for a Dataproc Serverless workload's service account) minus its 28
# storage.* permissions: granted at project level they reach every bucket (raw, gold incl. full_run_2b, bundles,
# tfstate) and make the gold v2/ condition below moot (5b live check L2, 2026-10-12). Storage access comes only from
# the bucket-level grants below. Taken from `gcloud iam roles describe roles/dataproc.worker` on 2026-10-12; if Google
# adds a permission the runtime needs, the batch fails with a permission error and this list is extended.
# dataprocrm.nodePools.update is NOT in roles/dataproc.worker (nor in any predefined role, checked 2026-10-12), but runtime
# 3.0's pre-flight permission validation requires it: the first live batch failed with "Service Account ... is missing
# required permissions: [dataprocrm.nodePools.update]" until it was added (5b, step 6; IAM accepted it in a custom role).
resource "google_project_iam_custom_role" "dataproc_runtime_worker" {
  role_id     = "dataprocRuntimeWorker"
  title       = "Dataproc runtime worker (no storage)"
  description = "roles/dataproc.worker without storage.*; storage is granted per bucket."
  permissions = [
    "cloudprofiler.profiles.create",
    "cloudprofiler.profiles.update",
    "datalineage.locations.processOpenLineageMessage",
    "dataproc.agents.create",
    "dataproc.agents.delete",
    "dataproc.agents.get",
    "dataproc.agents.list",
    "dataproc.agents.update",
    "dataproc.batches.computeTuningConfig",
    "dataproc.batches.sparkApplicationWrite",
    "dataproc.sessions.sparkApplicationWrite",
    "dataproc.tasks.lease",
    "dataproc.tasks.listInvalidatedLeases",
    "dataproc.tasks.reportStatus",
    "dataprocrm.nodePools.create",
    "dataprocrm.nodePools.delete",
    "dataprocrm.nodePools.deleteNodes",
    "dataprocrm.nodePools.get",
    "dataprocrm.nodePools.list",
    "dataprocrm.nodePools.resize",
    "dataprocrm.nodePools.update",
    "dataprocrm.nodes.get",
    "dataprocrm.nodes.heartbeat",
    "dataprocrm.nodes.list",
    "dataprocrm.nodes.mintOAuthToken",
    "dataprocrm.operations.get",
    "logging.logEntries.create",
    "logging.logEntries.route",
    "monitoring.metricDescriptors.create",
    "monitoring.metricDescriptors.get",
    "monitoring.metricDescriptors.list",
    "monitoring.monitoredResourceDescriptors.get",
    "monitoring.monitoredResourceDescriptors.list",
    "monitoring.timeSeries.create",
  ]

  depends_on = [google_project_service.enabled]
}

resource "google_project_iam_member" "dataproc_runtime_worker" {
  project = var.project_id
  role    = google_project_iam_custom_role.dataproc_runtime_worker.id
  member  = "serviceAccount:${google_service_account.dataproc_runtime.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "dataproc_reads_raw" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.dataproc_runtime.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "dataproc_lists_raw" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.dataproc_runtime.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "dataproc_reads_artifacts" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.dataproc_runtime.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "dataproc_lists_artifacts" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.dataproc_runtime.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "dataproc_uses_staging" {
  bucket = google_storage_bucket.dataproc_staging.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.dataproc_runtime.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "dataproc_lists_staging" {
  bucket = google_storage_bucket.dataproc_staging.name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.dataproc_runtime.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "dataproc_reads_gold" {
  bucket = google_storage_bucket.gold.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.dataproc_runtime.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "dataproc_lists_gold" {
  bucket = google_storage_bucket.gold.name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.dataproc_runtime.email}"

  depends_on = [google_project_service.enabled]
}

# Write and delete under v2/ only (IAM condition on the object name).
resource "google_storage_bucket_iam_member" "dataproc_writes_gold_v2" {
  bucket = google_storage_bucket.gold.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.dataproc_runtime.email}"

  condition {
    title       = "gold-v2-prefix-only"
    description = "Spark's committer creates and deletes objects; never outside v2/ (full_run_2b/ is the scorer's v1 gold)."
    expression  = "resource.name.startsWith(\"projects/_/buckets/${google_storage_bucket.gold.name}/objects/v2/\")"
  }

  depends_on = [google_project_service.enabled]
}
