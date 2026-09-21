# PRD: The Big Join + The Triage List — Architecture & Package Lock-In

**Date:** 2026-09-15
**Workflow:** `/eg-prd` (elephant/goldfish — codebase grounding, structured gap-filling, 4 parallel research goldfish)
**Depth:** Standard (3-5 pages) | **Research scope:** Web search only
**Precedes:** `notes/eg-brainstorms/readmission-risk-cv-project-2026-09-15.md` (concept selection — read that first for *why* this concept was chosen; this PRD answers *what stack executes it*)
**Feeds into:** an eventual `/eg-new-feature` implementation pass, and an "Architecture" section added to `CLAUDE.md`

---

## Executive summary

This PRD locks in the technical architecture and package list for "The Big Join + The Triage List" — the readmission-risk portfolio project's chosen v1 concept — before implementation starts. It resolves the warehouse/compute stack (a hybrid PySpark-on-Dataproc-Serverless + BigQuery pipeline), the data population's staged scale-up path, where data physically lives at each build phase, and the dependency-management approach, then stress-tests those choices against current (2025/2026) technical, cost, and market-positioning research. The single most consequential finding: the repo's existing `.python-version` pin (3.14) creates a real compatibility risk with PySpark and Dataproc Serverless that should be resolved by downgrading to Python 3.12 before any Spark code is written.

## Problem statement

The repo has a chosen concept (`notes/eg-brainstorms/...md`) but zero architectural commitments — no warehouse, no compute engine, no dependency manager, no confirmed Python version compatible with the intended stack. Every one of these is a decision the user will build weeks of work on top of; getting any of them wrong costs calendar time this "weeks not months" project can't easily absorb. The user explicitly asked to lock these in now, as a discrete pass separate from feature-level design work, and to have the outcome captured durably in `CLAUDE.md` so every future `/eg-new-feature` pass inherits it automatically instead of re-deciding it.

## Target users

Unchanged from the brainstorm's dual-front-door framing (see the brainstorm doc, lines 50-56): a data-engineer/data-scientist reviewer reading code and docs directly, and a team-lead-style reviewer wanting a visual, clickable artifact. This PRD's own "user" is narrower — it's a decision document for the project owner (Florence), not a product requirement for either reviewer persona. Its output (the architecture) exists to serve both personas downstream.

## Current state (codebase brief)

Confirmed by two parallel Explore goldfish (2026-09-15):

- **Zero implementation code exists.** No `src/`, `sql/`, `dbt/`, `notebooks/`, or `data/` directories. No data ingestion, no SQL/Spark/dbt/BigQuery code, no ML training code, no dashboard code, no GCP infra config (Terraform, gcloud, Dataproc) anywhere in the tree.
- **Scaffolding present:** `README.md` (1 line), `.python-version` (pinned to `3.14`), `.gitignore` (pre-emptively ignores `data/`, `*.csv`, `*.parquet`, `models/`, `*.pkl`, `*.joblib` with the comment "track via a data pipeline, not git"), `.venv/` with only `pip` installed, and `.claude/commands/*.md` (the five `/eg-*` workflow definitions).
- **No dependency manifest existed before this session** — no `requirements.txt`, `pyproject.toml`, `Pipfile`, or `environment.yml`.
- **Git history:** 3 commits, all scaffolding/tooling (`.gitignore`+README, the `/eg-*` workflow + CLAUDE.md, the Python version pin) — nothing touching data, SQL, ML, or GCP.
- **`notes/eg-brainstorms/readmission-risk-cv-project-2026-09-15.md`** (untracked, 112 lines) records the chosen concept and five open questions this PRD directly addresses: warehouse commitment, live vs. static dashboard hosting, population scale under free-tier budget, prominence of the synthetic-data-limitations section, and DAG/orchestration tool choice.
- **CLAUDE.md conventions to mirror** when extending it: flat `##` headings, markdown tables for structured comparisons, bold lead-ins, `<!-- TODO -->` HTML-comment placeholders, and a standing instruction to grow the file incrementally rather than rewrite it.

## Proposed solution

A **staged, hybrid architecture**:

1. **Local proving phase** — generate ~10,000-20,000 Synthea patients locally, run the "Big Join" feature-engineering step as local PySpark (`local[*]` master, no GCP dependency), keep all intermediate data on local disk. Goal: prove the pipeline's correctness and the join's necessity without paying any cloud setup tax.
2. **Scale-up phase** — once the local pipeline is correctness-proven, swap the same PySpark job (via a config-driven base-path seam, not a rewrite) to run on Dataproc Serverless against GCS-resident data at ~50,000-100,000 patients, and write the gold/serving table directly into BigQuery using the `spark-bigquery-connector`'s **direct write method** (BigQuery Storage Write API) — deliberately avoiding the indirect write path's GCS-staging-bucket requirement, since it would force bucket/IAM setup earlier than necessary.
3. **Serving layer** — BigQuery holds only the lean, patient-encounter-level gold table (features + risk score); the dashboard and any SQL analysis read from BigQuery, not from GCS or Spark output directly.

This directly executes "The Big Join" concept (round-2 brainstorm pick): the join is only credibly "necessary" if the population scale genuinely stresses single-node tooling — see the Risks section below for a research-backed caveat on this.

**MLOps spine (added 2026-09-15, second pass):** a staged, local-first rollout mirrors the data pipeline's own staging pattern:

1. **Model training** — scikit-learn `LogisticRegression` as an interpretable baseline, `XGBClassifier` (XGBoost's scikit-learn-compatible API) as the stronger challenger model, both trained on the BigQuery gold table pulled via `google-cloud-bigquery`'s `to_dataframe()`. The XGBoost model is wrapped in `CalibratedClassifierCV` so both models report calibrated probabilities, not just raw scores — directly executing the brainstorm's "calibration over raw accuracy" evaluation priority.
2. **Experiment tracking** — MLflow, starting on a **local file-store/SQLite backend** (zero infrastructure, `pip install mlflow` only) via a single `mlflow.autolog()` call covering both models. A hosted GCP tracking server (Cloud Run + Cloud SQL + GCS artifact store) is an explicit **later-phase upgrade**, not a v1 commitment — the migration only requires changing the tracking URI, not re-architecting training code, though historical local runs won't automatically carry over.
3. **Batch scoring (v1)** — a single Python CLI script (Typer-based entrypoint), no orchestrator. Triggered manually or via a literal crontab entry. Structured as pure, importable functions (`load_features()` → `score()` → `write_predictions()`) so a later Airflow migration wraps each as a task without a rewrite. Idempotent by design (overwrites the full prediction partition for a given run date, safe to re-run after failure). Airflow is the named later-phase upgrade for orchestration, not Cloud Scheduler+Cloud Run — see Risks for the Cloud Composer cost flag that applies when that phase is actually scoped.

**Cost ceiling & process evidence (added 2026-09-16, fourth pass — resolves G11, G12, data-realism prominence):**

- **G11 — GCP budget ceiling:** a GCP Budgets & Alerts threshold (e.g. **$20/month**) configured before the scale-up phase begins (i.e. before the first Dataproc Serverless / GCS / Cloud Run spend of any kind). This is a notification, not a hard spend cap — it directly closes the "unverified GCP cost figures" risk flagged in the first PRD pass by giving an early warning if a mistake (bad IAM scope, a runaway query, an accidentally-always-on resource) starts costing real money, without adding new infrastructure of its own (GCP Budgets is a free, built-in feature).
- **G12 — Agentic-coding process evidence:** the existing `notes/` trail (`notes/eg-brainstorms/`, `notes/prds/`, future `notes/designs/`) plus curated PR descriptions (generated via the `/eg-*` workflow, narrating design doc → goldfish critique → review → merge) is sufficient. No new dedicated artifact (e.g. an `ENGINEERING.md`) — the trail already accumulates as a byproduct of using the workflow, consistent with the brainstorm's "process evidence only, never a runtime component" framing.
- **Data-realism prominence:** resolved as a **smaller caveat**, not a headline section — a concise, honest paragraph (in the README, near the model-evaluation section) naming Synthea's key limitations (SNOMED-CT vs. ICD-10-CM, isolated disease modules / no comorbidity interaction, ~20-condition module coverage ceiling, no resource-capacity modeling, absence of real-world data messiness) without a dedicated `docs/` file or a Streamlit tab. Chosen after reviewing the fuller scope of the headline option (a `docs/data-realism-and-limitations.md` + a Streamlit `st.tabs()` page, cross-referenced with the calibration evaluation, estimated at a few focused hours) — the caveat still names the same specific limitations, just without the dedicated-artifact investment.

**Repo hygiene (added 2026-09-16, fourth pass — resolves G9, G10, dbt):**

- **Layout:** `src/` layout, one importable package (`src/readmission_risk/`) with sub-modules per stage (`pipeline/` — the PySpark join, `models/` — training + calibration, `scoring/` — the v1 CLI script, `dashboard/` — the Streamlit app), `tests/` mirroring that structure, `notebooks/` reserved for exploration only, never production code.
- **CI:** GitHub Actions from day one (`.github/workflows/ci.yml`) running `ruff check .` + `pytest` on every push/PR — no GCP credentials needed since lint/unit tests run against local fixtures and pure functions per the pyspark-testing pattern already locked (see Functional requirements). Closes the "no CI" gap CLAUDE.md's own standing rule flags as a missing-practice deviation, rather than retrofitting it later.
- **dbt:** **not adopted for v1.** BigQuery native constraints (`NOT NULL`, unenforced `PRIMARY KEY`/`FOREIGN KEY` for documentation/optimization intent) plus a small validation script run after the PySpark write (row-count sanity check, null-risk-score check, duplicate-key check) cover the single gold table's data-quality needs without a second tool or a second isolated Python environment. **Named later-phase trigger:** adopt `dbt-bigquery` once the project has more than one BigQuery-side table (e.g. a scoring-history/audit table, a model-comparison metrics table) — consistent with the staged-adoption pattern used for Dataproc Serverless, Airflow, and hosted MLflow elsewhere in this PRD (prove it small, add tooling when scale actually justifies the setup cost).

**Dashboard / team-lead front door (added 2026-09-15, third pass — resolves G8):** Streamlit, hosted on **Streamlit Community Cloud** (confirmed free/current in 2026), reading the BigQuery gold table via the plain `google-cloud-bigquery` client (Streamlit's own documented BigQuery pattern, not the generic `st.connection(type="sql")` path). A self-hosted Apache Superset option was evaluated and explicitly rejected — it requires a standing Postgres + Redis + Celery worker/beat + web-app stack even in its "light" Docker Compose form, directly reproducing the GCP/IAM infra-surface-area risk this project has repeatedly flagged as its top timeline threat, and is architecturally built for multi-chart self-serve BI exploration rather than a single opinionated triage-list artifact. Preset.io's free tier was also considered and rejected: it excludes embedded dashboards and API access, and hibernates idle workspaces after 30 days — unacceptable for a portfolio artifact that must stay reachable for reviewers indefinitely.

Concrete decisions:
- **Credentials:** a dedicated GCP service account scoped to `roles/bigquery.dataViewer` on the gold/serving dataset only (not project-wide Viewer), stored exclusively in Streamlit Community Cloud's Secrets manager, never committed to the repo.
- **Caching:** `st.cache_data(ttl=...)` set to several hours (e.g. 12h) given the underlying gold table only refreshes weekly — bounds BigQuery query cost regardless of public traffic volume.
- **Sleep mode:** Streamlit Community Cloud's free tier sleeps an app after 12 hours of no traffic (visible "wake the app" screen + short delay on the first cold visitor, not a silent failure). Decision: add a small **scheduled keep-alive ping** (e.g. a GitHub Actions cron job hitting the app URL every few hours) so reviewers never see the wake screen — accepted as a small, justified piece of standing automation given the project's explicit reviewer-experience goals.

**Dashboard hosting v2, revisited 2026-09-16 (still fourth pass):** the user raised a fair CV-differentiation concern — they've already shipped Streamlit apps before, so Streamlit-on-Community-Cloud alone demonstrates no new skill to a reviewer. Self-hosted Apache Superset was reconsidered on CV-signal grounds specifically (not just cost), but rejected again: this project's chosen concept wants *one* opinionated triage-list artifact, which is exactly the use case Superset's actual differentiator (self-serve multi-chart BI exploration) isn't built for — a reviewer who knows Superset would likely read a single-dashboard Superset deployment as under-justified tool choice, diluting rather than strengthening the signal, on top of reopening the standing-infra risk this PRD avoids everywhere else.

**Resolved instead as a staged v1/v2, consistent with every other tool decision in this PRD:**
- **v1 (as already locked above):** Streamlit app, hosted on Streamlit Community Cloud — ships fastest, zero new infra risk.
- **v2 (named later-phase upgrade, not built now):** keep the same Streamlit application code, but self-deploy it via a **Dockerfile + Cloud Run** instead of Community Cloud. This is a real, differentiated GCP deployment skill (containerization, Cloud Run service config, IAM for the BigQuery-reading service account) that directly addresses the "nothing new to a reviewer" concern, without taking on Superset's Postgres+Redis+Celery standing-infra stack. Trigger point: once the v1 pipeline and dashboard are proven end-to-end, same staging logic used for Dataproc Serverless, Airflow, and hosted MLflow elsewhere in this PRD.

## Scope

**In:**
- Local-mode PySpark development environment and job structure (config-driven base path, `SparkSession` factory abstracting `local[*]` vs. Dataproc Serverless)
- Dataproc Serverless deployment path for the scale-up run, using the BigQuery Storage Write API (direct write) connector method
- BigQuery as the sole gold/serving table location
- `pip` + `requirements.txt`/`requirements-dev.txt` dependency management, upgraded to `pip-compile --generate-hashes` / `pip install --require-hashes` for reproducibility
- `.python-version` downgrade from 3.14 to 3.12
- `pytest` + `chispa` (or PySpark 4.1+'s built-in `assertDataFrameEqual`) as the PySpark unit-testing pattern, with transformation logic factored into pure, testable functions
- A hand-declared `StructType` schema per Synthea CSV table (not relying on Spark's schema inference)
- A `CLAUDE.md` "Architecture" section documenting all of the above as a standing constraint for future `/eg-*` passes
- **(Added 2026-09-15, second pass)** Model training: scikit-learn `LogisticRegression` baseline + `XGBClassifier` challenger, both calibrated via `CalibratedClassifierCV`
- **(Added)** Experiment tracking: MLflow, local file-store/SQLite backend for v1, `mlflow.autolog()` for both models
- **(Added)** v1 batch scoring: single Typer CLI script, no orchestrator, idempotent per-run-date partition overwrite
- **(Added 2026-09-15, third pass)** Dashboard: Streamlit hosted on Streamlit Community Cloud, `google-cloud-bigquery` client reading the gold table, scoped `dataViewer` service account via Streamlit secrets, `st.cache_data` TTL, GitHub Actions keep-alive ping to prevent sleep
- **(Added 2026-09-16, fourth pass)** Repo layout: `src/` layout, one package with `pipeline/`/`models/`/`scoring/`/`dashboard/` sub-modules; CI via GitHub Actions (`ruff` + `pytest`) from day one; dbt explicitly not adopted for v1 (BigQuery native constraints + validation script instead), named later-phase trigger recorded

**Out (explicitly deferred, see Open Questions):**
- **(Named later-phase upgrades, not v1):** hosted GCP MLflow tracking server (Cloud Run + Cloud SQL + GCS); Airflow for orchestration; self-deployed Streamlit-on-Cloud-Run (Dockerfile + Cloud Run, replacing Streamlit Community Cloud) — all explicitly chosen as the *next* step once v1 is proven, not open/undecided, but not built now
- Repo layout convention (`src/` layout, `sql/`, `dbt/` project structure)
- CI setup (GitHub Actions or otherwise)
- Whether `dbt`/`dbt-bigquery` is adopted at all for the gold-table testing/docs layer
- Explicit GCP cost ceiling / budget alert configuration
- How "agentic coding as dev-process evidence" concretely surfaces in the repo beyond the existing `/eg-*` + `notes/` trail

## Functional requirements

1. The PySpark job MUST read/write via a single, environment-agnostic code path — only `SparkSession` construction (master, GCP config/credentials) branches between local and Dataproc Serverless; `spark.read`/`spark.write` calls themselves must not change based on environment, using the `gs://`/`file://` scheme abstraction Spark's GCS connector already provides.
2. The base data path (local directory vs. `gs://bucket/...`) MUST be parameterized via a single config seam (env var or small dataclass), not hardcoded or branched at multiple call sites.
3. Each Synthea source table MUST be read with an explicitly declared `StructType` schema, not `inferSchema=True`, to avoid Spark mis-inferring UUID/date columns.
4. The BigQuery write step MUST use `df.write.format("bigquery")` with `writeMethod=direct` (BigQuery Storage Write API), not the indirect GCS-staging path, during the scale-up phase.
5. Transformation/join logic MUST be factored into pure functions (DataFrame in, DataFrame out) so they can be unit-tested against small in-memory fixture DataFrames independent of local-vs-cloud environment.
6. `requirements.txt` MUST be generated via `pip-compile --generate-hashes` from a `requirements.in` source file, and both files MUST be committed; installs MUST use `pip install --require-hashes`.
7. `.python-version` MUST be downgraded from `3.14` to `3.12` before any PySpark code is written.
8. The train/test split for the readmission model (when that work begins) MUST be chronological (by admission/discharge date) and grouped by patient ID — never a random row-level split. *(Carried forward here because it's a hard architectural constraint on how the gold table's date/patient-ID columns must be structured, even though model training itself is out of this PRD's scope.)*
9. **(Added 2026-09-15)** Both models (logistic regression baseline, XGBoost challenger) MUST report calibrated probabilities — the XGBoost model wrapped in `CalibratedClassifierCV` — and evaluation MUST report calibration curve/reliability diagram + Brier score alongside AUC, not AUC/accuracy alone.
10. **(Added)** MLflow tracking MUST be wired in from the first training run (via `mlflow.autolog()`), not retrofitted after models exist — this is a stated PRD success metric (no mid-build architecture reversals).
11. **(Added)** The v1 batch scoring script MUST be structured as pure, importable functions behind a CLI entrypoint, MUST be idempotent (safe to re-run for the same run date), and MUST exit non-zero with a structured log line on failure — there is no orchestrator to catch or alert on a silent failure in v1.

## Non-functional requirements

- **Performance:** no hard latency budget (this is a batch pipeline, not a live service). Target: local proving-phase runs complete in well under an hour on developer hardware; scale-up Dataproc Serverless run completes within a single work session (target: under 2 hours wall-clock, informed by the ~$0.036/minute-at-minimum-DCU-sizing cost estimate below — needs human verification, see Risks).
- **Reproducibility:** hash-pinned `requirements.txt` (`pip-compile --generate-hashes`) + a fixed `.python-version` (3.12) are the reproducibility baseline. Model-level reproducibility (seed control) is out of scope for this architecture PRD but is a standing CLAUDE.md requirement for the eventual model-training feature.
- **Cost:** no confirmed hard ceiling yet (deferred to Open Questions as G11) — see Risks for a rough, *unverified* cost estimate.
- **Multi-tenant / scoping:** n/a — single-user portfolio project.
- **i18n / offline / accessibility:** n/a for this architecture-layer PRD.

## Success metrics

This PRD's own success is measured by whether the locked-in architecture survives contact with implementation without a mid-build pivot:

- Local PySpark job runs end-to-end against ~10-20k Synthea patients without requiring any GCP credentials.
- The identical job (only `SparkSession` construction changed) runs successfully on Dataproc Serverless against GCS-resident data at ~50-100k patients.
- BigQuery gold table load completes via the direct-write connector method without provisioning a GCS staging bucket.
- Zero mid-implementation architecture reversals attributable to a gap this PRD should have caught (e.g. a Python-version incompatibility discovered after code is written).

## Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Python 3.14 / PySpark incompatibility.** PySpark's full local-distribution Python 3.14 support only landed in PySpark 4.2.0 (Jul 2026, ~2 months before this PRD); Dataproc Serverless's managed runtime may lag even that. | Was high before this PRD | High — blocks all Spark work | **Resolved by this PRD**: downgrade `.python-version` to 3.12, well inside PySpark's stable, long-proven support window. Action item, not just documentation — must land before any PySpark code is written. |
| **`dbt-bigquery` Python-version support is unconfirmed** (support band stated as 3.10-3.13 as of mid-2026; 3.14 "for some adapters" only) — moot now given the 3.12 downgrade, but flagging since dbt adoption itself is still an open question. | Low (post-3.12-downgrade) | Low | If dbt is adopted later (Open Questions), it comfortably fits the now-3.12 environment; no separate venv needed. |
| **Scale-credibility gap.** Market/prior-art research found comparable public Synthea-readmission repos running comfortably on plain PostgreSQL/pandas at populations from a few thousand up to ~1M patients — one comparable repo processes ~1,467 index admissions in ~5 seconds on Postgres. At the chosen 50-100k-patient scale, a technical reviewer familiar with this repo class may read the Spark/distributed-join framing as unnecessary tooling rather than a genuine necessity, undercutting the "Big Join" concept's core credibility claim. | Medium | Medium (narrative/credibility risk, not a technical blocker) | Not re-litigated here since the staged 10-20k→50-100k scale was already deliberately chosen for timeline safety. But the eventual project documentation (README/ENGINEERING.md, not this PRD) should make the row-count/shuffle math explicit — e.g. show the actual join cardinality across patients×encounters×conditions×medications×procedures at chosen scale — so the necessity claim is demonstrated, not asserted. Revisit population scale if this remains unconvincing once the pipeline is running. |
| **Unverified GCP cost figures.** Research could not directly fetch/confirm Dataproc Serverless's live pricing page; the $0.06/DCU-hour rate, 12-DCU default minimum, and ~$0.036/minute cost estimate are search-synthesized, not confirmed against `cloud.google.com/dataproc-serverless/pricing` directly. | Low-Medium | Low (small dollar amounts either way for a portfolio-scale project) | **Needs human verification** before running any Dataproc Serverless job: confirm current pricing via the GCP pricing calculator, and set a GCP budget alert before the scale-up phase begins. |
| **BigQuery/GCS free-tier storage may be undersized at 50-100k-patient raw scale.** A published 150k-patient Synthea reference dataset produced ~175M total rows across tables (observations dominating); raw exports at 50-100k patients could plausibly exceed GCS's 5GB free tier, though overage is cheap (~$0.02/GB). | Medium | Low | Keep raw/intermediate data in GCS only (not loaded into BigQuery) per the scope's data-layout decision; only the lean gold table lands in BigQuery, comfortably inside its 10GiB free tier. |
| **No authoritative Synthea generation-time benchmark exists** for 10-20k or 50-100k patients; one data point (GitHub issue #776) showed 7,000 patients taking 4h45m — but caused by leaving database/CCDA export enabled, a documented anti-pattern (`generate.database_type=none` is the fast-path config). | Medium | Low-Medium (calendar time, not architecture) | Budget an early timed dry-run at 500-1,000 patients with `generate.database_type=none` to extrapolate actual generation time before committing to a scale number in the build order. |
| **IAM/quota setup friction**, independently corroborated by this PRD's research as the more likely timeline risk than raw compute cost (matches the brainstorm's own "GCP Rabbit Hole" flag from two independent rounds). | Medium-High | Medium (calendar time) | Sequence IAM role setup (`serviceusage.serviceUsageAdmin`, service-account `ActAs` permission) and any quota-increase requests *before* the scale-up phase begins, not discovered mid-sprint — a quota-increase request can take time to process. |
| **(Added 2026-09-15) Cloud Composer (managed Airflow) is a real, non-trivial future cost** — even a "Small" Composer 2/3 environment runs ~$400+/month minimum just for idle control-plane infrastructure (GKE + Cloud SQL + web server), vs. a few dollars/month for self-hosted Airflow (single VM or `docker compose`). Only relevant when the later Airflow-upgrade phase is actually scoped, but should not be a surprise when it is. | Low now, relevant later | Medium (recurring cost, not one-time) | When the Airflow phase is scoped, default to self-hosted Airflow (VM or Docker Compose) over Cloud Composer unless there's a specific reason to want the managed service; note this explicitly in that future PRD/design pass. |
| **(Added) `CalibratedClassifierCV` + `XGBClassifier` has had scikit-learn-version-specific compatibility rough edges** (float32 prediction dtype, `feature_importances_` access through the calibration wrapper) in past scikit-learn releases — fixed in current releases but not something to assume works without a check. | Low | Low (caught early by testing) | Add an explicit smoke test of the full calibrated pipeline (fit → predict_proba → score) as part of initial setup, not as an afterthought; pin scikit-learn/XGBoost versions together via the `pip-compile` lockfile. |
| **(Added) MLflow's local file-store backend is in maintenance mode**, and MLflow shifted its own server default from `./mlruns` files to a local SQLite file as of MLflow 3.7.0 — an unstated-default assumption could silently change behavior on a future `pip-compile` upgrade. | Low | Low | Explicitly pass `mlflow.set_tracking_uri(...)` rather than relying on an unstated default, and pin the MLflow version in `requirements.in`. |
| **(Added 2026-09-15, third pass) A public Streamlit app is effectively a public-triggerable BigQuery query endpoint** — uncoordinated reviewer traffic could otherwise drive up query volume against BigQuery's free tier. | Low | Low | Mitigated by design: service account scoped to `bigquery.dataViewer` on the gold dataset only (read-only, minimal blast radius) + a generous `st.cache_data` TTL (hours, not minutes) bounding real query frequency regardless of viewer count. |
| **(Added) Apache Superset (self-hosted) was evaluated and rejected** as the dashboard choice — including it here as a documented decision, not an open risk, so a future session doesn't re-litigate it without the context: it requires a standing Postgres+Redis+Celery+web-app stack even in its lightest form, reproducing the GCP-infra risk this project deliberately avoids, and is architecturally shaped for multi-chart BI exploration, not a single opinionated artifact. | N/A (resolved) | N/A | No action needed — documented for future reference only. |

## Implementation hints

**Package list (Python 3.12 environment):**

| Package | Purpose | Notes |
|---|---|---|
| `pyspark` | Local + Dataproc Serverless Spark jobs | Pin an exact version compatible with Python 3.12 and whatever Dataproc Serverless runtime image is targeted at implementation time — verify against Dataproc's current supported-runtime matrix, don't assume PySpark 4.2.0 is what Dataproc actually runs. |
| `google-cloud-bigquery` | BigQuery Python client (for any non-Spark BQ interaction, e.g. scripted loads, dashboard queries) | Standard GCP client library. |
| `chispa` (or rely on `pyspark.testing.assertDataFrameEqual` if the pinned PySpark version includes it) | DataFrame-equality test assertions | `chispa` gives more readable diffs; PySpark's built-in helper (4.1+) avoids an extra dependency — pick one, don't need both. |
| `pytest` | Test runner | Already named in `CLAUDE.md`'s build/test commands. |
| `pip-tools` | `pip-compile --generate-hashes` lockfile generation | Dev-only dependency (`requirements-dev.txt`), not a runtime dependency. |
| `ruff` | Lint | Already named in `CLAUDE.md`. |
| `scikit-learn` | Logistic regression baseline, `CalibratedClassifierCV`, calibration-curve evaluation | Fully compatible with Python 3.12. |
| `xgboost` | `XGBClassifier` — the challenger model | scikit-learn-API-compatible; fully compatible with Python 3.12; ships prebuilt wheels. |
| `mlflow` | Experiment tracking, `mlflow.autolog()` for both models | Local file-store/SQLite backend for v1 — pin the version and pass `set_tracking_uri()` explicitly (see Risks: file-store is in maintenance mode). |
| `google-cloud-bigquery[bqstorage,pandas]` | Pull the BigQuery gold table into a pandas DataFrame for training/scoring | Install with the `bqstorage` + `pandas` extras for Arrow-based transfer speed. |
| `db-dtypes` | Handles BigQuery-specific types (DATE/TIME) when converting to pandas | Required alongside `to_dataframe()`. |
| `pyarrow` | Arrow-format transfer for BigQuery reads | ~30x faster than row-by-row REST transfer. |
| `typer` | CLI entrypoint for the v1 batch scoring script | Type-hint-driven, modern default; keeps the script wrappable as Airflow tasks later without a rewrite. |
| `streamlit` | Team-lead-facing Triage List dashboard | Hosted on Streamlit Community Cloud (free). Reads BigQuery via the plain `google-cloud-bigquery` client, not `st.connection`. |

The `spark-bigquery-connector` itself is **not** a `pip` package — it's a Java/Scala connector pre-installed on Dataproc Serverless runtime images ≥2.1 and invoked via Spark's `.format("bigquery")` write path; no separate Python install is needed, though local-mode testing of the BigQuery write step will need the connector jar available if that path is exercised locally at all (likely deferred to the scale-up phase only, per the staged plan).

**Sequencing hint:** the `.python-version` downgrade (3.14 → 3.12) should be the very first change made, before any dependency is installed or any PySpark code is written, since it changes what every subsequent package version pin needs to be compatible with.

## Open questions

All gaps from the original architecture lock-in are now resolved as of 2026-09-16 (four `/eg-prd` passes). Kept here as a resolution log rather than deleted, per this repo's incremental-documentation convention:

- **G5 — Spark execution environment:** resolved as a byproduct of G1+G3 (local mode → Dataproc Serverless).
- **G6 — ML library + experiment tracking:** resolved 2026-09-15 — scikit-learn `LogisticRegression` baseline + `XGBClassifier` challenger (both calibrated), MLflow local file-store/SQLite for v1, hosted GCP MLflow server named as a later-phase upgrade.
- **G7 — Orchestration/scheduling:** resolved 2026-09-15 — v1 is a single Typer CLI script, manually or crontab-triggered, no orchestrator. Airflow (self-hosted, not Cloud Composer) named as the later-phase upgrade. Also resolves the brainstorm's "DAG/orchestration tool choice" question for v1.
- **G8 — Dashboard framework + hosting:** resolved 2026-09-15/16 — Streamlit, **v1 hosted on Streamlit Community Cloud**, **v2 self-deployed via Dockerfile + Cloud Run** (named later-phase upgrade, chosen specifically to give a differentiated CV signal beyond the user's prior Streamlit experience). Reads BigQuery via the `google-cloud-bigquery` client; scoped read-only service account via Streamlit secrets; generous `st.cache_data` TTL; GitHub Actions keep-alive ping against the 12h free-tier sleep. Self-hosted Superset and Preset.io's free tier were evaluated twice (cost, then CV-signal grounds) and rejected both times. Also resolves the brainstorm's "live hosted vs. static/pre-computed dashboard" question in favor of live-hosted.
- **G9 — Repo layout convention:** resolved 2026-09-16 — `src/` layout, one package, sub-modules per pipeline stage.
- **G10 — CI setup:** resolved 2026-09-16 — GitHub Actions running `ruff` + `pytest` from day one.
- **G11 — Explicit GCP cost ceiling:** resolved 2026-09-16 — GCP Budgets & Alerts threshold (~$20/month), configured before the scale-up phase begins.
- **G12 — Agentic-coding process evidence:** resolved 2026-09-16 — the existing `notes/` trail + curated PR descriptions is sufficient; no new dedicated artifact.
- **dbt adoption:** resolved 2026-09-16 — not adopted for v1; BigQuery native constraints + a validation script instead, with `dbt-bigquery` named as the trigger-based later-phase upgrade once the project has more than one BigQuery-side table.
- **Data-realism prominence** (from the original brainstorm): resolved 2026-09-16 — a **smaller caveat** (concise README paragraph naming Synthea's specific limitations), not a headline section, chosen after reviewing the fuller implementation scope of the headline option.

No open questions remain from this PRD's original list. Any new architecture questions that surface during implementation belong in a fresh `/eg-prd` pass or the relevant `/eg-new-feature` design doc.

## Sources & references

**Technical patterns:**
- [Dataproc Serverless BigQuery connector guide](https://docs.cloud.google.com/dataproc-serverless/docs/guides/bigquery-connector-spark-example)
- [spark-bigquery-connector README](https://github.com/GoogleCloudDataproc/spark-bigquery-connector/blob/master/README.md)
- [Setting up a PySpark local dev environment for Dataproc Serverless](https://levelup.gitconnected.com/setting-up-a-pyspark-local-developmet-environment-for-dataproc-serverless-cc7d05779e7d)
- [MungingData — Testing PySpark Code with pytest and chispa](https://www.mungingdata.com/pyspark/testing-pytest-chispa/) / [chispa GitHub](https://github.com/MrPowers/chispa)
- [dbt-bigquery PyPI](https://pypi.org/project/dbt-bigquery/) / [dbt Core Python compatibility FAQ](https://docs.getdbt.com/faqs/Core/install-python-compatibility)

**Compatibility & reproducibility:**
- [ASF Jira SPARK-54287 — Python 3.14 support](https://issues.apache.org/jira/browse/SPARK-54287)
- [PyPI — pyspark 4.2.0](https://pypi.org/project/pyspark/)
- [jazzband/pip-tools](https://github.com/jazzband/pip-tools) / [pip-tools docs](https://pip-tools.readthedocs.io/en/stable/)
- [pip docs — Repeatable Installs](https://pip.pypa.io/en/stable/topics/repeatable-installs/)
- [arXiv 2503.23050 — Prediction of 30-day hospital readmission (leakage-safe splits)](https://arxiv.org/html/2503.23050v1)

**Cost & scale (flagged as needing direct re-verification, see Risks):**
- [GCP BigQuery pricing](https://cloud.google.com/bigquery/pricing)
- [GCP Dataproc Serverless pricing](https://cloud.google.com/dataproc-serverless/pricing) *(could not be directly fetched during research — verify manually)*
- [GCP Dataproc IAM roles](https://cloud.google.com/dataproc/docs/concepts/iam/iam) / [GCP Dataproc quotas](https://cloud.google.com/dataproc/quotas)
- [Synthea GitHub issue #776 — generation time report](https://github.com/synthetichealth/synthea/issues/776)
- [Synthea Common Configuration wiki](https://github.com/synthetichealth/synthea/wiki/Common-Configuration)
- [Synthea MITRE "About" page](https://synthea.mitre.org/about)

**Market & prior art:**
- [gadesaiharika/hrrp-readmission-analytics](https://github.com/gadesaiharika/hrrp-readmission-analytics)
- [jwalcutt/risk-scoring-service](https://github.com/jwalcutt/risk-scoring-service)
- [GoogleCloudPlatform/healthcare — generate_synthea_dataset.ipynb](https://github.com/GoogleCloudPlatform/healthcare/blob/master/ml_solutions/generate_synthea_dataset.ipynb)
- [PMC5726103 — LACE index](https://pmc.ncbi.nlm.nih.gov/articles/PMC5726103/) / [PMC8589185 — Epic readmission model external validation](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8589185/)
- [PMC12622564 — Vanderbilt Discharge Care Center triage team](https://pmc.ncbi.nlm.nih.gov/articles/PMC12622564/) / [CMS HRRP](https://www.cms.gov/medicare/quality/value-based-programs/hospital-readmissions)

**MLOps spine (added 2026-09-15, second pass):**
- [MLflow Backend Stores docs](https://mlflow.org/docs/latest/self-hosting/architecture/backend-store/) / [MLflow filesystem backend deprecation notice](https://github.com/mlflow/mlflow/issues/18534)
- [MLflow official GCP deploy guide](https://mlflow.org/docs/latest/self-hosting/deploy-to-cloud/gcp/)
- [MLflow Autolog docs](https://mlflow.org/docs/latest/ml/tracking/autolog/) / [MLflow XGBoost integration guide](https://mlflow.org/docs/latest/ml/traditional-ml/xgboost/)
- [scikit-learn install docs](https://scikit-learn.org/stable/install.html) / [XGBoost install guide](https://xgboost.readthedocs.io/en/stable/install.html)
- [Cloud Composer official pricing](https://cloud.google.com/composer/pricing)
- [Google Cloud: Use open source Python libraries (BigQuery→pandas)](https://docs.cloud.google.com/bigquery/docs/python-libraries)
- [scikit-learn: CalibratedClassifierCV docs](https://scikit-learn.org/stable/modules/generated/sklearn.calibration.CalibratedClassifierCV.html)
- [scikit-learn issue #28245 — CalibratedClassifierCV float32 compat](https://github.com/scikit-learn/scikit-learn/issues/28245)
- [Astronomer: Migrate Python Jobs to Airflow in 4 Simple Steps](https://www.astronomer.io/blog/migrate-python-jobs-to-airflow-in-4-simple-steps/)
- [PMC: Weighted Brier Score for risk prediction models](https://pmc.ncbi.nlm.nih.gov/articles/PMC12523994/)

**Dashboard (added 2026-09-15, third pass):**
- [Apache Superset installation docs](https://superset.apache.org/admin-docs/installation/installation-methods/) / [Docker Compose light bundle](https://superset.apache.org/admin-docs/installation/docker-compose/)
- [Superset BigQuery connector docs](https://superset.apache.org/user-docs/databases/supported/google-bigquery/)
- [Preset.io free-tier limits](https://docs.preset.io/docs/free-tier-limits)
- [Streamlit Community Cloud](https://streamlit.io/cloud) / [Streamlit BigQuery tutorial](https://docs.streamlit.io/develop/tutorials/databases/bigquery)
- [Streamlit Secrets management](https://docs.streamlit.io/develop/concepts/connections/secrets-management)
- [Streamlit — Manage your app (sleep behavior)](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app)
- [st.cache_data docs](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.cache_data)

## Out-of-scope follow-ups

- The full feature-engineering design (what specifically gets joined, comorbidity index choice, leakage-trap feature checklist) belongs to a future `/eg-new-feature` design doc, not this architecture PRD.
- The "Test Pyramid for a Model" concept (data contract tests, feature unit tests, model behavioral tests) is a named stretch goal from the brainstorm — light research confirms it's a sound pattern, but it's explicitly deferred, not core v1 scope.
- A future PRD pass should resolve the Open Questions above (G6-G12 + the two remaining brainstorm questions) before `/eg-new-feature` design work on the model-training or dashboard features begins.
