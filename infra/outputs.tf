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
    artifacts        = google_storage_bucket.artifacts.name
    dataproc_staging = google_storage_bucket.dataproc_staging.name
  }
}

output "scorer_service_account_email" {
  value = google_service_account.scorer.email
}

output "bigquery_datasets" {
  value = {
    serving = google_bigquery_dataset.serving.dataset_id
    scratch = google_bigquery_dataset.scratch.dataset_id
  }
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

