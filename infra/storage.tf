# Four buckets, all in us-central1, uniform access, public access prevention enforced. Bucket Lock is deliberately not
# used (PRD FR7: irreversible, blocks teardown). Teardown order: docs/setup.md > Teardown checklist.

resource "google_storage_bucket" "tfstate" {
  name                        = "synthea-readmission-risk-tfstate"
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels = {
    purpose = "terraform-state"
  }

  # Recommended by the gcs backend: every state write keeps the previous version.
  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 10
      with_state         = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket" "raw" {
  name                        = "synthea-readmission-risk-raw"
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  # Synthea output is regenerable and deterministic per seed.
  force_destroy = true
  labels = {
    purpose = "raw-synthea"
  }

  # OQ5: raw data is deleted after 90 days (regenerating costs about $1).
  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }

  # No billed soft-deleted copies of deleted raw data.
  soft_delete_policy {
    retention_duration_seconds = 0
  }

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket" "gold" {
  name                        = "synthea-readmission-risk-gold"
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  # Rebuilt from raw by the Big Join.
  force_destroy = true
  labels = {
    purpose = "gold"
  }

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket" "bundles" {
  name                        = "synthea-readmission-risk-bundles"
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels = {
    purpose = "model-bundles"
  }

  # IAM keeps the writer create-only (iam.tf), but a project Owner or Editor can still overwrite; versioning keeps the
  # overwritten generation.
  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.enabled]
}
