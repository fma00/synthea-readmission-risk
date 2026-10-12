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
| `make score` | Scores run date `20250630` with the champion bundle (`deploy/champion.json`); override `RUN_DATE`, `GOLD_DIR`, extra flags in `SCORE_ARGS` (e.g. `--bundle bundles/<id>`) |
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

**V-post (after v2 merges into `main`; open until then).** (1) `gh workflow run smoke.yml --ref main`: green, and the log lists the two bundle prefixes. (2) The negative proof: on a throwaway branch that contains `smoke.yml`, `gh workflow run smoke.yml --ref <branch>`: `auth` succeeds and "List the bundle bucket" fails with the **STS** refusal by the attribute condition (`sts.googleapis.com`, `unauthorized_client`); an IAM denial, a context or a network error does not count. Record both run URLs in the design note and CLAUDE.md. Not proven by these runs: that the `workflow_ref` binding refuses another workflow file on `main` (first exercised in slice 4), and the `event_name` clause (every workflow that can reach the exchange is dispatch-only).

## Scoring into BigQuery (v2 slice 4)

Design and live results: `notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md`. Every command below starts from `export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk`; impersonated `gcloud` calls also need `CLOUDSDK_BILLING_QUOTA_PROJECT=LEGACY`. The Python CLIs take `--impersonate-service-account` and handle the quota project themselves.

**The gold table in GCS (once per gold table).** The scorer reads gold from `gs://synthea-readmission-risk-gold/full_run_2b/` (downloaded to a temporary directory, then read by the same loader as a local copy). Upload the Parquet parts and the metadata as yourself (the gold bucket is not create-only), then check the bytes:

```sh
export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk
gcloud storage cp data/gold/full_run_2b/part-*.parquet data/gold/full_run_2b/_gold_metadata.json gs://synthea-readmission-risk-gold/full_run_2b/
# Byte check: GCS stores md5 (base64), not sha256.
for f in data/gold/full_run_2b/part-*.parquet data/gold/full_run_2b/_gold_metadata.json; do
  remote=$(gcloud storage objects describe "gs://synthea-readmission-risk-gold/full_run_2b/$(basename "$f")" --format='value(md5_hash)')
  [ "$remote" = "$(openssl md5 -binary "$f" | base64)" ] && echo "equal $(basename "$f")" || echo "DIFFERENT $(basename "$f")"
done
```

**Score and prove the data tests.** `make score-bq RUN_DATE=20250630` and `make m5-check RUN_DATE=20250630` ([scoring.md](scoring.md#scoring-into-bigquery-v2-slice-4)). Read results back as the scorer with a partition filter, for example `bq query --use_legacy_sql=false --maximum_bytes_billed=104857600 "SELECT status, COUNT(*) FROM serving.scoring_runs WHERE started_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY) GROUP BY status"`.

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

It uploads the jar, JDK, Spark zip and generation script to `gs://synthea-readmission-risk-artifacts/sha256/<hex>/` (create-only), creates the VM, checks it (labels, startup script, spot, DELETE, run duration, no external IP, its own service account, the disk label), waits for `gs://synthea-readmission-risk-raw/v2/pilot_20k/_DONE`, deletes the VM and **proves** no labelled instance or disk remains. Exit codes: 0 done, 1 refused, 2 failed, 3 a labelled instance or disk remains (the message prints its delete command), 130 interrupted. Timings and peak memory are saved under `data/pilot/pilot_20k/`. A failed or preempted run leaves a partial raw prefix that the next attempt refuses; delete it first: `gcloud storage rm -r gs://synthea-readmission-risk-raw/v2/pilot_20k/`.

**Big Join on Dataproc Serverless 3.0** (`maxExecutors=7`, standard tier, ttl 2 h, the `dataproc-runtime` service account; never while a generation VM exists, since both draw on the 32-vCPU `CPUS_ALL_REGIONS` quota):

```sh
make bigjoin-batch RUN=pilot_20k BATCH_ARGS=--dry-run   # no gcloud call
make bigjoin-batch RUN=pilot_20k
```

It checks that the raw run matches `prereg.yaml` (P7), writes gold to `gs://synthea-readmission-risk-gold/v2/pilot_20k/` with metadata version 2 (the Spark-computed `spark-rowhash-v1` fingerprint, the raw run's generation summary and the build provenance), and records wall-clock and DCU-hours (`runtimeInfo.approximateUsage.milliDcuSeconds / 3.6e6`) in `data/pilot/pilot_20k/batch_<id>.json`. A failed batch may leave partial output that the next submit refuses; delete it first: `gcloud storage rm -r gs://synthea-readmission-risk-gold/v2/pilot_20k/`. Ctrl-C leaves the batch running (bounded by its ttl); cancel it with `gcloud dataproc batches cancel <id> --region=us-central1`.

**Proving nothing is left running:**

```sh
gcloud compute instances list --filter=labels.purpose=synthea-generation
gcloud compute disks list --filter=labels.purpose=synthea-generation
gcloud dataproc batches list --region=us-central1 --filter="state=RUNNING OR state=PENDING"
```

All three must print nothing.

**Lessons from the first live run (slice 5b, 2026-10-12; details in the slice-5 note's Results).** Runtime 3.0 rejects `--staging-bucket` (the submitter no longer passes it). Its pre-flight permission check requires `dataprocrm.nodePools.update`, which no predefined role grants; `dataproc-runtime` uses the custom role `dataprocRuntimeWorker` (`roles/dataproc.worker` minus `storage.*`, plus that permission), so after changing that role wait about 8 minutes before submitting, or the batch fails in validation (no cost). Policy Troubleshooter cannot check object-level or object-prefix-conditioned storage access: pass the bucket as the resource and the object as `--resource-name=projects/_/buckets/<b>/objects/<o> --resource-service=storage.googleapis.com --resource-type=storage.googleapis.com/Object`, and expect a prefix condition to evaluate `false` regardless; prove such grants with a live probe instead.

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
5. **Destroy the rest:** `terraform -chdir=infra destroy`. Raw and gold are deleted with their contents; the CHF 5 budget, both service accounts, all IAM members, the pool and the provider go; the APIs stay enabled. The final state is written to the state bucket, which still exists.
6. **Delete the state bucket last:** `gcloud storage rm --recursive --all-versions "gs://synthea-readmission-risk-tfstate/**"`, then `gcloud storage buckets delete gs://synthea-readmission-risk-tfstate`. `infra/` needs a new bootstrap after this.
7. **Slice 5 resources:** no generation VM or Dataproc batch may be running (the three commands above print nothing). Raw and gold `v2/` objects go with their buckets in step 5; the artifacts and Dataproc staging buckets are `force_destroy` and go there too. The `spark` network, subnet and firewall, the instance template and the `synthea-generator` and `dataproc-runtime` service accounts are destroyed by step 5.
8. **BigQuery (v2 slice 4):** set `deletion_protection = false` on `google_bigquery_table.predictions` and `.scoring_runs` and apply; `serving` does not delete its contents on destroy, so empty it first (`bq rm -r -f -d serving` as the owner, after exporting anything you want to keep); `scratch` deletes its own tables. The custom role `scorerTableWriter` stays soft-deleted for 7-37 days after `destroy`; re-creating it in that window needs `gcloud iam roles undelete scorerTableWriter --project=synthea-readmission-risk`.
9. **Check for leftovers:** `gcloud storage buckets list --project=synthea-readmission-risk`, `gcloud dataproc batches list --region=us-central1 --project=synthea-readmission-risk`, `gcloud compute instances list --project=synthea-readmission-risk`, `gcloud compute disks list --project=synthea-readmission-risk`, `gcloud compute instance-templates list --project=synthea-readmission-risk`, `bq ls --project_id=synthea-readmission-risk` (later slices), `gcloud iam service-accounts list --project=synthea-readmission-risk`.
10. The WIF pool stays soft-deleted for 30 days; re-creating `github` in that window needs `gcloud iam workload-identity-pools undelete github --location=global --project=synthea-readmission-risk`.
11. The final stop: `gcloud billing projects unlink synthea-readmission-risk`, or delete the project.
