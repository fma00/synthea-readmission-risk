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
  }
}
