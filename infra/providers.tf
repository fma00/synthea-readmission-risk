provider "google" {
  project = var.project_id
  region  = var.region

  # Applied with the operator's user credentials (ADC). The Billing Budget API refuses user credentials without a quota
  # project, so every call is billed to this project explicitly.
  billing_project       = var.project_id
  user_project_override = true

  default_labels = {
    repo       = "synthea-readmission-risk"
    managed-by = "terraform"
  }
}
