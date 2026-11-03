# v3 slice 4 (PRD FR22, FR24; notes/eg-new-feature/v3-slice4-cloud-run-deploy-2026-11-03.md, Interfaces section 6): the dashboard's
# Cloud Run service and image repository, and its cost guard (a dashboard-only budget -> Pub/Sub -> the dashboard-kill function,
# which disables only this service). Terraform owns every service setting; deploy.yml swaps only the image (V15). The service
# accounts and their bindings are in iam.tf.

locals {
  dashboard_label       = { component = "dashboard" }
  dashboard_placeholder = "us-docker.pkg.dev/cloudrun/container/hello" # replaced by deploy.yml; ignored after create
}

# Immutable tags (deploy.yml tags <sha>-<run id>-<attempt>); the newest 5 versions survive the cleanup, not 2 (F12): a push precedes
# a deploy that may fail, so two failed deploys must not let the daily cleanup delete the served image.
resource "google_artifact_registry_repository" "dashboard" {
  location      = var.region
  repository_id = "dashboard"
  description   = "The dashboard image, pushed by deploy.yml (v3 slice 4)."
  format        = "DOCKER"
  labels        = local.dashboard_label

  docker_config {
    immutable_tags = true
  }

  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "delete-all"
    action = "DELETE"
    condition {
      tag_state = "ANY"
    }
  }
  cleanup_policies {
    id     = "keep-5-newest"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }

  depends_on = [google_project_service.enabled]
}

# The kill function's build output (build_config.docker_repository), so the builder's writer grant is repository-scoped. Mutable
# tags: the buildpack re-pushes fixed tags on every rebuild (F12).
resource "google_artifact_registry_repository" "functions" {
  location      = var.region
  repository_id = "functions"
  description   = "Build output of the dashboard-kill function (v3 slice 4)."
  format        = "DOCKER"
  labels        = local.dashboard_label

  docker_config {
    immutable_tags = false
  }

  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "delete-all"
    action = "DELETE"
    condition {
      tag_state = "ANY"
    }
  }
  cleanup_policies {
    id     = "keep-5-newest"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }

  depends_on = [google_project_service.enabled]
}

# Public (invoker IAM check off; no --allow-unauthenticated anywhere), scaled to zero, at most one instance (V12), gen1 with a
# startup CPU boost (cold start, M7). No startup/liveness probe block: the default TCP startup probe (PRD risk "HTTP probe").
resource "google_cloud_run_v2_service" "dashboard" {
  name                 = "dashboard"
  location             = var.region
  description          = "The readmission-risk dashboard (Streamlit), reading only the dashboard dataset's views (v3)."
  deletion_protection  = true
  ingress              = "INGRESS_TRAFFIC_ALL"
  invoker_iam_disabled = true
  labels               = local.dashboard_label

  scaling {
    scaling_mode       = "AUTOMATIC"
    min_instance_count = 0
    max_instance_count = 1
  }

  template {
    labels                           = local.dashboard_label # F4: Cloud Billing reads only revision-template labels
    service_account                  = google_service_account.dashboard_runtime.email
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN1"
    timeout                          = "900s" # PRD 15-30 min; Streamlit reconnects after the cut (F3)
    max_instance_request_concurrency = 80
    session_affinity                 = true

    containers {
      image = local.dashboard_placeholder

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle          = true # request-based billing; must be explicit once resources is set
        startup_cpu_boost = true
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  lifecycle {
    # deploy.yml swaps only the image (V15); gcloud writes client/client_version and the F15 template keys. NEVER
    # template[0].revision (provider issue #14569: later applies reuse the revision name and fail with 409), never the whole
    # template[0].labels (F4's billing label).
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
      template[0].labels["client.knative.dev/nonce"],
      template[0].annotations["run.googleapis.com/client-name"],
      template[0].annotations["run.googleapis.com/client-version"],
    ]
  }

  depends_on = [google_project_service.enabled]
}

# ---------------------------------------------------------------- the cost guard (FR24, V18)

resource "google_pubsub_topic" "dashboard_budget" {
  name   = "dashboard-budget"
  labels = local.dashboard_label

  depends_on = [google_project_service.enabled]
}

# Billing publishes budget notifications as this Google-managed account; the console grants it when a topic is connected there,
# Terraform must grant it here (budgets-programmatic-notifications). P10 if the apply rejects the member.
resource "google_pubsub_topic_iam_member" "billing_publishes_dashboard_budget" {
  topic  = google_pubsub_topic.dashboard_budget.id
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:billing-budget-alert@system.gserviceaccount.com"

  depends_on = [google_project_service.enabled]
}

# A notification and a trigger, not a cap: dashboard-kill acts once the current budget month's cost reaches the amount. The default
# email recipients are kept, so the owner also hears of it at 50 % and 100 %.
resource "google_billing_budget" "dashboard" {
  billing_account = var.billing_account
  display_name    = "dashboard kill switch"

  budget_filter {
    projects               = ["projects/${var.project_number}"]
    labels                 = local.dashboard_label # P5 if the API refuses a label without usage yet
    calendar_period        = "MONTH"
    credit_types_treatment = "INCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      currency_code = "CHF"
      units         = tostring(var.dashboard_budget_chf)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
    spend_basis       = "CURRENT_SPEND"
  }
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }

  all_updates_rule {
    pubsub_topic   = google_pubsub_topic.dashboard_budget.id
    schema_version = "1.0"
  }

  depends_on = [google_project_service.enabled]
}

data "archive_file" "dashboard_kill" {
  type             = "zip"
  source_dir       = "${path.module}/functions/dashboard_kill"
  output_path      = "${path.module}/.build/dashboard_kill.zip"
  excludes         = ["__pycache__", ".pytest_cache", ".DS_Store"] # and tests: the folder holds exactly the three files
  output_file_mode = "0644"                                        # the same zip, and hash, on every machine
}

# The artifacts bucket's content-addressed convention, sha256/<hex>/<file>; a changed source is a new object and a rebuild.
resource "google_storage_bucket_object" "dashboard_kill_source" {
  bucket = google_storage_bucket.artifacts.name
  name   = "sha256/${data.archive_file.dashboard_kill.output_sha256}/dashboard_kill.zip"
  source = data.archive_file.dashboard_kill.output_path

  depends_on = [google_project_service.enabled]
}

resource "google_cloudfunctions2_function" "dashboard_kill" {
  name        = "dashboard-kill"
  location    = var.region
  description = "Disables the dashboard service once the dashboard budget's current-month cost reaches its amount (v3 slice 4)."
  labels      = local.dashboard_label

  build_config {
    runtime           = "python314"
    entry_point       = "on_budget_message"
    service_account   = google_service_account.function_builder.id
    docker_repository = google_artifact_registry_repository.functions.id

    source {
      storage_source {
        bucket = google_storage_bucket.artifacts.name
        object = google_storage_bucket_object.dashboard_kill_source.name
      }
    }
  }

  service_config {
    service_account_email          = google_service_account.dashboard_kill.email
    ingress_settings               = "ALLOW_INTERNAL_ONLY"
    max_instance_count             = 1
    available_memory               = "256Mi"
    timeout_seconds                = 60
    all_traffic_on_latest_revision = true
    environment_variables = {
      TARGET_SERVICE  = google_cloud_run_v2_service.dashboard.id # projects/<p>/locations/<r>/services/dashboard
      EXPECTED_BUDGET = google_billing_budget.dashboard.name     # billingAccounts/<a>/budgets/<id>
    }
  }

  event_trigger {
    trigger_region        = var.region
    event_type            = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic          = google_pubsub_topic.dashboard_budget.id
    retry_policy          = "RETRY_POLICY_DO_NOT_RETRY"
    service_account_email = google_service_account.dashboard_kill_trigger.email # F20
  }

  depends_on = [google_project_service.enabled]
}
