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
  # Synthea output is regenerable: content-deterministic given seed, clinician seed, end date and JVM time zone (slice 5).
  force_destroy = true
  labels = {
    purpose = "raw-synthea"
  }

  # OQ5: pilot raw data is deleted after 90 days (regenerating costs about $1). Slice 6a (owner decision 2026-10-14): the
  # validation population v2/main_200k/ is kept 365 days. Only the run names pilot_20k and main_200k exist
  # (ops/generation_vm.py TEMPLATE_CLASS_FOR_RUN); an object outside both prefixes would not expire.
  lifecycle_rule {
    condition {
      age            = 90
      matches_prefix = ["v2/pilot_"]
    }
    action {
      type = "Delete"
    }
  }

  lifecycle_rule {
    condition {
      age            = 365
      matches_prefix = ["v2/main_"]
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

# v2 slice 5: content-addressed run artifacts (the Synthea jar, the JDK, the Spark --py-files zip, the two scripts) under
# sha256/<hex>/<file>, uploaded create-only by the operator's CLIs; read by the generation VM and the Dataproc runtime.
resource "google_storage_bucket" "artifacts" {
  name                        = "synthea-readmission-risk-artifacts"
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  # Everything here is rebuildable from the repo and the pinned upstream releases.
  force_destroy = true
  labels = {
    purpose = "artifacts"
  }

  depends_on = [google_project_service.enabled]
}

# No Dataproc staging bucket: runtime 3.0 rejects the staging_bucket field, and Google's runtime 3.0 page says "3.0+
# runtimes don't use Cloud Storage staging buckets." The named bucket created in slice 5 was removed on 2026-10-14
# (notes/eg-new-feature/infra-cleanup-editor-and-staging-2026-10-13.md); no auto-created staging bucket existed then.

