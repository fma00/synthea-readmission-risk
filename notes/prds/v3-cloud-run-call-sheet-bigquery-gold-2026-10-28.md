# PRD: v3.0 — keyless Cloud Run call sheet, BigQuery gold, model card

**Status:** Approved by the owner 2026-10-31 (drafted 2026-10-28 via `/eg-prd`, Standard depth, web research). Builds on the [v2 PRD](v2-mlops-keyless-scheduled-scale-up-2026-10-09.md) and takes over its v3.0-marked requirements (FR14 remainder, FR21's dashboard half, FR22, FR23); where they conflict, this document wins for v3.0.

## Decision log (owner, 2026-10-27/28)

| # | Decision | Effect on earlier locks |
|---|---|---|
| V1 | **Hosting: Cloud Run only, keyless.** The app runs as a runtime service account via Cloud Run's identity. | **Reverses** the 2026-09-16 Streamlit Community Cloud lock and **removes** the NFR-S4 "single documented key exception" (key controls, rotation runbook, per-key quota). `tests/test_infra.py:202` (no `google_service_account_key`) stays. Keep-alive ping: none (scale to zero). |
| V2 | **Audience:** reviewer first (hiring DE/DS/MLE, ~60 s after a cold click), framed as the simulated team lead's call sheet; the owner's weekly check is equally important, so ops views are in scope. | — |
| V3 | **Views:** Top-N call sheet (landing), monitoring O/E over simulated weeks, run history / data tests. **No** model/gate summary view; the model card is linked. | Narrows v2 FR22. |
| V4 | **Run date:** latest published by default + a picker of published dates. | — |
| V5 | **Call-sheet row:** rank, discharge time, admission reason, risk score (as %), Top-N flag, plus `age_at_index_years`, `length_of_stay_days`, `prior_inpatient_count`, `prior_emergency_count`, `active_condition_count`. **No** race/ethnicity/gender columns. **No** per-row contribution column: the admission reason and its historical rate are the "why". | Fairness goes into the model card as a subgroup table. |
| V6 | **Gold in BigQuery: kept**, via connector direct write (Storage Write API), after a MERGE test on a faithful throwaway table. | Executes v2 FR14's remainder. |
| V7 | **Clock:** simulated date is "today" everywhere; **one** wall-clock exception, the heartbeat line ("last tick ran <real time> · stale since <real date>"). | Refines v2 FR21. |
| V8 | **Access:** a Terraform-managed dataset of **authorized views**; the runtime SA reads only that dataset; label columns are never exposed. | — |
| V9 | **Model card:** `docs/model-card.md` generated from the champion manifest + `prereg.yaml` `phase_b` + committed gate reports + `deploy/PROMOTIONS.md`, with a drift test; linked from the app. | Refines v2 FR23 (the manifest itself holds no metrics). |
| V10 | **Carry-overs:** guard-8 champion-compatibility check **In**; FR16 portable fallback, label v2, as-of scoring **Out**. | — |
| V11 | **Definition of done:** live URL + smoke check; data path proven end to end; docs/model card/tests updated; heartbeat follows a real scheduled tick. | — |
| V12 | **Budgets:** scale-to-zero, `max-instances` 1, cold start ≤ ~15 s accepted, `maximum_bytes_billed` on every query, cache TTL in hours. | — |
| V13 | **Style/a11y:** reuse `reporting/style.py` tokens (Okabe-Ito, light/dark) as one source; status as text + icon; one documented manual check; no formal WCAG audit. Charts: Altair/Vega-Lite. | — |
| V14 | **Image:** `python:3.14-slim` pinned by tag + digest; a dashboard-only lockfile compiled **for Linux**, registered in `tests/test_lockfiles.py`. | — |
| V15 | **Deploy:** `deploy.yml`, **dispatch-only** on `main`, via WIF with its own deploy SA; Terraform owns the service, CI only swaps the image. | WIF event condition unchanged. |
| V16 | **Failures:** a text error panel per failing query, never a stack trace; "stale since" reuses `ledger.py`'s rule. | — |
| V17 | **Instrumentation:** platform logs + BigQuery job metadata; no visitor tracking. | — |
| V18 | **Cost guard:** a **dashboard-only budget → Pub/Sub → function** that sets only the dashboard's `max-instances` to 0; re-enable runbook. | New. |
| V19 | **v2 leftovers** (T4.5, final fired-vs-due count, L16) close **in parallel**, not gating. | — |

## Executive summary

v3.0 makes the project's running system visible. A public, keyless Streamlit app on Cloud Run shows this week's Top-N call sheet, the delayed-label monitoring record and the run history, reading BigQuery only through authorized views. The 200k gold table lands in BigQuery via the Spark connector's direct write, and a generated model card replaces hand-written claims. Now, because v2.0 shipped the scheduled, monitored scoring pipeline (M3 met 2026-10-26), but its evidence is only visible as Markdown on GitHub.

## Problem statement

A reviewer who clicks through from a CV today finds a README, a ledger on a side branch and badges. Nothing shows the system as the product a team lead would use, or proves at a glance that it is fresh. The planned Community Cloud route would also have put the project's only long-lived credential into an otherwise keyless design.

## Target users

| Persona | Moment | Job to be done |
|---|---|---|
| Hiring reviewer (DE/DS/MLE) | Cold click from README/CV; ~60 s | "Is this real, does it run, is it honest about its limits?" |
| Simulated clinical team lead (the frame, not a real user) | Weekly call round | "Which of this week's discharges should my team call first, and why?" |
| Owner | After each Monday tick | "Did the tick run, did tests pass, is calibration in the band?" |

## Current state

- **Data:** `serving.predictions` (partitioned `as_of_date`, filter required; ids, reason, score, rank, provenance; **no features**), `serving.scoring_runs`, `serving.monitoring` (partitioned `started_at`; verdicts `ok/alert/insufficient_data/incomplete_cohort`; O/E with exact-Poisson CI and band) — `infra/bigquery.tf:31-72`. No views; no BigQuery gold table; gold is GCS Parquet (`v2/main_200k`, fingerprint `80edd13d…63f3`).
- **Reusable:** "stale after" rule `src/readmission_risk/monitoring/ledger.py:117-135`; `DEMO_NOTICE` `src/readmission_risk/scoring/constants.py:18`; palette `src/readmission_risk/reporting/style.py:61-77`; import-boundary test pattern `tests/scoring/test_pipeline.py:462-487`; unpickle-trap helper `tests/scoring/helpers.py:308`; hand-built frames in `monitoring/store.py` (no `db-dtypes`).
- **Absent:** `src/readmission_risk/dashboard/`, streamlit/altair/db-dtypes pins, a Dockerfile, Artifact Registry, Cloud Run, a dashboard SA, connector-write code, `docs/model-card.md`.
- **Tests that must change deliberately:** `tests/test_docs.py:339` (exactly 3 docs pages), `:119`/`:477` ("streamlit.app" banned), `:452-463` (roadmap/status "not started"); `tests/test_lockfiles.py:46` hardcodes `LOCK_PAIRS`.
- **Stale text:** `docs/results.md:3` (and `scoring.md`, `setup.md`) still call the v2 population "planned"; CLAUDE.md says `QueryUsagePerUserPerDay` can be set "on that service account" — it is project-wide per principal and approximate.

## Proposed solution

1. **Gold → BigQuery:** the 200k Big Join rerun on Dataproc with an added direct write into `serving.gold` (Terraform-created, partitioned, clustered, filter-required), preceded by a faithful throwaway-table test; the rerun must reproduce the existing fingerprint.
2. **Authorized views:** a new `dashboard` dataset with three views (call sheet = predictions ⋈ gold feature subset without labels and `organization_utilization`; scoring runs; monitoring), the only thing the runtime SA can read.
3. **The app:** `src/readmission_risk/dashboard/` — three views + chrome (honesty strip modelled on Duke's Model Facts, heartbeat, provenance footer, synthetic notice), hardened Streamlit config, fixed parameterised queries, hours-long cache.
4. **Container + deploy:** a digest-pinned image with a Linux-compiled hash lockfile, Artifact Registry, a dispatch-only workflow via WIF; Terraform owns every service setting.
5. **Cost guard:** max-instances 1, a short request timeout, a budget-triggered kill function scoped to the dashboard.
6. **Docs:** a generated model card with a subgroup calibration table, a README "60-second tour" with screenshot and live link, the stale "planned" wording fixed.
7. **Hygiene:** guard-8 champion check; pip-audit/osv-scanner; Trivy image scan; Dependabot `docker` ecosystem.

## Scope

**In:** items 1-7 above; Terraform for the dataset, views, gold table, Artifact Registry (cleanup policy), Cloud Run service, three SAs (runtime, deploy, kill), the dashboard budget, Pub/Sub topic and function; tests; docs; the `v3.0` tag.

**Out (named follow-ups):** Streamlit Community Cloud and any SA key; push-triggered deploys; load balancer / Cloud Armor; per-row contribution ("why") column; demographic columns; a model/gate view in the app; visitor tracking; formal WCAG audit; SLSA L3 / Binary Authorization; FR16 portable fallback (blocked on follow-up 7); label v2; as-of scoring; hosted MLflow; pandas 3 (the dashboard image could take it but stays on 2.3.3 for parity — Open Questions).

## User stories

- As a **reviewer**, I open the link and within one screen see what this is (honesty strip), that it ran recently (heartbeat) and a ranked call sheet — without scrolling or reading docs.
- As the **team lead persona**, I see this simulated week's Top-N discharges with admission reason, score as a percentage and a few context features, so I know whom to call first and roughly why.
- As the **owner**, after a Monday tick I see the new simulated date, data-test counts and the O/E point against its band, with "insufficient data" / "incomplete cohort" rendered differently from alerts, and can jump to the GitHub run.
- As the **owner**, if the scheduled workflow stops (60-day auto-disable, dropped slots), the heartbeat says "stale since <real date>" without my having to look elsewhere.
- As the **owner**, if a bot floods the app, it goes offline by itself before the month's bill matters, and one runbook command brings it back.

## Functional requirements

**Gold in BigQuery**
- **FR1** Terraform creates `serving.gold`: the 24 gold columns, DAY-partitioned on the discharge date (`index_stop`), clustered (e.g. `admission_reason_code`), `require_partition_filter = true`, deletion protection (passes the existing `tests/test_infra.py:418` rules); its description carries the gold fingerprint and label definition.
- **FR2** A **throwaway MERGE test** before any production write: a table with gold's exact settings (partitioned, clustered, filter required), written under the production custom role with `materializationDataset` set to a dedicated `scratch_gold` dataset (1-day table expiry). Record the overwrite path taken, job IDs, bytes billed, and whether the replace was atomic. **Pre-registered "unacceptable":** a partition-filter error, a stale partition surviving, a need for `tables.create`/`delete` on `serving`, or a schema change → the fallback FR4.
- **FR3** The Big Join gains an opt-in direct write (`writeMethod=direct`, STATIC overwrite mode, `materializationDataset=scratch_gold`), passed only by the submitter for v2/main runs. `dataproc-runtime` SA gets `scorerTableWriter` on `serving.gold` only, `dataEditor` on `scratch_gold`, project `jobUser` — **no create/delete on `serving`**. The 200k rerun's fingerprint must equal `80edd13d…63f3`, and BigQuery row/positive counts must equal `_gold_metadata.json`.
- **FR4** **Fallback** (if FR2 fails): write-audit-publish, the scorer's existing pattern — load into `scratch_gold`, ASSERT tests, one DML transaction into `serving.gold`. No load job ever writes into `serving`.
- **FR5** Data tests `GOLD-01…` (ASSERT scripts, labelled, NULL fails): row count and positive count equal the metadata; `encounter_id` unique; no NULL partition keys; every `serving.predictions.encounter_id` of the champion's gold fingerprint exists in gold. Run after each gold write; results recorded.

**Authorized views**
- **FR6** Terraform creates dataset `dashboard` (us-central1) with views `v_call_sheet` (predictions ⋈ gold on `encounter_id`, exposing the V5 columns plus `as_of_date` and the discharge date; **no** `is_readmitted`, `label_horizon`, `organization_utilization`, demographics), `v_scoring_runs`, `v_monitoring`, authorized on `serving` via `google_bigquery_dataset_access` only (preferably one dataset grant with `target_types = ["VIEWS"]`).
- **FR7** A test forbids `google_bigquery_dataset_iam_*` resources and inline `access` blocks on any dataset that grants views (they silently wipe authorizations).
- **FR8** Every dashboard query names constant partition predicates via query parameters (`as_of_date`, the discharge-date range, a `started_at` range for ops views); none uses `CURRENT_DATE`/`CURRENT_TIMESTAMP` (breaks pruning and the result cache). A test/runbook dry-runs each query and asserts scanned bytes under the cap.
- **FR9** Runtime SA `dashboard-runtime`: `dataViewer` on `dashboard` only + project `jobUser`; nothing on `serving`; no `readSessionUser` (client uses `create_bqstorage_client=False`).

**App**
- **FR10** `src/readmission_risk/dashboard/` (entrypoint `app.py`) renders the **chrome** on every view: honesty strip (3-4 lines, Model Facts style: intended use = simulated triage demo; not for clinical use; "a reason-code lookup does as well"; model card link) whose text derives from `DEMO_NOTICE`/a shared constant (a test keeps them equal, never a copy); heartbeat; provenance footer (champion bundle id, gold fingerprint, git commit of the deployed image, Synthea credit with Apache-2.0 link); persistent "synthetic data, demo only, not for clinical use" notice.
- **FR11** **Heartbeat:** simulated date of the newest succeeded tick, tests passed, champion bundle, and the real time the last tick ran. Freshness is three-state: ok; warn after 8 days; **stale since <date>** after the `ledger.py` rule (newest succeeded scheduled weekly tick + 2 periods = 14 days), imported from one shared pure function, not reimplemented. The only wall-clock element.
- **FR12** **Call sheet** (landing): run-date picker of published dates (newest first, default newest); table sorted by rank with the V5 columns, score as a percentage, Top-N as a labelled band ("call this week"), IDs labelled synthetic; the admission reason's historical rate shown as the "why" (from a view/aggregate computed in the view, no label row exposed).
- **FR13** **Monitoring:** an Altair chart of O/E per simulated week with the exact-Poisson CI as a shaded band and the alert band as horizontal lines; `alert` marked distinctly; `insufficient_data` / `incomplete_cohort` drawn hollow/grey with a text label (not as alerts) and observed positives vs the floor in the tooltip; burn-in ticks labelled as in the ledger; a text table alternative below.
- **FR14** **Run history:** recent `scoring_runs` and `monitoring` rows — status (text + icon), trigger, cadence, tests passed, link to the GitHub run.
- **FR15** **Queries:** a small fixed set, `st.cache_data(ttl=<hours>)`; user input only selects among values the app itself listed (no free-text into SQL or cache keys beyond the listed dates); each sets `maximum_bytes_billed` (proposed 200 MiB, tightened after measurement) and job label `component=dashboard`.
- **FR16** **Error states:** each query failure class (BigQuery unreachable, bytes cap, quota, empty result) renders a text panel naming it and what still works; no traceback ever reaches the browser.
- **FR17** **Hardened `.streamlit/config.toml`**, asserted by a test: `client.showErrorDetails = "none"`, `browser.gatherUsageStats = false`, `client.toolbarMode = "viewer"`, `server.fileWatcherType = "none"`, `server.runOnSave = false`, `server.headless = true`, XSRF and CORS on, reduced `maxUploadSize`/`maxMessageSize`, `server.allowedHosts` restricted to the service host; no `st.file_uploader` anywhere (source test).
- **FR18** **Import boundary:** a subprocess and source-ban test: the dashboard modules never import mlflow, pyspark, sklearn, xgboost, cloudpickle/pickle, `readmission_risk.bundles`, `readmission_risk.scoring.pipeline` or `readmission_risk.models.*`; it never unpickles (unpickle trap armed in its tests); `google.cloud.bigquery` allowed.
- **FR19** **Styling:** colours/themes from the `reporting/style.py` tokens, moved into a dependency-free module shared by matplotlib and Altair; status never by colour alone.

**Container, deploy, cost guard**
- **FR20** `requirements-dashboard.in` → `requirements-dashboard.txt`, hash-locked, compiled **inside** `python:3.14-slim` by `make lock-dashboard`, registered in `LOCK_PAIRS`; streamlit, altair, pandas, pyarrow (≠ 25.0.0), google-cloud-bigquery, uvicorn ≥ 0.52 (Streamlit 1.66 proxy warning); db-dtypes only if the hand-built-frame route is not used.
- **FR21** `Dockerfile`: `FROM python:3.14-slim@sha256:…` (tag + digest), non-root user, `pip install --require-hashes`, installs the package without dev extras, runs Streamlit with `--server.port=$PORT --server.address=0.0.0.0`.
- **FR22** Terraform: Artifact Registry repo `dashboard` with a cleanup policy keeping the 2 most recent images; Cloud Run v2 service (placeholder image, gen1, startup CPU boost, min 0 / max 1 instances, request timeout 15-30 min, `invoker_iam_disabled = true`, ingress `all`, runtime SA `dashboard-runtime`, label `component=dashboard`, `lifecycle.ignore_changes` on image and client/annotation fields).
- **FR23** `deploy.yml`: `workflow_dispatch` only, on `main`; builds in the runner, pushes by digest, `gcloud run deploy --image <digest>` (waits for readiness), smoke-checks `/_stcore/health` = 200 and the served revision's image digest. Deploy SA `dashboard-deploy` via WIF, impersonable only by a `workflow_ref` principalSet for `deploy.yml`: `run.developer` on the service, `iam.serviceAccountUser` on `dashboard-runtime`, `artifactregistry.writer` on the repo — **no** `run.admin`, no `--allow-unauthenticated`. Actions SHA-pinned; zizmor clean.
- **FR24** **Kill switch:** a budget filtered to label `component=dashboard` / Cloud Run (proposed CHF 3) → Pub/Sub topic → a function running as `dashboard-kill` (`run.developer` on the service only) that sets max-instances to 0 at 100% actual; idempotent, logs its action; re-enable runbook; tested offline and live by publishing a synthetic budget message.

**Docs and hygiene**
- **FR25** `docs/model-card.md`, generated by a script from the champion manifest, `prereg.yaml` `phase_b`, committed gate reports and `deploy/PROMOTIONS.md`; sections in Model Card / TRIPOD+AI style (intended use, out of scope, data, label, metrics vs the lookup, frozen-v1 validation, override disclosure, limitations) plus a **subgroup calibration table** (age band, sex, race/ethnicity) computed offline from the champion and gold and committed as a report; a drift test regenerates and compares.
- **FR26** README "60-second tour" with a landing-view screenshot (light/dark), the live URL, Mermaid diagram updated (dashboard and gold solid, clickable nodes); stale "planned" wording fixed in `results.md`/`scoring.md`/`setup.md`; `tests/test_docs.py` updated deliberately (4 pages; the `streamlit.app` rule replaced by a `run.app` URL rule; status/roadmap wording).
- **FR27** **Guard-8 champion check:** the champion's `library_versions` read from its tracked manifest copy (or written into `deploy/champion.json`), with a test failing when `requirements.txt` pins differ in a way guard 8 would refuse.
- **FR28** **CI hygiene:** pip-audit (or osv-scanner) over all lockfiles; Trivy scan of the built image in `deploy.yml` (fail on HIGH/CRITICAL with a fix available); Dependabot `docker` ecosystem for the Dockerfile with a cooldown (pip stays excluded).

## Non-functional requirements

- **Performance:** cold start to first render ≤ 15 s (median of 5 measured cold starts); warm view switch ≤ 3 s; each query ≤ `maximum_bytes_billed`; monthly dashboard query volume ≪ the 1 TiB free tier; scales to 0 within the request timeout after the last tab closes (observed on the instance-count metric).
- **Accessibility:** Okabe-Ito tokens; text + icon status; text alternatives for charts; one documented manual pass (keyboard, contrast, light/dark). No WCAG audit.
- **Security & privacy:** keyless everywhere; least-privilege SAs (runtime / deploy / kill); authorized views only, no labels exposed; hardened config, no tracebacks, no telemetry, no uploads; synthetic data (no PHI) with a persistent notice.
- **Cost:** normal month ≈ CHF 0-0.50; a flood bounded by FR24 to ≈ CHF 5-10 (billing-data lag ≈ up to a day at ≈ $1.7/day compute plus egress in that window); the existing CHF 20 + CHF 5 project budgets remain.
- **Degraded modes:** BigQuery down → FR16 panels; kill switch fired → service unavailable (the README screenshot carries the payoff); schedule stale → heartbeat "stale since".
- **Multi-tenant / i18n:** n/a; English only.

## Success metrics (v3.0 tag criteria, per V11)

| # | Metric | Measured by |
|---|---|---|
| M1 | Live URL serves all three views; deploy smoke green | `deploy.yml` run + owner screenshot |
| M2 | Data path proven end to end | FR2 record; FR3 fingerprint and counts equal; GOLD tests pass; views return rows with features for the newest tick |
| M3 | Docs complete | model card generated, drift test green; README tour; `test_docs` updated |
| M4 | Heartbeat follows a real scheduled tick, no redeploy | after a scheduled Monday tick, within the cache TTL, the app shows the new simulated date |
| M5 | Terraform owns the service | `terraform plan` = No changes after a CI deploy |
| M6 | Scale to zero and cost guard work | instance count reaches 0 after idle; the kill function sets max 0 on a synthetic message; billing for the v3 days recorded |
| M7 | Cold start within budget | 5 measured cold starts, median ≤ 15 s |

## Risks & mitigations

| Risk | L | I | Mitigation |
|---|---|---|---|
| Connector MERGE rejected on a filter-required table | Med | Med | Faithful FR2 test first; pre-registered fallback FR4 (write-audit-publish) |
| `materializationDataset` ignored on write → temp table in `serving` | Low | Med | FR2 checks where the temp table lands; no create grant on `serving`, so it fails loudly |
| Authorized views silently lost (an IAM resource wipes the ACL) | Med | High | FR7 test; one resource style |
| Flood or forgotten tab bills the instance | Low | Med | max 1, short timeout, FR24 kill switch |
| `ignore_changes` does not stop image drift (provider 8.7.0) | Med | Low | M5 acceptance check; pinned ignore list |
| An HTTP health probe forces instance-based billing | Low | Low | No custom HTTP probe (TCP startup only) until verified |
| Cold start > 15 s | Med | Low | gen1 + CPU boost, lazy client in `st.cache_resource`; M7 measures |
| Streamlit 1.66 WebSocket/proxy break | Low | Med | uvicorn ≥ 0.52 pin; reconnect test after the timeout |
| Heartbeat lags a tick by the cache TTL | High | Low | Documented: TTL of hours ≪ weekly cadence; M4 allows for it |
| Demographic optics | Low | Med | V5: no demographic columns; subgroup table in the card |
| Scope creep from carry-overs | Med | Med | V10 Out list; guard-8 the only addition |

## Implementation hints (refined in `/eg-new-feature`)

Suggested slices:
0. **v2 leftovers, in parallel:** T4.5 (Thu-Sun); the final fired-vs-due count (appendable after 2026-10-28 23:59 UTC); L16 calendar reminder (last `monitor.yml` commit + 50 days) + billing check.
1. **Gold in BigQuery** (FR1-FR5, live): Terraform table + `scratch_gold`; throwaway MERGE test; 200k rerun with direct write (~30 min, ~CHF 0.8; raw retained until ~2027-10); GOLD data tests.
2. **Views + runtime SA** (FR6-FR9): dry-run each query to prove pruning. The call-sheet view exposes the gold discharge date so the outer parameter prunes gold as well as predictions; the published-dates list comes from `v_scoring_runs` (succeeded + published) over a constant `started_at` range.
3. **App, offline** (FR10-FR19): `streamlit.testing.v1.AppTest` with a fake BigQuery (the `tests/warehouse/fake_bigquery.py` pattern); move the stale rule into a pure shared module; move the style tokens into a dependency-free module.
4. **Container, deploy, cost guard** (FR20-FR24, live): Linux lockfile, Dockerfile, Artifact Registry, Cloud Run, `deploy.yml`, kill function (code under `infra/functions/` or `ops/`, with tests).
5. **Docs + hygiene** (FR25-FR28), then release (tag `v3.0` when M1-M7 hold).

Branching mirrors v2 (a `v3` branch with a draft PR into `main`); the live slices need `main` for WIF, so decide in slice 1 when to merge (as v2's D1 did).

## Open questions

1. **Live rendered-content smoke:** Streamlit renders client-side, so checking rendered text live needs a headless browser (Playwright, a new dependency). Is health + revision digest + AppTest in CI enough for M1?
2. **Gold's dataset:** `serving.gold` (elephant default: one ACL, table-level custom-role grants) vs its own `gold` dataset.
3. **pandas in the dashboard image:** keep 2.3.3 for parity (default) or take pandas 3 (no PySpark there)?
4. **Kill-switch budget threshold** (CHF 3 proposed) and whether budget label filters cover the egress SKUs.
5. **Subgroup calibration table:** which subgroups, a minimum cell size before a cell is shown, and whether it appears in the card only (default) or also in the app.
6. **Verify before or during the slices (from research):** MERGE on a filter-required table; `materializationDataset` honoured by the bundled connector; the Dataproc runtime 3.0's current connector patch; current Cloud Run / Artifact Registry / BigQuery prices (an unconfirmed "August 2026 price refresh"); HTTP probes vs billing mode; `ignore_changes` on provider 8.7.0; whether WebSocket instances bill at instance-based rates; the `workflow_ref` claim under a GitHub environment (not planned); Synthea NOTICE file; the UX citations (PMC8821785 quotes, LACE bands, HTI-1 attribute names).
7. **CLAUDE.md corrections to apply when this PRD lands:** the dashboard paragraph (Community Cloud → Cloud Run, key exception removed), the `QueryUsagePerUserPerDay` wording, the "BigQuery holds" line.

## Sources & references

**Cloud Run:** websockets https://docs.cloud.google.com/run/docs/triggering/websockets · session affinity https://docs.cloud.google.com/run/docs/configuring/session-affinity · billing settings https://docs.cloud.google.com/run/docs/configuring/billing-settings · execution environments https://docs.cloud.google.com/run/docs/about-execution-environments · general tips https://docs.cloud.google.com/run/docs/tips/general · deploying (roles) https://docs.cloud.google.com/run/docs/deploying · public access https://docs.cloud.google.com/run/docs/authenticating/public · Streamlit quickstart https://docs.cloud.google.com/run/docs/quickstarts/build-and-deploy/deploy-python-streamlit-service · pricing https://cloud.google.com/run/pricing · deploy-cloudrun releases https://github.com/google-github-actions/deploy-cloudrun/releases · Terraform drift thread https://discuss.hashicorp.com/t/ignore-existing-cloud-run-service-management/67400 · provider issue https://github.com/hashicorp/terraform-provider-google/issues/25560

**Artifact Registry / Analysis:** https://cloud.google.com/artifact-registry/pricing · https://docs.cloud.google.com/artifact-registry/docs/repositories/cleanup-policy · https://cloud.google.com/artifact-analysis/pricing

**Streamlit:** config https://docs.streamlit.io/develop/api-reference/configuration/config.toml · 2026 release notes https://docs.streamlit.io/develop/quick-reference/release-notes/2026 · advisories https://github.com/streamlit/streamlit/security/advisories · PyPI https://pypi.org/pypi/streamlit/json

**BigQuery / connector:** 0.44.0 source https://github.com/GoogleCloudDataproc/spark-bigquery-connector/blob/0.44.0/spark-bigquery-connector-common/src/main/java/com/google/cloud/spark/bigquery/write/context/BigQueryDirectDataSourceWriterContext.java · README https://github.com/GoogleCloudDataproc/spark-bigquery-connector/blob/master/README.md · CHANGES https://github.com/GoogleCloudDataproc/spark-bigquery-connector/blob/master/CHANGES.md · issue #1476 https://github.com/GoogleCloudDataproc/spark-bigquery-connector/issues/1476 · Storage Write API https://docs.cloud.google.com/bigquery/docs/write-api · partitioned tables https://docs.cloud.google.com/bigquery/docs/querying-partitioned-tables · authorized views https://docs.cloud.google.com/bigquery/docs/authorized-views · dataset_access https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/bigquery_dataset_access · cached results https://docs.cloud.google.com/bigquery/docs/cached-results · custom quotas https://docs.cloud.google.com/bigquery/docs/custom-quotas · cost best practices https://docs.cloud.google.com/bigquery/docs/best-practices-costs · Parquet loads https://docs.cloud.google.com/bigquery/docs/loading-data-cloud-storage-parquet

**Billing:** https://docs.cloud.google.com/billing/docs/how-to/disable-billing-with-notifications

**UX / clinical precedent:** https://pmc.ncbi.nlm.nih.gov/articles/PMC8821785 (verify) · https://www.rwjbh.org/documents/nursing/nurses-hub/Unplanned-Readmission-Risk-Stratification-Tool-SBAR.pdf · Wong et al. 2021 https://jamanetwork.com/journals/jamainternalmedicine/fullarticle/2781313 · LACE https://pmc.ncbi.nlm.nih.gov/articles/2845681 · NannyML https://nannyml.readthedocs.io/en/v0.4.1/tutorials/performance_estimation.html · Arize https://docs.arize.com/arize/machine-learning/concepts-ml/how-to-send-delayed-actuals · Goddard et al. 2012 https://academic.oup.com/jamia/article/19/1/121/732254 · Sendak et al. 2020 "Model Facts" (npj Digit Med 3:41) · Model Cards https://arxiv.org/abs/1810.03993 · TRIPOD+AI (BMJ 2024;385:e078378) · HTI-1 https://www.healthit.gov/sites/default/files/page/2023-12/HTI-1_DSI_fact sheet_508.pdf · Obermeyer et al. 2019 (Science) · dbt freshness https://docs.getdbt.com/reference/commands/freshness

**Supply chain:** https://docs.docker.com/scout/integrations/source-code-management/github/ · https://edu.chainguard.dev/chainguard/containers/staying-secure/updating-images/dependabot/ · GitHub environments https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments · Synthea license https://github.com/synthetichealth/synthea/blob/master/LICENSE

## Out-of-scope follow-ups

FR16 portable fallback (after the cross-platform reference policy, follow-up 7); label v2; as-of scoring; per-row LR contributions (predictions schema v3); a model/gate view in the app; a Playwright live content check (if Open Question 1 says no); Cloud Armor / load balancer; a formal WCAG AA audit; hosted MLflow; pandas 3 in the main lockfile.
