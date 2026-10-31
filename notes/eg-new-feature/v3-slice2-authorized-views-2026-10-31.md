# v3 slice 2: authorized views + the dashboard runtime SA (PRD FR6-FR9)

**Status:** built 2026-10-31 (design revision 5.1; `/eg-new-feature`). Design check: round 1 comprehension passed, critic
15 gaps, readiness 7 questions; round 2 critic 6, readiness 1; round 3 readiness closed, critic 4; at the 3-revision cap the owner
chose "apply fixes, re-run B/C" (revision 4); round 4 critic 4, readiness 1 (revision 5); round 5 readiness closed, critic 1
(`FakeJob.cache_hit`, fixed in 5.1). **Gate note:** readiness closed in rounds 3 and 5; the critic never closed (its round-5 gap
was fixed but not re-reviewed); the owner chose "proceed to implementation" (override, 2026-10-31). D5 was added by the owner
after round 1; E1/E2 are the elephant's resolutions of PRD wording, flagged for the owner. Live steps L0-L5 green 2026-10-31 (Results); L6 open. Branch `v3-slice2-authorized-views` off `v3` (`97d5401`);
PR into `v3` (ruleset 24876876: PR required, 0 approvals), never into `main`. PRD:
`notes/prds/v3-cloud-run-call-sheet-bigquery-gold-2026-10-28.md` (V4, V5, V7, V8, FR6-FR9, FR12's "why", FR15's labels, FR18,
M2, Implementation hints slice 2, Risks row "Authorized views silently lost"). Slice 1
(`notes/eg-new-feature/v3-slice1-gold-bigquery-2026-10-31.md`, owner decisions D1-D8) is binding: `serving.gold` is
MONTH-partitioned on `index_stop`, filter-required, and holds exactly the gold `SERVING_GOLD_FINGERPRINT` (`80edd13d…63f3`).
Where this note and the PRD differ, this note records an owner decision.

## Owner decisions (2026-10-31)

| # | Decision | PRD effect |
|---|---|---|
| D1 | **Stray v1 rows: filter, keep.** `serving.predictions` holds 44 rows of the v1 gold (`205c4c64…`, `as_of_date` 2025-07-31) that join to nothing in `serving.gold`. `v_call_sheet` keeps only `gold_fingerprint = SERVING_GOLD_FINGERPRINT`; the published-dates query takes, per `run_date`, the LATEST scoring run with `published IS TRUE` (whatever its status: `warehouse/run.py:220-234` can publish rows and still record `data_tests_failed`, when a history test failed, or `failed`, when a raised publish is found to have landed; the newest published run owns the date's rows) and keeps the date only if that run's `gold_fingerprint` matches. No DML. | refines V4/FR12 "latest published" |
| D2 | **The "why" rate's population:** gold rows with `index_stop < 2023-01-01T00:00Z` AND `label_horizon < 2023-01-01T00:00Z` (the champion's `test_start_date`, `prereg.yaml` `test_start_date_resolved: "20230101"`; the second condition is the split's step-4b rule, which subsumes the label-window purge because `label_horizon >= index_stop + 30d`). Raw rate (`AVG(is_readmitted)`) and row count per admission reason, NULL reason as its own group (`'MISSING'`, the lookup's key), rate NULL when n < 30 (the count is always shown). | defines FR12's "historical rate" |
| D3 | **Infra table rule split.** Views are `google_bigquery_table` resources but cannot be partitioned or filter-required, so `test_every_bigquery_table_is_partitioned_filtered_and_protected` applies to base tables only; views get their own rule (V-I2). | amends a standing test deliberately |
| D4 | **No free text in the ops views.** `v_scoring_runs` drops `error_message`, `gold_uri`, `predictions_table`; `v_monitoring` drops `error_message`. `error_type` and `data_test_failure` (labels such as `PRED-03`) stay. | refines FR14 |
| D5 | **Dates listed up to the simulated clock.** The clock = the newest `sim_date` among `succeeded` monitoring rows of the CURRENTLY monitored bundle, i.e. the `model_bundle_id` of the newest `succeeded` monitoring row by `started_at` (the monitor's own rule, `monitoring/store.py:117-119`, filters by `deploy/monitoring.json`'s bundle; after a champion change the grid restarts and the newest succeeded row carries the new bundle, so the SQL follows it without shipping the config). The call sheet lists only published 200k dates with `run_date <=` the clock, newest first; the default is the newest listed, i.e. this simulated week's tick. Manual dispatches dated after the clock (e.g. the L-post run of 2025-06-30, ~20 months ahead of the clock's ~2023-11) are hidden. No tick yet → no dates. | applies V7 to V4; M2's "newest tick" is checked on that date |

Elephant resolutions of PRD wording (not owner decisions; flagged):
- **E1 (FR9 `create_bqstorage_client=False`):** this slice never builds a DataFrame or Arrow table: `queries.run` iterates
  `.result()` rows over REST, so no Storage client can be created. V-B bans `to_dataframe`/`to_arrow` in `dashboard/` for now;
  slice 3, if it needs a frame, must pass `create_bqstorage_client=False` and change V-B to require that keyword instead.
- **E2 (FR18 "unpickle trap armed in its tests"):** armed now. Every test module under `tests/dashboard/` uses an autouse fixture
  calling `tests/scoring/helpers.py::forbid_model_loading(monkeypatch)`, and V-B also bans `pickle`/`cloudpickle` imports in
  the source.

## Why

Slice 3's Streamlit app (PRD V1, V8) must read BigQuery as a keyless runtime SA that can see the call sheet, the scoring runs and
the monitoring record but never a label row. `serving.predictions` has no features and `serving.gold` has the labels, so the app
needs a curated layer: three authorized views in a dataset the runtime SA alone can read, plus a fixed set of queries proven to
prune partitions and stay under the byte cap before any app code exists.

## Scope

**In:**
1. Terraform (`infra/bigquery.tf`): dataset `dashboard` (us-central1); views `v_call_sheet`, `v_scoring_runs`, `v_monitoring`
   whose SQL is packaged (`src/readmission_risk/warehouse/sql/views/*.sql`, rendered by `templatefile`); ONE
   `google_bigquery_dataset_access` on `serving` authorizing the `dashboard` dataset with `target_types = ["VIEWS"]`.
2. Terraform (`infra/iam.tf`): SA `dashboard-runtime` + 3 IAM members. `infra/outputs.tf`: `bigquery_datasets` gains
   `scratch_gold` (slice 1 follow-up) and `dashboard`; new output `dashboard_runtime_service_account_email`.
3. `src/readmission_risk/warehouse/dashboard_views.py`: view names, column classifications, the D2 constants, the SQL renderer,
   the pandas reference of the reason rate.
4. `src/readmission_risk/dashboard/__init__.py` + `dashboard/queries.py`: the four fixed dashboard queries (§Interfaces 5). The
   package is created now with no Streamlit code; slice 3 adds `app.py`. Its FR18 import boundary is tested from now on (V-B).
5. Live checks: `warehouse/dashboard_access_check.py` and `warehouse/dashboard_query_check.py` (§Interfaces 6), CLIs in
   `scripts/`, `make dashboard-access-check` / `make dashboard-query-check` (LIVE; read-only except a probe view that must be
   refused).
6. Tests (§Verification), docs (`docs/setup.md` section "Dashboard views (v3 slice 2)", CLAUDE.md slice-2 bullet + Build & test
   line), this note's Results.

**Out (named follow-ups):** the Streamlit app, caching, error panels (slice 3); Cloud Run, deploy SA (slice 4); deleting the 44
v1 rows (D1); materializing the reason rate; authorized routines (TVFs) unless F1 fires; a data test that
`predictions.discharge_time = gold.index_stop` (the view exposes gold's value; `dashboard-runtime` cannot read predictions'
own `discharge_time`, so this slice does not compare them); exposing `payer_ownership`; the published-dates limit beyond 500
(F7); reconciling `OPS_EPOCH` with the monitoring epoch into one constant.

## Surfaces touched

| Layer | File | Change |
|---|---|---|
| infra | `infra/bigquery.tf` | `google_bigquery_dataset.dashboard`, `locals.dashboard_view_vars`, `google_bigquery_table.{v_call_sheet,v_scoring_runs,v_monitoring}`, `google_bigquery_dataset_access.serving_authorizes_dashboard_views`; header comment |
| infra | `infra/iam.tf` | section "v3 slice 2: dashboard runtime": SA + 3 members |
| infra | `infra/outputs.tf` | `bigquery_datasets` + `scratch_gold`, `dashboard`; `dashboard_runtime_service_account_email` |
| sql | `src/readmission_risk/warehouse/sql/views/{v_call_sheet,v_scoring_runs,v_monitoring}.sql` | new |
| packaging | `pyproject.toml` | `[tool.setuptools.package-data]` adds `"warehouse/sql/views/*.sql"` next to `"warehouse/sql/*.sql", "warehouse/schemas/*.json"` |
| warehouse | `warehouse/bigquery.py` | `DASHBOARD_DATASET = "dashboard"` |
| warehouse | `warehouse/gold_access_check.py` | `_probe` renamed `probe` (`_probe = probe` alias kept); `ProbeResult.outcome` doc gains `"ok"`/`"failed"`; `exit_code`: 3 if any `allowed` or `failed`, else 5 if any `error`, else 0 |
| warehouse | `warehouse/dashboard_views.py`, `warehouse/dashboard_access_check.py`, `warehouse/dashboard_query_check.py` | new |
| dashboard | `src/readmission_risk/dashboard/__init__.py`, `dashboard/queries.py` | new |
| CLI | `scripts/dashboard_access_check.py`, `scripts/dashboard_query_check.py` | new, thin Typer wrappers (the `scripts/gold_access_check.py` shape) |
| make | `Makefile` | `DASHBOARD_SA ?= dashboard-runtime@synthea-readmission-risk.iam.gserviceaccount.com`; `dashboard-access-check: … --dashboard-sa $(DASHBOARD_SA)`; `dashboard-query-check: … --dashboard-sa $(DASHBOARD_SA) --gold-dir $(SCORE_GOLD_DIR)`; `.PHONY`; `##` help text (both LIVE) |
| tests | `tests/test_infra.py`; `tests/test_tooling.py` (`EXPECTED_TARGETS` +2, `MAKEFILE_VARIABLES` + `DASHBOARD_SA`, a test like `test_make_gold_targets_run_as_the_gold_publisher_sa` at `:143`); `tests/warehouse/fake_bigquery.py` (`FakeJob.total_bytes_processed: int | None = None`, `FakeJob.cache_hit: bool | None = None`; `FakeBigQuery.list_tables(dataset)` recording the call and raising `raise_on_list_tables` if set); `tests/warehouse/test_dashboard_views.py`, `test_dashboard_access_check.py`, `test_dashboard_query_check.py`, `test_gold_access_check.py` (exit-code cases); `tests/dashboard/__init__.py`, `tests/dashboard/conftest.py` (E2 trap), `tests/dashboard/test_queries.py`, `tests/dashboard/test_import_boundary.py` | §Verification |
| docs | `docs/setup.md`, `CLAUDE.md`, this note | |

Fake rows: `queries.run` builds `dict(row.items())`, which works for `google.cloud.bigquery.Row` and for plain dicts, so fake
jobs return dicts. Fake views: `table_objects[name] = SimpleNamespace(table_type="VIEW", schema=[SimpleNamespace(name=…)])`.
Dry runs are scripted through `on_query` (it receives the config; `config.dry_run` tells them apart): it RAISES the dry-run
error itself (as the real client does at submission) or returns a `FakeJob` with `total_bytes_processed`; a test also asserts
that `queries.dry_run` never calls `.result()` (a `FakeJob` whose `raise_on_result` is set must not trip).

Not touched: `serving.*` table definitions and data, the scorer, the monitor, `requirements*.txt` (no new pin).

## Interfaces

### 1. Terraform (`infra/bigquery.tf`)

```hcl
resource "google_bigquery_dataset" "dashboard" {
  dataset_id                 = "dashboard"
  location                   = var.region
  description                = "Authorized views read by the dashboard (synthetic Synthea data; not for clinical use). No label columns."
  delete_contents_on_destroy = false
  labels = {
    purpose = "dashboard"
  }

  depends_on = [google_project_service.enabled]
}

locals {
  dashboard_view_vars = {
    predictions  = "${var.project_id}.${google_bigquery_table.predictions.dataset_id}.${google_bigquery_table.predictions.table_id}"
    gold         = "${var.project_id}.${google_bigquery_table.gold.dataset_id}.${google_bigquery_table.gold.table_id}"
    scoring_runs = "${var.project_id}.${google_bigquery_table.scoring_runs.dataset_id}.${google_bigquery_table.scoring_runs.table_id}"
    monitoring   = "${var.project_id}.${google_bigquery_table.monitoring.dataset_id}.${google_bigquery_table.monitoring.table_id}"
  }
}

resource "google_bigquery_table" "v_call_sheet" {   # likewise v_scoring_runs, v_monitoring
  dataset_id          = google_bigquery_dataset.dashboard.dataset_id
  table_id            = "v_call_sheet"
  description         = "<one sentence; no label columns>"
  deletion_protection = true

  view {
    query          = templatefile("${path.module}/../src/readmission_risk/warehouse/sql/views/v_call_sheet.sql", local.dashboard_view_vars)
    use_legacy_sql = false
  }

  depends_on = [google_project_service.enabled]
}

# THE authorization (FR6/FR7): every view in `dashboard` may read `serving`. Authoritative alternatives (an `access` block on
# google_bigquery_dataset.serving, any google_bigquery_dataset_iam_* on serving) silently drop it; tests/test_infra.py forbids both.
resource "google_bigquery_dataset_access" "serving_authorizes_dashboard_views" {
  dataset_id = google_bigquery_dataset.serving.dataset_id

  dataset {
    dataset {
      project_id = var.project_id
      dataset_id = google_bigquery_dataset.dashboard.dataset_id
    }
    target_types = ["VIEWS"]
  }

  depends_on = [google_project_service.enabled]
}
```
The table references go through `templatefile` vars, so views depend on their base tables implicitly and `depends_on =
[google_project_service.enabled]` stays the exact list `test_every_resource_depends_on_enabled_apis` requires. Each `.sql` file
uses exactly the placeholders it needs and no other `${`, `$${`, `%{` or `%%{` sequence (templatefile directives/escapes).

Consequence (disclosed, Risks): the dataset authorization lets EVERY view in `dashboard` read all of `serving`, labels included.
Only principals that can create/update tables in `dashboard` (the operator, project owner) can use that; the runtime SA has
`dataViewer` only, and probe A5 proves it cannot create a view there.

### 2. `dashboard-runtime` (`infra/iam.tf`)

`google_service_account.dashboard_runtime`: `account_id = "dashboard-runtime"`, display name "Dashboard runtime (v3 slice 2)",
description "Reads the authorized views in the dashboard dataset; the Cloud Run service identity from slice 4; impersonated by
the operator for live checks."

| Resource name | Type | Role | On |
|---|---|---|---|
| `dashboard_runtime_reads_dashboard` | `google_bigquery_dataset_iam_member` | `"roles/bigquery.dataViewer"` | `google_bigquery_dataset.dashboard.dataset_id` |
| `dashboard_runtime_runs_jobs` | `google_project_iam_member` | `"roles/bigquery.jobUser"` | `var.project_id` |
| `operator_impersonates_dashboard_runtime` | `google_service_account_iam_member` | `"roles/iam.serviceAccountTokenCreator"` | `google_service_account.dashboard_runtime.name`, `member = var.operator_member` |

Nothing on `serving`, `scratch` or `scratch_gold` (dataset or tables); no `readSessionUser` (FR9; V-B pins that the dashboard
code never calls `to_dataframe`/`to_arrow`, the only paths that would build a Storage client); no WIF binding (slice 4). A
`dataset_iam_member` on `dashboard` is safe: `dashboard` grants no view authorizations itself (FR7's rule is about the dataset
that grants them, `serving`).

### 3. The views (`src/readmission_risk/warehouse/sql/views/`)

Every view selects an explicit column list (never `*`), uses no `CURRENT_DATE`/`CURRENT_TIMESTAMP`/`CURRENT_DATETIME`/
`CURRENT_TIME`, and its output columns equal `VIEW_COLUMNS[view]` in order. A file starts with a `--` comment naming this note.

**`v_call_sheet.sql`** (exact shape; aliases `p` = predictions, `g` = gold, `r` = rates):
```sql
WITH reason_rates AS (
  SELECT IFNULL(admission_reason_code, 'MISSING') AS reason_key, COUNT(*) AS n, AVG(is_readmitted) AS rate
  FROM `${gold}`
  WHERE index_stop < TIMESTAMP '2023-01-01 00:00:00+00' AND label_horizon < TIMESTAMP '2023-01-01 00:00:00+00'
  GROUP BY reason_key
)
SELECT
  p.as_of_date, p.rank, p.is_top_n, p.top_n, p.patient_id, p.encounter_id,
  g.index_stop AS discharge_time,
  p.admission_reason_code, p.admission_reason_description, p.risk_score,
  g.age_at_index_years,
  TIMESTAMP_DIFF(g.index_stop, g.index_start, MICROSECOND) / 86400000000 AS length_of_stay_days,
  g.prior_inpatient_count, g.prior_emergency_count, g.active_condition_count,
  r.n AS reason_history_n,
  IF(r.n >= 30, r.rate, NULL) AS reason_history_rate,
  p.model_bundle_id, p.scoring_run_id, p.scoring_window_days
FROM `${predictions}` AS p
JOIN `${gold}` AS g ON g.encounter_id = p.encounter_id
LEFT JOIN reason_rates AS r ON r.reason_key = IFNULL(p.admission_reason_code, 'MISSING')
WHERE p.gold_fingerprint = '80edd13dcc967d69b6fa4cfa17b4d7fa40188f8e7e17b2031b833332a59c63f3'
```
- `discharge_time` is GOLD's `index_stop` (gold's partition column) so an outer filter on it can prune gold; `as_of_date` is
  predictions' partition column. **Unverified so far:** that `require_partition_filter` is enforced through a view and that the
  outer predicates push down to both base tables. (Slice 1's probe 1 tested a MERGE on a plain table, not a view; revision 1
  misattributed this.) Q3 is the first test of the requirement (both directions, F1/F8), Q2 of pruning.
- `length_of_stay_days` = `models.data.prepare_features`' formula (`(index_stop - index_start) / 1 day`, FLOAT64; parity
  tolerance 1e-9 days).
- `is_readmitted` and `label_horizon` appear ONLY inside the CTE's aggregate/filter.
- The SQL's constants each equal a Python constant (V-W).

**Column classification (`warehouse/dashboard_views.py`):**
- `CALL_SHEET_COLUMNS` (output, in order) = `as_of_date, rank, is_top_n, top_n, patient_id, encounter_id, discharge_time,
  admission_reason_code, admission_reason_description, risk_score, age_at_index_years, length_of_stay_days, prior_inpatient_count,
  prior_emergency_count, active_condition_count, reason_history_n, reason_history_rate, model_bundle_id, scoring_run_id,
  scoring_window_days`.
- `PREDICTIONS_EXPOSED` = the 12 `p.` output columns (`as_of_date, rank, is_top_n, top_n, patient_id, encounter_id,
  admission_reason_code, admission_reason_description, risk_score, model_bundle_id, scoring_run_id, scoring_window_days`);
  `PREDICTIONS_NOT_EXPOSED` = `discharge_time` (gold's is used), `model_run_id`, `model_name`, `partition`, `label_definition`,
  `gold_fingerprint` (a constant filter), `gold_reference_date`, `gold_fingerprint_definition`. The two partition all 20
  `predictions.json` columns.
- `GOLD_READ` = the gold columns the main SELECT/JOIN reads: `encounter_id`, `index_stop`, `index_start`, `age_at_index_years`,
  `prior_inpatient_count`, `prior_emergency_count`, `active_condition_count`; `GOLD_RATE_INPUTS` = `admission_reason_code`,
  `is_readmitted`, `label_horizon`, `index_stop` (CTE only); `GOLD_NOT_READ` = every other `gold.json` column. `GOLD_READ ∪
  GOLD_NOT_READ` partitions the 24 gold columns (`index_stop`/`admission_reason_code` may also be rate inputs).
- `FORBIDDEN_OUTPUT_COLUMNS` = `is_readmitted, label_horizon, organization_utilization, gender, race, ethnicity,
  marital_status`: no view outputs any of them (checked on all three).
- `SCORING_RUNS_EXCLUDED = {"error_message", "gold_uri", "predictions_table"}`, `MONITORING_EXCLUDED = {"error_message"}`;
  `SCORING_RUNS_COLUMNS = tuple(c for c in column_names("scoring_runs") if c not in SCORING_RUNS_EXCLUDED)`, likewise
  `MONITORING_COLUMNS`; `VIEW_COLUMNS = {"v_call_sheet": CALL_SHEET_COLUMNS, "v_scoring_runs": …, "v_monitoring": …}`.

**`v_scoring_runs.sql`:** `SELECT <SCORING_RUNS_COLUMNS, one per line, in schema order> FROM \`${scoring_runs}\``.
**`v_monitoring.sql`:** `SELECT <MONITORING_COLUMNS> FROM \`${monitoring}\``.

### 4. `warehouse/dashboard_views.py`

```python
VIEW_NAMES = ("v_call_sheet", "v_scoring_runs", "v_monitoring")
# §3 classification constants
REASON_RATE_CUTOFF = datetime(2023, 1, 1, tzinfo=UTC)
REASON_RATE_MIN_N = 30
MISSING_REASON_KEY = "MISSING"
VIEW_PLACEHOLDERS = {"v_call_sheet": ("predictions", "gold"), "v_scoring_runs": ("scoring_runs",), "v_monitoring": ("monitoring",)}

def view_template(name: str) -> str        # the packaged file text (importlib.resources); ValueError for an unknown name
def view_sql(name: str, *, project_id: str = PROJECT_ID) -> str:
    """Replaces each `${key}` of VIEW_PLACEHOLDERS[name] with `<project_id>.<dataset>.<table>` (serving tables; infra/bigquery.tf's
    local.dashboard_view_vars gives the same strings). ValueError for an unknown name, or if the template contains `$${`, `%{`,
    or any `${` left over after the replacement."""
def select_list(sql: str) -> list[str]:
    """Output column names of the LAST top-level SELECT: the text between it and its FROM (after removing `--` comments), split on commas at
    parenthesis depth 0, each item's name = the identifier after a final `AS`, else the part after the last `.`. Used by tests and nothing
    else; raises ValueError when no SELECT/FROM pair is found."""
def reason_rates(gold: pd.DataFrame) -> pd.DataFrame:
    """Pure pandas reference of the CTE: columns reason_key (str), n (int64), rate (float64, NaN when n < REASON_RATE_MIN_N);
    rows with index_stop < cutoff AND label_horizon < cutoff (both strict); NULL code -> MISSING_REASON_KEY; sorted by
    reason_key. Does not mutate gold."""
```
Imports: stdlib, pandas, `readmission_risk.gcp.PROJECT_ID`, `warehouse.bigquery` only (never `models.*`, `scoring.*`,
`bundles`; V-B). The view placeholders use Terraform's `${…}` syntax, unlike the scorer's Python `{predictions_table}`
placeholders in `warehouse/sql/*.sql`. That split is deliberate: Terraform renders these files, so `data_tests`' renderer is not
reused.

`warehouse/bigquery.py` gains `DASHBOARD_DATASET = "dashboard"`.

### 5. The fixed queries (`src/readmission_risk/dashboard/queries.py`)

```python
DASHBOARD_LABELS = {"component": "dashboard"}   # FR15 job label
# Constant started_at lower bound for every ops read. 2026-10-01 is before the first BigQuery scoring run (2026-10-11, v2 slice 4),
# so the run history keeps it; deploy/monitoring.json's epoch (2026-10-18) is later and only for the monitor's own reads (V-D
# pins OPS_EPOCH <= it).
OPS_EPOCH = date(2026, 10, 1)
PUBLISHED_DATES_LIMIT = 500    # the grid has 178 tick dates; Q0 fails if the limit is reached (F7)

@dataclass(frozen=True)
class DashboardQuery:
    name: str
    sql: str
    parameters: tuple[tuple[str, str, object], ...]   # (name, BigQuery type, value), in the SQL's first-use order

@dataclass(frozen=True)
class PublishedDate:
    run_date: date; scoring_run_id: str; model_bundle_id: str; window_days: int; top_n: int; n_predictions: int
    clock_sim_date: date
    @classmethod
    def from_row(cls, row: Mapping) -> PublishedDate   # KeyError on a missing key; ValueError if any value is None

def published_dates(*, limit: int = PUBLISHED_DATES_LIMIT) -> DashboardQuery
def call_sheet(published: PublishedDate) -> DashboardQuery
def monitoring_history(*, limit: int = 1000) -> DashboardQuery
def recent_scoring_runs(*, limit: int = 50) -> DashboardQuery
def config_kwargs(query: DashboardQuery, *, dry_run: bool = False, use_cache: bool = True) -> dict
    # {"query_parameters": [bigquery.ScalarQueryParameter(n, t, v) ...], "labels": dict(DASHBOARD_LABELS), "dry_run": dry_run,
    #  "use_query_cache": use_cache and not dry_run}; the caller passes them to warehouse.bigquery.query_config, so
    #  maximum_bytes_billed = MAX_BYTES_BILLED (100 MiB). Every call site is written literally as
    #  `client.query(query.sql, job_config=query_config(**config_kwargs(query, ...)))`, which keeps
    #  tests/warehouse/test_bigquery.py:54-61 (only bigquery.py builds a QueryJobConfig; every `.query(` call contains
    #  `job_config=query_config(`) unchanged and passing.
def run_job(client, query: DashboardQuery, *, use_cache: bool = True) -> tuple[list[dict], object]   # job = client.query(query.sql, job_config=query_config(**config_kwargs(query, use_cache=use_cache))); ([dict(r.items()) for r in job.result()], job)
def run(client, query: DashboardQuery) -> list[dict]                       # run_job(client, query)[0]
def dry_run(client, query: DashboardQuery) -> int
    # job = client.query(query.sql, job_config=query_config(**config_kwargs(query, dry_run=True))); return job.total_bytes_processed. NO .result(): BigQuery
    # validates a dry run at submission, so its errors (e.g. "without a filter", Forbidden) are raised by client.query itself.
```
All SQL reads only `` `synthea-readmission-risk.dashboard.<view>` `` (built with `table_id(DASHBOARD_DATASET, …)` + `quoted`);
every value is a query parameter; `limit` validated (`type(limit) is int and limit >= 1`, else `ValueError`); `google.cloud`
imported inside `config_kwargs` only.

**Literal rule (V-D):** the query SQL contains no quoted string, no `DATE '`/`TIMESTAMP '` literal, and no numeric literal other
than the `1` of `rn = 1` and of `LIMIT 1`; booleans are tested as `published IS TRUE` (a column, no literal).

- **`published_dates(limit)`** (D1 + D5):
  ```sql
  WITH ticks AS (
    SELECT sim_date, model_bundle_id, started_at, monitoring_run_id FROM `…dashboard.v_monitoring`
    WHERE started_at >= TIMESTAMP(@epoch) AND status = @succeeded AND sim_date IS NOT NULL AND model_bundle_id IS NOT NULL
  ), monitored_bundle AS (
    SELECT model_bundle_id FROM ticks ORDER BY started_at DESC, monitoring_run_id DESC LIMIT 1
  ), clock AS (
    SELECT MAX(t.sim_date) AS sim_date FROM ticks AS t JOIN monitored_bundle AS b USING (model_bundle_id)
  ), latest AS (
    SELECT run_date, scoring_run_id, model_bundle_id, window_days, top_n, n_predictions, gold_fingerprint,
      ROW_NUMBER() OVER (PARTITION BY run_date ORDER BY started_at DESC, scoring_run_id DESC) AS rn
    FROM `…dashboard.v_scoring_runs`
    WHERE started_at >= TIMESTAMP(@epoch) AND published IS TRUE AND run_date IS NOT NULL
      AND model_bundle_id IS NOT NULL AND window_days IS NOT NULL AND top_n IS NOT NULL AND n_predictions IS NOT NULL
      AND scoring_run_id IS NOT NULL
  )
  SELECT l.run_date, l.scoring_run_id, l.model_bundle_id, l.window_days, l.top_n, l.n_predictions, c.sim_date AS clock_sim_date
  FROM latest AS l CROSS JOIN clock AS c
  WHERE l.rn = 1 AND l.gold_fingerprint = @gold_fingerprint AND l.run_date <= c.sim_date
  ORDER BY l.run_date DESC
  LIMIT @limit
  ```
  Parameters: `epoch DATE = OPS_EPOCH`, `succeeded STRING = "succeeded"` (used by the clock's `ticks` only), `gold_fingerprint STRING = SERVING_GOLD_FINGERPRINT`,
  `limit INT64`. No succeeded tick → `c.sim_date` NULL (or no `clock` row) → no rows. The literal rule's single numeric
  exception grows to two: the `1` of `rn = 1` and of `LIMIT 1`. The `IS NOT NULL` predicates guarantee
  `PublishedDate.from_row` never sees a None (Q0 still parses every row through it). The CTE is named `monitored_bundle`
  (not `current_bundle`, which a case-insensitive `CURRENT_` ban would match). Exposed for checks only:
  `_published_dates_query(*, clock_bound: bool, fingerprint_filter: bool, limit: int) -> DashboardQuery`, which
  `published_dates(limit)` returns with both `True` (name `"published_dates"`; variants are named
  `"published_dates[clock_bound=<b>,fingerprint_filter=<b>]"`). Contract of the flags:
  - `clock_bound=False`: no `ticks`/`monitored_bundle`/`clock` CTEs, no `CROSS JOIN clock`, no `run_date <= c.sim_date`
    predicate, no `clock_sim_date` output column, and no `succeeded` parameter;
  - `fingerprint_filter=False`: no `l.gold_fingerprint = @gold_fingerprint` predicate and no `gold_fingerprint` parameter, and
    the outer SELECT also returns `l.gold_fingerprint` (last column);
  - outer SELECT order: `run_date, scoring_run_id, model_bundle_id, window_days, top_n, n_predictions`, then `clock_sim_date` iff
    `clock_bound`, then `gold_fingerprint` iff not `fingerprint_filter`; `epoch` and `limit` are always parameters.
  Q0b reads variant rows as plain dicts (never `PublishedDate.from_row`, which requires `clock_sim_date`).
- **`call_sheet(published)`**: `SELECT <CALL_SHEET_COLUMNS> FROM …v_call_sheet WHERE as_of_date = @as_of_date AND
  discharge_time >= @discharge_from AND discharge_time < @discharge_to ORDER BY rank`. No `scoring_run_id` predicate: the rows
  are whatever is published for the date, and each carries its `scoring_run_id`, so a mismatch with the listed run is visible
  (F10) instead of an empty sheet.
  Parameters: `as_of_date DATE = published.run_date`; `(discharge_from, discharge_to) =
  scoring.selection.window_bounds(published.run_date, published.window_days)` as `TIMESTAMP` params of `.to_pydatetime()`
  values (tz-aware UTC; the scorer's own window, so no published row is filtered out).
- **`monitoring_history(limit)`**: `SELECT <MONITORING_COLUMNS> FROM …v_monitoring WHERE started_at >= TIMESTAMP(@epoch) ORDER BY
  started_at DESC, monitoring_run_id DESC LIMIT @limit`; params `epoch`, `limit`.
- **`recent_scoring_runs(limit)`**: `SELECT <SCORING_RUNS_COLUMNS> FROM …v_scoring_runs WHERE started_at >= TIMESTAMP(@epoch)
  ORDER BY started_at DESC, scoring_run_id DESC LIMIT @limit`; params `epoch`, `limit`.

`dashboard/queries.py` imports: stdlib, `readmission_risk.warehouse.bigquery`, `readmission_risk.warehouse.dashboard_views`,
`readmission_risk.scoring.selection` (pandas + `scoring.constants` only; not `scoring.pipeline`).

### 6. Live checks

Common: CLIs build clients with `gcp.configure(impersonate_service_account=email)` + `gcp.bigquery_client()` (slice 1's
`make_client`). Reports (gitignored `data/`) are JSON with `sort_keys=True`, `indent=2`.

**`warehouse/dashboard_access_check.py::run_access_checks(make_client, *, dashboard_sa) -> list[ProbeResult]`** (one client as
`dashboard-runtime`; a client that cannot be built → one `error` result). Reuses `gold_access_check.ProbeResult`, `is_denied`,
`probe`, `exit_code`. Probes, each must be **denied**:
- A1 `serving.predictions` (`WHERE as_of_date = DATE '1900-01-01'`), A2 `serving.gold` (`WHERE index_stop >= TIMESTAMP
  '0001-01-01'`), A3 `serving.scoring_runs` (`WHERE started_at >= TIMESTAMP '2000-01-01'`), A4 `serving.monitoring` (`WHERE
  started_at >= TIMESTAMP '2000-01-01'`): `SELECT COUNT(*) AS n` queries with `query_config()`, `.result()` (the predicates
  keep a missing-filter error from masking the access answer).
- A5 `create_table` of a VIEW `dashboard.access_probe` (`SELECT 1 AS x`), undo = `delete_table(not_found_ok=True)` on the same
  client. If undo also fails, the result says `ALLOWED, undo failed` (exit 3) and the runbook's remedy is the operator's
  `bq rm -f -t synthea-readmission-risk:dashboard.access_probe`.
- A6 `list_tables("scratch")`, A7 `list_tables("scratch_gold")` (labelled staging copies live there), consumed with `list(...)`.
Checks that must **pass** (`probe`-like helper `check(sa, name, fn) -> ProbeResult` with outcome `"ok"` if `fn` returns `None`,
`"failed"` with the returned message, `"error"` on an exception):
- S1-S3 `get_table(dashboard.<view>)`: `table_type == "VIEW"`, `[f.name for f in schema] == list(VIEW_COLUMNS[view])`, and no
  name in `FORBIDDEN_OUTPUT_COLUMNS`.
Report `data/checks/dashboard_access_check/<UTC %Y%m%dT%H%M%SZ>.json` = `{"checked_at", "dashboard_sa", "probes":
[asdict(ProbeResult)…]}`. Exit: `gold_access_check.exit_code`.

**`warehouse/dashboard_query_check.py::run_query_checks(client, *, gold: pd.DataFrame) -> QueryCheckReport`** where
`CheckResult(check: str, outcome: "pass"|"fail"|"error", detail: str)` and `QueryCheckReport(results: list[CheckResult],
measurements: dict)`. Outcome rule for every check: the check's assertion false → `fail`; an exception for which `is_denied` is
true → `fail` (an access refusal means the authorization or a grant is wrong, F2); any other exception → `error` (detail =
`Type: message`, 500 chars); Q3 has its own rule. Every real (non-dry) query a check runs uses `run_job(..., use_cache=False)`, so `total_bytes_billed` is a real figure;
each billed measurement is recorded with `cache_hit` (read as `job.cache_hit` directly, a `bool | None`; keys
`q0_cache_hit`, `q4_cache_hit`, `q0b_<step>_cache_hit`). Every `try` wraps the `client.query` call itself (a dry run raises there,
not in `.result()`). Dependencies (a skipped check is `error` "skipped: <prerequisite>"; nothing else is ever skipped, and a
check's `fail` never skips anything):

| Check | Runs iff |
|---|---|
| Q0 | always |
| Q0b, Q1, Q3, Q4 | Q0 returned ≥ 1 parseable row (even if Q0 is `fail` for another reason, e.g. newest ≠ clock or the limit reached); they use Q0's newest row |
| Q2 | additionally, Q1's `call_sheet` dry run produced a byte count |

Each of Q1's four dry runs and each sub-step of Q0b/Q3 is its own `CheckResult` (`check` = `Q1:<name>`, `Q0b:i`/`ii:<date>`/`iii`,
`Q3:discharge`/`Q3:as_of`), so one raising does not hide the others.
- **Q0** real `run(published_dates())`: ≥ 1 row and fewer than `PUBLISHED_DATES_LIMIT`; every row's `run_date <=
  clock_sim_date`; the newest `run_date == clock_sim_date` (M2: the clock's tick is the default and is listed). Measurements:
  `clock_sim_date`, `newest_published_date`, `n_published_dates`, `q0_bytes_billed` (`job.total_bytes_billed` from `queries.run_job`). Every row is
  parsed with `PublishedDate.from_row`; an exception there is `fail` "malformed published row".
- **Q0b** (D1 positive control; real queries, check-only queries from `queries._published_dates_query`, through `run_job` with
  `use_cache=False`):
  (i) the clock-free list (`clock_bound=False, fingerprint_filter=True`) contains no date whose latest published run has another
  fingerprint, AND the clock-and-fingerprint-free list (`clock_bound=False, fingerprint_filter=False`, its outer SELECT also
  returning `gold_fingerprint`; parameter `gold_fingerprint` then unused and omitted) contains ≥ 1 such date (live: 2025-07-31),
  so the fingerprint predicate is shown to matter, not hidden by the clock;
  (ii) for each such other-fingerprint date `d` (from (i), its run's `n_predictions` recorded, live 44): a real `SELECT COUNT(*)
  AS n FROM …v_call_sheet WHERE as_of_date = @d AND discharge_time >= @from AND discharge_time < @to` with the wide range
  1900-01-01..2100-01-01 returns 0. This proves only that those rows never reach the call sheet. It does NOT prove the view's fingerprint
  predicate (the v1 rows join to nothing in gold, so the inner join alone yields 0; a real control would need DML, which D1
  rules out). The predicate itself is verified offline (V-W literal) and its effect on the date list by (i);
  (iii) the clock-free, fingerprint-filtered list contains ≥ 1 date `> clock_sim_date` (live: the L-post run of 2025-06-30), so
  the clock predicate is shown to matter, and every such date is absent from Q0's list; none → `fail` "no future date to
  exclude" (like (i), a control that depends on today's data: the owner amends it if that date is ever unpublished).
  Measurements: `excluded_other_fingerprint_dates` (ISO dates → `n_predictions`), `excluded_future_dates`.
- **Q1** `queries.dry_run` each of the four fixed queries (call sheet on the newest published date): `total_bytes_processed <=
  MAX_BYTES_BILLED`. Measurements `dry_run_bytes_<name>`. (Billed bytes also include BigQuery's 10 MB minimum per referenced
  table; the cap is enforced by BigQuery on billed bytes, and Q0/Q4 record the billed figure of real runs.)
- **Q2** pruning: dry-run a WIDE call sheet (same date, discharge range 1900-01-01T00:00Z..2100-01-01T00:00Z):
  narrow bytes `<` wide bytes. Measurements `dry_run_bytes_call_sheet_wide`.
- **Q3** filter required, both directions: dry-run the call sheet with the discharge predicates removed and, separately, with
  the `as_of_date` predicate removed. Each: `BadRequest` containing "without a filter" → `pass`; the dry run accepted → `fail`
  (F8: the requirement does not survive the view; stop and decide); another exception → `error`.
- **Q4** real `run_job(call_sheet(newest))`: every row's `scoring_run_id` == the listed run's (F10); row count == `n_predictions`; `rank` == 1..n; count of `is_top_n` == min(`top_n`,
  n); `age_at_index_years`, `length_of_stay_days`, `prior_inpatient_count`, `prior_emergency_count`, `active_condition_count`,
  `discharge_time` non-null in every row; parity with local gold per `encounter_id` (`discharge_time == index_stop`, the four
  features exactly equal, `length_of_stay_days` within 1e-9 of `prepare_features`', `(reason_history_n, reason_history_rate)`
  equal to `reason_rates(gold)` (rate within 1e-12; NULL iff NaN; a reason absent from `reason_rates` ⇔ NULL n). A NULL
  `reason_history_n` is NOT a failure (F4): measurement `rows_without_reason_history`. Measurements `call_sheet_rows`,
  `q4_bytes_billed`.
Q2/Q3's variants come from ONE helper in `dashboard/queries.py`, `_call_sheet_variant(published, *, discharge: tuple[datetime,
datetime] | None, as_of: bool) -> DashboardQuery` (`call_sheet(p)` == `_call_sheet_variant(p, discharge=window_bounds(...),
as_of=True)`): the call sheet's column list and ORDER BY; `discharge=None` omits both discharge predicates AND the
`discharge_from`/`discharge_to` parameters; `as_of=False` omits the `as_of_date` predicate AND its parameter. Q0b (ii)'s count
is a `DashboardQuery` built by the check module the same way (`SELECT COUNT(*) AS n FROM …v_call_sheet WHERE as_of_date =
@as_of_date AND discharge_time >= @discharge_from AND discharge_time < @discharge_to`), so every check query goes through
`run_job`/`dry_run`; the check modules have no `.query(` call of their own except the access probes, which already pass
`job_config=query_config()`.

CLI `scripts/dashboard_query_check.py [--dashboard-sa EMAIL] [--gold-dir PATH]` (defaults: the `dashboard-runtime` email and
`data/gold/main_200k`). Both check CLIs ALWAYS impersonate (`--dashboard-sa` has a default; an empty string → exit 2): there is
no run-as-operator mode, because the operator can read `serving` directly and would bypass the authorized-view path. The email
is recorded in each report. Then the query-check CLI: loads the gold
with `models.data.load_gold_table_with_metadata` (the scorer's and monitor's loader; it verifies the `spark-rowhash-v1`
fingerprint) and requires `metadata`'s fingerprint == `SERVING_GOLD_FINGERPRINT`; on any load failure prints the reason and
exits **2** without a BigQuery call. A client that cannot be built (no ADC, failed impersonation) → one `error` result
`build client`, report written, exit 5. Else exit 0 all pass, 3 any `fail`, 5 any `error` (fail wins). Report
`data/checks/dashboard_query_check/<UTC>.json` = `{"checked_at", "dashboard_sa", "gold_dir", "checks": [asdict…],
"measurements": {…}}`; every measurement is also printed.

## UX flow (pipeline)

`terraform apply` (dataset, views, authorization, SA) → operator impersonates `dashboard-runtime` → access check (denied ×7,
schemas ×3) → `make gold-200k` if `data/gold/main_200k` is absent → query check (published dates and the clock → D1/D5 control →
dry-runs → pruning → filter-required → real call sheet on the clock's tick + parity with local gold) → slice 3 imports
`dashboard.queries` unchanged.

## Data handling

- **No label rows (V8):** no view outputs `is_readmitted` or `label_horizon`. The call sheet's rate is an aggregate over ≥ 30
  rows from BEFORE 2023-01-01 whose labels are settled before that date, so it says nothing about any discharge the call sheet
  shows (all listed dates are in the champion's test window) or about later ones. Suppression below 30 limits single-row
  disclosure of pre-2023 labels (the count stays visible).
- **Deliberate aggregate exception:** `v_monitoring` exposes outcome aggregates of the test-window cohorts (`observed`,
  `expected`, `oe_*`, `auc`, `n_matured`, `n_label_after_sim`, …). That is the monitoring view's purpose (PRD FR13). It shows
  only cohort-level figures over matured cohorts, never a row, and the ledger already publishes the same figures.
- **Leakage / split integrity:** the rate population is not exactly the champion's training partition (training also drops
  test-window patients), but it uses strictly pre-test-window labels, the property an outcome-blind "why" needs. Stated in the
  view description and `docs/setup.md`. A test pins `REASON_RATE_CUTOFF` to `prereg.yaml`'s `test_start_date_resolved`; the
  next promotion with another cutoff must change both.
- **Determinism:** ORDER BY with id tie-breaks; no randomness.
- **Cost:** every query through `query_config` (100 MiB billed cap), label `component=dashboard`, no `CURRENT_*`.

## Failure modes

- **F1 pushdown/requirement too strict** (the NORMAL call sheet refused "without a filter"): Q1 errors/fails. Stop; the TVF
  fallback (authorized routines, `target_types = ["ROUTINES"]`) is an owner decision.
- **F2 authorization lost:** Q0 is `fail` (access denied); L2/L5 plans; FR7's test (V-I1) prevents the known causes.
- **F3 empty result** for the newest date: Q4 fails (count ≠ `n_predictions`).
- **F4 reason without pre-2023 history:** `reason_history_n` NULL, recorded, not a failure (slice 3 renders "no history").
- **F5 IAM propagation:** a fresh grant takes minutes; a check denied right after apply is rerun after ~10 minutes before it
  counts (slice 5b), recorded in Results.
- **F6 malformed input:** limits not `int` ≥ 1 → `ValueError`; `PublishedDate.from_row` → `KeyError` (missing key) /
  `ValueError` (None value, e.g. a NULLABLE `window_days`).
- **F7 published dates ≥ limit:** Q0 fails; raise the limit or paginate (follow-up; 178 grid dates + dispatches < 500).
- **F8 requirement does NOT survive the view** (a filter-less dry run accepted): Q3 fails. The fixed queries still constrain
  both columns and the 100 MiB cap bounds cost; whether to proceed is an owner decision recorded in Results.
- **F10 a publish whose `scoring_runs` row was never inserted** (`warehouse/run.py`, exit 4, warned at the time): the date's
  rows belong to an unlisted run; the listed run is an older published one. The call sheet still shows the published rows, whose
  `scoring_run_id` differs from the listed run; Q4 fails on the newest date, and slice 3 shows a provenance mismatch notice.
  Disclosed residual; rerunning the scorer for that date repairs it.
- **F9 no succeeded tick** (fresh project or wiped monitoring): Q0 returns no rows → fail "no published date up to the clock".

## Verification

Offline (CI):
- **V-I1** `tests/test_infra.py::test_views_authorized_only_by_dataset_access` (FR7): exactly one
  `google_bigquery_dataset_access` resource; its collapsed body contains `dataset_id = google_bigquery_dataset.serving.dataset_id`
  and `dataset { dataset { project_id = var.project_id dataset_id = google_bigquery_dataset.dashboard.dataset_id } target_types =
  ["VIEWS"] }`; no resource of a type starting `google_bigquery_dataset_iam_` has a `dataset_id` naming a dataset targeted by a
  `dataset_access`; no `google_bigquery_dataset` body contains `access {` (all datasets; replaces the per-name loop at `:446`).
- **V-I2** (D3) `test_every_bigquery_table_is_partitioned_filtered_and_protected`: base tables (bodies without `view {`) == the
  four, rule unchanged; new `test_bigquery_views_are_exactly_the_dashboard_views`: views == `VIEW_NAMES`, each with `dataset_id =
  google_bigquery_dataset.dashboard.dataset_id`, `deletion_protection = true`, `use_legacy_sql = false`, `query =
  templatefile("${path.module}/../src/readmission_risk/warehouse/sql/views/<name>.sql", local.dashboard_view_vars)`, no
  `schema`, no `time_partitioning`; `local.dashboard_view_vars` = exactly the four keys with the strings of §1.
- **V-I3** `test_dashboard_runtime_grants_are_exactly`: the 2 grants + the operator's token creator; no member of it carries a
  `table_id`, nor a `dataset_id` other than the dashboard's; `on_gold == {GOLD_PUBLISHER}` still holds;
  `test_iam_is_additive_only` 47 → **50** (comment "+ 3 (v3 slice 2: dashboard-runtime 2, its impersonation 1)").
- **V-I4** `test_dashboard_dataset_settings` (`location = var.region`, no `default_table_expiration_ms`,
  `delete_contents_on_destroy = false`, no `access {`); `test_outputs_list_every_dataset` (`bigquery_datasets`' keys == the
  names of all `google_bigquery_dataset` resources, each `= google_bigquery_dataset.<n>.dataset_id`); the
  `dashboard_runtime_service_account_email` output exists.
- **V-W** `tests/warehouse/test_dashboard_views.py`: each view file loads through `importlib.resources`; its `${…}` placeholders
  == `VIEW_PLACEHOLDERS[name]`; no `$${`/`%{`; no `CURRENT_` (case-insensitive); no `SELECT *` / `.*` in the view files;
  `select_list(view_sql(name)) == list(VIEW_COLUMNS[name])`; `FORBIDDEN_OUTPUT_COLUMNS` ∩ every view's output = ∅;
  `PREDICTIONS_EXPOSED ⊔ PREDICTIONS_NOT_EXPOSED == column_names("predictions")` (disjoint) and `GOLD_READ ⊔ GOLD_NOT_READ ==
  column_names("gold")`; the `p.`/`g.` references in the main SELECT/JOIN (regex `\b[pg]\.(\w+)` on the text after the CTE) ⊆
  `PREDICTIONS_EXPOSED ∪ {"gold_fingerprint", "admission_reason_code"}` / `GOLD_READ`; the gold column names occurring in the CTE text (whole-word,
  case-sensitive matches of `column_names("gold")` in the text between `WITH reason_rates AS (` and its closing `)`) ⊆
  `GOLD_RATE_INPUTS`; the fingerprint literal == `SERVING_GOLD_FINGERPRINT`; `TIMESTAMP '2023-01-01 00:00:00+00'` occurs exactly
  twice and equals `REASON_RATE_CUTOFF` and `prereg.yaml`'s `test_start_date_resolved`; `>= 30` equals `REASON_RATE_MIN_N`;
  `'MISSING'` (twice) equals `MISSING_REASON_KEY` and `models.baselines._MISSING_KEY` (imported by the TEST only); `view_sql`
  errors (unknown name, `$${`, leftover `${`); `select_list` on nested commas (`IF(a, b, c) AS x` → `x`); `reason_rates` on a
  hand-built frame (`index_stop == cutoff` and `label_horizon == cutoff` excluded, NULL code → MISSING, n = 29 → NaN, n = 30 →
  rate, input unmutated, sorted).
- **V-D** `tests/dashboard/test_queries.py`: each query reads only `` `synthea-readmission-risk.dashboard.v_*` ``; the literal
  rule; no `CURRENT_`; the set of `@names` in the SQL == the parameter names; the config each call passes to the fake client: `maximum_bytes_billed ==
  MAX_BYTES_BILLED`, `labels == {"component": "dashboard"}`, `dry_run`/`use_query_cache` for each setting (`use_query_cache` is
  `False` for `dry_run=True` and for `use_cache=False`), parameter types as
  specified; `call_sheet` bounds == `window_bounds(...)` (tz-aware UTC datetimes); `published_dates`' `latest` CTE has no `status` predicate (only `ticks` uses `@succeeded`); it carries
  `SERVING_GOLD_FINGERPRINT` and `"succeeded"` and equals `_published_dates_query(clock_bound=True, fingerprint_filter=True, …)`;
  the four flag combinations follow the contract above (predicates, CTEs, output columns, parameters), `_call_sheet_variant`'s
  omissions likewise, and the `@names` == parameter-names rule holds for EVERY `DashboardQuery` (fixed queries and variants); selected columns == the constants; `limit` validation; `run` against
  `FakeBigQuery` returns the dict rows and passes the config; `PublishedDate.from_row` (ok, missing key, None value); `OPS_EPOCH
  <= monitoring.config.read_monitoring_config().epoch` (deploy/monitoring.json).
- **V-B** `tests/dashboard/test_import_boundary.py` (FR18, the `tests/scoring/test_pipeline.py:462-487` pattern): a subprocess
  imports every `readmission_risk.dashboard` module and `readmission_risk.warehouse.dashboard_views`, then asserts none of
  `mlflow`, `pyspark`, `sklearn`, `xgboost`, `readmission_risk.bundles`, `readmission_risk.scoring.pipeline`,
  `readmission_risk.models` is in `sys.modules`; a source test: no `import pickle`/`cloudpickle`, no `to_dataframe`/`to_arrow`,
  no `file_uploader` in `src/readmission_risk/dashboard/**/*.py` (E1). `cloudpickle` is NOT in the `sys.modules`
  list: `import pandas` loads it through pyarrow (measured 2026-10-31 in `.venv`: `import pandas` alone leaves `cloudpickle` and
  `pickle` in `sys.modules`), so "never unpickles" rests on the source ban and the trap; `tests/dashboard/conftest.py` arms
  `forbid_model_loading` autouse (E2).
- **V-C** `test_dashboard_access_check.py` / `test_dashboard_query_check.py` with `FakeBigQuery` (dry-run errors raised from
  `on_query`; the dependency table: Q0 raising or empty → all others `error` skipped; Q0 `fail` with rows → others still run;
  Q1 call-sheet dry run raising → Q2 skipped, Q3/Q4 run; Q0b (i) passing only when the unfiltered list has the other
  fingerprint, (ii) non-zero count → fail, a client build failure → exit 5 with the report written): all denied + all schemas ok →
  0; each allowed probe → 3 (A5 undone, `delete_table` called; undo failure text); a schema with a forbidden column / wrong order
  / not a VIEW → `failed` → 3; client build failure → `error` → 5; Q0-Q4 pass on scripted jobs; each fail path (Q0 empty, limit
  reached, newest ≠ clock, a date > clock; Q0b no other-fingerprint date / one listed, no date after the clock / one listed; real check runs use `use_cache=False`; Q1 bytes over cap; Q2 narrow ≥ wide; Q3
  accepted → fail, other error → error; Q4 a row with another `scoring_run_id`, count, rank, top-n count, a null feature, a parity mismatch per field; a denied
  exception → fail; another exception → error, later checks skipped); NULL `reason_history_n` → measurement, still pass; the
  CLIs: exit codes 0/2/3/5, impersonation always configured with the given or default email, an empty `--dashboard-sa` → 2, report path and keys, gold-load failure → 2 with no BigQuery call (monkeypatched loader and client).
  `test_gold_access_check.py`: `exit_code` with `ok`/`failed` outcomes; the `_probe` alias.
- **V-T** `tests/test_tooling.py`: the two targets in `EXPECTED_TARGETS` and `.PHONY`; `DASHBOARD_SA` in `MAKEFILE_VARIABLES`;
  `make -n dashboard-access-check` contains `--dashboard-sa dashboard-runtime@synthea-readmission-risk.iam.gserviceaccount.com`,
  `make -n dashboard-query-check` that plus `--gold-dir data/gold/main_200k`.
- Existing: `tests/warehouse/test_bigquery.py::test_only_query_config_builds_query_job_configs` (both its rules) passes unchanged.

Live (each owner-confirmed, one at a time; Results recorded here):
- **L0** read-only: `terraform -chdir=infra plan -out=<scratchpad>/slice2.tfplan` = **9 to add, 0 to change, 0 to destroy**
  (dataset, 3 views, dataset_access, SA, 3 members); `terraform show` confirms no change to `google_bigquery_dataset.serving`.
- **L1** `terraform apply <saved plan>`.
- **L2** `terraform plan -detailed-exitcode` → 0 (F2), and `bq show --format=prettyjson synthea-readmission-risk:serving` lists
  the `dashboard` dataset entry with `targetTypes ["VIEWS"]`.
- **L3** `make dashboard-access-check` → exit 0.
- **L4** (prerequisite `make gold-200k` if `data/gold/main_200k` is absent) `make dashboard-query-check` → exit 0; record every
  measurement (expected: newest published date == the clock's sim date; 2025-07-31 and 2025-06-30 excluded).
- **L5** `terraform plan` = No changes again; **L6** billing for the slice's days (owner) and CI green on the PR into `v3`.

## Risks

| Risk | Mitigation |
|---|---|
| Authorization silently dropped | V-I1; L2/L5 plans; Q0 fails |
| Any `dashboard` view can read all of `serving` | only the owner can create views there; A5 proves the runtime SA cannot |
| Requirement/pushdown behaves unexpectedly through the view (F1/F8) | Q2/Q3 measure both directions before slice 3 |
| Rate cutoff drifts from a future champion | V-W pins it to prereg |
| `deletion_protection` on views blocks a rename | in-place SQL updates unaffected; a rename is a deliberate two-step change |

## Out-of-scope follow-ups

Slice 3 app over `dashboard.queries`; slice 4 Cloud Run as `dashboard-runtime`; deleting the v1 rows; TVF fallback (F1); a
predictions/gold discharge-time data test; rate materialization; `payer_ownership`; re-pinning the cutoff at the next promotion;
the published-dates limit (F7); one shared epoch constant.

## Implementation and pre-commit review (2026-10-31)

Built as designed (revision 5.1), with these implementation-level refinements:
- **Q0 parses all-or-nothing** (pre-commit round 1): a malformed published row now makes Q0 `fail` and skips every dependent check, so no
  check or report measurement is computed from a partial list. This is stricter than Interfaces 6's "≥ 1 parseable row".
- **The FR7 test resolves every `dataset_id` spelling and fails closed** (rounds 1-2): `tests/test_infra.py::_dataset_named` resolves
  string literals, `"${…}"` interpolations, dataset references and table references (the spelling the table-level IAM members use) to
  the dataset id. Any dataset-IAM resource that does not resolve to a declared dataset fails the test.
- **Teardown:** `docs/setup.md` step 8 now covers the three views and empties `dashboard` (round 1).
- **Q4 and the local gold:** an encounter of the call sheet that is absent from the local gold counts as a parity mismatch, not an
  error.

`/eg-precommit-review`: 3 rounds, 4 findings fixed (round 1: 3, round 2: 1), 0 rebutted; round 3 returned `no findings`. The round-2
finding hit the early-stop rule, and the owner chose "keep working". Offline sanity check on the local `data/gold/main_200k`
(fingerprint `80edd13d…`):
- **Population:** `reason_rates` covers 152,876 settled pre-2023 discharges over 42 reason keys; 37 reasons show a rate and 5 are
  withheld (< 30 rows).
- **Spread:** rates span about 0.08%-10.8%.
- **F4 occurs:** of the 31,754 discharges from 2023 on, 3 have a reason with no pre-2023 history and 3 more have a withheld rate.

## Results (live, owner-confirmed)

The owner authorized L0-L5 in sequence (2026-10-31), on the condition that the run stop if the L0 plan differed from the design.

- **L0 (just before L1):** `terraform plan -out=<scratchpad>/slice2.tfplan` = `Plan: 9 to add, 0 to change, 0 to destroy`, the 9 designed
  resources. Re-checked from the saved plan file: 89 no-op, 9 create, 0 change/destroy (serving untouched). Each view's `query` is
  byte-identical to `dashboard_views.view_sql(name)`, with `deletion_protection = true` and `use_legacy_sql = false`; the
  dataset_access is `dashboard` with `target_types ["VIEWS"]` on `serving`. The grants are exactly dataViewer on `dashboard`,
  project jobUser and the operator's token creator. `dashboard` is in us-central1, with no expiry and
  `delete_contents_on_destroy = false`.
- **L1 (19:42Z):** `terraform apply` of exactly that saved plan: `9 added, 0 changed, 0 destroyed`. The plan file was then deleted.
- **L2:** `terraform plan -detailed-exitcode` = No changes. `bq show` of `serving` lists its 4 default entries plus
  `{"dataset": {"datasetId": "dashboard", …}, "targetTypes": ["VIEWS"]}`, so the dataset resource did not rewrite the ACL.
- **L3:** the first run (19:42Z, seconds after the apply) failed: every probe was `error` (exit 5), with impersonation itself
  refused (`iam.serviceAccounts.getAccessToken` denied): F5, propagation of the new token-creator grant. A credential failure is
  correctly not counted as "denied". Impersonation worked about 2 minutes later. The re-run `make dashboard-access-check` gave
  **exit 0**: all 4 `serving` reads were denied (403 "User does not have permission to query table"), as were creating a view in
  `dashboard` and listing `scratch` / `scratch_gold`; all 3 views were `ok` (VIEW, designed columns, no forbidden column).
  Report: `data/checks/dashboard_access_check/20261031T194359Z.json`.
- **L4:** `make dashboard-query-check` gave **exit 0**, every check passing. Report:
  `data/checks/dashboard_query_check/20261031T194424Z.json`.

  | Check | Result |
  |---|---|
  | Q0 | 29 published dates; newest 2023-10-30 = the clock (M2) |
  | Q0b (i) | the v1 date 2025-07-31 (44 predictions of another gold) excluded |
  | Q0b (ii) | 0 call-sheet rows on 2025-07-31 |
  | Q0b (iii) | the 2025-06-30 dispatch (after the clock) excluded |
  | Q1 dry runs (bytes) | published_dates 14,611; call_sheet 16,963,638; monitoring_history 20,633; recent_scoring_runs 19,711 (all ≤ 100 MiB) |
  | Q2 | narrow 16,963,638 < wide 20,355,453 bytes |
  | Q3 | both filter-less variants refused "without a filter" (F1 and F8 did not fire: the requirement survives the view, and the normal queries are accepted) |
  | Q4 | 184 rows on 2023-10-30 = n_predictions, ranks 1..184, parity with the local `main_200k` gold for every row; `rows_without_reason_history` 0 |

  Billed bytes, with the cache off: 10-20 MiB per real query (the 10 MB per-table minimum), about 115 MiB for the whole check.

  Finding (not a failure): the pruning gap is modest because ~13.5 MB of the call sheet's 17 MB is the reason-rate CTE's constant
  scan of all pre-2023 gold. Materializing the rate (an out-of-scope follow-up) would bring a call-sheet query down to a few MB.
- **L5:** `terraform plan -detailed-exitcode` gave exit 0 (No changes). `dashboard` holds exactly `v_call_sheet`, `v_monitoring`,
  `v_scoring_runs` (no leftover probe view).
- **L6 (open):** billing for 2026-10-31 (owner, console) and CI green on the PR into `v3`.
