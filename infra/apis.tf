# bootstrap.sh enables these with gcloud first: provider 8.5.0 reads each newly enabled service back through the Cloud
# Resource Manager API, which is itself in this list and disabled on a new project, so enabling it in the same batch
# would fail the first apply. Terraform then adopts them as already enabled.
locals {
  services = toset([
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "storage.googleapis.com",
    "billingbudgets.googleapis.com",
  ])
}

resource "google_project_service" "enabled" {
  for_each = local.services
  service  = each.value

  # Leaving an API on costs nothing; turning it off on destroy can break things outside this config.
  disable_on_destroy = false
}
