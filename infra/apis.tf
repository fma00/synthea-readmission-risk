# bootstrap.sh enables the first six with gcloud first: provider 8.5.0 reads each newly enabled service back through the
# Cloud Resource Manager API, which is itself in this list and disabled on a new project, so enabling it in the same
# batch would fail the first apply. Terraform then adopts them as already enabled. Later additions (bigquery, v2 slice
# 4) are enabled by Terraform itself: Cloud Resource Manager is on by then.
locals {
  services = toset([
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "storage.googleapis.com",
    "billingbudgets.googleapis.com",
    "bigquery.googleapis.com",
    # v2 slice 5: compute and dataproc were already enabled outside Terraform (checked 2026-10-12), so these two are adopted;
    # policytroubleshooter is a real enable (free), used by the live IAM check L2.
    "compute.googleapis.com",
    "dataproc.googleapis.com",
    "policytroubleshooter.googleapis.com",
    # v3 slice 4: the dashboard service, its image repository and the kill switch (Pub/Sub, a Cloud Run function built by Cloud
    # Build, triggered through Eventarc). logging is on in every project already, so it is adopted.
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "pubsub.googleapis.com",
    "cloudfunctions.googleapis.com",
    "cloudbuild.googleapis.com",
    "eventarc.googleapis.com",
    "logging.googleapis.com",
  ])
}

resource "google_project_service" "enabled" {
  for_each = local.services
  service  = each.value

  # Leaving an API on costs nothing; turning it off on destroy can break things outside this config.
  disable_on_destroy = false
}
