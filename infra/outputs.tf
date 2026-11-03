output "workload_identity_provider" {
  description = "Full resource name of the WIF provider; .github/workflows/smoke.yml hard-codes this value."
  value       = google_iam_workload_identity_pool_provider.github_actions.name
}

output "smoke_service_account_email" {
  value = google_service_account.gha_smoke.email
}

output "bundle_writer_service_account_email" {
  value = google_service_account.bundle_writer.email
}

output "bucket_names" {
  value = {
    tfstate = google_storage_bucket.tfstate.name
    raw     = google_storage_bucket.raw.name
    gold    = google_storage_bucket.gold.name
    bundles = google_storage_bucket.bundles.name
    # v2 slice 5
    artifacts = google_storage_bucket.artifacts.name
  }
}

output "scorer_service_account_email" {
  value = google_service_account.scorer.email
}

output "bigquery_datasets" {
  value = {
    serving      = google_bigquery_dataset.serving.dataset_id
    scratch      = google_bigquery_dataset.scratch.dataset_id
    scratch_gold = google_bigquery_dataset.scratch_gold.dataset_id
    dashboard    = google_bigquery_dataset.dashboard.dataset_id
  }
}

output "dashboard_runtime_service_account_email" {
  value = google_service_account.dashboard_runtime.email
}

output "generation_templates" {
  description = "Generation VM template class -> self link (the launcher resolves a run's template by name)."
  value       = { for k, t in google_compute_instance_template.synthea_generator : k => t.self_link }
}

output "synthea_generator_service_account_email" {
  value = google_service_account.synthea_generator.email
}

output "dataproc_runtime_service_account_email" {
  value = google_service_account.dataproc_runtime.email
}

output "spark_subnet" {
  value = google_compute_subnetwork.spark_us_central1.self_link
}


# v3 slice 4
output "dashboard_urls" {
  description = "Every run.app URL of the dashboard service (the deterministic one and the legacy one; F6)."
  value       = google_cloud_run_v2_service.dashboard.urls
}

output "dashboard_image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.dashboard.repository_id}"
}

output "dashboard_budget_name" {
  description = "billingAccounts/<account>/budgets/<id>: contains the billing account id, so it is never committed (F16)."
  value       = google_billing_budget.dashboard.name
  sensitive   = true # hidden in apply/plan output; `terraform output -raw dashboard_budget_name` still prints it
}

output "dashboard_budget_chf" {
  value = var.dashboard_budget_chf
}
