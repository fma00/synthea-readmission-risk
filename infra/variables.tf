# Public identifiers are committed as defaults: the project id and number, and GitHub's numeric repository and owner ids
# (`gh api repos/fma00/synthea-readmission-risk` returns them to anyone). The billing account, the pre-existing budget's
# id and the operator's email are not: they go in the gitignored terraform.tfvars (see terraform.tfvars.example).

variable "project_id" {
  description = "GCP project id."
  type        = string
  default     = "synthea-readmission-risk"
}

variable "project_number" {
  description = "GCP project number (used in budget filters and the WIF provider's resource name)."
  type        = string
  default     = "271458030145"
}

variable "region" {
  description = "The single region for everything in v2."
  type        = string
  default     = "us-central1"

  validation {
    condition     = var.region == "us-central1"
    error_message = "v2 runs everything in us-central1 (PRD, Region and cost)."
  }
}

variable "github_repository" {
  description = "owner/name of the GitHub repository; only used to build workflow_ref values, never in the WIF condition."
  type        = string
  default     = "fma00/synthea-readmission-risk"
}

variable "github_repository_id" {
  description = "Numeric GitHub repository id: unlike the name it cannot be renamed or re-registered by someone else."
  type        = string
  default     = "1380503419"

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "github_repository_id must be the numeric id, not the name."
  }
}

variable "github_repository_owner_id" {
  description = "Numeric GitHub id of the repository owner."
  type        = string
  default     = "270004823"

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_owner_id))
    error_message = "github_repository_owner_id must be the numeric id, not the login."
  }
}

variable "billing_account" {
  description = "Billing account id (XXXXXX-XXXXXX-XXXXXX). Kept out of the repository."
  type        = string
}

variable "existing_budget_id" {
  description = "Id of the CHF 20 budget created with gcloud on 2026-10-09, imported into google_billing_budget.monthly."
  type        = string
}

variable "operator_member" {
  description = "IAM member of the person who applies this config and uploads bundles, e.g. user:you@example.com."
  type        = string

  validation {
    condition     = startswith(var.operator_member, "user:")
    error_message = "operator_member must be a user: member."
  }
}

variable "generation_templates" {
  description = "Generation VM template classes -> max run duration in seconds (v2 slice 5: pilot; slice 6 adds main from the pilot's measurement)."
  type        = map(number)
  default     = { pilot = 7200 }

  validation {
    condition     = alltrue([for k, v in var.generation_templates : contains(["pilot", "main"], k) && v >= 600 && v <= 86400])
    error_message = "Classes are pilot or main; durations 600-86400 seconds."
  }
}

