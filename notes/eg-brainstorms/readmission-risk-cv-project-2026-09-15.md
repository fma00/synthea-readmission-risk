# Brainstorm: readmission-risk CV/portfolio project

**Date:** 2026-09-15
**Workflow:** `/eg-brainstorm` (elephant/goldfish, inverted — multiple parallel goldfish, elephant synthesizes)
**Stage:** Raw concept → narrowed to hypothesis
**Chosen direction:** The Big Join + The Triage List (see "Decision" below)
**Next step:** `/eg-prd` to research and firm up open questions (warehouse choice, dashboard hosting, population scale) before design

---

## Seed v1

- **The thought:** Build an end-to-end ML system predicting a patient's 30-day readmission risk from synthetic Synthea EHR data (demographics, conditions, medications, procedures, prior utilization), demonstrating SQL/Spark, visualization/dashboarding, ML model training, MLOps, and agentic coding, with production ML practices on Google Cloud — as a clean, well-documented CV/portfolio project.
- **What I think the user is really asking:** A portfolio project that functions as a credible demonstration — proof of hands-on breadth across the modern data/ML/MLOps stack — not just a working model, with the repo itself holding up to scrutiny from a technical reviewer.
- **Stage:** Raw concept
- **Breadth target:** ~10 concepts
- **Web research:** Off

## Round 1 — 13 concepts (5 lenses + "what they missed" sweep)

### Architecture backbone
- **Managed-Everything BigQuery Spine** (technical) — BigQuery as sole source of truth, SQL-only feature engineering, BigQuery ML/Vertex AI training, Vertex Pipelines, Looker Studio/Streamlit dashboard.
- **Agent-in-the-Loop Pipeline** (technical) — an LLM agent as an actual running pipeline stage, paired with real Dataproc Serverless/PySpark for ingestion.

### Reviewer-facing front door
- **The Zero-Clone Live Demo** (reviewer experience) — primary artifact is a hosted, zero-setup live URL, optimized for a 90-second reviewer skim.
- **The Show-Your-Work Decision Trail** (reviewer experience) — primary artifact is 3-5 curated PRs narrating design doc → goldfish critique → precommit review.

### Structural risk warnings
- **Five Merit Badges, No Scout** (contrarian) — warns against five parallel, independently-deletable skill folders; pivot is one causal spine that forces all five skills to interact.
- **The AUC Nobody Should Believe** (contrarian) — warns of suspiciously high AUC from Synthea leakage; pivot is a documented "before/after" leakage audit as the headline.

### What's the actual spine
- **The One-Screen Verdict** (first-principles) — everything subordinate to one polished screen; other skills demoted to backing infrastructure.
- **The Workflow Is the Product** (first-principles) — agentic coding is the spine; the visible `/eg-*` trail is the primary artifact.

### Reframe the problem/domain
- **Credit Bureau for Patient Risk** (adjacent/lateral) — reframe as a credit-risk scorecard (score bands, reason codes, PSI/KS drift monitoring, MRM-style governance).
- **Time-to-Readmission, Not Will-They** (adjacent/lateral) — swap binary classification for survival/hazard modeling.

### Operational rigor as differentiator (what everyone missed)
- **The Itemized Bill** — GCP cost as a tracked metric, with a documented cost-optimization pass.
- **The Test Pyramid for a Model** — layered testing taxonomy (data contracts, feature unit tests, model behavioral tests, regression pinning) as the headline artifact.
- **Manufactured Drift** — deliberately generate two differing Synthea cohorts to prove the monitoring layer catches real shift.

**What agreed:** reviewer experience must not read as a checklist (3 lenses converged); raw metrics won't be trusted without rigor made explicit; BigQuery-only path risks the "Spark" claim being thin; agentic coding as something *real* (runtime or process) beats asserting it happened.

**What disagreed:** live demo vs. curated PR trail vs. single screen as primary artifact; agentic coding as runtime capability vs. process evidence; strict readmission framing vs. fintech/survival reframes; cost visibility as differentiator or not.

## User refinements (after round 1)

- **Target job family:** data engineer / data scientist, not ML engineer (widened later: the published README also shows ML-engineering evidence).
- **Timeline:** weeks — "show a pipeline, not do research."
- **Agentic coding framing:** dev-process evidence only, never a runtime pipeline component.
- **Reviewer front doors:** wants **both** — an engineer (docs/code) and a team lead (visual, clickable) — simultaneously, not a choice between them.
- **Domain framing:** keep binary 30-day readmission for v1; reframes (drift, survival, scorecard) are explicit stretch goals for later.

## Seed v2 (tightened)

Same core thought, narrowed by the refinements above — full text captured in conversation; key constraints: DE/DS audience, weeks timeline, two simultaneous front doors sharing one source of truth, agentic coding as build-trail evidence only, binary 30-day readmission locked for v1.

## Round 2 — 13 concepts (5 lenses + "what they missed" sweep)

### Data platform / stack choice
- **Medallion-to-BigQuery, One Shared Table** — GCS bronze → Dataproc Serverless PySpark silver → dbt gold table with real window-function SQL; both audiences read the same BigQuery tables.
- **Iceberg Lakehouse, DuckDB Front Door** — open Iceberg tables on GCS; both audiences query via DuckDB; zero standing compute between sessions.
- **The Big Join** — population scaled large enough (100k+ patients) that the SQL/Spark join is genuinely necessary, not decorative; everything downstream stays lean (one strong model, cron-triggered batch scoring, static-filter dashboard).
- **Claims-Adjudication Pipeline, Not Event Export** — frame the SQL/Spark layer as an insurance claims-adjudication pipeline; the readmission-window logic is literally an adjudication rule.

### Unifying the two front doors
- **Receipts on Every Number** — every dashboard metric has an inline expandable "receipt" showing the exact query/pipeline stage that produced it; an "Engineering" toggle expands all receipts for the engineer.
- **One Narrative, Two Renders** — one canonical `ENGINEERING.md` rendered both as a repo file and as a dashboard tab.
- **Console/API Parity as the Front-Door Contract** — a capability-parity table proves the dashboard is a thin client over the same artifacts the engineer inspects.

### Scope-risk pivots
- **One Repo, Two Doors, Not Two Builds** — warns both fronts could land at 70%; pivot to a static, pre-computed, zero-backend dashboard.
- **The GCP Rabbit Hole Nobody Budgeted** — warns IAM/Dataproc/quota/CI debugging is the invisible way weeks become months; pivot is a hard GCP surface-area ceiling, no managed Spark cluster.
- **The Build Order Is The Plan** — publish an explicit, honestly-updated week-by-week checkpointed build order as its own artifact.

### MLOps spine
- **The Retrain Button** — the whole pipeline is a single orchestrated, versioned, on-demand DAG a reviewer can trigger and watch produce a new model + scored table.

### Credibility & narrative hooks (what everyone missed)
- **The Synthetic Data Confession** — a prominent "Data Realism & Limitations" artifact naming Synthea's known synthetic-data quirks and how the pipeline accounts for them.
- **The Triage List Is The Point** — anchor the whole project around one named, concrete decision the pipeline enables (a weekly "Top-N discharge risk list"), not a metrics grid.

**What agreed:** two front doors must be thin views over one shared, versioned source of truth (never two parallel builds); GCP infra/orchestration plumbing is the single most likely way weeks become months; genuine vs. decorative SQL/Spark usage is the real credibility crux.

**What disagreed:** BigQuery+dbt vs. Iceberg+DuckDB; live/hosted dashboard vs. static/pre-computed; whether the project needs an explicit narrative/insight hook or whether architecture-and-process rigor alone is sufficient signal.

## Ranked picks (round 2)

1. **The Retrain Button, Honestly Scoped** — on-demand orchestrated DAG as MLOps spine + hard GCP infra ceiling + one shared `ENGINEERING.md` as both front doors + published week-by-week build order.
2. **The Big Join + The Triage List** — large enough population that Spark/SQL joins are genuinely necessary + dashboard anchored on one memorable actionable artifact + explicit synthetic-data limitations section. ← **chosen**
3. **Receipts on Every Number** — one UI element serving both personas at different zoom levels; riskiest on the weeks timeline.

## Decision

**Chosen: The Big Join + The Triage List.**

Combines:
- **The Big Join** (round 2, first-principles) — scale the Synthea population large enough that the SQL/Spark feature-engineering join is genuinely necessary, not decorative; keep the model, MLOps, and dashboard deliberately lean around it (one strong model with a leakage-safe temporal split, cron-triggered batch scoring, a filterable dashboard reading straight from the scored table).
- **The Triage List Is The Point** (round 2, what-they-missed) — anchor the dashboard around one concrete, memorable artifact — a weekly "Top-N discharge risk" list — rather than a generic metrics grid.
- **The Synthetic Data Confession** (round 2, what-they-missed) — a prominent, explicit section naming Synthea's known synthetic-data limitations and how the pipeline/evaluation accounts for them (calibration over raw accuracy, subgroup slices, clear caveats on clinical validity).

## Open questions to resolve in `/eg-prd`

- Warehouse commitment: BigQuery+dbt (safer, faster) vs. Iceberg+DuckDB lakehouse (more novel, more plumbing risk)?
- Live hosted dashboard (small ongoing GCP cost) vs. static/pre-computed export (zero maintenance, protects the weeks timeline)?
- How large a Synthea population is realistically generatable/storable within free-tier budget while still being "big enough that pandas would struggle"?
- How prominent should the synthetic-data-limitations framing be — headline section or smaller caveat?
- DAG/orchestration tool choice deferred (not part of the chosen concept directly, but relevant if MLOps automation gets added back in later).
