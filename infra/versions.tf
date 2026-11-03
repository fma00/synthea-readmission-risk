# Versions are pinned exactly: the providers here and in .terraform.lock.hcl (hashes for linux_amd64 and darwin_arm64),
# the CLI in ci.yml's terraform-validate job (SHA256-checked zip). tests/test_infra.py keeps the three in agreement.
terraform {
  required_version = ">= 1.16.5, < 1.17.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.8.1"
    }
    # v3 slice 4: zips the dashboard-kill function's source (dashboard.tf).
    archive = {
      source  = "hashicorp/archive"
      version = "2.8.1"
    }
  }

  # Backends cannot read variables, so the bucket name is literal. bootstrap.sh creates this bucket first (with a
  # temporary local backend) and then migrates the state into it.
  backend "gcs" {
    bucket = "synthea-readmission-risk-tfstate"
    prefix = "infra"
  }
}
