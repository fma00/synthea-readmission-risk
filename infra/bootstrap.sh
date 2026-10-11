#!/usr/bin/env bash
# Creates the Terraform state bucket and moves the state into it (PRD FR8: "create with a local backend, then
# -migrate-state"). Run once from the laptop; safe to re-run: it decides what to do from what exists (design note
# notes/eg-new-feature/v2-slice3-terraform-wif-2026-10-11.md, infra/bootstrap.sh). It changes the live project
# (enables APIs, creates the bucket), and every apply waits for interactive approval.
#
# After it succeeds, the rest is `terraform -chdir=infra plan` / `apply` by hand (docs/setup.md > Infrastructure).
set -euo pipefail
cd "$(dirname "$0")"

PROJECT=synthea-readmission-risk
STATE_BUCKET=synthea-readmission-risk-tfstate
# The gcs backend's object for prefix "infra" (versions.tf), workspace "default".
STATE_OBJECT="gs://$STATE_BUCKET/infra/default.tfstate"
OVERRIDE=bootstrap_override.tf
# The operator's gcloud default project is deliberately another one; never change it, override it per command.
export CLOUDSDK_CORE_PROJECT="$PROJECT"

die() {
  echo "bootstrap.sh: ERROR: $*" >&2
  exit 1
}

trap 'rm -f "$OVERRIDE"' EXIT

# ---------------------------------------------------------------- preflight (changes nothing)

command -v terraform >/dev/null || die "terraform is not on PATH (docs/setup.md > Infrastructure (Terraform))"
command -v gcloud >/dev/null || die "gcloud is not on PATH"
case "$(terraform version | head -n 1)" in
  "Terraform v1.16."*) ;;
  *) die "Terraform 1.16.x is required (infra/versions.tf); found: $(terraform version | head -n 1)" ;;
esac
[ -f terraform.tfvars ] || die "infra/terraform.tfvars is missing: copy terraform.tfvars.example and fill it in"
terraform fmt -check terraform.tfvars >/dev/null || die "infra/terraform.tfvars is not fmt-clean; run: terraform -chdir=infra fmt"
# Two separate logins; either can be broken while the other works.
gcloud auth print-access-token >/dev/null 2>&1 || die "the gcloud CLI login does not work: run gcloud auth login"
gcloud auth application-default print-access-token >/dev/null 2>&1 ||
  die "Application Default Credentials (used by Terraform) do not work: run gcloud auth application-default login"
[ "$(gcloud projects describe "$PROJECT" --format='value(lifecycleState)' 2>/dev/null)" = ACTIVE ] ||
  die "project $PROJECT is not reachable and ACTIVE with the gcloud login"
# A stale override from an interrupted run is removed; any other override could silently redirect the backend (a
# "migration" from local to local would then pass every check), so it is refused.
rm -f "$OVERRIDE"
# Terraform treats override.tf, override.tf.json, *_override.tf and *_override.tf.json as override files.
if [ -e override.tf ] || [ -e override.tf.json ] || compgen -G '*_override.tf' >/dev/null ||
  compgen -G '*_override.tf.json' >/dev/null; then
  die "infra/ contains a Terraform override file (override.tf[.json] or *_override.tf[.json]); remove it (bootstrap.sh writes and removes its own $OVERRIDE)"
fi

# Enabled with gcloud before Terraform: provider 8.5.0 reads each newly enabled service back through the Cloud Resource
# Manager API, which is disabled on a new project, so enabling it inside the first apply would fail (infra/apis.tf).
echo "Enabling the APIs infra/apis.tf manages (idempotent)..."
gcloud services enable iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  cloudresourcemanager.googleapis.com storage.googleapis.com billingbudgets.googleapis.com --project="$PROJECT"

# ---------------------------------------------------------------- probe

# probe objects|buckets <gs://url> prints "present" or "absent". Only a 404 counts as absent: an auth, permission or
# network error stops the script, because treating "unknown" as "absent" could migrate over a real remote state.
probe() {
  local err
  if err=$(gcloud storage "$1" describe "$2" 2>&1 >/dev/null); then
    echo present
  elif grep -qiE '404|not found' <<<"$err"; then
    echo absent
  else
    echo "$err" >&2
    die "could not tell whether $2 exists (the error above is not a 404)"
  fi
}

# The manual fix for a bucket that exists without (local) state: import it under the same override bootstrap.sh uses, so
# the next run's preflight removes it again.
IMPORT_HINT="From infra/, run:
  printf 'terraform {\n  backend \"local\" {}\n}\n' > $OVERRIDE
  terraform init -input=false -reconfigure
  terraform import google_storage_bucket.tfstate $STATE_BUCKET
then re-run bootstrap.sh."

write_override() {
  printf '%s\n' '# Written by bootstrap.sh and removed on exit (gitignored).' 'terraform {' '  backend "local" {}' '}' >"$OVERRIDE"
}

create() {
  echo "Creating the state bucket with a temporary local backend..."
  write_override
  terraform init -input=false -reconfigure
  terraform apply -target=google_project_service.enabled -target=google_storage_bucket.tfstate
}

migrate() {
  # -force-copy answers "yes" to overwriting state in the new backend, so only ever run it into an empty backend.
  local object
  object=$(probe objects "$STATE_OBJECT")
  [ "$object" = absent ] || die "$STATE_OBJECT appeared during the run; not migrating over it"
  echo "Migrating the local state into $STATE_OBJECT..."
  write_override
  terraform init -input=false -reconfigure
  # Check BEFORE the irreversible copy: a local state without the bucket (an apply killed after GCS created it) would
  # otherwise be migrated, and the next run would take branch A and report success over an incomplete state.
  # grep reads all input (no -q): under pipefail an early exit could SIGPIPE terraform and fail a matching pipeline.
  terraform state list | grep -x 'google_storage_bucket.tfstate' >/dev/null ||
    die "the local state does not record google_storage_bucket.tfstate. $IMPORT_HINT"
  rm -f "$OVERRIDE"
  terraform init -input=false -migrate-state -force-copy
  # Delete the local copy only once the state object demonstrably exists in GCS and records the bucket: `state list`
  # alone cannot tell which backend it read.
  object=$(probe objects "$STATE_OBJECT")
  if [ "$object" = present ] &&
    gcloud storage cat "$STATE_OBJECT" | grep '"google_storage_bucket"' >/dev/null &&
    terraform state list | grep -x 'google_storage_bucket.tfstate' >/dev/null; then
    rm -f terraform.tfstate terraform.tfstate.backup
    echo "Bootstrapped: state is in $STATE_OBJECT. Next: terraform -chdir=infra plan (docs/setup.md)."
  else
    die "the state in $STATE_OBJECT could not be confirmed; the local terraform.tfstate is kept"
  fi
}

# ---------------------------------------------------------------- decide

object=$(probe objects "$STATE_OBJECT")
if [ "$object" = present ]; then
  # A: already bootstrapped.
  terraform init -input=false -reconfigure
  if [ -f terraform.tfstate ]; then
    echo "WARNING: infra/terraform.tfstate still exists from an earlier run; it is not deleted (inspect, then remove it)." >&2
  fi
  echo "Already bootstrapped: state is in $STATE_OBJECT."
  exit 0
fi

bucket=$(probe buckets "gs://$STATE_BUCKET")
if [ "$bucket" = present ] && [ -f terraform.tfstate ]; then
  # B: the bucket was created but the migration did not finish.
  migrate
elif [ "$bucket" = present ]; then
  # C: a bucket without state anywhere.
  die "gs://$STATE_BUCKET exists but no state does. $IMPORT_HINT"
else
  # D: fresh (an existing local state from a failed earlier run is reused: Terraform plans only what is missing).
  create
  migrate
fi
