# Brainstorm: v2 scope (cloud + 200k scale-up + live dashboard, MLOps-first) and v1 closeout

**Date:** 2026-10-09
**Workflow:** `/eg-brainstorm` (elephant/goldfish, inverted: parallel goldfish, elephant synthesizes), three rounds
**Stage:** Raw concept → converged on a scope
**Chosen direction:** "Recommended v2 synthesis" (bundle-first spine → 200k Dataproc + pre-registered retrain → weekly delayed-label monitoring tick; repo-as-control-room + heartbeat call sheet). See "Decision".
**Next step:** `/eg-prd` to lock the architecture decisions this direction reopens (see "Open decisions for /eg-prd"), then slice into `/eg-new-feature`. The v1 closeout list can run first.

Facts marked **(verified)** were checked against the repo or a primary source during this session; everything else is a goldfish claim or arithmetic and should be re-checked before it is relied on.

---

## State of v1 at the start (verified)

- All four slices plus 2b are committed; the repo is public at `github.com/fma00/synthea-readmission-risk`, `main` == `origin/main`, CI on `56fa832` passed, the MIT `LICENSE` exists, and the pre-rewrite backup bundle has been deleted.
- Nothing left in v1 blocks the next phase; the remaining items are polish or deliberately deferred follow-ups (closeout list below).

## How the framing evolved

| Round | Seed change | Lenses |
|---|---|---|
| 1 | "What's left in v1; can we move to visualization + scale-up; define it." Raw concept, ~5 concepts, web on. | technical, UX, contrarian |
| 2 | User fixed: 200k on the cloud next; live Streamlit (scope open); TWO BigQuery tables (scores kept apart from data); audience DE + DS + MLE/MLOps; next application round after 30 sent. | technical, UX, contrarian |
| 3 | User fixed: **keyless/scheduled MLOps shape** (MLE/MLOps roles highest priority); retraining at 200k mandatory, frozen-v1 external validation optional; **reinvestigate Python/PySpark versions** (code reportedly ran on a newer Python). Timebox placeholder 4–6 weeks (not confirmed). | version research, technical, UX, contrarian |

Round 1–2 concepts that were superseded: Shard Factory (cloud-sharded generation; unnecessary because a single 200k run is ~25–30 min — 10k took 78 s **(verified)**), Serving Layer First, Beat-the-Lookup quiz, "Did 20x the data change the answer?" results page, Scale Proves the Simulator (honest-negative-result headline without cloud), Freeze/Ship/Apply one-weekend cloud, Parity-pinned thin cloud slice (the round-2 recommendation, replaced once the user chose the MLOps-rich shape).

---

## Findings that shape any v2

1. **Versions (verified, round 3).**
   - PySpark has supported Python 3.14 since **4.1.0** (PyPI classifiers; SPARK-54286). CLAUDE.md's note that 3.14 support "only landed in 4.2.0" is wrong.
   - The repo's own `requires-python = ">=3.12,<3.13"` is what blocks newer Python. The version-research goldfish reports 456/456 tests passing on Python 3.14.7 with the current lockfile, and 456/456 on Python 3.12 with `pyspark==4.0.1`.
   - Dataproc Serverless ("Managed Service for Apache Spark") newest runtime **3.0.14 = Spark 4.0.1 / Python 3.12 / Java 21 / BigQuery connector 0.44.0**; no Spark 4.1 runtime; sub-versions cannot be pinned; runtime **2.3 LTS** also exists (Spark 3.5.3, Python 3.11); 2.2 LTS is still the default. Custom containers cannot change the Spark version. The bundled BigQuery connector overrides `--jars`/`--packages` unless `dataproc.sparkBqConnector.version`/`.uri` is set.
   - Dataproc **cluster** image 3.0 (GA 2026-07-15) ships **Spark 4.1.2 / Python 3.12**.
   - PySpark's driver/worker Python-minor-version check applies within one Spark application, so local 3.14 and a cloud batch on 3.12 do not conflict.
   - The Big Join uses no Spark-4.1-only API.
   - pandas 3: PySpark docs cap `pandas <3.0.0` in all releases; under pyspark 4.2.0 + pandas 3.0.6 there are 7 failures in the project's own pandas code (string dtype, `[us]` datetimes). Still deferred.
2. **"Only the base path changes" is false today (verified):** `big_join.py:105` (`csv_path.open`), `big_join.py:402` (`output_dir.exists()`), `gold_metadata.py:133` (`os.replace`) assume a local filesystem.
3. **External validation vs retraining:** the scorer's `gold_fingerprint`/`reference_date`/split guards (correctly) refuse frozen v1 models on v2 gold, so frozen-model validation needs its own evaluation mode. The guards must not be weakened. Retraining is a separate, mandatory result. Both must be pre-registered before v2 data exists.
4. **Scoring is coupled to local MLflow state:** `scoring/runs.py::validate_run` opens the SQLite store, reads tags and params, and downloads artifacts. Scheduled scoring off the laptop needs either a model bundle + manifest or an MLflow store reachable from the runner.
5. **Cost and permission traps (goldfish-reported; check during setup):** `spark.dynamicAllocation.maxExecutors` defaults to 1000; the dashboard service account needs `roles/bigquery.jobUser` at project level (plus `readSessionUser`, or `create_bqstorage_client=False`); the Storage Write API has no `WRITE_TRUNCATE`, so predictions go through a load job on the `predictions$YYYYMMDD` partition decorator; new-project CPU/Dataproc quotas may need increase requests (days of lead time); a budget alert notifies, it does not cap spend.
6. **Streamlit Community Cloud sleeps after ~12 h idle; keep-alive pings are unreliable** (forum reports). The README must carry the payoff on its own (hero image/GIF and sample table above the live link). Community Cloud cannot use WIF, so the app needs a service-account key: "keyless" must be stated with that exception, or the app must avoid credentials.
7. **GitHub disables scheduled workflows in public repos after 60 days without repository activity**, and cron is best-effort (runs can be late or dropped; avoid `:00`).
8. **Generation:** a single 200k run is plausible (~25–30 min, ~40 GB raw for the 10 tables; arithmetic), so no sharding and no reconciliation of `payers`/`providers`/`organizations` across shards. Generating on a short-lived spot VM writing to GCS avoids a 40 GB home upload.

---

## Round-3 concepts

### Version strategies (version research)
- **A, Match the managed runtime:** Python 3.12 + PySpark 4.0.1 everywhere. Simplest; a downgrade; locks out newer Python indefinitely; Google can move 4.0.x underneath.
- **B, Newest locally, compatible in the cloud (elephant's lean):** Python 3.14 + PySpark 4.1.3 for local, CI, training, scoring and Streamlit; the Big Join on Serverless runtime 3.0; a CI leg {Python 3.12 + pyspark 4.0.1} over `tests/pipeline`. Needs `requires-python` relaxed, `.python-version` bumped, a constraints file for the CI leg, and a CLAUDE.md correction.
- **C1, Own the runtime via a Dataproc cluster image 3.0:** keeps PySpark 4.1.x with one version set, but Python 3.12, cluster lifecycle work, and it reopens the locked Serverless choice.
- **C2, pip-installed PySpark `local[*]` on a VM/Cloud Run Job:** any version everywhere; loses the managed-Spark signal; Cloud Run's 8 vCPU / 32 GiB memory-backed disk is risky for the shuffle.

### Platform shape (technical)
- **Git is the registry, Actions is the orchestrator:** training exports an immutable model bundle (model + `manifest.json` with every fact the 13 guards check + sha256) to GCS; promotion is a pull request editing `deploy/champion.json` with the metric table against `prereg.yaml`; light jobs run in GitHub Actions via WIF; minimal `dbt-bigquery` (sources, tests, a `call_sheet` view). About $1–3/month.
- **GCP runs it, Actions only deploys:** Cloud Run Jobs (train, score) + Cloud Scheduler; MLflow registry alias `@champion` on a SQLite file in GCS with a home-built generation-precondition lock; native BigQuery `ASSERT` tests instead of dbt (amend the rule). Fewer guard changes, more Terraform, failures less publicly visible.

### What gets scheduled (technical + contrarian, independently)
- **Delayed-label monitoring loop:** each weekly tick advances a simulated clock, scores the new window, evaluates the cohort whose 30-day labels have now matured (calibration, Brier, top-N precision; rolling 13-week aggregate because weekly positives are too few), runs the data tests, and opens a GitHub issue on a pre-registered threshold breach. Retraining is a separate `workflow_dispatch` gate (retrain → evaluate vs champion and lookup → promote or refuse). The clock is labelled as simulated everywhere.

### Sequencing (contrarian)
- **Bundle-first spine:** before any 200k work, on the existing 10k gold: bundle + guards ported to the manifest, Terraform + WIF, and one manual end-to-end run writing predictions to BigQuery. If everything after this slips, the repo still shows IaC, keyless CI, a validated model artifact and BigQuery serving; the 200k run becomes a data swap. Scoring stops importing mlflow/pyspark.

### Reviewer surface (UX)
- **The repo is the control room:** scheduled-workflow badge, an auto-updated run ledger (`docs/run-ledger.md`), an Actions job summary per run, a Mermaid diagram whose nodes link to workflows and Terraform, and `docs/model-card.md` with promotion decisions (lookup comparison first). Works when the app sleeps and on phones.
- **Heartbeat on the call sheet:** one line above the Top-10 (last run, replay date, tests passed, champion version, freshness), labelled as a simulated clock, below and never above the honesty strip. The highest-signal line: "challenger did not beat champion + lookup → promotion refused."

---

## Decision: recommended v2 synthesis (chosen by the user)

**Spine (weeks 1–2, on 10k):**
1. Model bundle + manifest; map each of the 13 guards explicitly, dropping none silently; promotion via a pull request on `champion.json`.
2. Terraform (applied from the laptop; CI runs only `fmt`/`validate`): GCS state bucket via a bootstrap script, buckets, BigQuery datasets, service accounts, a WIF pool restricted to `repository == fma00/synthea-readmission-risk && ref == refs/heads/main`, the budget alert.
3. Supply chain before any `id-token: write`: SHA-pinned actions, Dependabot for actions, zizmor, no `pull_request_target`, `id-token` only on `schedule`/`workflow_dispatch` jobs.
4. One manually dispatched run scoring 10k into BigQuery `predictions` (load job, partition replace).

**Scale-up (weeks 3–4):**

5. Pre-registration committed before generation: seed, `reference_date`, test-cutoff rule (a formula of `reference_date`), metrics, promotion criteria, and an explicit "the lookup still wins" outcome.
6. 200k generation on a spot VM writing to GCS.
7. `gs://`-ready Big Join.
8. Dataproc Serverless runtime 3.0 with `maxExecutors` capped; gold direct-written to BigQuery; GCS Parquet + `_gold_metadata.json` as the canonical gold.
9. Retrain → promotion-gate pull request.

**Operations (weeks 5–6):**

10. Weekly delayed-label monitoring tick (idempotent partition overwrites).
11. Data tests.
12. GitHub issue on threshold breach.
13. Run ledger, badge and job summary.
14. Streamlit call sheet with honesty strip, heartbeat and provenance footer.
15. `docs/results.md` and the model card.

**Kill triggers:**
- WIF not working by day 9: one narrowly documented workaround; never service-account keys in CI.
- Guard port needing more than 20% semantic test changes: keep the manifest as an additive layer.
- Dataproc not green by end of week 4: the same job `local[*]` on the VM reading GCS, documented.
- Monitoring tick not idempotent by week 5: ship scoring + tests only.

**Out:** hosted MLflow, Vertex AI, Cloud Run dashboard, Terraform apply in CI, daily cron, feature-drift statistics, scheduled retraining, hyperparameter tuning, SHAP, label v2, as-of scoring, pandas 3.

**Definition of done:**
- `main` green; `v2.0` tagged.
- 5+ consecutive green scheduled ticks.
- A deliberately bad model visibly rejected by the gate.
- A seeded bad row visibly failing the data tests.
- `terraform plan` clean; no service-account keys in CI.
- 200k results reported against the committed pre-registration.
- Every README claim links to a run or a table.

## Open decisions for /eg-prd (elephant's leans)

1. **Version strategy:** A / B / C1. Lean **B**.
2. **Where jobs run:** GitHub Actions (public history, free; 60-day disable) vs Cloud Run + Scheduler (no decay, more Terraform, hidden). Lean **Actions**, with a staleness banner and a calendar reminder.
3. **dbt:** v2 now has ~4 tables (gold, predictions, scoring_runs, monitoring) plus a call-sheet view and SQL monitoring metrics, i.e. real warehouse-side transformations. Lean **adopt minimal `dbt-bigquery`** (sources, tests, `call_sheet` + `monitoring` models, separate lockfile), honouring the locked rule. Alternative: amend the rule and use native `ASSERT`s.
4. **"Keyless" honesty for the dashboard:** a documented single read-only key exception (`dataViewer` + `jobUser`) vs the scheduled job publishing a public snapshot. Lean **documented exception**.
5. **Timebox:** the plan is ~6 weeks; the real window is not yet confirmed.
6. **Frozen-v1 external validation:** stretch goal; needs its own evaluation mode outside the scorer.

## Standards flags carried forward

- CLAUDE.md's Python-version note is factually wrong (3.14 support landed in PySpark 4.1.0) and omits Serverless runtime 2.3 LTS.
- Version strategy B reverses the documented 3.12 pin; C1 reverses the locked Serverless choice.
- The bundle replaces MLflow as the scorer's source of truth (MLflow stays the experiment record), which changes slice 4's documented `load_logged_model` contract and touches the 123 scoring tests. It is the riskiest code change.
- `id-token: write` in a public repo makes SHA-pinning mandatory. A `contents: write` ledger commit must run in a separate job without `id-token`.
- Two or more BigQuery tables reverse "BigQuery holds the gold table only" and fire the locked dbt trigger. Decide explicitly, before the second table exists.

## v1 closeout to-do list

**Do before v2 (~1.5 days):**

| # | Item | Effort |
|---|---|---|
| 1 | Tag `v1.0` + GitHub release; do v2 on a branch (30 applications point at `main`) | 30 min |
| 2 | README: 5–10-row sample Top-N table, reliability plot, one guard refusal shown, a "60-second tour for ML/MLOps reviewers" with deep links (split, guards, gold metadata/fingerprint, hash-locked deps, honest baseline); repo About text, topics, social preview, pin on profile | 3–4 h |
| 3 | `pyproject.toml` license metadata (+ the `requires-python` change once the version strategy is decided) | 15 min |
| 4 | Correct the CLAUDE.md version note | 15 min |
| 5 | Harden the ~6-day label/test leakage margin into an assertion in the split code (the pre-registration depends on it). **Done 2026-10-10** as a purge + post-condition (owner decision; `notes/eg-new-feature/label-horizon-purge-2026-10-09.md`) | 2–4 h |
| 6 | SHA-pin the three actions; Dependabot for `github-actions`; zizmor | 1 h |
| 7 | GCP project + budget alert + quota check/increase request | 1 h + wait |
| 8 | Record the dbt decision in CLAUDE.md | 15 min |

**Fold into v2:** gold content fingerprint; lockfile-drift CI check (needed because the library-version guard would refuse a drifted scheduled run; needs a design for the Linux-only `nvidia-nccl` dependency); `DEMO_NOTICE` rewrite for the 200k population; `[project.scripts]`; hoisting the MLflow-state test fixture into `tests/conftest.py` (becoming manifest fixtures).

**Defer:** pandas 3 / numpy 2.5 (7 project-side failures + PySpark's `<3` cap); SECURITY/CONTRIBUTING; hyperparameter tuning; SHAP; hosted MLflow; Ubuntu 26 runner.

---

## Where the goldfish agreed / disagreed

**Agreed:**
- The honest "models ≈ reason-code lookup" result is the strongest asset; surface it, don't bury it.
- Show checkable evidence (runs, tables, cost receipts), not just architecture.
- Scale buys precision, not AUC.
- Pre-register before generating v2.
- The README must work without the live app.

**Disagreed:**
- Keep-alive (headless-browser action vs drop it).
- dbt (adopt vs amend).
- Actions vs Cloud Run for the runtime.
- Whether to harden the leakage margin before v2 or re-measure on v2. The elephant chose hardening.

## Sources (selected)

- PySpark on PyPI: https://pypi.org/pypi/pyspark/json; Python 3.14 support: https://issues.apache.org/jira/browse/SPARK-54286
- PySpark 4.2.0 install docs (pandas cap): https://spark.apache.org/docs/4.2.0/api/python/getting_started/install.html
- Serverless runtime 3.0: https://docs.cloud.google.com/dataproc-serverless/docs/concepts/versions/spark-runtime-3.0; runtime list: https://docs.cloud.google.com/dataproc-serverless/docs/concepts/versions/spark-runtime-versions; custom containers: https://docs.cloud.google.com/dataproc-serverless/docs/guides/custom-containers; autoscaling: https://cloud.google.com/dataproc-serverless/docs/concepts/autoscaling; pricing: https://cloud.google.com/dataproc-serverless/pricing; quotas: https://docs.cloud.google.com/dataproc-serverless/quotas
- Dataproc cluster image 3.0: https://docs.cloud.google.com/dataproc/docs/concepts/versioning/dataproc-release-3.0
- spark-bigquery-connector: https://github.com/GoogleCloudDataproc/spark-bigquery-connector; Storage Write API: https://docs.cloud.google.com/bigquery/docs/write-api-intro; partitioned loads: https://docs.cloud.google.com/bigquery/docs/load-data-partitioned-tables; access control: https://docs.cloud.google.com/bigquery/docs/access-control
- GitHub scheduled-workflow 60-day disable: https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows; tj-actions compromise: https://semgrep.dev/blog/2025/popular-github-action-tj-actionschanged-files-is-compromised
- Streamlit Community Cloud deploy/Python versions: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy; sleep reports: https://discuss.streamlit.io/t/streamlit-app-hibernating-even-after-daily-repo-pushes/97582
- Readmission-model literature (pooled AUC ~0.71): https://mijn.bsl.nl/article/10.1007/s11606-020-05638-z; ML vs LR: https://pmc.ncbi.nlm.nih.gov/articles/PMC12153988
- MLOps portfolio expectations: https://datatalks.club/docs/courses/mlops-zoomcamp/project/; https://letsdatascience.com/blog/the-ml-portfolio-that-actually-gets-you-hired-in-2026
