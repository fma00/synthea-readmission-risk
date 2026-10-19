# Setup and running the pipeline

> Terminology: "slice N" names the build stages in the [README Status table](../README.md#status) (1 Synthea generation, 2 PySpark feature join, 2b unplanned label, 3 models, 4 Top-N CLI); "v2 population" is the planned scale-up cohort (see the README Roadmap).

## Setup

Developed and CI-tested on Python 3.14 (pinned via [.python-version](../.python-version)). The pipeline subpackage is also CI-tested on Python 3.12 with PySpark 4.0.2, the stack of the Dataproc Serverless runtime the scale-up phase targets (see [Testing against the Dataproc runtime](#testing-against-the-dataproc-runtime)). The rest of the package is not tested on 3.12, although `requires-python` still admits it.

```sh
# Create and activate the virtualenv
python3.14 -m venv .venv
source .venv/bin/activate

# Install: three separate invocations, since a local editable install can't carry
# pip's --require-hashes guarantee that the lockfiles use.
pip install -e .
pip install --require-hashes -r requirements.txt -r requirements-linux-extras.txt
pip install --require-hashes -r requirements-dev.txt
```

macOS only: XGBoost needs the OpenMP runtime — `brew install libomp`. A JDK 17+ must be on `PATH` for PySpark's local mode (the feature join and its tests).

`requirements-linux-extras.txt` holds the few dependencies `pip-compile` cannot see when run on macOS (they are Linux-only); each is gated by an environment marker, so pip skips them on macOS and the same command works on both platforms. `pandas` and `numpy` are deliberately pinned below their newest releases (PySpark's `pyspark.pandas` cannot import under pandas 3) — see the comment in [requirements.in](../requirements.in).

## Verify the code works

```sh
ruff check .
zizmor --offline .
pytest
```

This is the fast path to confirm the codebase is sound — no generated data, and no external downloads beyond what the dependency install already fetched (it does need a JDK on `PATH` for the PySpark tests). It's what CI runs on every push to `main` and on every pull request. `zizmor` audits the GitHub Actions workflows. `zizmor --offline .` (the command above) never touches the network and skips the online audits CI runs, which query the GitHub API (known-vulnerable actions, and a check that each pin's `# vX.Y.Z` comment matches its SHA); `GH_TOKEN=$(gh auth token) zizmor .` reproduces CI's audit exactly. A plain `zizmor .` goes online whenever `GH_TOKEN`, `GITHUB_TOKEN` or `ZIZMOR_GITHUB_TOKEN` is set.

## Makefile and pre-commit

The [Makefile](../Makefile) wraps the commands on this page. Its recipes call the tools in `.venv` by path, so no activated virtualenv is needed; `make help` lists the targets. It works with the GNU Make 3.81 that macOS ships.

| Target | What it does |
|---|---|
| `make setup` | Creates `.venv` with `python3.14` (or reuses it, refusing one of another Python) and runs the three installs above |
| `make hooks` | Installs the git pre-commit hook (see below) |
| `make lint` | `ruff check .` and `zizmor --offline .`, the full-repository equivalent of CI's lint |
| `make test` | `pytest` |
| `make pre-commit` | Runs the hooks over all tracked files |
| `make train TRAIN_EXPERIMENT=<name>` | Trains both models on `data/gold/full_run_2b`; the name is required and must differ from the export experiment (`EXPERIMENT`, default `readmission-risk-v1`), because training into an experiment that already holds runs makes the exporter's `--model` ambiguous (`make export` reads `EXPERIMENT`). It does not check other existing experiments. Extra flags go in `TRAIN_ARGS` |
| `make export` | Exports the `EXPORT_MODEL` run (default `logistic_regression`) of `EXPERIMENT` (default `readmission-risk-v1`) as a model bundle under the gitignored `bundles/` and prints the `deploy/champion.json` text that would promote it; extra flags in `EXPORT_ARGS` ([scoring.md](scoring.md#model-bundles)) |
| `make score` | Scores run date `20250630` with the champion bundle (`deploy/champion.json`) on `SCORE_GOLD_DIR` (default `data/gold/main_200k`, downloaded by `make gold-200k`); override `RUN_DATE`, `SCORE_GOLD_DIR`, extra flags in `SCORE_ARGS` (e.g. `--bundle bundles/<id>`) |
| `make setup-runtime`, `make test-runtime` | The Dataproc runtime environment, next section |
| `make lock-main`, `make lock-dev` | Recompile `requirements.txt` / `requirements-dev.txt` under Python 3.14, on macOS (keeps existing pins) |
| `make lock-runtime` | Recompile `requirements-dataproc-3.0.txt` under Python 3.12 (re-resolves to the newest allowed patches) |
| `make tf-validate` | Checks `infra/` like CI's `terraform-validate` job (`fmt -check`, `init` without a backend, `validate`; no credentials); needs Terraform 1.16.x ([Infrastructure](#infrastructure-terraform)) |

The pre-commit hook (`make hooks`, configured in [.pre-commit-config.yaml](../.pre-commit-config.yaml)) runs ruff on the staged Python files and notebooks, and zizmor offline whenever a file under `.github/` or `.pre-commit-config.yaml` itself is staged (zizmor audits both). Both tools come from the main checkout's `.venv`, so the versions are the hash-pinned ones in `requirements-dev.txt`. A third hook runs `terraform fmt -check` on staged `infra/*.tf` files with the `terraform` on PATH, and skips with a notice when there is none (CI's `terraform-validate` job is the gate). Things to know:

- `make setup` does not install the hook; `make hooks` does. `pre-commit install` fails outside a git checkout and refuses to run when `core.hooksPath` is set.
- The hook is installed with `--allow-missing-config`: it is shared by every branch, and a branch without `.pre-commit-config.yaml` commits with a skip notice instead of being refused.
- It checks staged files only. A commit that changes only `pyproject.toml` (ruff's own configuration) re-lints nothing; `make lint` checks everything. CI runs both tools again (zizmor online), so `git commit --no-verify` skips nothing CI does not catch.
- In a linked git worktree the hook uses the main checkout's `.venv`, but the Makefile does not: its `VENV` default is the relative `.venv`. Run `make lint VENV="$(git rev-parse --git-common-dir)/../.venv"` there, or create a `.venv` in the worktree with `make setup`.

## Testing against the Dataproc runtime

The feature join runs on Dataproc Serverless runtime 3.0 in the scale-up phase: Python 3.12, Spark 4.0.x (4.0.2 ran on 2026-10-12; Google's page still lists 4.0.1) and Java 21, with the runtime's own pandas 2.3, pyarrow 19.0 and numpy 2.1. [requirements-dataproc-3.0.txt](../requirements-dataproc-3.0.txt) is a hash-locked mirror of those libraries ([requirements-dataproc-3.0.in](../requirements-dataproc-3.0.in) names its sources and dates). It is used only for testing; it is never installed on Dataproc or combined with `requirements.txt`. CI's `pipeline-dataproc-runtime` job runs `tests/pipeline` under it and asserts the exact stack, Java 21 included. To run the same tests locally:

```sh
make setup-runtime   # deletes and recreates .venv-dataproc with python3.12 (override PYTHON312=/path/to/python3.12)
make test-runtime    # pytest tests/pipeline in .venv-dataproc
```

`make test-runtime` needs a JDK that Spark 4.0 supports, 17 or 21; PySpark uses `JAVA_HOME` if set, otherwise the `java` on `PATH` (JDK 25, for example, is not supported). The Java version and exact library versions are asserted only in CI. The test `tests/pipeline/test_runtime_dependencies.py` also enforces that the code shipped to Dataproc (`src/readmission_risk/pipeline/`, the package `__init__.py` and `scripts/run_big_join.py`) imports only the standard library, its own subpackage and the runtime-bundled packages.

The library minors follow Google's runtime page; the Spark patch follows what the live runtime ran (the page lags), as recorded in a batch's gold metadata (`spark_version`). When either changes, edit `requirements-dataproc-3.0.in` and `RUNTIME_3_0` in `tests/pipeline/test_runtime_dependencies.py`, run `make lock-runtime` and `make test-runtime`, and commit the `.in` file, the lockfile and the test together (any one of them alone fails `tests/test_lockfiles.py` or `test_runtime_lockfile_mirrors_runtime_3_0`). `make lock-runtime` re-resolves to the newest allowed patches. The command recorded in a lockfile's header is not the recipe, so use the Makefile targets: `make lock-main` and `make lock-dev` keep existing pins (Python 3.14, macOS only), `make lock-runtime` passes `--upgrade` (Python 3.12). `tests/test_lockfiles.py` fails when a lockfile no longer matches its `.in` file. The `.venv-*` directories are deleted and recreated by these targets.

## Running the actual pipeline (Slice 1: Synthea generation)

This step is separate from the above: it exercises the real Synthea tool, not just this repo's code around it. It requires a JDK 17+ on `PATH` and a one-time ~200MB download, and produces real synthetic patient data on disk (about 7.5 GB for the 10,000-patient reference run, measured 2026-09-21; 4.3 GB of that is `claims_transactions.csv`, which the feature join does not read; the generator's default is `exporter.years_of_history=10`). Skip this unless you actually want to generate data.

```sh
# Download and checksum-verify the Synthea jar into tools/synthea/ (gitignored)
./scripts/setup_synthea.sh

# Dry run first: a small population to sanity-check output and timing before committing
# to the full run (Synthea's own generation time isn't predictable in advance).
python scripts/generate_synthea_data.py --population 1000 --seed 42 --output data/raw/dry_run/

# Full local-proving-phase population
python scripts/generate_synthea_data.py --population 10000 --seed 42 --output data/raw/full_run/
```

Each run writes Synthea's CSV export to `<output>/csv/*.csv` and a durable `<output>/generation_summary.json` (population size, seed, reference date, wall-clock duration, per-table row counts, and any non-fatal warnings). Generated data is gitignored — it's reproducible from the commands above, not tracked in version control.

## Training the models (Slice 3)

Trains a `LogisticRegression` baseline and a calibrated `XGBClassifier` challenger on the gold table from slice 2, scores a chronological, patient-disjoint held-out window once, and tracks everything in a local SQLite-backed MLflow store (`mlflow/`, gitignored).

```sh
# Prerequisite: the gold table (slice 2 + 2b). Build into a FRESH --output-dir: the script refuses an existing one, and a gold
# table built before slice 2b (all-cause label, no _gold_metadata.json) or before 2026-10-10 (no label_horizon column) is
# refused by training and the scorer. The results in results.md were measured
# on data/gold/full_run_2b.
python scripts/run_big_join.py --input-dir data/raw/full_run --output-dir data/gold/full_run_2b --reference-date 20260916

# Train + evaluate. --reference-date MUST equal the value used for the Synthea run and the feature join.
# In a store that already holds runs under this experiment name (e.g. the reference runs), pass a fresh
# --experiment-name, or --model becomes ambiguous for the bundle exporter (docs/scoring.md).
python scripts/train_models.py --gold-dir data/gold/full_run_2b --reference-date 20260916 --test-start-date 20230101 --experiment-name readmission-risk-v1

# Browse runs, metrics, calibration diagrams
mlflow ui --backend-store-uri sqlite:///$(pwd)/mlflow/mlflow.db
```

MLflow's autolog also writes in-sample `training_*` metrics into each run; only the `test_*` metrics are results.

The MLflow store records absolute artifact paths, so moving the repository directory invalidates the paths stored in `mlflow/mlflow.db`.

## Cloud project and cost guard (prepared for the scale-up phase)

This records the account-level setup done with gcloud on 2026-10-09, before any Terraform existed. Since v2 slice 3 the CHF 20 budget below is imported into Terraform (`infra/budgets.tf`) and the rest of the infrastructure is created there ([next section](#infrastructure-terraform)); the commands stay as the record of how the project and budget were born. Replace `<BILLING_ACCOUNT_ID>` with your own (`gcloud billing accounts list`).

```sh
gcloud projects create synthea-readmission-risk --labels=purpose=portfolio,repo=synthea-readmission-risk
gcloud billing projects link synthea-readmission-risk --billing-account=<BILLING_ACCOUNT_ID>

# Budget alert: a notification to the billing account's admins, NOT a spend cap. The amount must be in the billing
# account's currency (CHF here, so CHF 20 rather than USD 20).
gcloud services enable billingbudgets.googleapis.com --project=synthea-readmission-risk
gcloud billing budgets create --billing-account=<BILLING_ACCOUNT_ID> --billing-project=synthea-readmission-risk \
  --display-name="synthea-readmission-risk monthly" --budget-amount=20CHF --calendar-period=month \
  --filter-projects=projects/synthea-readmission-risk \
  --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0 \
  --threshold-rule=percent=1.0,basis=forecasted-spend

# Quota check for us-central1 (the chosen region)
gcloud services enable compute.googleapis.com dataproc.googleapis.com --project=synthea-readmission-risk
gcloud compute regions describe us-central1 --project=synthea-readmission-risk --format="table(quotas.metric,quotas.limit,quotas.usage)"
gcloud compute project-info describe --project=synthea-readmission-risk --format="table(quotas.metric,quotas.limit,quotas.usage)"
```

Quotas found on 2026-10-09 (a new project; Google can change defaults): `CPUS` 200 in us-central1, `CPUS_ALL_REGIONS` 32, `DISKS_TOTAL_GB` 4,096, `INSTANCES` 24. A Serverless for Apache Spark batch on the standard tier consumes `CPUS` and `DISKS_TOTAL_GB`; its default footprint is a 4-vCPU driver plus two 4-vCPU executors (12 vCPUs) with 100 GiB of disk per core (1,200 GiB), and it autoscales. The binding limit is therefore `CPUS_ALL_REGIONS` = 32 vCPUs, i.e. a driver plus 7 executors, so the scale-up job should set `spark.dynamicAllocation.maxExecutors=7` rather than let autoscaling run into the quota. No increase was requested; revisit only if the first real run is too slow.

## Infrastructure (Terraform)

Since v2 slice 3, [infra/](../infra/) manages the project's buckets, service accounts, Workload Identity Federation (WIF) and budgets. Design and reasoning: `notes/eg-new-feature/v2-slice3-terraform-wif-2026-10-11.md`. It is applied **from a laptop only**; CI runs `fmt -check`, `init -backend=false` and `validate`, never `plan` or `apply`, and holds no credentials.

| Resource | What |
|---|---|
| Buckets (us-central1, uniform access, public access prevention enforced) | `synthea-readmission-risk-tfstate` (Terraform state, versioned), `-raw` (objects deleted after 90 days), `-gold`, `-bundles` (model bundles, versioned) |
| `gha-smoke` service account | Lists the bundle bucket (`roles/storage.legacyBucketReader`: no object reads); impersonable only by [smoke.yml](../.github/workflows/smoke.yml) on `main` through WIF |
| `bundle-writer` service account | Creates objects in the bundle bucket (`roles/storage.objectCreator`: no overwrite, no delete, no list); impersonated by the operator for uploads |
| `scorer` service account (v2 slice 4) | Reads the bundle and gold buckets (`roles/storage.objectViewer`), owns dataset `scratch` (`roles/bigquery.dataEditor`), runs BigQuery jobs (`roles/bigquery.jobUser`), and on `serving.predictions` / `serving.scoring_runs` holds only the custom role `scorerTableWriter` (`tables.get`, `getData`, `updateData`: no create, drop or metadata change); impersonable only by [score.yml](../.github/workflows/score.yml) on `main` through WIF, and by the operator |
| BigQuery datasets (v2 slice 4, us-central1) | `serving` (`predictions`, partitioned by `as_of_date`; `scoring_runs`, partitioned by `started_at`; both require a partition filter and are deletion-protected; schemas from `src/readmission_risk/warehouse/schemas/`) and `scratch` (tables expire after 1 day) |
| WIF pool `github`, provider `github-actions` | Accepts a GitHub OIDC token only if repository id `1380503419`, owner id `270004823`, ref `refs/heads/main` and event `workflow_dispatch`/`schedule` all match |
| Budgets | CHF 20/month (imported, created 2026-10-09) and CHF 5/month early warning; alerts only, not caps |
| Default compute service account (2026-10-14) | Its project `roles/editor`, granted by Google when the Compute Engine API is enabled, is removed (`google_project_iam_member_remove`); no workload runs as it |

**Prerequisites.** Terraform 1.16.x (`brew install hashicorp/tap/terraform`; `terraform version`), the gcloud CLI logged in (`gcloud auth login`), Application Default Credentials for Terraform with this project as quota project (`gcloud auth application-default login`, then `gcloud auth application-default set-quota-project synthea-readmission-risk`), and Billing Account Administrator (or Costs Manager) on the billing account. Your gcloud default project can stay whatever it is: every command block below starts with `export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk`.

**Variables.** `cp infra/terraform.tfvars.example infra/terraform.tfvars` and fill in the billing account id, the CHF 20 budget's id (`gcloud billing budgets list --billing-account=<BILLING_ACCOUNT_ID> --billing-project=synthea-readmission-risk`) and your `user:` member. The file is gitignored (it holds the billing account id and your email); run `terraform -chdir=infra fmt` after editing it, because `make tf-validate` and the bootstrap check its formatting too.

**Bootstrap (once).** `infra/bootstrap.sh` enables the six APIs with gcloud, creates the state bucket under a temporary local backend, then migrates the state into `gs://synthea-readmission-risk-tfstate/infra/`. It is safe to re-run: it decides from what exists (state object, bucket, local state file), treats only a 404 as "absent", and never migrates over an existing remote state. Every apply asks for approval.

```sh
export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk
infra/bootstrap.sh
```

**Plan and apply.** The first plan should read `Plan: 1 to import, 12 to add, 0 to change, 0 to destroy` (the CHF 20 budget imported with no change; 3 buckets, 2 service accounts, 4 IAM members, the pool, the provider and the CHF 5 budget). Anything else: stop and investigate. Afterwards `terraform plan` must say "No changes". v2 slice 4's change on top of that state reads `Plan: 15 to add, 0 to change, 0 to destroy` (the BigQuery API, 2 datasets, 2 tables, the custom role, the `scorer` account and 8 IAM members).

```sh
terraform -chdir=infra plan
terraform -chdir=infra apply
```

**Provider lock file.** `infra/.terraform.lock.hcl` is committed with hashes for CI (`linux_amd64`) and an Apple-silicon laptop (`darwin_arm64`); CI refuses to change it (`-lockfile=readonly`). Regenerate it on every provider bump (Dependabot proposes those):

```sh
terraform -chdir=infra init -backend=false -input=false
terraform -chdir=infra providers lock -platform=linux_amd64 -platform=darwin_arm64
```

**Uploading the v1 bundles (slice 3, one-off).** New IAM grants can take minutes to apply: if the first impersonated call fails with an `iam.serviceAccounts.getAccessToken` denial, wait and retry for up to 10 minutes. `gcloud storage cp` cannot be used as `bundle-writer`: it reads the destination's metadata first (`storage.objects.get`), which the create-only role deliberately lacks (found on the first live run, 2026-10-11). The upload therefore calls the Cloud Storage JSON API directly with a short-lived `bundle-writer` token; `ifGenerationMatch=0` refuses to replace anything, and `manifest.json` goes last, so a reader never sees a manifest without its files.

```sh
export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk
# set -euo pipefail in a subshell: the first failed upload stops the loop. Re-running resumes: objects that already
# exist are skipped (checked as YOU, because bundle-writer cannot read or list), and V6 below compares every
# uploaded object's sha256 with the local file. The token is never printed.
( set -euo pipefail
  W=bundle-writer@synthea-readmission-risk.iam.gserviceaccount.com
  BUCKET=synthea-readmission-risk-bundles
  TOKEN=$(CLOUDSDK_BILLING_QUOTA_PROJECT=LEGACY gcloud auth print-access-token --impersonate-service-account="$W" 2>/dev/null)
  for id in 75a1448729ef561c596f5e850f5bedce50ed63355ca2de38dc724c635d24eef4 30009d198070f66db1a0c35b033f11de9e08d88292330e0e71265553c4645d2a; do
    for f in model.pkl split_assignment.csv manifest.json; do
      if gcloud storage objects describe "gs://$BUCKET/$id/$f" >/dev/null 2>&1; then echo "exists, skipped: $id/$f"; continue; fi
      curl -sS --fail-with-body -o /dev/null -w "uploaded $id/$f: HTTP %{http_code}\n" -X POST \
        -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/octet-stream" --data-binary "@bundles/$id/$f" \
        "https://storage.googleapis.com/upload/storage/v1/b/$BUCKET/o?uploadType=media&ifGenerationMatch=0&name=$id%2F$f"
    done
  done )
```

The bucket is private: a fresh clone still trains and exports its own bundle ([scoring.md](scoring.md#model-bundles)). Slice 4 adds the Python GCS reader and points `deploy/champion.json` at the bucket.

**Live checks after the apply (V1–V8).** Record results in the design note under its redaction rule (billing account, budget id and your email replaced by placeholders; plans summarised, never pasted).

```sh
export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk
W=bundle-writer@synthea-readmission-risk.iam.gserviceaccount.com
B=gs://synthea-readmission-risk-bundles
M=$B/75a1448729ef561c596f5e850f5bedce50ed63355ca2de38dc724c635d24eef4/manifest.json
# V1: "No changes."
terraform -chdir=infra plan
# V2: among budgets whose filter contains projects/271458030145, exactly two: CHF 20 (thresholds 0.5/0.9/1.0 actual,
# 1.0 forecast) and CHF 5 (1.0 actual, 1.0 forecast).
gcloud billing budgets list --billing-account=<BILLING_ACCOUNT_ID> --billing-project=synthea-readmission-risk --format=json
# V3: each bucket: US-CENTRAL1, public access prevention enforced, uniform access on, versioning and lifecycle as in infra/storage.tf.
for b in tfstate raw gold bundles; do gcloud storage buckets describe gs://synthea-readmission-risk-$b --format=json; done
# V4: the upload above succeeded (6 objects).
gcloud storage ls --recursive "$B"
# V5: bundle-writer cannot overwrite or list (JSON API, like the upload). Expected: (a) 412 (or 403), (b) 403, (c) 403;
# afterwards the generation and sha256 are unchanged and only one generation exists. That, not the status code, is the proof.
TOKEN=$(CLOUDSDK_BILLING_QUOTA_PROJECT=LEGACY gcloud auth print-access-token --impersonate-service-account="$W" 2>/dev/null)
U="https://storage.googleapis.com/upload/storage/v1/b/synthea-readmission-risk-bundles/o?uploadType=media&name=75a1448729ef561c596f5e850f5bedce50ed63355ca2de38dc724c635d24eef4%2Fmanifest.json"
gcloud storage objects describe "$M" --format='value(generation)'; gcloud storage cat "$M" | shasum -a 256
curl -sS -o /dev/null -w "(a) %{http_code}\n" -X POST -H "Authorization: Bearer $TOKEN" --data-binary 'tampered' "$U&ifGenerationMatch=0"
curl -sS -o /dev/null -w "(b) %{http_code}\n" -X POST -H "Authorization: Bearer $TOKEN" --data-binary 'tampered' "$U"
curl -sS -o /dev/null -w "(c) %{http_code}\n" -H "Authorization: Bearer $TOKEN" "https://storage.googleapis.com/storage/v1/b/synthea-readmission-risk-bundles/o"
gcloud storage objects describe "$M" --format='value(generation)'; gcloud storage cat "$M" | shasum -a 256
gcloud storage ls --all-versions "$M"
# V6: every uploaded object's sha256 equals the local file's; manifest.json's equals deploy/champion.json's sha256.
for id in 75a1448729ef561c596f5e850f5bedce50ed63355ca2de38dc724c635d24eef4 30009d198070f66db1a0c35b033f11de9e08d88292330e0e71265553c4645d2a; do
  for f in model.pkl split_assignment.csv manifest.json; do
    echo "$id/$f $(gcloud storage cat "$B/$id/$f" | shasum -a 256 | cut -d' ' -f1) $(shasum -a 256 "bundles/$id/$f" | cut -d' ' -f1)"
  done
done
# V7: no user-managed keys on either account.
for sa in gha-smoke bundle-writer; do gcloud iam service-accounts keys list --iam-account=$sa@synthea-readmission-risk.iam.gserviceaccount.com --managed-by=user --project=synthea-readmission-risk; done
# V8: the real policies. gha-smoke: one binding, workloadIdentityUser -> the smoke.yml principalSet; bundle-writer: one
# binding, serviceAccountTokenCreator -> you; the bucket: the project legacy bindings plus exactly legacyBucketReader ->
# gha-smoke and objectCreator -> bundle-writer; the provider: the condition literal below and the six mappings.
gcloud iam service-accounts get-iam-policy gha-smoke@synthea-readmission-risk.iam.gserviceaccount.com --project=synthea-readmission-risk
gcloud iam service-accounts get-iam-policy bundle-writer@synthea-readmission-risk.iam.gserviceaccount.com --project=synthea-readmission-risk
gcloud storage buckets get-iam-policy "$B"
gcloud iam workload-identity-pools providers describe github-actions --workload-identity-pool=github --location=global --project=synthea-readmission-risk --format='value(attributeCondition)'
# expected: assertion.repository_id == '1380503419' && assertion.repository_owner_id == '270004823' && assertion.ref == 'refs/heads/main' && assertion.event_name in ['workflow_dispatch', 'schedule']
gcloud iam workload-identity-pools providers describe github-actions --workload-identity-pool=github --location=global --project=synthea-readmission-risk --format='json(attributeMapping)'
```

**The keyless smoke job.** [smoke.yml](../.github/workflows/smoke.yml) is dispatch-only. Its job holds `id-token: write`, exchanges the GitHub OIDC token for a short-lived `gha-smoke` token, and lists the bundle bucket. Because the WIF condition accepts only `refs/heads/main`, it can only succeed once the workflow is on `main` (`gh workflow run smoke.yml --ref main`). The `auth` step only writes a credentials file; a refused exchange shows up in the "List the bundle bucket" step, as an STS error (the condition refused the token) or an IAM `getAccessToken` denial (the binding did not match).

**V-post (done 2026-10-18, runs 39156510316 and 39156545023; results in the slice-3 note).** (1) `gh workflow run smoke.yml --ref main`: green, and the log lists the bundle prefixes: the two schema-1 v1 bundles and, once uploaded (v2 slice 6c), the two schema-2 v1 bundles `fb44c38910b4…` and `bf66d1cb272b…`. (2) The negative proof: on a throwaway branch that contains `smoke.yml`, `gh workflow run smoke.yml --ref <branch>`: the **`auth`** step fails with the **STS** refusal by the attribute condition (the action performs the exchange itself, measured at V-post) (`sts.googleapis.com`, `unauthorized_client`); an IAM denial, a context or a network error does not count. Record both run URLs in the design note and CLAUDE.md. Not proven by these runs: that the `workflow_ref` binding refuses another workflow file on `main` (first exercised in slice 4), and the `event_name` clause (every workflow that can reach the exchange is dispatch-only).

## Scoring into BigQuery (v2 slice 4)

Design and live results: `notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md`. Every command below starts from `export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk`; impersonated `gcloud` calls also need `CLOUDSDK_BILLING_QUOTA_PROJECT=LEGACY`. The Python CLIs take `--impersonate-service-account` and handle the quota project themselves.

**The gold table in GCS (once per gold table).** The scorer reads gold from GCS: since v2 slice 7 from `gs://synthea-readmission-risk-gold/v2/main_200k/` (written there by the Dataproc Big Join), before that from `gs://synthea-readmission-risk-gold/full_run_2b/` (downloaded to a temporary directory, then read by the same loader as a local copy). Upload the Parquet parts and the metadata as yourself (the gold bucket is not create-only), then check the bytes:

```sh
export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk
gcloud storage cp data/gold/full_run_2b/part-*.parquet data/gold/full_run_2b/_gold_metadata.json gs://synthea-readmission-risk-gold/full_run_2b/
# Byte check: GCS stores md5 (base64), not sha256.
for f in data/gold/full_run_2b/part-*.parquet data/gold/full_run_2b/_gold_metadata.json; do
  remote=$(gcloud storage objects describe "gs://synthea-readmission-risk-gold/full_run_2b/$(basename "$f")" --format='value(md5_hash)')
  [ "$remote" = "$(openssl md5 -binary "$f" | base64)" ] && echo "equal $(basename "$f")" || echo "DIFFERENT $(basename "$f")"
done
```

**Score and prove the data tests.** `make score-bq RUN_DATE=20250630` and `make m5-check M5_RUN_DATE=<a date published by slice-7 code; default 20230417>` (since v2 slice 7, PRED-11 fails the clean copy of any date published before slice 7, whose rows lack `gold_fingerprint_definition`) ([scoring.md](scoring.md#scoring-into-bigquery-v2-slice-4)). Read results back as the scorer with a partition filter, for example `bq query --use_legacy_sql=false --maximum_bytes_billed=104857600 "SELECT status, COUNT(*) FROM serving.scoring_runs WHERE started_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY) GROUP BY status"`.

**Bundles in GCS.** `python scripts/export_bundle.py ... --bundles-dir gs://synthea-readmission-risk-bundles --impersonate-service-account bundle-writer@synthea-readmission-risk.iam.gserviceaccount.com` uploads a new bundle create-only. `bundle-writer` cannot read, so verify an upload as yourself (or the scorer): `gcloud storage ls -l gs://synthea-readmission-risk-bundles/<id>/` lists three objects, and scoring with `--bundle gs://synthea-readmission-risk-bundles/<id>` checks every hash. An interrupted upload leaves objects without `manifest.json` (the scorer refuses that bundle as "no manifest"); a re-run reports "already exists". As the owner, delete the partial prefix with `gcloud storage rm --recursive --all-versions gs://synthea-readmission-risk-bundles/<id>/` and export again.

**When the history data test fails** (`DATA TESTS FAILED: RUNS-…` on every run): a `scoring_runs` row breaks an invariant, and every later run reports it until the row is repaired; publishing is not blocked. As the owner, find it (the assertion's ID says which rule), then delete or correct it with DML and note what you did in the slice note or run ledger:

```sh
bq query --use_legacy_sql=false --maximum_bytes_billed=104857600 \
  "SELECT * FROM serving.scoring_runs WHERE started_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 400 DAY) AND finished_at < started_at"
bq query --use_legacy_sql=false --maximum_bytes_billed=104857600 \
  "DELETE FROM serving.scoring_runs WHERE started_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 400 DAY) AND scoring_run_id = '<id>'"
```

**Recovering rows** (an accidental `DELETE` or truncation, by anyone): BigQuery time travel keeps the last 7 days. Restore a table's earlier state with `bq cp "serving.scoring_runs@<epoch milliseconds before the change>" scratch.scoring_runs_restored`, inspect it, then re-insert the missing rows with DML.

## Scale-up pilot: generation VM and Dataproc (v2 slice 5)

Design, live runbook (L0-L9) and results: `notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md`. Both commands below **cost money** and run only from the laptop, as you (ADC), after you confirm. Start every shell with `export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk`.

**What is pre-registered.** `prereg.yaml` (repo root) fixes the seeds, dates, label, split rule, metrics, bootstrap, promotion and monitoring rules before any v2 population exists. Both CLIs refuse unless it is committed, the working tree is clean, `HEAD` is on an `origin/*` branch (after `git fetch`), and every value matches the code (preflights P1 and P1b). A later change is made in place **and** recorded under `deviations`.

**Generation** (a spot `n2-standard-32` from the Terraform template `synthea-generator-pilot-7200s`, no external IP, deleted by the launcher or after 2 h at the latest):

```sh
# The JDK tarball pinned in prereg.yaml (generation.jdk): download it from Adoptium's GitHub release and check the hash.
# Into tools/jdk/ (gitignored): any untracked file elsewhere makes the launcher's clean-tree check (P1) refuse.
mkdir -p tools/jdk
curl -L -o tools/jdk/OpenJDK21U-jdk_x64_linux_hotspot_21.0.12.1_1.tar.gz \
  "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/OpenJDK21U-jdk_x64_linux_hotspot_21.0.12.1_1.tar.gz"
shasum -a 256 tools/jdk/OpenJDK21U-jdk_x64_linux_hotspot_21.0.12.1_1.tar.gz   # ce79869e...aee94, as in prereg.yaml
make generate-vm RUN=pilot_20k JDK_TARBALL=tools/jdk/OpenJDK21U-jdk_x64_linux_hotspot_21.0.12.1_1.tar.gz GEN_ARGS=--dry-run   # no gcloud call
make generate-vm RUN=pilot_20k JDK_TARBALL=tools/jdk/OpenJDK21U-jdk_x64_linux_hotspot_21.0.12.1_1.tar.gz
```

It uploads the jar, JDK, Spark zip and generation script to `gs://synthea-readmission-risk-artifacts/sha256/<hex>/` (create-only), waits for IAM propagation like the Big Join submitter below if IAM changed in the last 8 minutes, creates the VM, checks it (labels, startup script, spot, DELETE, run duration, no external IP, its own service account, the disk label), waits for `gs://synthea-readmission-risk-raw/v2/pilot_20k/_DONE`, deletes the VM and **proves** no labelled instance or disk remains. Exit codes: 0 done, 1 refused, 2 failed, 3 a labelled instance or disk remains (the message prints its delete command), 130 interrupted. Timings and peak memory are saved under `data/pilot/pilot_20k/`. A failed or preempted run leaves a partial raw prefix that the next attempt refuses; delete it first: `gcloud storage rm -r gs://synthea-readmission-risk-raw/v2/pilot_20k/`. Ctrl-C during that IAM wait (or its re-check) creates no VM; later, the VM is deleted and proven gone as usual. When the VM fails, the launcher prints the last lines of `_FAILED` (or, when no marker appeared, the first lines of the VM's startup-script log in Cloud Logging that mention a denial or a startup.sh failure) and, when they name a denied permission, a hint about IAM propagation and `infra/iam.tf`.

**Big Join on Dataproc Serverless 3.0** (`maxExecutors=7`, standard tier, ttl 2 h, the `dataproc-runtime` service account; never while a generation VM exists, since both draw on the 32-vCPU `CPUS_ALL_REGIONS` quota):

```sh
make bigjoin-batch RUN=pilot_20k BATCH_ARGS=--dry-run   # no gcloud call
make bigjoin-batch RUN=pilot_20k
```

It checks that the raw run matches `prereg.yaml` (P7), writes gold to `gs://synthea-readmission-risk-gold/v2/pilot_20k/` with metadata version 2 (the Spark-computed `spark-rowhash-v1` fingerprint, the raw run's generation summary and the build provenance), and records wall-clock and DCU-hours (`runtimeInfo.approximateUsage.milliDcuSeconds / 3.6e6`) in `data/pilot/pilot_20k/batch_<id>.json`. A failed batch may leave partial output that the next submit refuses; delete it first: `gcloud storage rm -r gs://synthea-readmission-risk-gold/v2/pilot_20k/`. Ctrl-C while the batch runs leaves it running (bounded by its ttl); cancel it with `gcloud dataproc batches cancel <id> --region=us-central1`. Ctrl-C during the pre-submit IAM wait submits nothing; Ctrl-C (or a gcloud error) during the usage wait of a finished batch still writes its record.

**Proving nothing is left running:**

```sh
gcloud compute instances list --filter=labels.purpose=synthea-generation
gcloud compute disks list --filter=labels.purpose=synthea-generation
gcloud dataproc batches list --region=us-central1 --filter="state=RUNNING OR state=PENDING"
```

All three must print nothing.

**Lessons from the first live run (slice 5b, 2026-10-12; details in the slice-5 note's Results).** Runtime 3.0 rejects `--staging-bucket` (the submitter no longer passes it). Its pre-flight permission check requires `dataprocrm.nodePools.update`, which no predefined role grants; `dataproc-runtime` uses the custom role `dataprocRuntimeWorker` (`roles/dataproc.worker` minus `storage.*`, plus that permission), so after changing that role wait about 8 minutes before submitting, or the batch fails in validation (no cost). Policy Troubleshooter cannot check object-level or object-prefix-conditioned storage access: pass the bucket as the resource and the object as `--resource-name=projects/_/buckets/<b>/objects/<o> --resource-service=storage.googleapis.com --resource-type=storage.googleapis.com/Object`, and expect a prefix condition to evaluate `false` regardless; prove such grants with a live probe instead. Since 2026-10-12 the submitter (`make bigjoin-batch`) returns as soon as a batch ends FAILED or CANCELLED without ever reaching RUNNING, instead of waiting 10 minutes for usage figures that such a batch never gets, and prints this propagation hint when the failure message contains "Permission validation failed"; the generation launcher prints `POSITIVE CONTROL OK: …` when its positive control passes. Before submitting (and the generation launcher before creating its VM), the submitter reads the Admin Activity audit log and, if a non-Google principal changed IAM in the last 8 minutes, waits until 8 minutes have passed since the latest change (Ctrl-C there starts nothing); changes by Google-managed principals (`…@system.gserviceaccount.com`, `service-<n>@gcp-sa-….iam.gserviceaccount.com`, `<n>@cloudservices.gserviceaccount.com`) are ignored, and an unreadable log is a warning; after a wait the preflight checks run again. Audit-log entries take seconds to a minute to become readable, so a change made just before submitting can be missed: after a `terraform apply`, wait a minute before `make bigjoin-batch` or `make generate-vm` (for the Big Join the failure hint still covers a miss; for the launcher a miss means a VM that boots and fails; the launcher then names the denied permission when the VM's output names one (if the VM cannot even write `_FAILED`, only after `max_run_duration` has ended it)).

## The validation population main_200k (v2 slice 6a)

Design, live runbook (L0-L8) and results: `notes/eg-new-feature/v2-slice6a-main-200k-and-checks-2026-10-14.md`. `main_200k` is the pre-registered validation population (`prereg.yaml`: seed 29, 200,000 patients): no model, threshold or rule may be chosen from it before the slice-6b promotion gate runs.

- **Generation and Big Join:** the same commands as the pilot above, with `RUN=main_200k` and the explicit opt-in: `make generate-vm RUN=main_200k JDK_TARBALL=… GEN_ARGS=--allow-main-run` (Terraform template `synthea-generator-main-7200s`), then, once the VM is proven gone, `make bigjoin-batch RUN=main_200k BATCH_ARGS=--allow-main-run`. The Big Join now also records best-effort facts about the runtime (`--record-runtime`): Python and Java versions, `DATAPROC_*` environment variables, `spark.dataproc.*version*` conf values and the BigQuery/GCS connector jars, as `runtime.*` keys of the gold metadata's `build_provenance` (metadata version unchanged).
- **Retention:** the raw bucket deletes `v2/pilot_*` after 90 days and `v2/main_*` after 365 days (`infra/storage.tf`; owner decision 2026-10-14). An object outside both prefixes does not expire.
- **Pre-training checks C1-C9** (read-only; your own gcloud identity and ADC, no impersonation; counts only, never loads or fits a model):

```sh
make check-population RUN=pilot_20k    # the rehearsal; downloads ~445 MB into data/checks/pilot_20k/
make check-population RUN=main_200k    # downloads ~4.5 GB into data/checks/main_200k/
```

It needs the local v1 raw data (`data/raw/full_run`) and v1 gold (`data/gold/full_run_2b`) for the disjointness check (`CHECK_ARGS="--v1-raw-dir … --v1-gold-dir …"` to point elsewhere), refuses a working-tree `prereg.yaml` that differs from `HEAD`'s, prints one `PASS|FAIL|WARN|INFO|SKIP` line per check and writes `data/checks/<run>/population_checks.json`. Exit codes: 0 no FAIL, 1 at least one FAIL, 2 could not run (no report when a precondition failed). The checks: C1 the gold loads and its label settings equal prereg's; C2 structure; C3 the raw run's generation summary against prereg and the gold; C4 patient Ids; C5 no patient shared with the pilot or with v1 (raw and `full_run_2b`); C6-C7b the corrected A7 (latest START, only posthumous Death Certifications after the end date, no modeling row whose label depends on a record after it); C8 the runtime facts (FR14; it WARNs when no `3.0.<n>` label or no BigQuery connector jar is found; on Serverless 3.0 the label is never exposed, and the component versions are the accepted record); C9 at least `power.min_test_positives` test-window positives for `main_200k`.

**Lessons from the live run (slice 6a, 2026-10-15; details in the 6a note's Results).** A spot VM can be preempted within minutes of creation (attempt 1: about 90 s): the launcher reports "vanished without a marker", proves nothing remains, and leaves no raw objects, so simply re-run it (same zone worked on the second attempt; `GEN_ARGS="--allow-main-run --zone=us-central1-c"` tries another). The launcher's own lines appear in a redirected log only when it exits (Python block-buffers stdout; `PYTHONUNBUFFERED=1` avoids that); follow progress in Cloud Logging's `google_metadata_script_runner` log instead. Inside a Serverless 3.0 batch the runtime sub-version label is not exposed, so C8 WARNs on it; FR14's record is the component versions instead (owner decision 2026-10-16).

## Promotion gate and frozen-v1 validation (v2 slice 6b)

Design, owner decisions and the live runbook (L0-L7): `notes/eg-new-feature/v2-slice6b-promotion-gate-frozen-v1-2026-10-16.md`. The challenger is the logistic regression trained on `main_200k`'s training partition; it must beat the champion (the frozen v1 logistic regression) **and** the reason-code lookup on ROC-AUC (one-sided: the lower bound of the 95% patient-clustered paired bootstrap CI of the difference is > 0) and be Brier-non-inferior (upper bound <= 0.05 x the champion's Brier). Every number comes from `prereg.yaml`.

```sh
make prereg-lock LOCK_ARGS="--experiment-name readmission-risk-v2-main200k"   # BEFORE any training on main_200k; commit + PR
make gate RUN=main_200k GATE_ARGS="--challenger-experiment readmission-risk-v2-main200k --challenger-bundle bundles/<id>"
make gate RUN=main_200k GATE_ARGS="--challenger-experiment readmission-risk-v2-main200k --challenger-bundle bundles/<id> --negative-control"
make prereg-phase-b                                                              # AFTER both gate runs; commit + PR
```

- **Lock:** `scripts/prereg_append.py lock` appends `analysis_lock` (challenger model, experiment name, FR16 rows, negative control, code commit) and refuses a dirty tree, an existing lock, a P1b mismatch, an existing experiment, and any MLflow run (deleted ones included) already trained on `main_200k`'s gold.
- **Gate:** `scripts/run_promotion_gate.py` verifies the lock (the code at HEAD and the challenger's training code equal the locked commit, which is on a remote branch; the runs started after it), the gold and Phase A (unchanged since generation), the run parameters, the Python and library versions (guard 14's and guard 8's rules), both bundles (scored from their verified bytes), that no other experiment used this gold, and that the frozen v1 models still reproduce their logged v1 metrics on `data/gold/full_run_2b`. It writes `data/gate/<run>/gate_report.json` + `gate_table.md` (`negative_control_*` with `--negative-control`). Exit **0** PROMOTE, **3** REFUSE, **2** could not run; through `make`, any non-zero exit prints `make: *** [gate] Error <n>`, so judge the outcome from the report's `mode`, `decision` and `error`. A successful main or negative-control report is never overwritten. `--rehearsal` goes with `RUN=pilot_20k` and only with it (pipeline sanity only). The gate also refuses when the `analysis_lock` block was edited after its commit, when **anything in `prereg.yaml` changed after the lock commit** (so commit Phase B only after both gate runs), and when a challenger run's commit does not contain the lock commit.
- **Needs locally:** the `mlflow/` store with `readmission-risk-v1`, the champion copy `bundles/<leaf of deploy/champion.json>` and `data/gold/full_run_2b` (recovery: `gcloud storage cp -r` from the bundle bucket / `gs://synthea-readmission-risk-gold/full_run_2b`), your own ADC, `CLOUDSDK_CORE_PROJECT=synthea-readmission-risk`, and no untracked files.
- **Phase B:** `scripts/prereg_append.py phase-b` checks both reports, copies them to `notes/eg-new-feature/v2-slice6b-reports/` and appends `phase_b`.

## Monitoring tick (v2 slice 7)

Design, owner decisions and the live runbook (L0-L17): `notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md`.

**What it does.** Each tick of [monitor.yml](../.github/workflows/monitor.yml):
1. advances a **simulated clock** by one week through the champion's held-out 2023-2026 discharges;
2. scores that week into BigQuery, a 7-day window, through the slice-4 write-audit-publish;
3. evaluates the 13-week cohort whose labels have matured: observed/expected with an exact Poisson 95% interval, under the pre-registered rule in `prereg.yaml`; AUC is reported only;
4. records one `serving.monitoring` row;
5. renders the run ledger (job A, read-only identity) and commits it to the `ledger` branch (job B, no cloud credentials);
6. opens or comments on ONE GitHub issue per kind: `monitoring-failure` when any job failed or was cancelled, `monitoring-alert` when the verdict is an alert.

The clock moves only on success, so a failed or dropped tick is retried on the same simulated date; replaying a date is idempotent. `deploy/monitoring.json` names the monitored bundle (it must be the champion), the gold, the simulated range (178 Mondays, 2023-04-17 to 2026-09-07) and the two cron strings.

```sh
export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk
make gold-200k && make monitor-preflight   # offline: all 178 simulated weeks pass the completeness guard
make monitor-simulate                      # offline: every week scored + evaluated; the sanity check and L10's oracle
make m5-monitoring                         # every MON assertion fires on a seeded synthetic row (monitor SA)
make monitor-catch-up WEEKS=18             # LIVE, laptop: the 18 warm-up weeks (clean checkout of main; no Actions tick running)
gh workflow run monitor.yml --ref main     # one tick; -f sim_date=YYYYMMDD replays an earlier simulated date
make ledger                                # render the ledger locally (ledger-reader SA)
make m3-check                              # metric M3 from the Actions runs + serving.monitoring
```

- **Cron states.** `monitor.yml` holds no schedule after the v2 merge. A PR adds the burn-in cron (`7,37 13-18 * * *`, every 30 minutes for one afternoon; the fallback `7 13-22 * * *` if one tick takes more than 25 minutes, changed together with `deploy/monitoring.json`). After at least 5 consecutive green scheduled ticks, a second PR replaces it with the weekly cron (`17 6 * * 1`). This is a burn-in, not weeks of unattended operation.
- **The ledger branch.** It is created once, by hand, **before `v2` merges into `main`** (so the README's badge and ledger links resolve from the start), as an orphan branch (a commit without parents) through the GitHub API, so the working tree is never touched; CI never creates it. The first commit holds a README and the two files job B later replaces: a placeholder ledger and the badge JSON the renderer writes when there is no tick yet.
  ```sh
  repo=fma00/synthea-readmission-risk
  tree=$(python3 - <<'PY' | gh api "repos/$repo/git/trees" --input - --jq .sha
  import json
  badge = {"schemaVersion": 1, "label": "last tick (simulated clock)", "message": "none yet", "color": "lightgrey"}
  files = {
      "README.md": "# Run ledger\n\nRendered by monitor.yml from BigQuery: docs/run-ledger.md and docs/last-tick.json (simulated clock, synthetic data).\n",
      "docs/run-ledger.md": "# Run ledger\n\nNo tick yet (simulated clock). Rendered by monitor.yml from BigQuery.\n",
      "docs/last-tick.json": json.dumps(badge, indent=2) + "\n",
  }
  print(json.dumps({"tree": [{"path": p, "mode": "100644", "type": "blob", "content": c} for p, c in files.items()]}))
  PY
  )
  commit=$(gh api "repos/$repo/git/commits" -f message='Start the run ledger branch' -f "tree=$tree" --jq .sha)
  gh api "repos/$repo/git/refs" -f ref=refs/heads/ledger -f "sha=$commit"
  gh api "repos/$repo/contents/docs/last-tick.json?ref=ledger" --jq .name   # prints last-tick.json
  ```
  It is not covered by ruleset 24876876 (which targets `main` and `v2`), so job B can commit without a bypass.
- **Staleness.** The README badge reads `docs/last-tick.json` on the `ledger` branch. It shows the last tick's wall time and simulated date, plus "stale after <time>": the last scheduled tick plus two periods of the committed cron. A static page cannot say "stale since"; the v3 dashboard will. In the burn-in cron state the badge reads stale about an hour after the afternoon's last slot, until the weekly-cron PR is merged.
- **GitHub disables scheduled workflows after 60 days without repository activity** (public repositories). Keep a calendar reminder 50 days after the last commit. To re-enable: `gh workflow enable monitor.yml`, then `gh workflow run monitor.yml --ref main` to catch up the clock.
- **Repair a failing history test.** If `monitoring.sql` fails, every later tick fails at evaluate. Repair it as follows:
  1. Read the MON label in the issue or the job summary.
  2. Find the offending row(s) with a `bq query` as yourself (the operator).
  3. Remove or correct them with one DML statement **as the operator** (never as the monitor service account), after checking it.
  4. Record the statement in the slice note's Results.

  BigQuery time travel (7 days, `FOR SYSTEM_TIME AS OF`) can restore deleted rows. A wrong assertion is fixed by a PR instead.
- **Identities.** `scorer` scores, `monitor` resolves the clock and evaluates (writes only `serving.monitoring`, reads predictions, scoring_runs and gold), and `ledger-reader` renders the ledger (read-only); all three are bound to `monitor.yml` on `main`. GitHub's OIDC token has no per-job claim, so the binding cannot tell the jobs apart (disclosed in the design note).

- **Never overlap** a laptop catch-up with an Actions tick: the catch-up refuses while `gh run list` shows a `monitor.yml` run that is not completed. `score.yml` shares the concurrency group; a `score.yml` run on a simulated date replaces that week's 7-day partition with a 30-day one, so the cohorts that need it report `incomplete_cohort` until the date is replayed.
- **Cost:** about 10 BigQuery queries (10 MB minimum each) and 40 MB of GCS egress per tick; about CHF 1-2 for the whole slice.
- **Reproducibility across runners:** hosted runners differ in CPU, and OpenBLAS picks its kernel per CPU, which changed the last bits of a few scores (slice 7, L11). The score jobs therefore set `OPENBLAS_CORETYPE=Haswell`, so every hosted tick is bit-identical. A laptop tick (macOS Accelerate) can still differ from a runner tick in the last bits; such replays are compared with a 1e-15 relative tolerance.

## Teardown checklist

Order matters: `prevent_destroy` makes `terraform destroy` refuse while the CHF 20 budget, the state bucket or the bundle bucket is still in state, and the bundle bucket's IAM members must go before the bucket. Start with `export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk`.

1. **Release the CHF 20 budget**, which pre-dates Terraform and stays as the standing guard while the billing account exists: delete the `import` block and the `google_billing_budget.monthly` resource from `infra/budgets.tf` in your working copy, then `terraform -chdir=infra state rm google_billing_budget.monthly`.
2. **Keep what you want from the bundle bucket:** `gcloud storage cp --recursive gs://synthea-readmission-risk-bundles/<id> <local dir>`.
3. **Remove the bundle bucket's IAM members, then the bucket:**
   ```sh
   terraform -chdir=infra destroy -target=google_storage_bucket_iam_member.smoke_lists_bundles -target=google_storage_bucket_iam_member.writer_creates_bundles
   gcloud storage rm --recursive --all-versions "gs://synthea-readmission-risk-bundles/**"
   terraform -chdir=infra state rm google_storage_bucket.bundles
   gcloud storage buckets delete gs://synthea-readmission-risk-bundles
   ```
4. **Release the state bucket from state:** `terraform -chdir=infra state pull > final.tfstate` (a local record, gitignored), then `terraform -chdir=infra state rm google_storage_bucket.tfstate`.
5. **Destroy the rest:** `terraform -chdir=infra destroy`. Raw and gold are deleted with their contents; the CHF 5 budget, both service accounts, all IAM members, the pool and the provider go (destroying `default_compute_no_editor` does not restore the default compute SA's roles/editor; to delete or disable that SA later, first remove `default_compute_no_editor` and its data source from `infra/`, because every plan and destroy reads it); the APIs stay enabled. The final state is written to the state bucket, which still exists.
6. **Delete the state bucket last:** `gcloud storage rm --recursive --all-versions "gs://synthea-readmission-risk-tfstate/**"`, then `gcloud storage buckets delete gs://synthea-readmission-risk-tfstate`. `infra/` needs a new bootstrap after this.
7. **Slice 5 resources:** no generation VM or Dataproc batch may be running (the three commands above print nothing). Raw and gold `v2/` objects go with their buckets in step 5; the artifacts bucket is `force_destroy` and goes there too (the Dataproc staging bucket was removed on 2026-10-14). The `spark` network, subnet and firewall, the instance template and the `synthea-generator` and `dataproc-runtime` service accounts are destroyed by step 5.
8. **BigQuery (v2 slices 4 and 7):** set `deletion_protection = false` on `google_bigquery_table.predictions`, `.scoring_runs` and `.monitoring` and apply; `serving` does not delete its contents on destroy, so empty it first (`bq rm -r -f -d serving` as the owner, after exporting anything you want to keep); `scratch` deletes its own tables. The custom role `scorerTableWriter` stays soft-deleted for 7-37 days after `destroy`; re-creating it in that window needs `gcloud iam roles undelete scorerTableWriter --project=synthea-readmission-risk`.
9. **Check for leftovers:** `gcloud storage buckets list --project=synthea-readmission-risk`, `gcloud dataproc batches list --region=us-central1 --project=synthea-readmission-risk`, `gcloud compute instances list --project=synthea-readmission-risk`, `gcloud compute disks list --project=synthea-readmission-risk`, `gcloud compute instance-templates list --project=synthea-readmission-risk`, `bq ls --project_id=synthea-readmission-risk` (later slices), `gcloud iam service-accounts list --project=synthea-readmission-risk`.
10. The WIF pool stays soft-deleted for 30 days; re-creating `github` in that window needs `gcloud iam workload-identity-pools undelete github --location=global --project=synthea-readmission-risk`.
11. The final stop: `gcloud billing projects unlink synthea-readmission-risk`, or delete the project.
