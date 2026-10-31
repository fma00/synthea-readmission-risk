# Service accounts: gha-smoke and bundle-writer (v2 slice 3), scorer (v2 slice 4), monitor and ledger-reader (v2 slice 7),
# gold-publisher (v3 slice 1).
# Later slices add one per job (Big Join submitter, Dataproc runtime, trainer), each bound to its own workflow file like gha-smoke below. Only additive *_iam_member resources:
# *_iam_policy or *_iam_binding would replace bindings this config does not own. One deliberate exception:
# google_project_iam_member_remove.default_compute_no_editor removes Google's project roles/editor grant from the default
# compute service account (applied 2026-10-14).

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
  pool_name             = google_iam_workload_identity_pool.github.name
  smoke_workflow_ref    = "${var.github_repository}/.github/workflows/smoke.yml@refs/heads/main"
  smoke_principal_set   = "principalSet://iam.googleapis.com/${local.pool_name}/attribute.workflow_ref/${local.smoke_workflow_ref}"
  score_workflow_ref    = "${var.github_repository}/.github/workflows/score.yml@refs/heads/main"
  score_principal_set   = "principalSet://iam.googleapis.com/${local.pool_name}/attribute.workflow_ref/${local.score_workflow_ref}"
  monitor_workflow_ref  = "${var.github_repository}/.github/workflows/monitor.yml@refs/heads/main"
  monitor_principal_set = "principalSet://iam.googleapis.com/${local.pool_name}/attribute.workflow_ref/${local.monitor_workflow_ref}"
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
# Neither workload uses the default compute service account. Its project roles/editor (granted by Google when the Compute
# Engine API is enabled) is removed below (owner decision 2026-10-13, notes/eg-new-feature/infra-cleanup-editor-and-staging-2026-10-13.md).
# legacyBucketReader gives storage.buckets.get + objects.list, which object-level roles and object-name conditions cannot
# grant and which gcloud and the Hadoop GCS connector may call.

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

# ---------------------------------------------------------------- v2 slice 7: the monitoring tick (monitor.yml)
# Three SAs are bound to monitor.yml's workflow_ref: scorer (its score job), monitor (clock + evaluate), ledger-reader
# (job A). GitHub's OIDC token has no per-job claim, so the binding cannot tell the jobs apart: the per-job SAs scope each
# job's credentials as written, but code in one job could request another job's SA (disclosed in the design note, E6).
resource "google_service_account_iam_member" "scorer_wif_monitor" {
  service_account_id = google_service_account.scorer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.monitor_principal_set

  depends_on = [google_project_service.enabled]
}

resource "google_service_account" "monitor" {
  account_id   = "monitor"
  display_name = "Monitoring tick: reads predictions, scoring_runs and gold labels, writes monitoring"

  depends_on = [google_project_service.enabled]
}

# Read and change DATA of serving.monitoring only (the scorer's no-create/no-delete custom role).
resource "google_bigquery_table_iam_member" "monitor_writes_monitoring" {
  dataset_id = google_bigquery_table.monitoring.dataset_id
  table_id   = google_bigquery_table.monitoring.table_id
  role       = google_project_iam_custom_role.scorer_table_writer.id
  member     = "serviceAccount:${google_service_account.monitor.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_table_iam_member" "monitor_reads_predictions" {
  dataset_id = google_bigquery_table.predictions.dataset_id
  table_id   = google_bigquery_table.predictions.table_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.monitor.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_table_iam_member" "monitor_reads_scoring_runs" {
  dataset_id = google_bigquery_table.scoring_runs.dataset_id
  table_id   = google_bigquery_table.scoring_runs.table_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.monitor.email}"

  depends_on = [google_project_service.enabled]
}

# make m5-monitoring's seeded copies only (scratch tables expire after 1 day).
resource "google_bigquery_dataset_iam_member" "monitor_edits_scratch" {
  dataset_id = google_bigquery_dataset.scratch.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.monitor.email}"

  depends_on = [google_project_service.enabled]
}

# Labels come from the gold table (objectViewer: objects.get + list, as the scorer).
resource "google_storage_bucket_iam_member" "monitor_reads_gold" {
  bucket = google_storage_bucket.gold.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.monitor.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_project_iam_member" "monitor_runs_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.monitor.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_service_account_iam_member" "monitor_wif" {
  service_account_id = google_service_account.monitor.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.monitor_principal_set

  depends_on = [google_project_service.enabled]
}

# The laptop catch-up and make m5-monitoring impersonate the monitor.
resource "google_service_account_iam_member" "operator_impersonates_monitor" {
  service_account_id = google_service_account.monitor.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = var.operator_member

  depends_on = [google_project_service.enabled]
}

resource "google_service_account" "ledger_reader" {
  account_id   = "ledger-reader"
  display_name = "Run ledger renderer (job A): reads scoring_runs and monitoring"

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_table_iam_member" "ledger_reads_scoring_runs" {
  dataset_id = google_bigquery_table.scoring_runs.dataset_id
  table_id   = google_bigquery_table.scoring_runs.table_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.ledger_reader.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_table_iam_member" "ledger_reads_monitoring" {
  dataset_id = google_bigquery_table.monitoring.dataset_id
  table_id   = google_bigquery_table.monitoring.table_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.ledger_reader.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_project_iam_member" "ledger_runs_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.ledger_reader.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_service_account_iam_member" "ledger_wif" {
  service_account_id = google_service_account.ledger_reader.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.monitor_principal_set

  depends_on = [google_project_service.enabled]
}

# make ledger and make m3-check impersonate the ledger reader.
resource "google_service_account_iam_member" "operator_impersonates_ledger_reader" {
  service_account_id = google_service_account.ledger_reader.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = var.operator_member

  depends_on = [google_project_service.enabled]
}

# ---------------------------------------------------------------- v3 slice 1: the gold publish (write-audit-publish)
# Laptop only (no WIF binding): loads gold Parquet into scratch_gold, audits it (GOLD data tests read serving.predictions),
# replaces serving.gold in ONE DML transaction and verifies it. Nothing on the serving dataset: no tables.create/delete there,
# so a load job into serving is impossible (slice 4's live finding). Disclosed residuals: updateData allows row deletes (time
# travel recovers them for 7 days); the reused custom role is titled "Scorer table writer". Design:
# notes/eg-new-feature/v3-slice1-gold-bigquery-2026-10-31.md (Interfaces section 2).
resource "google_service_account" "gold_publisher" {
  account_id   = "gold-publisher"
  display_name = "Gold publisher (v3 slice 1)"
  description  = "Loads gold Parquet into scratch_gold, audits it and publishes it into serving.gold; impersonated by the operator."

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "gold_publisher_reads_gold" {
  bucket = google_storage_bucket.gold.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.gold_publisher.email}"

  depends_on = [google_project_service.enabled]
}

# The champion manifest (GOLD-07 checks that the champion's predictions still join).
resource "google_storage_bucket_iam_member" "gold_publisher_reads_bundles" {
  bucket = google_storage_bucket.bundles.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.gold_publisher.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset_iam_member" "gold_publisher_edits_scratch_gold" {
  dataset_id = google_bigquery_dataset.scratch_gold.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.gold_publisher.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_table_iam_member" "gold_publisher_writes_gold" {
  dataset_id = google_bigquery_table.gold.dataset_id
  table_id   = google_bigquery_table.gold.table_id
  role       = google_project_iam_custom_role.scorer_table_writer.id
  member     = "serviceAccount:${google_service_account.gold_publisher.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_table_iam_member" "gold_publisher_reads_predictions" {
  dataset_id = google_bigquery_table.predictions.dataset_id
  table_id   = google_bigquery_table.predictions.table_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.gold_publisher.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_project_iam_member" "gold_publisher_runs_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.gold_publisher.email}"

  depends_on = [google_project_service.enabled]
}

# make gold-publish, gold-seeded-check and gold-access-check impersonate the gold publisher.
resource "google_service_account_iam_member" "operator_impersonates_gold_publisher" {
  service_account_id = google_service_account.gold_publisher.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = var.operator_member

  depends_on = [google_project_service.enabled]
}

# ---------------------------------------------------------------- v3 slice 2: the dashboard runtime
# Reads only the authorized views in the dashboard dataset (PRD FR9): nothing on serving, scratch or scratch_gold, no
# readSessionUser (the dashboard never builds a BigQuery Storage client). The Cloud Run service identity from slice 4; for now
# impersonated by the operator only (make dashboard-access-check / dashboard-query-check). The dashboard dataset grants no view
# authorizations itself, so a dataset_iam_member on it cannot drop one. Design:
# notes/eg-new-feature/v3-slice2-authorized-views-2026-10-31.md (Interfaces section 2).
resource "google_service_account" "dashboard_runtime" {
  account_id   = "dashboard-runtime"
  display_name = "Dashboard runtime (v3 slice 2)"
  description  = "Reads the authorized views in the dashboard dataset; the Cloud Run service identity from slice 4; impersonated by the operator for live checks."

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset_iam_member" "dashboard_runtime_reads_dashboard" {
  dataset_id = google_bigquery_dataset.dashboard.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.dashboard_runtime.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_project_iam_member" "dashboard_runtime_runs_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.dashboard_runtime.email}"

  depends_on = [google_project_service.enabled]
}

resource "google_service_account_iam_member" "operator_impersonates_dashboard_runtime" {
  service_account_id = google_service_account.dashboard_runtime.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = var.operator_member

  depends_on = [google_project_service.enabled]
}
