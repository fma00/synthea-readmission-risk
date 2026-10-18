# BigQuery serving (v2 slice 4, PRD FR17/FR18/FR20; design notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md).
# serving holds only the real tables; scratch holds short-lived staging and data-test copies. The table schemas are the
# package's own JSON files, so Python and Terraform cannot disagree. No `access` blocks on the datasets: they are
# authoritative and would fight the additive *_iam_member grants in iam.tf.

resource "google_bigquery_dataset" "serving" {
  dataset_id                 = "serving"
  location                   = var.region
  description                = "Demo predictions and scoring telemetry from synthetic Synthea data; not for clinical use."
  delete_contents_on_destroy = false
  labels = {
    purpose = "serving"
  }

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "scratch" {
  dataset_id                  = "scratch"
  location                    = var.region
  description                 = "Short-lived staging and data-test tables (expire after 1 day)."
  default_table_expiration_ms = 86400000
  delete_contents_on_destroy  = true
  labels = {
    purpose = "scratch"
  }

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_table" "predictions" {
  dataset_id = google_bigquery_dataset.serving.dataset_id
  table_id   = "predictions"
  # The scorer's DEMO_NOTICE (src/readmission_risk/scoring/constants.py); tests/test_infra.py keeps the two equal.
  description = chomp(<<-EOT
    DEMO, not clinical guidance. Scores come from a weak model trained on synthetic Synthea data, and the ranking mostly reflects the admission reason (a reason-code-only lookup does as well). The rows are held-out discharges from the model's own test partition; that window was already used to select models, so it is not a virgin holdout. Not clinically validated.
    EOT
  )
  schema                   = file("${path.module}/../src/readmission_risk/warehouse/schemas/predictions.json")
  require_partition_filter = true
  clustering               = ["model_bundle_id"]
  deletion_protection      = true
  labels = {
    prediction_schema_version = "2"
  }

  time_partitioning {
    type  = "DAY"
    field = "as_of_date"
  }

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_table" "scoring_runs" {
  dataset_id               = google_bigquery_dataset.serving.dataset_id
  table_id                 = "scoring_runs"
  description              = "One row per scoring attempt (succeeded, data_tests_failed, refused or failed)."
  schema                   = file("${path.module}/../src/readmission_risk/warehouse/schemas/scoring_runs.json")
  require_partition_filter = true
  clustering               = ["status"]
  deletion_protection      = true

  time_partitioning {
    type  = "DAY"
    field = "started_at"
  }

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_table" "monitoring" {
  dataset_id               = google_bigquery_dataset.serving.dataset_id
  table_id                 = "monitoring"
  description              = "Monitoring ticks on a simulated clock (synthetic Synthea data, not for clinical use): one row per clock-failure or evaluate attempt."
  schema                   = file("${path.module}/../src/readmission_risk/warehouse/schemas/monitoring.json")
  require_partition_filter = true
  clustering               = ["sim_date"]
  deletion_protection      = true

  time_partitioning {
    type  = "DAY"
    field = "started_at"
  }

  depends_on = [google_project_service.enabled]
}

# The scorer's only access to serving (owner decision D6): read and change table DATA, granted per table. No
# tables.create/delete (cannot add or drop tables) and no tables.update (cannot set an expiration, an indirect drop).
# Residual, documented: updateData allows DML DELETE of rows; BigQuery time travel (7 days) recovers them.
resource "google_project_iam_custom_role" "scorer_table_writer" {
  role_id     = "scorerTableWriter"
  title       = "Scorer table writer (no create/delete)"
  description = "Read and change data of the serving tables it is granted on; nothing else."
  permissions = [
    "bigquery.tables.get",
    "bigquery.tables.getData",
    "bigquery.tables.updateData",
  ]

  depends_on = [google_project_service.enabled]
}
