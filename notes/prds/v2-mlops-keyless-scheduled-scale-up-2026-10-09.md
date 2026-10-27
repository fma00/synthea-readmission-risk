# PRD: v2 — MLOps-first keyless, scheduled scale-up

**Date:** 2026-10-09
**Workflow:** `/eg-prd` (Standard depth, web research, 5 research goldfish)
**Seed:** `notes/eg-brainstorms/v2-scope-and-v1-closeout-2026-10-09.md` ("Recommended v2 synthesis", chosen by the owner)
**Supersedes in part:** `notes/prds/big-join-architecture-lock-in-2026-09-15.md` and the CLAUDE.md Architecture rules listed under "Locked rules this PRD changes"
**Status:** Approved by the owner 2026-10-09 (open questions OQ1–OQ6 resolved in review; NHV checklist still open)
**Revision 2026-10-09 (post-approval, owner request):** brought in line with five commits that landed on `main` in parallel with the PRD session (`cb8163e`, `f5e78fe`, `436e9bd`, `832898d`, `c2526ab`; local, not yet pushed). Affected: FR0.1 (license metadata, README polish, CLAUDE.md version note done), FR1 (done), FR2 (partly done), FR4 (Python version is a known gap now named in CLAUDE.md), slice 1, the branch plan, and the CLAUDE.md follow-up list. No decision changed.
**Revision 2, 2026-10-09:** brought in line with two more local commits:
- `91cb37c`: guard 14 records `python_version` at training and refuses a Python major.minor mismatch before unpickling, with no override. The 3.12-trained reference runs are refused under 3.14.
- `76a991b`: GCP project created, a CHF 20/month budget alert, us-central1 quotas read, and no increase requested. `CPUS_ALL_REGIONS` = 32 is the binding limit, so `maxExecutors` = 7.

Affected: the guard count (13 → 14), FR0.1, FR4, FR5, FR13, FR14, FR16 and two risk rows. No decision changed. The $5 early-warning budget from OQ4 is **not yet created**.
**Revision 3, 2026-10-10: v1 closeout finished** (owner statement). Brought in line with five commits now on `origin/main`, with `v2` fast-forwarded to it:
- `4f8b016` and `4fad5a2`: the label-horizon purge. Gold gains a 24th column, `label_horizon`. v1 was rebuilt and retrained on **Python 3.14.7** into the MLflow experiment `readmission-risk-v1`, with identical metrics and predictions.
- `51f1d62`: the supply-chain baseline.
- `0e29df6`: the README 60-second tour and a real refusal.
- `2242649`: the README links the latest release.

Also verified on GitHub: the `v1.1` release, and the repo setting "require SHA pinning". Four planned closeout items were not done and are **carried into v2 slices by owner decision**: Makefile + pre-commit (slice 1), the CHF 5 budget (slice 3), the dbt → `ASSERT` CLAUDE.md amendment (the CLAUDE.md update after this PRD is committed), and the FR0.3 portable export (slice 6). Affected: FR0.1–FR0.3, FR5, FR12, FR16, the risk table, the slices, the branch plan, and a new CI note for the `v2` branch. No decision changed.

Facts marked **(verified)** were checked against the repo or a primary source in this session; **(extrap)** is arithmetic; **(NHV)** needs human verification before it is relied on.

---

**Amendment 2026-10-18 (v2 slice 7, owner decisions at its Step 0; `notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md`):**
- **D1, release scope:** **v2.0 = slices 1-7 + M3.** Slice 8 (FR22 dashboard, FR23 model card and docs overhaul, the FR14 BigQuery gold direct-write + connector MERGE test, and FR21's dashboard half) **moves to a v3.0 scope**; the requirements below that name the dashboard are kept as written and marked "v3.0" where they occur. `v2` merges into `main` during slice 7, before the burn-in (scheduled workflows and the WIF condition need `main`); V-post, L-post, the burn-in and M3 run on `main`; the `v2.0` tag follows M3. This replaces the 2026-10-11/12 plan to merge only at v2.0.
- **D2, champion:** the 200k logistic regression (`569fdab6…`) is promoted by a disclosed owner override of the pre-registered gate's REFUSE (`deploy/PROMOTIONS.md`), because on the 10k population a 13-week cohort never reaches the 20-positive monitoring floor. `prereg.yaml` is unchanged; the lookup still wins. M4's "real promotion PR with gate table" is this PR, with its override stated.
- **D3, ledger (OQ3):** the PRD's fallback is taken: job B commits to a dedicated `ledger` branch; ruleset 24876876 keeps no bypass actors.
- **FR21 in v2:** "stale since" is shown on the README as a "stale after <time>" badge (a static page cannot compare with the current time); the dashboard's live "stale since" is v3.0.

**Amendment 2026-10-20 (owner decisions, made after observing the scheduled burn-in; `notes/eg-new-feature/monitoring-burn-in-timer-and-weekly-retry-2026-10-20.md`):** GitHub's scheduler dropped almost every slot for this repository (the burn-in cron started 3 runs in 24 slots on 2026-10-19/20; a permission-free probe started 7 in about 120), so the design no longer depends on slot reliability.
- **E1, burn-in trigger:** the accelerated burn-in is dispatched by a timer on the owner's laptop (`make burn-in-timer`, dispatch input `cadence=burn_in`, one tick at a time, any hour), not by a cron.
- **E2, M3 amended, after the outcome was observed:** M3 now counts ≥ 5 consecutive green *timer-dispatched* burn-in runs, then ≥ 1 green *scheduled* weekly tick. **The bar is lowered in one respect:** a laptop timer is the owner, so the burn-in no longer evidences running without the owner (FR19); only the scheduled weekly tick still does. The three green scheduled burn-in ticks of 2026-10-19/20 stay in the history and do not count.
- **E3, weekly retry slots (effective with Part 2 of the design):** the weekly cron becomes hourly Monday to Wednesday (`17 * * * 1-3`, 72 slots). This relaxes the "daily cron" Out line and OQ1: at most one scheduled weekly tick succeeds per week, barring owner dispatches or re-runs; the other slots are retries that do nothing once the week's tick has run.
- **E4, skip and retry cap (Part 2):** a scheduled run skips (no row, stays green) once this week's weekly tick succeeded or 3 weekly attempts failed or were refused.

## Executive summary

v2 turns the v1 local demo into a small system that runs by itself on GCP without any long-lived credential in CI. In order, it delivers:

- an immutable, validated model bundle with promotion by pull request;
- Terraform-managed infrastructure reached through Workload Identity Federation;
- a 200k-patient Big Join on Dataproc Serverless;
- a retrained champion judged against a pre-registration committed before the data existed;
- a frozen-v1 external validation;
- a weekly delayed-label monitoring tick whose evidence (run ledger, badge, GitHub issues) is visible on GitHub without opening the dashboard.

It is built for hiring reviewers in MLE/MLOps roles first and data-engineering roles second. The timing follows from v1 being public and finished, and from the next application round targeting MLOps roles, where v1 is weakest.

## Problem statement

v1 shows sound ML methodology (leakage-controlled split, calibration, an honest "models ≈ reason-code lookup" result) but no operations. Everything runs on one laptop, models are trusted through a local MLflow SQLite file, nothing runs on a schedule, nothing is monitored, nothing is in the cloud, and the documented scale-up path ("only the base path changes") is false in the code (verified: `big_join.py:105,117-119,402-408`, `gold_metadata.py:127-143`, `models/data.py:135`, `scoring/store.py:98-118`, and argparse `type=Path`, which collapses `gs://` to `gs:/`). The 10k test window holds only 41 positives, so every v1 metric has wide intervals. An MLOps reviewer currently has nothing to check that shows the author can deploy, gate, monitor and roll back a model.

## Target users

| Persona | Priority | Job to be done | Moment of need |
|---|---|---|---|
| MLE / MLOps hiring reviewer | **1** | "Show me, in 2–3 minutes and without running anything, that this person can ship and operate a model safely." | Opens the GitHub repo from a CV link. |
| Data-engineering reviewer | 2 | "Show me a real distributed job, IaC, a warehouse with data tests, and cost control." | Same moment; reads the README and the architecture diagram. |
| Data-science reviewer | 3 (supporting) | "Are the results honest and statistically sound?" | Follows the results/model-card link. |
| Clinical team lead (simulated) | Demo persona only | "Which of this week's discharges should my team call first?" | The Streamlit call sheet. The data is synthetic and not for clinical use. |

**Reviewer payoff (G2), as a priority order for cuts:**
1. Promotion-gate evidence: a `champion.json` PR with the metric table against the pre-registration, plus one deliberately bad model visibly refused.
2. A scheduled-run badge plus the run ledger.
3. The model card plus the pre-registration with results reported against it.
4. (Cheap) an upgraded architecture diagram with deep links.

## Current state

- **Reuse:**
  - the Big Join logic, the label rules and `_gold_metadata.json`;
  - training (LR + calibrated XGBoost, chronological patient-disjoint split, patient-grouped calibration folds, bootstrap CIs);
  - the scorer's pure functions (`selection`, `ranking`, `report`) and its injection seam, `score_run_date(config, *, loader, writer)` (`scoring/pipeline.py:70`);
  - the 14 guards in `scoring/runs.py::validate_run` as the *specification* of what a bundle manifest must carry. Guard 14, the Python major.minor check, was added in `91cb37c`;
  - the 38 `GUARD_CASES`;
  - the CI, the hash-locked lockfiles and the explicit ruff rule set.
- **Replace:**
  - MLflow as the scorer's source of truth, replaced by the bundle manifest (MLflow stays the experiment record);
  - `store.write_partition` as the production writer, replaced by a BigQuery load job (the local writer stays for tests and demos);
  - the pandas-side `gold_fingerprint`, replaced by a Spark-side fingerprint in `_gold_metadata.json`;
  - the hardcoded `local[*]` in `spark_session.py:17`, replaced by a configurable session.
- **Absent today:** Terraform, `google-cloud-*` packages, Streamlit, the `dashboard/` module, scheduled workflows, SHA-pinned actions, zizmor and Dependabot.
- **Docs tests that pin v1's public state and must change on purpose:**
  - `tests/test_docs.py` requires "Planned" status rows and ≥3 "not started";
  - it forbids the phrase `streamlit.app`;
  - it requires exactly three `docs/` pages and exactly one Mermaid block;
  - it requires the top-level CI permissions to be exactly `contents: read` (line 432).

## Proposed solution

Three phases inside a 6-week box (a ceiling, not a target), started only after the v1 closeout is complete (G14):

1. **Spine (weeks 1–2, on the existing 10k gold):**
   - Version strategy B.
   - The model bundle with the guards ported to its manifest.
   - Terraform + WIF + least-privilege service accounts.
   - One manually dispatched GitHub Actions run that scores 10k held-out discharges into BigQuery, records the run in `scoring_runs`, and passes `ASSERT` data tests.

   If everything after this slips, the repo still shows IaC, keyless CI, a validated artifact and BigQuery serving.
2. **Scale-up (weeks 3–4):**
   - Commit `prereg.yaml`.
   - A `gs://`-ready Big Join.
   - A measured 20k pilot.
   - A 200k Synthea run on a spot VM.
   - The Big Join on Dataproc Serverless runtime 3.0 (gold to GCS Parquet + metadata, and direct-written to BigQuery).
   - Retrain.
   - A promotion-gate PR.
   - Frozen-v1 external validation.
3. **Operations (weeks 5–6):**
   - The weekly delayed-label monitoring tick with GitHub issues on breach.
   - The run ledger, badge and job summary.
   - The Streamlit call sheet (heartbeat, honesty strip, provenance).
   - The model card and an updated `docs/results.md` and README.

**Locked rules this PRD changes (deliberate decisions, recorded so they are not mistaken for drift):**

| Locked rule (CLAUDE.md) | v2 decision | Why |
|---|---|---|
| Python 3.12 pin; "3.14 support only in PySpark 4.2.0" | **Strategy B:** Python 3.14 + PySpark 4.1.3 for local, CI, training, scoring and dashboard; the Big Join on Serverless runtime 3.0 (Python 3.12 / Spark 4.0.1) behind a CI leg {3.12 + pyspark 4.0.1}. The 4.2.0 claim is wrong: support landed in 4.1.0 (SPARK-54286, verified). **The Python half already landed in `c2526ab`** (3.14.7 locally, `requires-python = ">=3.12,<3.15"`, a 3.12 + 3.14 CI matrix, CLAUDE.md note corrected); the pyspark 4.0.1 leg is still open (FR2). Slice 1 (2026-10-11): the matrix became a 3.14 full suite plus the {3.12, pyspark 4.0.1} `tests/pipeline` job; the 3.12 full-suite leg was removed by owner decision. | Newest supported Python where we control the runtime; compatible where Google controls it. |
| "BigQuery holds only the gold table" | BigQuery holds gold, `predictions`, `scoring_runs` and `monitoring` (and the dashboard's call-sheet view). Raw and intermediate data stay in GCS. | Serving and telemetry need queryable tables; everything stays far inside the free tier (extrap: < 1 GiB). |
| "Adopt dbt-bigquery once there's more than one BigQuery table" | **Amended:** no dbt. Data tests are native BigQuery `ASSERT` scripts run by a Python step. | Fewer dependencies and no second toolchain to lock. The cost is no dbt signal and hand-written lineage. |
| Scoring loads models via MLflow (`load_logged_model`) | An immutable bundle + `manifest.json` is the scorer's source of truth; the scorer no longer imports mlflow. | Scheduled runs cannot reach a laptop SQLite store. |
| "No orchestrator — manual trigger or literal crontab" | The GitHub Actions `schedule` + `workflow_dispatch` is the orchestrator. | Public run history; no standing infrastructure. |
| Streamlit service-account key in Secrets | Kept as the **single documented exception** to "keyless" (G6), with stricter controls (NFR-S4). | Community Cloud cannot use WIF. |

## Scope

**In:**
- Version strategy B, with two lockfiles compiled under two interpreters.
- Model bundle + manifest + `deploy/champion.json`, with promotion by PR and rollback by `git revert`.
- Terraform for: the state bucket (bootstrap), data and bundle buckets, BigQuery datasets and tables, service accounts and IAM, the WIF pool and provider, budget alerts, and the custom query quota.
- A supply-chain baseline as a hard prerequisite for any `id-token: write` (done during the closeout).
- A `gs://`-ready Big Join, a configurable Spark session, the Spark fingerprint, and a 20k pilot.
- 200k generation on a spot VM; Dataproc Serverless runtime 3.0; gold to GCS + BigQuery.
- A pre-registration committed before generation; retraining at 200k; the promotion gate; frozen-v1 external validation.
- The weekly monitoring tick; `ASSERT` data tests; GitHub issues on breach; the run ledger, badge and job summary.
- The Streamlit Community Cloud call sheet; the model card; README and docs updates; the `v2.0` tag.

**Out:**
- hosted MLflow;
- Vertex AI;
- a Cloud Run dashboard;
- `terraform apply` in CI;
- a daily cron (an accelerated cron is allowed only for the one-afternoon burn-in, FR19); **amended 2026-10-20 (E3, owner decision): hourly weekly *retry slots* Monday to Wednesday are In; the tick cadence stays weekly, and the burn-in is dispatched by a laptop timer, not a cron**;
- feature-drift statistics;
- scheduled (automatic) retraining;
- hyperparameter tuning;
- SHAP;
- label v2;
- as-of scoring of unlabelled discharges;
- pandas 3;
- dbt;
- automatic re-scoring on rollback;
- Bucket Lock / Object Retention Lock;
- an automated keep-alive workflow.

## User stories

- As an **MLOps reviewer**, I open the README and within one screen see a scheduled-run badge, the date of the last tick, and links to a promotion PR that *refused* a bad model, so I believe the system is operated, not just built.
- As an **MLOps reviewer**, I click through to the run ledger and see weekly entries, each linked to an Actions run, a bundle id and data-test results, so I can check that the claims are real.
- As a **DE reviewer**, I see Terraform, a Dataproc batch with recorded runtime, DCU-hours and cost, and BigQuery tables with data tests, so I see production data plumbing at 200k scale within a stated budget.
- As a **DS reviewer**, I read the pre-registration and the results against it (including whether the lookup still wins and how frozen v1 did on new data), so I can judge whether the evaluation was honest.
- As the **owner**, a failed or skipped tick opens a GitHub issue and turns the dashboard heartbeat stale, so nothing fails silently.
- As the **owner**, a bad champion is rolled back with one `git revert`, so recovery is a reviewed, recorded change.
- As the **simulated team lead**, I open the call sheet and see this simulated week's Top-N discharges with a freshness line and a clear synthetic-data notice.

## Functional requirements

Each requirement is testable; slice designs refine them.

**Prerequisites (v1 closeout, before any v2 work — G14). Status 2026-10-10: finished**, with four items carried into v2 slices (marked **→ slice N** below).
- **FR0.1** The whole closeout list in the brainstorm is done, including:
  - the `v1.0` GitHub release. **Done:** `v1.0` (2026-10-09) and `v1.1` "v1.0 plus post-release hardening" (2026-10-10, latest);
  - README polish. **Done:** `cb8163e` (v1.0 marked, roadmap framed as v2) and `f5e78fe` (reliability figure, sample Top-N table, `reporting/` package, 66 tests). Then `0e29df6` added the 60-second reviewer tour (7 stops) and a real scorer refusal, with a docs test that regenerates the refusal from the code, and `2242649` made the release link follow `releases/latest`. The repo About text and 10 topics are set (verified). **Not verified:** a custom social-preview image (none is set, `usesCustomOpenGraphImage: false`) and the profile pin. Both are cosmetic and not carried as requirements;
  - `pyproject.toml` license metadata. **Done:** `436e9bd` (SPDX `MIT`, `license-files`);
  - CLAUDE.md corrections. **Version note done** in `c2526ab`. **The dbt → `ASSERT` amendment → carried** into the CLAUDE.md update that follows committing this PRD (see Out-of-scope follow-ups). CLAUDE.md still states the old "adopt dbt-bigquery once >1 table" rule;
  - pushing the closeout commits to `origin/main`, then CI green on both matrix legs. **Done:** `main` = `origin/main` = `2242649`, and the push run is green;
  - the leakage-margin assertion in `models/split.py` (a test fails if any training row's label-classifying record is dated on or after `test_start_date`); **amended 2026-10-10:** implemented as a label-horizon **purge + post-condition** (owner decision, see `notes/eg-new-feature/label-horizon-purge-2026-10-09.md`, commit `4f8b016`). It never fails on data. `n_dropped_label_horizon_purge` is reported and should be pre-registered as a reported quantity;
  - the GCP project, budget alerts and quota check. **Mostly done in `76a991b`** (commands in `docs/setup.md`):
    - project `synthea-readmission-risk` created, with billing linked;
    - a **CHF 20/month** alert-only budget, at 50/90/100% of actual spend and 100% of forecast. The billing account's currency is CHF, so this PRD's "$" amounts are approximate equivalents;
    - us-central1 quotas read: `CPUS` 200, `CPUS_ALL_REGIONS` 32, `DISKS_TOTAL_GB` 4,096. No increase was requested, because the job fits the defaults when capped (FR14).

    **The CHF 5/month early-warning budget (OQ4) → slice 3:** Terraform creates it next to importing the CHF 20 budget. **Done in slice 3 (2026-10-11, `notes/eg-new-feature/v2-slice3-terraform-wif-2026-10-11.md`):** `google_billing_budget.early_warning` (CHF 5, 100% actual and forecast) created; the CHF 20 budget imported with no change.
  - a `Makefile` (targets: setup, lint, test, train, score, tf-validate) and **pre-commit hooks** running ruff and zizmor (OQ6). **→ slice 1** (not done in the closeout). **Done in slice 1 (`notes/eg-new-feature/v2-slice1-tooling-and-dataproc-runtime-leg-2026-10-11.md`);** tf-validate → slice 3; a `hooks` target was added.
- **FR0.2** Supply-chain baseline (G17), merged before any workflow holds `id-token: write`. **Done in `51f1d62`** (design: `notes/eg-new-feature/ci-supply-chain-hardening-2026-10-10.md`), except the two rules that only bite once `id-token` workflows exist (they become review checks for slices 3–7):
  - every `uses:` pinned to a full SHA with a `# vX.Y.Z` comment;
  - `dependabot.yml` for `github-actions`;
  - zizmor running in CI with zero findings (a separate `audit-workflows` job, **online** audits, hash-locked from the dev lockfile; Dependabot has a 7-day cooldown and groups minor+patch updates);
  - `persist-credentials: false` on checkout;
  - no `pull_request_target`;
  - `id-token: write` only at job level, on `schedule`/`workflow_dispatch` jobs;
  - GitHub's repo-level "require SHA pinning" policy enabled (verified: `sha_pinning_required: true`).

  Side effects of `51f1d62` that matter for v2:
  - **Push CI now runs only on `main`.** Other branches get CI only through a pull request. **Pushes to `v2` therefore run no CI until a PR `v2 → main` is open.** Open a draft PR early and keep it open for the whole of v2.
  - **No branch ruleset exists yet** (the note names it as a follow-up). OQ3's ledger design (an Actions bypass on a `main` ruleset) presupposes one, so slice 7 creates the ruleset first.
  - **Three open Dependabot PRs move to new major versions:** `checkout` v7, `setup-java` v6 and `setup-python` v7. Their CI is green, but merge or close them deliberately before slice 3 adds new workflows, so v2 workflows start from one set of pins.
- **FR0.3 → slice 6** (not done in the closeout; moved next to FR16, which it serves). A one-time portable export of the v1 models: per-fold XGBoost UBJ boosters, the sigmoid calibrator `a_`/`b_` values as JSON, and the preprocessing parameters (or skops). A parity test shows a maximum absolute difference of 0 against the cloudpickle predictions on the v1 test rows. This protects the G8 must-have against later library upgrades. Since `4fad5a2`, "the v1 models" means the 3.14.7-trained `readmission-risk-v1` runs. **Slice 6c (2026-10-17, `notes/eg-new-feature/v2-slice6c-portable-v1-export-2026-10-17.md`):** done locally. Format: UBJ boosters + JSON parameters read by an own numpy predictor (not skops), shipped as manifest schema-2 bundles (`fb44c38910b4…` LR, `bf66d1cb272b…` XGBoost) that also store the parity reference; max abs diff 0.0 on the 1,539 v1 test rows (+ 325 probe rows) for both models; the scorer accepts schema 1 and 2 and only validates the portable files; champion unchanged. Both bundles uploaded to the private bundle bucket and verified from GCS (2026-10-17). Cross-environment (2026-10-18): the portable form equals the pickle exactly on macOS arm64, Linux arm64 and Linux x86-64; the stored macOS reference re-checks bit-exactly only on macOS (Linux differs by ≤ 2.6e-9, the pickle included), so the FR16 fallback needs a decision (tolerance via /eg-prd, a Linux reference, or macOS only) before it is built.

**Versions (G3)**
- **FR1** Local, CI, training, scoring and the dashboard use Python 3.14 + PySpark 4.1.3. `requires-python` and `.python-version` are updated. **Done in `c2526ab`** (`.python-version` 3.14.7, `requires-python = ">=3.12,<3.15"`; the unchanged lockfiles, compiled on 3.12, install with hashes on 3.14.7 and the full suite passes). It came with `832898d`: `make_spark_session` defaults `PYSPARK_PYTHON` to the driver's `sys.executable`, fixing `PYTHON_VERSION_MISMATCH` under a 3.14 driver. The dashboard part applies when FR22 is built.
- **FR2** The cloud runtime's Spark version is tested in CI. **Partly done:** `c2526ab` runs the full suite as a 3.12 + 3.14 matrix, but both legs use the lockfile's `pyspark==4.1.3`, so CI proves Python 3.12 compatibility, not Spark 4.0.1 compatibility (CLAUDE.md says so too). **Remaining:** a pinned {Python 3.12, `pyspark==4.0.1`} dependency set, hash-locked and compiled under a 3.12 interpreter, plus a CI leg running `tests/pipeline` under it. The slice decides whether that leg replaces the current 3.12 full-suite leg or is added next to it. ~~Any recompile of the main lockfile is verified by both Python legs.~~ Superseded in slice 1 by owner decision (2026-10-11): the 3.12 full-suite leg was replaced by the {3.12, pyspark 4.0.1} `tests/pipeline` job, so a main-lockfile recompile is verified on 3.14 only. **Done in slice 1 (`notes/eg-new-feature/v2-slice1-tooling-and-dataproc-runtime-leg-2026-10-11.md`):** `requirements-dataproc-3.0.txt` (pyspark 4.0.1, pandas 2.3.3, pyarrow 19.0.1, numpy 2.1.3, compiled under 3.12) and CI job `pipeline-dataproc-runtime` (Python 3.12, Java 21, environment asserted).
- **FR3** The Spark job's code depends only on libraries that Dataproc runtime 3.0 bundles. It ships as a zip via `--py-files`, with no custom container. **Done in slice 1 (`notes/eg-new-feature/v2-slice1-tooling-and-dataproc-runtime-leg-2026-10-11.md`)** for the dependency rule, enforced by `tests/pipeline/test_runtime_dependencies.py`; zip build → slice 5.

**Model bundle and promotion (G7, G19)**
- **FR4** Training exports a bundle to `gs://<bundles>/<bundle_id>/`, where `bundle_id` is a content hash. The bundle contains:
  - the model in cloudpickle **and** a portable form (UBJ/JSON or skops);
  - `manifest.json`, carrying every fact the 14 guards check today, mapped one to one with none dropped silently:
    - model name;
    - label definition;
    - gold fingerprint (new definition, FR12);
    - `reference_date` and window;
    - library versions **plus the Python version**. Since `91cb37c`, training tags runs with `python_version` and guard 14 refuses a major.minor mismatch before unpickling, with no override. The manifest carries the same fact and the same rule, including guard 14's refusal when the version is missing, unparseable, or the tag and `MLmodel` disagree;
    - feature columns and dtypes (signature);
    - split assignment and its hash;
    - `test_start_date`;
    - git commit and dirty flag;
    - the MLflow run id (provenance);
    - the sha256 of each file.

  **Slice 2 (2026-10-11, `notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md`):** done for the export command (`scripts/export_bundle.py`, `make export`, from an existing MLflow run; a non-FINISHED or deleted run is exported with a warning and refused by the scorer) and the manifest (all 14 guard facts, the split assignment file and its sha256, `test_start_date`, git commit and dirty flag, the MLflow run id, every file's sha256); training-time export and the portable form (a null `portable` slot; manifest schema 2) → slice 6; `gs://` → slice 3 (local `bundles/` behind a storage seam until then); fingerprint definition `pandas-csv-sha256-v1` recorded in the manifest until FR12.
- **FR5** The scorer validates the manifest and the file sha256s before any unpickling, and never imports `mlflow`. All guard cases, including those `91cb37c` added for guard 14, are ported to manifest fixtures. The reference runs are now `readmission-risk-v1`, trained on 3.14.7 (`4fad5a2`), so the spine's first champion bundle can be exported from them directly. The 3.12-trained `readmission-risk-2b` runs are history only, and guard 14 refuses them under 3.14. **Kill trigger:** if more than 20% of the scoring tests need semantic changes, the manifest becomes an additional layer over the existing MLflow guards. **Slice 2 (2026-10-11, `notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md`):** done; the scorer imports neither mlflow nor pyspark; 23 of 140 scoring tests (16.4%) changed semantically, under the kill trigger.
- **FR6** `deploy/champion.json` names `{bundle_uri, sha256}`. The scheduled tick reads it from `main`. Promotion is a PR that edits it, and the PR carries the gate table (FR15) as a check output. Rollback is `git revert` of that PR (G19); partitions already scored are not re-scored. **Slice 2 (2026-10-11, `notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md`):** `deploy/champion.json` and its reader done (pinning the v1 logistic-regression bundle); the gate table → slice 6. **Slice 4 (2026-10-11, `notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md`):** the champion names the `gs://` copy (same pin, commit `26a534b`).
- **FR7** Bundles cannot be overwritten. Three controls enforce this:
  - the writer service account has `storage.objectCreator` only;
  - uploads use `ifGenerationMatch=0`;
  - the scorer's service account has read-only access.

  Bucket Lock is explicitly rejected because it is irreversible and blocks teardown.

  **Slice 2 (2026-10-11, `notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md`):** local no-overwrite done (atomic temp-dir rename, idempotent identical re-export, any other existing entry refused, read-only files); the three GCS controls → slice 3. **Slice 3 (2026-10-11, `notes/eg-new-feature/v2-slice3-terraform-wif-2026-10-11.md`):** the bucket and IAM controls are live: `bundle-writer` has `objectCreator` only on the versioned bundle bucket, uploads use `ifGenerationMatch=0` (through the JSON API: `gcloud storage cp` needs `objects.get`), and live check V5 measured a refused overwrite (412 with the precondition, 403 without; object unchanged). The scorer's read-only service account and the Python GCS writer → slice 4. **Slice 4 (2026-10-11, `notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md`):** done: `bundles/gcs.py` (reader; create-only writer, `if_generation_match=0`, manifest last, no retries), proven live (a re-upload refused with 412, no object GET before it; a fresh create read back and scored identically); `scorer` has `objectViewer` only.

**Infrastructure and identity (G4, G16, G17)**
- **FR8** Terraform manages:
  - a GCS state bucket (bootstrap: create with a local backend, then `-migrate-state`; versioning on, public access prevention enforced);
  - the raw, gold and bundle buckets in **us-central1**, with a lifecycle rule deleting `raw/` after **90 days** (OQ5; covers FR16 and Big Join re-runs; afterwards regenerate, ≈ $1, content-deterministic given seed, clinician seed, end date and JVM time zone, except row order and, across machines, 45 encounter-less QALY/DALY/QOLS rows of observations.csv; the gold fingerprint is the reproducible unit (slice 5));
  - the BigQuery datasets and tables;
  - the service accounts and IAM;
  - the WIF pool and provider;
  - the budget alerts;
  - the dashboard service account's custom query quota.

  It is applied from the laptop; CI runs only `terraform fmt -check` and `validate`. Plain resources are used, not the `gh-oidc` module (its defaults have no condition and map by name). **Slice 3 (2026-10-11, `notes/eg-new-feature/v2-slice3-terraform-wif-2026-10-11.md`):** done and applied for the state bucket (bootstrap script), the raw/gold/bundle buckets (raw: 90-day lifecycle), two service accounts (`gha-smoke`, `bundle-writer`) with additive IAM, the WIF pool and provider, and both budgets; CI job `terraform-validate`; `make tf-validate`. BigQuery datasets and tables → slice 4; the dashboard SA and its query quota → slice 8. **Slice 4 (2026-10-11, `notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md`):** applied (15 added): datasets `serving` and `scratch`, tables `predictions` and `scoring_runs` (day-partitioned, partition filter required, deletion-protected), the `scorer` SA and the custom role `scorerTableWriter`.
- **FR9** The WIF provider's attribute condition uses numeric claims: `assertion.repository_id=='<id>' && assertion.repository_owner_id=='<id>' && assertion.ref=='refs/heads/main'`. If they turn out to be mappable, it also restricts `event_name` to `schedule`/`workflow_dispatch` (NHV). Each job impersonates its own least-privilege service account (scorer, monitor, Big Join submitter / Dataproc runtime, trainer). No service-account key exists anywhere in CI. **Slice 3 (2026-10-11, `notes/eg-new-feature/v2-slice3-terraform-wif-2026-10-11.md`):** the condition is the conjunction of the numeric repository and owner ids, `ref == refs/heads/main` and `event_name in ['workflow_dispatch', 'schedule']` (both claims mappable per GitHub's and Google's docs); `gha-smoke` is bound to `smoke.yml`'s `workflow_ref` only; later service accounts arrive with their jobs. Live WIF proof (V-post) runs after v2 merges into `main`.

**Scale-up (G10, G12, G15)**
- **FR10** `prereg.yaml` is committed (and its commit hash recorded) **before** the 200k generation. It is shaped after Hofman et al.'s Phase A, and Phase B is appended after validation. It contains:
  - the seed and `reference_date`;
  - the test-cutoff rule as a formula of `reference_date`;
  - the label definition;
  - metrics;
  - the patient-level paired bootstrap (B and seed);
  - the promotion rule (FR15);
  - the frozen-v1 metrics and acceptance criteria;
  - the monitoring alert rules (FR19);
  - an explicit "the lookup still wins" outcome and what is reported then.
- **FR11** The Big Join, gold metadata, data loader and CLIs accept `gs://` URIs. Every local-filesystem call listed under Current state is replaced by a filesystem-agnostic equivalent. `make_spark_session` takes its master and config from the caller. The local behaviour and the existing tests are unchanged.
- **FR12** The Big Join computes, over all 24 gold columns including `label_horizon` (`4f8b016`), an order-independent content fingerprint in Spark and writes it into `_gold_metadata.json` (`metadata_version` bumped). The fingerprint is:
  - computed over canonical per-row strings (sorted columns, explicit null marker, UTC ISO timestamps, fixed float format);
  - the row count plus two independent hash sums, accumulated in `decimal(38,0)` because ANSI mode overflows a `bigint` sum.

  The scorer guard compares against it. A fixture test proves the Spark and Python implementations agree.
- **FR13** Generation uses `exporter.csv.included_files` set to the 10 joined tables (extrap: ~44 GB instead of ~140 GB at 200k). It runs on a spot VM of **at most 32 vCPUs** (the measured `CPUS_ALL_REGIONS` quota, `76a991b`; it cannot overlap with the Dataproc batch) with `--instance-termination-action=DELETE`, `--max-run-duration`, an auto-delete disk and resource labels, and writes to GCS in the same region. A **20k pilot on the same VM shape and Dataproc config** precedes the 200k run and records generation wall-clock time, peak memory, Big Join wall-clock time and DCU-hours.
- **FR14** The Big Join runs on Dataproc Serverless runtime 3.0, standard tier, with `spark.dynamicAllocation.maxExecutors` explicitly capped at **7** (the default is 1000). A 4-vCPU driver plus 7 four-vCPU executors is 32 vCPUs, which fills the measured `CPUS_ALL_REGIONS` quota exactly; disk is 3,200 GiB of the 4,096 GiB `DISKS_TOTAL_GB` (`76a991b`). The batch must not run while the generation VM (FR13) is up, because both draw on the same 32-vCPU quota and `spark.sql.shuffle.partitions` set. It records the actual runtime sub-version and connector version in `_gold_metadata.json`, because sub-versions cannot be pinned. Gold is written as canonical GCS Parquet plus metadata, and direct-written to BigQuery (Storage Write API). A throwaway-table test first confirms connector 0.44.0's overwrite behaviour, which is a MERGE, on this runtime. **Slice 6a (2026-10-15, `notes/eg-new-feature/v2-slice6a-main-200k-and-checks-2026-10-14.md`):** the `main_200k` Big Join ran on runtime 3.0 with `maxExecutors=7` (30.6 min, 2.39 DCU-hours), never alongside the generation VM; gold is canonical GCS Parquet plus metadata. The direct-write to BigQuery and the connector MERGE test are **moved to slice 8** (owner decision 2026-10-14; the dashboard is their only consumer). Runtime facts are recorded as `runtime.*` keys of `build_provenance` (no metadata v3): the connector version (`spark-4.0-bigquery-0.44.0`) is recorded, the runtime sub-version label (`3.0.<n>`) is not exposed inside a Serverless batch. **Owner decision 2026-10-16:** the clause is met by the recorded component versions (Spark, Python, Java, BigQuery and GCS connectors) plus runtime `3.0`, which are what a patch release can change; the patch label itself is not required.
- **FR15** Retraining at 200k produces a challenger bundle. The gate refuses promotion unless the challenger beats **both** the current champion and the reason-code lookup on ROC-AUC (patient-level paired bootstrap, CI of the difference excluding 0), and its Brier score is **non-inferior**: the upper bound of the patient-level paired bootstrap CI of (challenger Brier − champion Brier) must be ≤ a margin fixed in `prereg.yaml` before the data exists (OQ2; e.g. 5% of the champion's Brier). One deliberately bad challenger is submitted and visibly refused (G2).
- **FR16** Frozen-v1 external validation runs in an evaluation mode separate from the scorer. The scorer's guards are never weakened or bypassed. The frozen v1 models are the `readmission-risk-v1` runs, retrained on **Python 3.14.7** in `4fad5a2` with metrics and test predictions identical to the 3.12 runs. So the evaluation mode runs under the default 3.14 environment with exactly the v1 sklearn/xgboost/numpy/scipy pins. A 3.12 environment is not needed. The FR0.3 portable export (slice 6) is the fallback once those pins move. It applies guard 14's major.minor rule itself, fails on `InconsistentVersionWarning`, and reports the pre-registered metrics on the 200k population.

**Operations (G9, G18, G20)**
- **FR17** Predictions are written to BigQuery `predictions`, partitioned by `run_date`, by a load job to `predictions$YYYYMMDD` with `WRITE_TRUNCATE`. Re-running a run date replaces its partition, keeping the v1 semantics. **Slice 4 (2026-10-11, `notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md`):** done with an amended mechanism (owner decision D5): the load job targets a staging table in `scratch`; after the data tests pass, one DML transaction replaces the run date's rows in `serving.predictions` (load and copy jobs into `serving` need dataset-level `tables.create`, which the scorer deliberately lacks, proven live). Replacement per run date measured live (L4b, L5).
- **FR18** Data tests are one `ASSERT` script per table, each assertion labelled `AS '<id>: <message>'`, with NULL treated as failure on purpose. They run in every tick, and pass/fail is recorded in `scoring_runs`. A seeded bad row visibly fails them. **Slice 4 (2026-10-11, `notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md`):** done: `predictions.sql` (10 assertions, gates publishing) and `scoring_runs.sql` (7, on the table's prior state); M5's recorded run drives every one of the 17 assertions to fail on its own seeded copy (`make m5-check`).
- **FR19** The weekly monitoring tick:
  - runs on an off-the-hour cron (e.g. `17 6 * * 1`) plus `workflow_dispatch`; **amended 2026-10-21 (E3/E4, Part 2): hourly retry slots `17 * * * 1-3`; the clock skips once the week's tick succeeded or 3 attempts failed**;
  - is preceded by an **accelerated burn-in** (OQ1): for one afternoon a temporary cron fires every 30 minutes off the hour (e.g. `7,37 13-18 * * *`, ~10 opportunities for ≥5 consecutive green ticks, with margin for GitHub's delayed or dropped scheduled runs); a second commit to `main` then switches to the weekly cron. Scheduled workflows run only from the default branch. Every burn-in tick advances the simulated clock by one simulated week, and the ledger labels it "burn-in (accelerated cadence)". The README must not imply weeks of unattended operation: the burn-in proves repeatability, idempotency and running without the owner, not long-run behaviour; **amended 2026-10-20 (E1/E2, owner decision after observing the scheduled burn-in): the burn-in is dispatched every 30 minutes (any hour) by a laptop timer, because GitHub's scheduler dropped most slots; the ledger labels burn-in ticks "burn-in (scheduled, accelerated)" (the three that GitHub did fire) or "burn-in (dispatched, accelerated)"; the burn-in now proves repeatability and idempotency but not running without the owner, which only the scheduled weekly tick shows**;
  - is keyed on the **simulated** clock, so a late or dropped run is caught up idempotently;
  - advances the clock, scores the new window and evaluates the cohort whose 30-day labels have matured over a rolling 13 weeks.

  Its primary alert is calibration-in-the-large: observed/expected events with a CI. AUC is reported, not alerted on. Windows below a pre-registered minimum-positives floor report "insufficient data", not an alert. For the immature cohort it reports a label-free expected-positives figure (the sum of predicted p). A threshold breach opens or updates one GitHub issue. "Simulated clock" is stated everywhere. **Kill trigger:** if the tick is not idempotent by week 5, ship scheduled scoring + data tests only.
- **FR20** Telemetry (G20):
  - BigQuery `scoring_runs` is the source of truth; **slice 4 (2026-10-11, `notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md`):** done (one row per attempt, refusals and failures included), plus the Actions job summary in `score.yml`;
  - `docs/run-ledger.md` is rendered from it by **two jobs**: job A (`id-token`, read-only) renders an artifact; job B (`contents: write`, no `id-token`) commits it **directly to `main`**, with GitHub Actions as a ruleset bypass actor for that job only (OQ3). If a personal repo can't do this, fall back to a dedicated `ledger` branch linked from the README. `GITHUB_TOKEN` commits trigger no workflows, so there's no loop;
  - every run writes an Actions job summary;
  - a README badge with `?event=schedule&branch=main` sits next to a "last tick" date.
- **FR21** Failure handling (G18):
  - a failed tick opens or updates a single GitHub issue (slice 4 records every failed or refused attempt in `scoring_runs`; the issue is slice 7);
  - after 2 missed ticks, the dashboard and README show "stale since <date>"; **v2 (amendment 2026-10-18): the README badge shows "stale after <time>"; the dashboard part is v3.0**; **amended 2026-10-21 (Part 2): "stale after" is computed from the newest *succeeded weekly* tick (retry slots that skip, failed attempts and scheduled burn-in rows no longer refresh it)**;
  - a calendar reminder covers the 60-day schedule auto-disable, and `gh workflow enable` is documented.

**Reviewer surface**
- **FR22** (**v3.0**, amendment 2026-10-18) The Streamlit Community Cloud call sheet lives in `src/readmission_risk/dashboard/`, with its own slim hash-locked requirements file next to the entrypoint (the app would otherwise install the repo-root lockfile, pyspark included). It shows:
  - a heartbeat line (last tick, simulated date, tests passed, champion bundle, freshness) below the honesty strip;
  - a provenance footer;
  - a "synthetic data, not for clinical use" notice.

  Its Python version is set at deploy time and documented, because changing it requires a redeploy.
- **FR23** (**v3.0**, amendment 2026-10-18; `docs/run-ledger.md` lives on the `ledger` branch since slice 7) `docs/model-card.md` (generated from the champion's manifest, so it cannot drift), `docs/results.md` (200k results against `prereg.yaml`, the lookup comparison first, frozen-v1 validation), `docs/run-ledger.md`, and a README "60-second tour" with deep links. The single Mermaid diagram is upgraded with clickable nodes. `tests/test_docs.py` is updated deliberately: the docs page count, the status and roadmap wording, and the `streamlit.app` rule.
- **FR24** Tag `v2.0` when the Definition of done (Success metrics) holds. **Amendment 2026-10-18:** v2.0 is tagged after slice 7's M3; metrics that depend on slice 8 (the dashboard, the model card) move with it to v3.0. **Done 2026-10-26/27:** M3 met (see the M3 row); `v2.0` is tagged on the merge commit of the release-prep PR.

## Non-functional requirements

- **Performance:** a weekly tick (scoring + data tests + monitoring) finishes in ≤15 minutes on a standard `ubuntu-24.04` runner (extrap: gold at 200k ≈ 186k rows, ≈ 53 MB in memory). The Big Join at 200k runs in ≤2 h on the capped batch. The target is set from the 20k pilot.
- **Accessibility:** Streamlit defaults plus colour-blind-safe chart palettes and text alternatives for status (not colour alone). No formal WCAG audit.
- **Security & privacy:**
  - **S1:** No service-account keys in CI; WIF only (FR9).
  - **S2:** The supply-chain baseline (FR0.2).
  - **S3:** Least-privilege service account per job.
  - **S4:** The dashboard key, the single documented exception:
    - `bigquery.dataViewer` on the serving dataset only;
    - project-level `bigquery.jobUser`;
    - `readSessionUser`, or the client set to `create_bqstorage_client=False`;
    - a `QueryUsagePerUserPerDay` custom quota on that service account (the real boundary, because a leaked key bypasses in-app limits);
    - `maximum_bytes_billed` on every app query;
    - `st.cache_data` TTL in hours;
    - a documented rotation runbook.
  - **S5:** No PHI exists. Synthea output is synthetic (Apache-2.0); the disclaimer appears on the README and the dashboard.
- **Multi-tenant / scoping:** n/a (single owner, single project).
- **i18n:** English only.
- **Degraded modes:**
  - If the app sleeps or breaks, the README and run ledger still carry the payoff.
  - If a tick fails, the issue and staleness banner appear.
  - If Dataproc is not green by the end of week 4, the same job runs `local[*]` on the VM reading GCS (documented).
  - If WIF is not working by day 9, one narrowly documented workaround is allowed, never service-account keys in CI.
- **Reproducibility:**
  - Every run records its bundle id, git commit, `prereg.yaml` hash, Dataproc runtime sub-version and seed.
  - Synthea generation is deterministic per seed. **Corrected 2026-10-12 (slice 5):** content is deterministic only with `-s`, `-cs`, `-e` and a pinned JVM time zone; row order varies.
  - The lockfiles stay hash-checked.
  - A lockfile-drift CI check is added, because the library-version guard would otherwise refuse a scheduled run after drift. **Partly met 2026-10-12** (`notes/eg-new-feature/ci-hygiene-lockfiles-2026-10-12.md`): `tests/test_lockfiles.py` proves each lockfile matches its `.in`; a champion-compatibility check for guard 8 is still open.
- **Cost:**
  - One-off ≤ $50 (extrap ≈ $10–30: VM ≈ $1, Dataproc ≈ $1–3 per run × 5–10 runs, GCS ≈ $1–3 over 90 days).
  - Steady state ≤ $20/month (extrap ≈ $0–1).
  - The $5 and $20 budgets notify; they do not cap spend.

## Success metrics (Definition of done)

| # | Metric | Target | How measured |
|---|---|---|---|
| M1 | CI on `main` | green, including the 3.12 pipeline leg, zizmor = 0 findings, `terraform validate` | Actions |
| M2 | Keyless | 0 service-account keys used by any workflow; WIF condition on numeric repo/owner ids + `main` | `terraform plan` clean; repo grep; IAM key list for CI service accounts = empty |
| M3 | Scheduled operation | ~~**≥5 consecutive green scheduled ticks in the accelerated burn-in, plus ≥1 green tick at the weekly cadence**~~ **Amended 2026-10-20 (E2, owner decision after observing the scheduled burn-in): ≥5 consecutive green timer-dispatched burn-in ticks, plus ≥1 green scheduled tick at the weekly cadence** before `v2.0` (OQ1) | `scoring_runs` + ledger (burn-in ticks labelled); `make m3-check` **Met 2026-10-26** (`m3_check`: the 5 timer-dispatched burn-in runs of 2026-10-21, then the scheduled weekly tick of 2026-10-26 04:44 UTC; `notes/eg-new-feature/monitoring-burn-in-timer-and-weekly-retry-2026-10-20.md`, Results T5) |
| M4 | Promotion gate | ≥1 real promotion PR with gate table; ≥1 deliberately bad model refused | PRs linked from the README |
| M5 | Data tests | a seeded bad row visibly fails the `ASSERT` suite | recorded run |
| M6 | Pre-registration | `prereg.yaml` commit precedes the 200k generation (timestamps); results reported against every pre-registered metric, including deviations | git log + `docs/results.md` |
| M7 | Statistical power | ≥400 test positives at 200k (extrap ≈ 800); the v1 "unreliable" warning no longer fires | results |
| M8 | Frozen-v1 validation | reported on the 200k population; portable-export parity max diff = 0 (met in slice 6c: 0.0 for both v1 models) | `docs/results.md`, parity test (`tests/portable/test_v1_parity.py`) |
| M9 | Cost | one-off ≤ $50; steady state ≤ $20/month | billing export / console screenshot in docs |
| M10 | Evidence | every README claim links to a run, PR, table or file | `tests/test_docs.py` link checks |
| M11 | Release | `v2.0` tag on `main` | git |

## Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| WIF / IAM setup stalls | Med | High | Spine first; day-9 kill trigger; plain Terraform resources; numeric-claim condition tested with a dispatch run |
| Guard port breaks the scorer's test suite | Med | High | One-to-one guard → manifest mapping table in the design doc; 20% kill trigger → additive layer |
| v1 pickles unusable for G8 after a library or Python-minor bump (guard 14 refuses a major.minor mismatch) | Low now (v1 retrained on 3.14.7, `4fad5a2`) / High after an upgrade | High for G8 | Keep the v1 pins in the evaluation environment; FR0.3 portable export + parity test in slice 6 |
| Dataproc runtime moves underneath (sub-versions unpinnable; bundled pyarrow 19) | Med | Med | FR3 bundled-only dependencies; 3.12 + 4.0.1 CI leg; runtime sub-version recorded per run |
| Cost leak (forgotten VM, uncapped executors, leaked dashboard key) | Med | Med | Spot VM with TTL + DELETE; `maxExecutors` cap; custom query quota; labels; teardown checklist; $5 early-warning budget next to the $20 one (OQ4) |
| Scheduled workflow auto-disabled after 60 days / cron delays | High over months | Med | Staleness banner, issue, reminder; idempotent catch-up on the simulated clock; "activity" definition unverified, so the ledger commits are not relied on as keep-alive; **2026-10-20/21: slots are also dropped en masse (3 of 24 burn-in slots fired), so the weekly tick has 72 retry slots (E3/E4); the 60-day rule applies again since the schedule returned in Part 2** |
| Ledger bot push rejected by branch protection | Med | Low | Ruleset bypass for the ledger job (OQ3); fall back to a `ledger` branch if a personal repo cannot do it (NHV) |
| Burn-in read as months of operation | Med | Med (credibility) | Ticks labelled "burn-in (accelerated cadence)" (since 2026-10-20: "burn-in (scheduled, accelerated)" / "burn-in (dispatched, accelerated)", amendment E1); ≥1 weekly-cadence tick required; README wording checked in review |
| Badge stays green after the schedule is disabled | High | Low | Badge always shown next to "last tick" date; staleness banner is the honest signal |
| Quota too small or contended (`CPUS_ALL_REGIONS` = 32, measured) | Med | Med | `maxExecutors` = 7 fits it exactly; never run the generation VM and the batch at once; request an increase only if the 20k pilot is too slow (days of lead time) |
| 200k generation slower than planned / spot preemption | Med | Low | 20k pilot sets expectations; preemption = restart (no checkpoint), ≈ $1 each |
| "Models ≈ lookup" again at 200k | High (expected) | Low if pre-registered | Pre-registered "the lookup still wins" outcome; report it as the headline finding |
| Timebox overrun with G8 + B as must-haves | Med | Med | Kill triggers; G2 priority order decides cuts; monitoring has its own fallback |
| Leakage via the ~6-day label/test margin at 200k | Low | High | FR0.1 assertion runs on the 200k split too. **Amended 2026-10-10:** the label-horizon purge runs on the 200k split; its count is reported |

## Implementation hints

Proposed `/eg-new-feature` slices, refined there:

0. **Closeout** (FR0.1–FR0.3). **Finished 2026-10-10.** The carried items are listed under the slices below.
1. **Versions B, remainder + tooling** (FR2–FR3, plus the carried Makefile and pre-commit from FR0.1/OQ6). The Python half (FR1, the 3.12 + 3.14 matrix, the CLAUDE.md note) already landed in `c2526ab`/`832898d`. What's left: the {3.12, `pyspark==4.0.1`} hash-locked set compiled under 3.12, its `tests/pipeline` CI leg, and FR3's bundled-libraries-only rule for the Spark job. Still small. **Done in slice 1 (`notes/eg-new-feature/v2-slice1-tooling-and-dataproc-runtime-leg-2026-10-11.md`).** The version part could be folded into slice 5; the Makefile and pre-commit should not wait that long.

**Branch plan (owner decision, 2026-10-09):**
- Closeout work stays on `main`. That includes the five commits that landed during the PRD session, among them the 3.14 move.
- Once the closeout is done, work switches to the `v2` branch, and the then-current v2 material (the v2 brainstorm note and this PRD) is committed there. **Status 2026-10-10:** `v2` and `origin/v2` were fast-forwarded to `main` (`2242649`), and both notes are staged on `v2`.
- CI for `v2` needs an open PR `v2 → main`, because push CI runs only on `main` since `51f1d62`.
2. **Bundle + manifest** (FR4–FR7): guard mapping table; manifest fixtures replace `clone_run`; `forbid_model_loading` still applies; the scorer drops its mlflow import; `champion.json`. **Done 2026-10-11** (`notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md`).
3. **Terraform + WIF** (FR8–FR9): bootstrap script; `infra/` directory; one `workflow_dispatch` smoke job that only lists a bucket. Also: `terraform import` the existing CHF 20 budget and create the carried CHF 5 budget. **Done 2026-10-11** (`notes/eg-new-feature/v2-slice3-terraform-wif-2026-10-11.md`), except V-post: the first real WIF run of `smoke.yml`, after v2 merges.
4. **Score to BigQuery** (FR17–FR18, part of FR20): a `bigquery_writer` injected into `score_run_date`; `scoring_runs`; `ASSERT` scripts; one manual run on 10k (end of the spine). **Done 2026-10-11** (`notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md`); the manual run was a laptop run impersonating the scorer (owner decision D1); the first dispatched `score.yml` run (L-post) waits for the v2 merge with V-post.
5. **`gs://` Big Join + fingerprint + pilot** (FR10–FR13): `prereg.yaml` lands here, before generation.
6. **200k + Dataproc + retrain + gate + frozen-v1** (FR14–FR16, plus the carried FR0.3 portable export).
7. **Monitoring tick + ledger + issues** (FR19–FR21).
8. **Dashboard + model card + README** (FR22–FR24). **Moved to v3.0** (amendment 2026-10-18).

Notes:
- `id-token` jobs live in separate workflow files from the PR/push CI, so `test_docs.py:432` (top-level `contents: read`) can stay or be adjusted on purpose. zizmor would prefer `permissions: {}` at top level; that decision is made in the closeout.
- Every new BigQuery table is partitioned or clustered by date so Streamlit and monitoring scans stay small.

## Open questions

**Resolved during the PRD review (2026-10-09):**
- **OQ1, scheduled-tick count vs the timebox → accelerated burn-in.** One afternoon of scheduled ticks every 30 minutes, then the weekly cron. The DoD requires ≥5 consecutive green burn-in ticks **plus ≥1 green tick at the weekly cadence** (FR19, M3). The ≥1-weekly-tick part is an elephant default added for honesty; the owner may drop it. "Daily cron" stays Out; the accelerated cron is In only for the burn-in. The 6 weeks is a ceiling, not a target. **Amended 2026-10-20 (E1-E3):** the burn-in is dispatched by a laptop timer, not a cron, and need not fit one afternoon; hourly weekly retry slots Monday to Wednesday are In (the tick cadence stays weekly).
- **OQ2, the Brier rule → non-inferiority.** The upper bound of the patient-level paired bootstrap CI of the Brier difference must be ≤ a pre-registered margin (FR15).
- **OQ3, ledger path → commit to `main`** with an Actions ruleset bypass for the ledger job, falling back to a `ledger` branch (FR20).
- **OQ4, budget → a $5/month early-warning budget plus the locked $20**, alert-only, no billing hard stop (FR0.1).
- **OQ5, raw retention → 90 days** via a GCS lifecycle rule (FR8).
- **OQ6 → Makefile and pre-commit (ruff, zizmor) added to the closeout** (FR0.1).

**Still open:**
- **NHV list (verify during the slices):**
  - Dataproc runtime 3.0 availability in us-central1;
  - whether Dataproc accepts direct-WIF principalSets (impersonation assumed);
  - whether `event_name` / `workflow_ref` can be mapped in the WIF condition; **Resolved on documentation (slice 3):** both are GitHub OIDC claims and Google allows mapping any claim; live proof at V-post.
  - connector 0.44.0 direct-write overwrite / DYNAMIC partition behaviour;
  - the Storage Write API free tier (reported 2 TiB/month);
  - batch load jobs being free; **measured (slice 4):** load jobs billed 0 bytes (`INFORMATION_SCHEMA.JOBS_BY_PROJECT`); the billing report showed CHF 0 for slice 4's runs (owner, 2026-10-12);
  - the GCS backend's native state locking; **Resolved (slice 3):** HashiCorp's docs say the gcs backend supports locking; observed ("Acquiring state lock") during the migration.
  - whether Python 3.14 is selectable on Community Cloud today;
  - whether uv enforces `--hash` on Community Cloud;
  - Dependabot updating SHA-pin version comments;
  - what counts as "activity" for the 60-day disable;
  - badge behaviour after disable;
  - the 6-hour job limit;
  - the Dataproc runtime service-account role names;
  - `storage.objectCreator` lacking overwrite. **Resolved live (slice 3, V5):** overwrite refused (412/403), object unchanged.

## Sources & references

**Versions & runtimes**
- PySpark 3.14 support: https://issues.apache.org/jira/browse/SPARK-54286 ; https://pypi.org/pypi/pyspark/4.1.3/json ; https://pypi.org/pypi/pyspark/4.0.1/json
- PySpark migration guides: https://spark.apache.org/docs/latest/api/python/migration_guide/pyspark_upgrade.html ; https://spark.apache.org/docs/latest/sql-migration-guide.html
- Dataproc Serverless runtime 3.0 / versions: https://docs.cloud.google.com/dataproc-serverless/docs/concepts/versions/spark-runtime-3.0 ; https://docs.cloud.google.com/dataproc-serverless/docs/concepts/versions/spark-runtime-versions
- Batches submit / custom containers: https://docs.cloud.google.com/sdk/gcloud/reference/dataproc/batches/submit/pyspark ; https://docs.cloud.google.com/dataproc-serverless/docs/guides/custom-containers
- setup-python manifest: https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json
- Streamlit Community Cloud Python and dependencies: https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/upgrade-python ; https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies ; https://discuss.streamlit.io/t/streamlit-cloud-forcing-python-3-14-before-tensorflow-supports-it/121567

**Model persistence**
- https://github.com/cloudpipe/cloudpickle ; https://xgboost.readthedocs.io/en/stable/tutorials/saving_model.html ; https://scikit-learn.org/stable/model_persistence.html

**GCP identity, IaC, BigQuery**
- https://github.com/google-github-actions/auth ; https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines ; https://discuss.google.dev/t/github-and-workload-identity-pools-providers-create-oidc-invalid-argument-error/167426
- https://github.com/terraform-google-modules/terraform-google-github-actions-runners/tree/master/modules/gh-oidc ; https://docs.cloud.google.com/docs/terraform/resource-management/store-state
- https://docs.cloud.google.com/bigquery/docs/load-data-partitioned-tables ; https://docs.cloud.google.com/bigquery/docs/write-api ; https://github.com/GoogleCloudDataproc/spark-bigquery-connector/blob/master/README-template.md
- https://docs.cloud.google.com/bigquery/reference/standard-sql/debugging-statements ; https://docs.cloud.google.com/bigquery/docs/access-control ; https://googleapis.dev/python/pandas-gbq/latest/reading.html ; https://docs.cloud.google.com/bigquery/docs/custom-quotas
- https://docs.cloud.google.com/storage/docs/bucket-lock ; https://docs.cloud.google.com/storage/docs/object-lock

**Cost & scale**
- https://cloud.google.com/dataproc-serverless/pricing ; https://docs.cloud.google.com/dataproc-serverless/docs/concepts/autoscaling ; https://docs.cloud.google.com/dataproc-serverless/docs/concepts/properties
- https://cloud.google.com/bigquery/pricing ; https://cloud.google.com/storage/pricing ; https://cloud.google.com/products/compute/pricing/compute-optimized
- https://docs.cloud.google.com/billing/docs/how-to/budgets ; https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- Repo measurements: `data/raw/full_run/generation_summary.json`, `notes/eg-new-feature/pyspark-big-join-2026-09-19-measurements.md`

**GitHub Actions security & scheduling**
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows ; https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows ; https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow
- https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands ; https://docs.github.com/en/actions/how-tos/monitor-workflows/add-a-status-badge
- https://docs.zizmor.sh/audits/ ; https://docs.zizmor.sh/integrations/ ; https://github.blog/changelog/2025-08-15-github-actions-policy-now-supports-blocking-and-sha-pinning-actions/ ; https://www.cisa.gov/news-events/alerts/2025/03/18/supply-chain-compromise-third-party-github-action-cve-2025-30066
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management ; https://github.com/synthetichealth/synthea

**MLOps prior art & evaluation methodology**
- MLflow aliases: https://mlflow.org/docs/3.0.1/getting-started/registering-first-model/step3-load-model
- GitOps model promotion: https://kodekloud.com/blog/argocd-gitops-ai-model-deployments/
- MLServer / BentoML: https://mlserver.readthedocs.io/en/stable/reference/model-settings.html ; https://bentoml.readthedocs.io/en/latest/guides/model-store.html
- ML Test Score (Breck et al.): https://research.google/pubs/the-ml-test-score-a-rubric-for-ml-production-readiness-and-technical-debt-reduction/ ; Rules of ML: https://developers.google.com/machine-learning/guides/rules-of-ml/
- DataTalksClub MLOps project rubric: https://github.com/DataTalksClub/mlops-zoomcamp/blob/main/07-project/README.md
- NannyML CBPE: https://nannyml.readthedocs.io/en/main/how_it_works/performance_estimation.html ; Arize: https://arize.com/blog/monitor-your-model-in-production
- Calibration drift (Davis et al.): https://pmc.ncbi.nlm.nih.gov/articles/PMC6857513/ ; Van Calster et al. 2019: https://pmc.ncbi.nlm.nih.gov/articles/PMC6912996
- Pre-registration for predictive modeling: https://ar5iv.labs.arxiv.org/html/2311.18807 ; TRIPOD+AI: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11025451/ ; Model Cards: https://research.google/pubs/model-cards-for-model-reporting/
- Portfolio expectations: https://letsdatascience.com/blog/the-ml-portfolio-that-actually-gets-you-hired-in-2026

## Out-of-scope follow-ups

- **CLAUDE.md updates once this PRD is approved** (not done by this PRD run, at the owner's choice of output target):
  - ~~the Python version note~~ (done in `c2526ab`); the rest of strategy B (the pyspark 4.0.1 leg) once FR2 lands;
  - the BigQuery multi-table amendment;
  - the dbt rule amended to `ASSERT` (carried over from the closeout; CLAUDE.md line ~90 still says adopt dbt-bigquery);
  - the bundle replacing `load_logged_model` for scoring;
  - Actions as the orchestrator;
  - the dashboard key exception and its controls;
  - us-central1.
- Hosted MLflow; a Cloud Run dashboard with WIF (removes the key exception); Vertex AI.
- Scheduled retraining (after the gate has refused/accepted manually at least twice); feature-drift statistics; NannyML-style CBPE beyond the simple expected-positives figure.
- Automatic re-score on rollback (a `workflow_dispatch` over a run-date range).
- A Pub/Sub billing-disable hard stop (declined in OQ4).
- Label v2 (four measured candidates; revisit at 200k), as-of scoring, pandas 3 / numpy 2.5, hyperparameter tuning, SHAP.
- An ML Test Score mapping table in the docs.
- `[project.scripts]`; SECURITY.md / CONTRIBUTING.md.
