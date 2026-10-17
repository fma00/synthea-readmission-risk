# Workload Identity Federation for GitHub Actions (PRD FR9): plain resources, not the gh-oidc module, whose defaults
# have no condition and map by name. GitHub's issuer is shared by every repository on github.com, so the condition is
# what keeps other repositories out; it uses numeric ids, which cannot be renamed or re-registered.

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"

  depends_on = [google_project_service.enabled]
}

resource "google_iam_workload_identity_pool_provider" "github_actions" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions"
  display_name                       = "GitHub Actions OIDC"

  # Google requires a mapping for every claim the condition uses. This repository issues immutable subject claims
  # (repo:<owner>@<owner_id>/<repo>@<repo_id>:ref:...), so google.subject is 76 characters, under the 127 limit.
  attribute_mapping = {
    "google.subject"                = "assertion.sub"
    "attribute.repository_id"       = "assertion.repository_id"
    "attribute.repository_owner_id" = "assertion.repository_owner_id"
    "attribute.ref"                 = "assertion.ref"
    "attribute.event_name"          = "assertion.event_name"
    "attribute.workflow_ref"        = "assertion.workflow_ref"
  }

  attribute_condition = join(" && ", [
    "assertion.repository_id == '${var.github_repository_id}'",
    "assertion.repository_owner_id == '${var.github_repository_owner_id}'",
    "assertion.ref == 'refs/heads/main'",
    "assertion.event_name in ['workflow_dispatch', 'schedule']",
  ])

  # No allowed_audiences: the default is this provider's resource name, which google-github-actions/auth requests.
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  depends_on = [google_project_service.enabled]
}
