# v3 slice 1: the 200k gold table in BigQuery (PRD FR1-FR5)

**Status:** design, revision 4, 2026-10-31 (after the 3-revision cap the owner chose "clarify in chat" and approved the
elephant's answers to the 13 open round-4 items, applied here). **Gate note:** round 6 closed readiness
(`implementation ready`); the critic's single remaining gap (auth errors remapped by `bundles.gcs.read_file`) is fixed below
(E1's cause-chain rule) but was not re-reviewed: the owner chose "apply fix and proceed" (override, 2026-10-31). Comprehension
passed in round 1. (`/eg-new-feature`). Branch `v3-slice1-gold-bigquery` off `v3`; PR into `v3`
(ruleset 24876876: PR required, 0 approvals), never into `main`. PRD: `notes/prds/v3-cloud-run-call-sheet-bigquery-gold-2026-10-28.md`
(V6, FR1-FR5, Risks rows 1-2, Open Questions 2 and 6). Where this note and the PRD differ, this note records an owner decision
(D1-D7). Requirement names: "v3 FRn" = the v3 PRD; "v2 FR3" = the v2 PRD's rule that the Spark job imports only the stdlib,
`readmission_risk.pipeline` and runtime-3.0-bundled libraries.

Revision 2 follows the two live probes below: the connector's direct overwrite is rejected by BigQuery on a filter-required
table (FAIL U1, pre-registered), so the pre-registered fallback v3 FR4 (write-audit-publish) is the production path, by a
BigQuery load job (D6) run by a new least-privilege SA (D7). The Spark job, the Dataproc submitter and the connector are not
touched in this slice.

## Owner decisions (2026-10-31)

| # | Decision | PRD effect |
|---|---|---|
| D1 | Gold lives in `serving.gold` (PRD Open Question 2, elephant default). | closes OQ2 |
| D2 | **MONTH** partitioning on `index_stop`, not DAY. Measured on `data/gold/main_200k`: discharges span 1918-01-26..2026-09-25; from 1960 on they fall on **22,184 distinct days**, above BigQuery's limits of 10,000 partitions per table and 4,000 per job (quotas page, read 2026-10-31). MONTH = 801 partitions + `__UNPARTITIONED__` (3,457 rows before 1960-01-01). | amends v3 FR1 ("DAY-partitioned") |
| D3 | The GOLD checks run on the laptop as part of the publish CLI (identity: D7), not by a Dataproc job; results in a JSON report under `data/checks/<gold name>/` and this note's Results. | refines v3 FR5 "results recorded" |
| D4 | (superseded by D5) A Terraform throwaway table written by Dataproc. | — |
| D5 | v3 FR2 is answered by the **SQL probe** (operator, scratch; it runs the connector 0.44.0's literal STATIC-overwrite MERGE): **FAIL U1**. By the pre-registered rule the production path is v3 FR4; the Dataproc throwaway test and `serving.gold_merge_test` are not built. The probe is the "FR2 record" of PRD M2. | executes FR2 → FR4; FR3 (connector write) not built |
| D6 | FR4 staging route **(a)**: a BigQuery **load job** of the existing GCS gold Parquet (`v2/main_200k`, the bytes the scorer, monitor and gate already use) into `scratch_gold`; no 200k rerun. Proof that BigQuery holds exactly that gold: the `spark-rowhash-v1` fingerprint of the rows read back equals the metadata's. | replaces FR3's "200k rerun fingerprint" with a BigQuery-content fingerprint; Spark not touched |
| D7 | The publisher is a new SA **`gold-publisher`**, impersonated by the operator (like `bundle-writer`/`scorer`). `dataproc-runtime` gets no BigQuery grant. | refines FR3/FR4 grants |
| D8 | (owner-accepted 2026-10-31) v3 FR5's "run after each gold write" is met by running GOLD-01..07 on the **staging** table immediately before the publish (write-audit-publish), and after the publish by verifying that `serving.gold`'s content fingerprint equals the audited one. Equal `spark-rowhash-v1` fingerprints mean the same multiset of rows, so GOLD-01..06 then hold on `serving.gold` without re-running them (re-running them would need partition-filtered variants of every count); GOLD-07 depends on `serving.predictions`, which only gains rows of the same fingerprint between the audit and the verify. | interprets FR5 |

## Live probes (both owner-approved, operator ADC, `scratch` dataset, tables dropped)

### Probe 1 (FR2's question, pre-registered before it ran; 2026-10-31 10:53 UTC)

U1 is a property of BigQuery's SQL semantics, independent of the connector and of identity. A target table with gold's
settings (MONTH partitioning on a TIMESTAMP column, `require_partition_filter = true`, clustering) with 3 rows (one before 1960)
and an unpartitioned source with 2 rows; then the connector 0.44.0's literal statement (from `BigQueryClient.java`,
`overwriteDestinationWithTemporary`): ``MERGE `dest`\nUSING (SELECT * FROM `temp`)\nON FALSE\nWHEN NOT MATCHED THEN INSERT
ROW\nWHEN NOT MATCHED BY SOURCE THEN DELETE``. Reading: an error containing "without a filter" = FAIL U1 → FR4; success → the
Dataproc FR2 test still runs.

**Result: FAIL U1.** `400 Cannot query over table '…gold_probe_target_20261031105330' without a filter over column(s)
'index_stop' that can be used for partition elimination; reason: invalidQuery` (job `5dab62e3-64e6-461e-8ff3-d40f1a3bd226`).
The connector has no filter option for STATIC; DYNAMIC lets stale partitions survive (U2); dropping `require_partition_filter`
breaks v3 FR1 and `tests/test_infra.py`. Other connector facts read from 0.44.0 (recorded for the follow-up): the temp table
goes to `materializationDataset` when set and copies the existing table's schema; an existing-but-empty table takes the same
temp + MERGE path; a mismatched DataFrame is refused with "Destination table's schema is not compatible with dataframe's schema".

### Probe 2 (load + publish transaction; 11:02 UTC)

Pre-registered: load types exact and the loaded rows' fingerprint equal to the source metadata, and the publish transaction
accepted and idempotent → FR4 rests on them. **Result: all pass.**
- Load job `a221649f-9adf-462b-bbf3-939a3bf9f34d`: `gs://…-gold/v2/pilot_20k/part-*.parquet` (Spark's INT96 timestamps) into an
  explicit all-NULLABLE schema: 18,861 rows, 258 positives, 0 NULL `index_stop`, 1920-09-08..2026-09-24, types as declared.
- `list_rows(...).to_arrow(create_bqstorage_client=False)` → `rowhash_fingerprint` = `b952a3d1…c4d4` = the pilot metadata's
  (assumption A1, exact FLOAT64 round-trip over REST, holds on these rows).
- `BEGIN TRANSACTION; DELETE FROM target WHERE index_stop >= TIMESTAMP '0001-01-01'; INSERT INTO target SELECT * FROM staging;
  COMMIT TRANSACTION;` on a MONTH, filter-required target: accepted; run 1 (empty) deleted 0 / inserted 18,861; run 2 deleted
  18,861 (incl. `__UNPARTITIONED__`) / inserted 18,861; fingerprint equal afterwards.
- 20 MiB billed per publish. This is probably two 10 MB per-table minimums (DELETE target + INSERT's staging scan), not a
  per-row figure, so it does not scale linearly. Estimate for 185,294 rows: the publish's INSERT reads all 24 columns,
  ≈ 400 B/row ≈ 75 MB; the audit reads 14 columns (≈ 170 B/row ≈ 32 MB) plus `serving.predictions`' `encounter_id` and
  `gold_fingerprint` (≈ 100 B/row, < 15 MB). Both are near or under `MAX_BYTES_BILLED = 100 MiB`, too close for comfort, so
  both the audit and the publish get `GOLD_PUBLISH_MAX_BYTES_BILLED = 1 GiB` (≈ $0.006 at on-demand rates); L3/L4 record the
  measured bytes.

## Why

v3's dashboard (slice 2's authorized view `v_call_sheet`) needs gold's feature columns next to `serving.predictions`, which holds
no features. Gold exists only as GCS Parquet (`gs://synthea-readmission-risk-gold/v2/main_200k/`, fingerprint
`80edd13dcc967d69b6fa4cfa17b4d7fa40188f8e7e17b2031b833332a59c63f3`, 185,294 rows / 2,825 positives). PRD V6 keeps gold in BigQuery.

## Scope

**In:**
1. Terraform: dataset `scratch_gold`; table `serving.gold`; SA `gold-publisher` and its 7 IAM members (Interfaces §2).
2. `src/readmission_risk/warehouse/schemas/gold.json`; `warehouse/sql/gold.sql` (GOLD-01..07); `warehouse/data_tests.py` gold
   branch; `warehouse/bigquery.py` constants and a `max_bytes_billed` parameter on `query_config`.
3. `src/readmission_risk/warehouse/gold.py` (the publish: load → audit → one transaction → verify), its CLI
   `scripts/publish_gold_to_bigquery.py`, `make gold-publish` (LIVE).
4. `src/readmission_risk/warehouse/gold_seeded_check.py` (the project's M5 standard for the GOLD assertions), CLI
   `scripts/gold_seeded_check.py`, `make gold-seeded-check` (LIVE, scratch only).
5. `warehouse/gold_source.py`: extract its bounded, validated listing into a shared function (behaviour unchanged).
5b. `warehouse/gold_access_check.py`, CLI `scripts/gold_access_check.py`, `make gold-access-check` (LIVE, expected-denied
   probes, §6).
6. Live runbook L0-L7, every LIVE step owner-confirmed.
7. Docs: `docs/setup.md` "Gold in BigQuery" section (runbook, the probes, cost); CLAUDE.md: ruleset sentence also names `v3`
   (chore), the "v3 architecture" Data bullet corrected (FR4, not connector direct write), a slice-1 bullet, Build & test lines
   for the two make targets.

**Out (named follow-ups):** connector-based staging (route (b): Spark → Storage Write API into a NEW staging table, no MERGE,
then the same audit + publish) for the CV signal; authorized views and the dashboard SA (slice 2); scheduling the publish in CI;
re-pointing scorer/monitor at BigQuery gold; a SQL-side fingerprint.

## Surfaces touched

| Layer | File | Change |
|---|---|---|
| infra | `infra/bigquery.tf` | `google_bigquery_dataset.scratch_gold`, `google_bigquery_table.gold` |
| infra | `infra/iam.tf` | section "v3 slice 1: gold publisher": `google_service_account.gold_publisher` + 7 IAM members |
| schema | `warehouse/schemas/gold.json` | new, 24 fields |
| warehouse | `warehouse/bigquery.py` | `_SCHEMA_NAMES` + `"gold"`; `GOLD_TABLE = "gold"`, `SCRATCH_GOLD_DATASET = "scratch_gold"`, `SERVING_GOLD_FINGERPRINT` (full hex), `GOLD_PUBLISH_MAX_BYTES_BILLED = 1024**3`; `query_config(*, max_bytes_billed: int = MAX_BYTES_BILLED, **kwargs)` |
| warehouse | `warehouse/sql/gold.sql`; `warehouse/data_tests.py` | new; gold branch (§4) |
| warehouse | `warehouse/gold.py`, `warehouse/gold_seeded_check.py`, `warehouse/gold_access_check.py` | new (§5, §6, §7) |
| pipeline | `pipeline/fingerprint.py` | `canonical_token = _token` (a public alias; the canonical form is unchanged and still pinned by `test_known_answer_vector`) |
| warehouse | `warehouse/gold_source.py` | extract `list_gold_objects` (§5) |
| bundles | `bundles/gcs.py` | `read_file(uri, name, *, client=None)` (optional injected client; default unchanged) |
| CLI | `scripts/publish_gold_to_bigquery.py`, `scripts/gold_seeded_check.py`, `scripts/gold_access_check.py` | new |
| make | `Makefile` | `GOLD_SA`, `PUBLISH_ARGS`; `gold-publish`, `gold-seeded-check`, `gold-access-check`; `.PHONY`, help |
| tests | `tests/test_infra.py`; `tests/warehouse/test_gold_schema.py`, `test_gold_sql.py`, `test_gold_publish.py` (incl. the gold CLI's tests), `test_gold_seeded_check.py`, `test_gold_access_check.py`, `test_data_tests.py`, `test_gold_source.py`, `fake_bigquery.py`, `gold_fixture.py`; `tests/bundles/test_gcs.py`; `tests/pipeline/test_fingerprint.py`; `tests/test_tooling.py` (Make targets, if it enumerates them) | Verification |
| docs | `docs/setup.md`, `CLAUDE.md`, this note's Results | |

Not touched: `pipeline/**` except the one-line public alias in `fingerprint.py`, `scripts/run_big_join.py`, `ops/**`, `requirements*.txt` (no new pin: `google-cloud-bigquery` and
`google-cloud-storage` are already runtime pins, and `list_rows` needs no `db-dtypes`).

## Interfaces

### 1. Terraform (`infra/bigquery.tf`)

```hcl
resource "google_bigquery_dataset" "scratch_gold" {
  dataset_id                  = "scratch_gold"
  location                    = var.region
  description                 = "Gold publish staging tables (expire after 1 day)."
  default_table_expiration_ms = 86400000
  delete_contents_on_destroy  = true
  labels                      = { purpose = "scratch" }
  depends_on                  = [google_project_service.enabled]
}

resource "google_bigquery_table" "gold" {
  dataset_id               = google_bigquery_dataset.serving.dataset_id
  table_id                 = "gold"
  description              = "Gold features + label, one row per index encounter (synthetic Synthea data; not for clinical use). Label unplanned_readmission_v1; spark-rowhash-v1 fingerprint 80edd13dcc967d69b6fa4cfa17b4d7fa40188f8e7e17b2031b833332a59c63f3 (v2/main_200k). Published by write-audit-publish (scripts/publish_gold_to_bigquery.py)."
  schema                   = file("${path.module}/../src/readmission_risk/warehouse/schemas/gold.json")
  require_partition_filter = true
  clustering               = ["admission_reason_code"]
  deletion_protection      = true
  time_partitioning {
    type  = "MONTH"
    field = "index_stop"
  }
  depends_on = [google_project_service.enabled]
}
```
Clustering on `admission_reason_code` (the PRD's example) although it is NULL in 50,543 of 185,294 rows (measured): NULLs
cluster together; harmless at this size. No `access` blocks on any dataset (the file's rule; slice 2's FR7 relies on it).

`gold.json`: the 24 columns in `big_join.GOLD_TABLE_COLUMNS` order, all `"mode": "NULLABLE"` (the load must accept the Parquet's
nullable columns; non-null rules are GOLD-01/05), types from `pipeline.fingerprint.GOLD_COLUMN_KINDS`: string → STRING, int →
INTEGER, double → FLOAT, timestamp → TIMESTAMP (probe 2's exact mapping); every field has a non-empty `description`.

### 2. `gold-publisher` (`infra/iam.tf`)

`google_service_account.gold_publisher`: `account_id = "gold-publisher"`, display name "Gold publisher (v3 slice 1)",
description "Loads gold Parquet into scratch_gold, audits it and publishes it into serving.gold; impersonated by the operator."

| Resource name | Type | Role | On |
|---|---|---|---|
| `gold_publisher_reads_gold` | `google_storage_bucket_iam_member` | `"roles/storage.objectViewer"` | `google_storage_bucket.gold.name` (load source + metadata) |
| `gold_publisher_reads_bundles` | `google_storage_bucket_iam_member` | `"roles/storage.objectViewer"` | `google_storage_bucket.bundles.name` (the champion manifest, for GOLD-07) |
| `gold_publisher_edits_scratch_gold` | `google_bigquery_dataset_iam_member` | `"roles/bigquery.dataEditor"` | `google_bigquery_dataset.scratch_gold.dataset_id` |
| `gold_publisher_writes_gold` | `google_bigquery_table_iam_member` | `google_project_iam_custom_role.scorer_table_writer.id` | `google_bigquery_table.gold` |
| `gold_publisher_reads_predictions` | `google_bigquery_table_iam_member` | `"roles/bigquery.dataViewer"` | `google_bigquery_table.predictions` (GOLD-07) |
| `gold_publisher_runs_jobs` | `google_project_iam_member` | `"roles/bigquery.jobUser"` | `var.project_id` |
| `operator_impersonates_gold_publisher` | `google_service_account_iam_member` | `"roles/iam.serviceAccountTokenCreator"` | `google_service_account.gold_publisher.name`, `member = var.operator_member` |

Table-level members use `dataset_id = google_bigquery_table.<t>.dataset_id` and `table_id = google_bigquery_table.<t>.table_id`,
like the scorer's. Nothing on the `serving` dataset; no `tables.create`/`delete` on `serving`; no WIF binding (laptop only). Load jobs into
`serving` are impossible by design (they need dataset-level `tables.create`, slice 4's live finding): the load targets
`scratch_gold`. Disclosed residuals: `updateData` allows row deletes (time travel, 7 days); the reused role is titled "Scorer
table writer" (permissions correct; renaming is a follow-up).

Infra-test consequences: `test_iam_is_additive_only` 40 → **47** `*_iam_member` resources (comment gains "+ 7 (v3 slice 1:
gold-publisher 6, its impersonation 1)"); `test_scorer_grants_are_exactly`'s "nothing on the serving dataset" assertion stays
true (table-level grants carry `google_bigquery_table.*.dataset_id`); the module docstring's BigQuery paragraph names
`serving.gold` and `scratch_gold`.

### 3. `warehouse/bigquery.py`

`query_config(*, max_bytes_billed: int = MAX_BYTES_BILLED, **kwargs)` passes `maximum_bytes_billed=max_bytes_billed`; existing
callers unchanged. `load_schema("gold")` works via `_SCHEMA_NAMES`. New constants as in Surfaces.

### 4. GOLD data tests (`warehouse/sql/gold.sql`, `warehouse/data_tests.py`)

`gold.sql` audits the **staging** table (unpartitioned, created by the load, so no partition filter is required and NULLs are
countable). Shape rules as `predictions.sql` (one DECLARE reads tables; every ASSERT reads only `c`; row checks
`COUNTIF((<must hold>) IS NOT TRUE)`; labels `AS 'GOLD-NN: <message>'`; no `CURRENT_*`; a script stops at its first failing
ASSERT). Placeholders `{table}` (staging) and `{predictions_table}`; parameters `@n_rows INT64`, `@n_positive INT64`,
`@predicted_fingerprint STRING`. The predictions subquery filters `as_of_date BETWEEN DATE '2000-01-01' AND DATE '2100-12-31'`
(predictions requires a partition filter).

| ID | Assertion |
|---|---|
| GOLD-01 | `c.null_index_stop = 0` (`COUNTIF(index_stop IS NULL)`: nothing would land in `__NULL__`) |
| GOLD-02 | `c.n_rows = @n_rows` (row count equals the metadata) |
| GOLD-03 | `c.n_positive = @n_positive` (`SUM(is_readmitted)`; no `COALESCE`, the scripts' house rule: an empty staging table fails here on NULL, and empty gold is never publishable) |
| GOLD-04 | `c.n_distinct_encounter = c.n_rows AND c.null_encounter = 0` (encounter_id unique, non-null) |
| GOLD-05 | `c.bad_not_null = 0`: these 13 columns are non-null. `GOLD_NOT_NULL_COLUMNS` in `gold.py` is DEFINED as `tuple(c for c in models.data._REQUIRED_NONNULL_COLUMNS if c not in ("encounter_id", "index_stop"))` (the loader the trainer, scorer and gate use; the two keys are GOLD-04/01), so the audit can never be looser than the loader; a test also compares it with the SQL. Today: `patient_id`, `index_start`, `is_readmitted`, `label_horizon`, `prior_encounter_count`, `prior_inpatient_count`, `prior_emergency_count`, `active_condition_count`, `active_medication_count`, `procedure_count_window`, `active_careplan_count`, `observation_count_window`, `age_at_index_years`. Not required non-null (nullable by construction; main_200k: `admission_reason_code`/`_description` 50,543 NULL, `marital_status` 16,799): `admission_reason_code`, `admission_reason_description`, `payer_ownership`, `provider_specialty`, `organization_utilization`, `gender`, `race`, `ethnicity`, `marital_status` |
| GOLD-06 | `c.bad_label = 0`: `is_readmitted IN (0, 1)` |
| GOLD-07 | `c.n_predicted >= 1 AND c.missing_predicted = 0`: `serving.predictions` has ≥ 1 row with `gold_fingerprint = @predicted_fingerprint`, and every such row's `encounter_id` is in staging (the champion's predictions stay joinable after the publish: slice 2's call-sheet join) |

`data_tests.py`: `SCRIPT_NAMES` gains `"gold"`; `_DESCRIPTION_RE` and `_ASSERT_MESSAGE_RE` accept `(?:PRED|RUNS|MON|GOLD)-\d{2}`;
`render_script(name, *, table, predictions_table=None)`: for `"gold"`, both are validated by `_TABLE_RE`; `table` must be in
dataset `scratch_gold`; `predictions_table` is required and must be EITHER exactly `<project>.serving.predictions` (production)
OR in dataset `scratch_gold` (the seeded check's copies, §7); anything else → `ValueError`. For the other scripts a non-None
`predictions_table` raises `ValueError`. `run_data_tests(client, "gold", *, table, predictions_table, n_rows, n_positive,
predicted_fingerprint, max_bytes_billed=MAX_BYTES_BILLED, job_id=None)`: the three gold keywords are required for gold and
refused (ValueError) for other scripts, as are `run_date`/`scoring_run_id` for gold; `max_bytes_billed` and `job_id` are new
keywords for every script (defaults unchanged behaviour), passed to `query_config` / `client.query(job_id=…)`; when (and only
when) `job_id` is given, `client.query` also gets `job_retry=None` (a caller-chosen id cannot be retried under the default
job retry; `tests/warehouse/fake_bigquery.py` asserts it, as `staging.publish` does), so existing callers are unchanged; the publish passes
`GOLD_PUBLISH_MAX_BYTES_BILLED` and `job_id="gold-audit-<publish_id>"`, and afterwards reads `client.get_job(job_id)
.total_bytes_billed` into `bytes_billed["audit"]` (a failed read → None, never an error). Parameter types: INT64, INT64,
STRING. Return value and error classification unchanged (`DataTestResult`, `classify_error`).

### 5. `warehouse/gold.py` (the publish)

```python
GOLD_NOT_NULL_COLUMNS: tuple[str, ...]       # GOLD-05's 13 columns (definition in §4)
PUBLISH_ID_RE = re.compile(r"[0-9a-f]{12}")

@dataclass(frozen=True)
class GoldPublishRequest:
    gold_uri: str                            # stored as given (no __post_init__ validation); step 1 refuses it unless it is
                                             # "gs://synthea-readmission-risk-gold/v2/<name>" with <name> fullmatching
                                             # [a-z0-9_]{1,64} (gold_name(gold_uri) -> str | None does the parse)
    champion_path: Path = Path("deploy/champion.json")
    expected_fingerprint: str = SERVING_GOLD_FINGERPRINT   # tests pass their fixture's; production never overrides it
    dry_run: bool = False                    # everything up to and incl. the audit; no transaction (staging dropped)

@dataclass(frozen=True)
class GoldPublishResult:
    status: str        # "published" | "refused" | "audit_failed" | "content_mismatch" | "verify_failed" | "permission_denied"
                       # | "unknown" | "dry_run_passed"
    publish_id: str
    gold_uri: str
    metadata: dict                           # literal GoldMetadata field names: gold_fingerprint, gold_fingerprint_definition,
                                             # n_rows, n_positive, reference_date, label_definition ({} if not read)
    champion_gold_fingerprint: str | None
    load_job_id: str | None; publish_job_id: str | None
    data_tests: DataTestResult | None
    staging_fingerprint: str | None; serving_fingerprint: str | None
    serving_num_rows: int | None
    diagnostic: list[str]                    # empty unless a fingerprint differed (see "Content-mismatch triage")
    schema_matches: bool | None; partitioning_matches: bool | None; description_names_fingerprint: bool | None
    bytes_billed: dict[str, int | None]      # {"audit": …, "publish": …} (job.total_bytes_billed or None); the load is free
    message: str                             # one line, printed by the CLI

def publish_gold(client, storage, request: GoldPublishRequest, *, publish_id: str | None = None) -> GoldPublishResult
    # publish_id None -> uuid.uuid4().hex[:12]; a given value must fullmatch PUBLISH_ID_RE, else ValueError raised before
    # any I/O (a programming error, not a status)
def write_report(result: GoldPublishResult, path: Path) -> None   # JSON, sorted keys, + "checked_at" (UTC ISO), "git_head"
def gold_name(gold_uri: str) -> str | None                        # "<name>" of a valid v2 gold URI, else None
def report_path(result: GoldPublishResult, root: Path = Path("data/checks")) -> Path
    # root/<gold_name>/gold_bigquery_<publish_id>.json, or root/_gold_publish_refused/<publish_id>.json if gold_name is None
def failed_result(request: GoldPublishRequest, publish_id: str, *, status: str, message: str) -> GoldPublishResult
    # every other field None / {} / []: the CLI's result for a failure before publish_gold ran
```

**Error classification** (one table, applied by `publish_gold` in this order, so the status never depends on which step hit
it; `_classify(exc, step) -> (status, exit)` is a pure function with a test per row):

| # | Exception (any step unless named) | Status | Exit |
|---|---|---|---|
| E1 | `google.api_core.exceptions.Forbidden`, Python `PermissionError`, `google.auth.exceptions.RefreshError`, `google.auth.exceptions.DefaultCredentialsError` (an un-propagated token-creator binding shows up as `RefreshError`) | `permission_denied`; message "permission/credentials refused at <step>: <error>; IAM may still be propagating (~8 min after a terraform apply): rerun" | 5 |
| E2 | step 1: ANY `ValueError`/`FileNotFoundError` raised in step 1 (this module's validation: URI, metadata content, fingerprint/label/definition mismatch, champion sha256, a manifest without a 64-lowercase-hex `gold_fingerprint`; and `gold_source.list_gold_objects`' refusals: object rule, `MAX_OBJECTS`, `MAX_TOTAL_BYTES`, no Parquet, no bucket), and `NotFound` for the metadata blob or the champion manifest | `refused` | 1 |
| E3 | step 2 (load): the load job finished with errors whose `reason` is `invalid`, `invalidQuery` or `badRequest` (schema/format problems), or `BadRequest` | `refused` (job errors in the message) | 1 |
| E4 | step 3 only: a failing ASSERT (`classify_error` verdict) | `audit_failed` | 3 |
| E5 | step 3 only: `ValueError` from `rowhash_fingerprint` on the staging rows (an unexpected cell type came back) | `content_mismatch` (the exception text in the message) | 3 |
| E5b | step 6 (after the commit): any failed check, a `ValueError` from `rowhash_fingerprint` included | `verify_failed` (the table WAS replaced; the message names the check) | 4 |
| E6 | anything else that is an `Exception`: transport/server errors (`ServiceUnavailable`, `InternalServerError`, `TooManyRequests`, `google.auth.exceptions.TransportError`, `requests` errors), the plain `OSError` that `read_file` raises for transport failures, other load-job errors, other API errors | `unknown`; message "could not run at <step>: <error>; rerun (the publish is idempotent)" | 5 |

`_classify` tests each row against the exception AND its `__cause__`/`__context__` chain, E1 first: `bundles.gcs.read_file`
remaps `Forbidden` → `PermissionError`, `NotFound` → `FileNotFoundError` and every other Google/auth error (incl. `RefreshError`)
→ a plain `OSError` raised `from` the original, so a remapped auth failure is still E1, never E6. Both step-1 blob reads (the
metadata and the champion manifest) go through `read_file(..., client=storage)`, one path, one mapping; a test feeds a real
`read_file`-wrapped `RefreshError`. `KeyboardInterrupt`/`SystemExit` propagate. Staging is dropped after the outcome is known and before the result is built
(deliberately not in `finally`, so a failing drop is noted in every result's message; skipped when the run stopped at step 1,
where no staging table can exist; `not_found_ok`; the dataset's 1-day expiry removes a leftover). A `Forbidden` whose reason is
`quotaExceeded`/`rateLimitExceeded` is E6 `unknown`, not E1 (pre-commit review round 2). Step 5 is special-cased below (resolve first, classify second).

Every GCS read uses the INJECTED `storage` client: `list_gold_objects(storage, gold_uri)`, the metadata blob, the champion
manifest via `bundles.gcs.read_file(uri, name, *, client=None)` (new optional keyword; `None` keeps today's
`gcp.storage_client()`; existing callers unchanged), and the diagnostic's downloads.

Steps:
1. **Inputs**:
   - `list_gold_objects(storage, gold_uri) -> list[tuple[str, Blob]]`, extracted from `gold_source.materialized_gold_dir`
     with ALL its rules: bounded listing (`MAX_OBJECTS + 1`), skipping `_SUCCESS`/`.crc`, the object rule (Parquet parts and
     `_gold_metadata.json` only, no `/`), the `MAX_TOTAL_BYTES` cap, and "no Parquet" → `FileNotFoundError`; bucket `NotFound` →
     `FileNotFoundError`; every OTHER Google API or auth error is re-raised **unwrapped** (so E1/E6 classify it).
     `materialized_gold_dir(gold_uri)` now calls `list_gold_objects(gcp.storage_client(), gold_uri)` inside a `try` that
     converts those unwrapped errors (`_download_failures()`) into today's `ValueError(f"cannot read gold at {gold_uri}: …")`,
     then downloads: its external behaviour (and every existing `tests/warehouse/test_gold_source.py` case) is unchanged.
   - `_gold_metadata.json` downloaded (`NotFound` between listing and download → E2 `refused`) and parsed by
     `parse_gold_metadata`; **refused unless all of**:
     `gold_fingerprint_definition == "spark-rowhash-v1"` (only v2 gold: the content check uses that canonicalizer);
     `label_definition == LABEL_DEFINITION`; `gold_fingerprint == request.expected_fingerprint` (= `SERVING_GOLD_FINGERPRINT`,
     the fingerprint the Terraform description names), so a different population can never replace `serving.gold` and leave
     the description stale.
   - Champion: `read_champion(champion_path)` → `read_file(bundle_uri, "manifest.json", client=storage)` → sha256 must equal
     the champion's `sha256` → `manifest["gold_fingerprint"]` **must equal the metadata's** (refused otherwise: the champion's
     predictions must refer to this gold); it is also `@predicted_fingerprint` for GOLD-07. Replacing gold with a new
     population therefore needs, in one change: the constant, the Terraform description, and a champion trained on it.
2. **Load** (job id `gold-load-<publish_id>`): `client.load_table_from_uri([gs://…/<part> for each Parquet part, sorted],
   "<project>.scratch_gold.gold_staging_<publish_id>", job_config=LoadJobConfig(source_format=PARQUET,
   schema=to_bq_schema(load_schema("gold")), write_disposition=WRITE_EMPTY, create_disposition=CREATE_IF_NEEDED))`;
   `.result()`. Errors classified by E1/E3/E6 (nothing published).
3. **Audit**: `run_data_tests(client, "gold", table=staging, predictions_table=<project>.serving.predictions, n_rows=…,
   n_positive=…, predicted_fingerprint=champion's, max_bytes_billed=GOLD_PUBLISH_MAX_BYTES_BILLED,
   job_id=f"gold-audit-{publish_id}")` → a failing ASSERT →
   `audit_failed`; other errors per the error rule. Then the staging content fingerprint (`list_rows(get_table(staging)).to_arrow(create_bqstorage_client=False)
   .to_pandas()` → `rowhash_fingerprint`) must equal the metadata's → else `content_mismatch` (never published).
4. `dry_run` → `dry_run_passed`, stop.
5. **Publish** (job id `gold-publish-<publish_id>`, `job_retry=None`, `query_config(max_bytes_billed=GOLD_PUBLISH_MAX_BYTES_BILLED)`):
   ```sql
   BEGIN TRANSACTION;
   DELETE FROM `<project>.serving.gold` WHERE `index_stop` >= TIMESTAMP '0001-01-01';
   INSERT INTO `<project>.serving.gold` (<24 back-quoted columns in gold.json order>)
     SELECT <same 24 columns> FROM `<staging>`;
   COMMIT TRANSACTION;
   ```
   (`publish_script(staging)`; probe 2's statement with explicit column lists, like `staging.publish_script`). The DELETE's
   constant predicate covers every non-NULL row (incl. `__UNPARTITIONED__`); NULL rows cannot exist in `serving.gold`, because
   only audited staging (GOLD-01) is ever inserted and the post-publish fingerprint covers all rows. If `client.query(...)`
   or `.result()` raises ANY `Exception` (Forbidden and transport errors included), `staging.resolve_job(client,
   publish_job_id)` runs FIRST: True (the transaction committed) → continue to 6; False (DONE with an error, or never
   created) → the ORIGINAL exception is classified by the table (E1 → `permission_denied`; a transient cause, i.e. a server
   error, `TooManyRequests`, a transport error or BigQuery's "Transaction is aborted due to concurrent update", → `unknown`,
   exit 5, "rerun" (pre-commit review round 1); `refused`, exit 1, ONLY for a genuine `BadRequest` that is not a
   concurrent-update abort, and every other cause (a network outage surfacing as `RetryError`/`ConnectionError`, timeouts)
   `unknown` (round 2)); None → `unknown` (exit 5; the message names the job id to check).
   `resolve_job`'s own exceptions are swallowed by it (it returns None).
6. **Verify** `serving.gold`: `get_table` → `num_rows == n_rows`; schema (names, `field_type`, `mode`, order) equal to
   `gold.json`; `table.time_partitioning.type_ == "MONTH"` and `table.time_partitioning.field == "index_stop"`;
   `table.require_partition_filter is True` (the TABLE-level property that Terraform sets; NOT the deprecated
   `time_partitioning.require_partition_filter`, which is None for such a table); `table.clustering_fields ==
   ["admission_reason_code"]`; description contains
   the metadata fingerprint; then `list_rows` (tabledata.list reads every partition, `__NULL__` included, and needs no filter)
   → fingerprint equal to the metadata's. All true → `published`; else `verify_failed` (the table WAS replaced by audited
   rows; the report names the failing check).

Content-mismatch triage (assumption A1, measured exact in probe 2): on a fingerprint difference in step 3 or 6,
`column_diagnostic(bq_frame, source_frame) -> list[str]` compares the BigQuery rows with the source Parquet (its parts downloaded
through the injected `storage` into a temporary directory and read with pyarrow) after an outer join on `encounter_id`. Output
lines, appended to `message` and stored as `diagnostic: list[str]` in the result: `only_in_bigquery=<n> only_in_source=<n>`,
then for every column with differences `column=<name> unequal=<n>`, where two cells are equal iff
`fingerprint.canonical_token(column, kind, a) == canonical_token(column, kind, b)` (the canonicalizer's own token, so dtype
and time-zone differences between BigQuery's tz-aware/float64 values and pyarrow's naive `timestamp[ns]`/int values cannot
show up as differences; two NULLs are equal; a token that raises counts as unequal). The `age_at_index_years` line is
`column=age_at_index_years unequal=<n> max_ulp=<m> other=<k>`: `m` is the max of `abs(np.float64(a).view(np.int64) -
np.float64(b).view(np.int64))` over unequal pairs where both are finite, non-null and of the same sign (`-` if there is none);
`k` counts the remaining unequal pairs (one side NULL, NaN or infinite, or opposite signs). A mismatch is classified
`float_serialization` in the message iff `age_at_index_years` is the only column listed, `k = 0` and `m <= 1`. If the
download or comparison raises, the single line `diagnostic unavailable: <ExceptionClass>: <message>` is stored instead. The
diagnostic never changes the status.

### 6. CLIs and make targets

`scripts/publish_gold_to_bigquery.py --gold-uri URI [--dry-run] [--impersonate-service-account EMAIL] [--champion PATH]`
(default URI `gs://synthea-readmission-risk-gold/v2/main_200k`, default SA `gold-publisher@synthea-readmission-risk.iam.gserviceaccount.com`):
The CLI, in this order: parse args; `publish_id = uuid4().hex[:12]`; `gcp.configure(impersonate_service_account=…)` (a
`ValueError` for a malformed email → `parser.error`, exit 2, no report: not an attempt); build `client = gcp.bigquery_client()`
and `storage = gcp.storage_client()` inside a `try` (credentials are built here, so a missing ADC (`gcp.credentials()` turns
`DefaultCredentialsError` into `FileNotFoundError`) or a `RefreshError` surfaces here) → the CLI builds
`gold.failed_result(request, publish_id, status="permission_denied", message=…)` (same hint as E1), writes its report and exits
5; then `publish_gold(client, storage, request, publish_id=publish_id)` (inside it, E1-E6 apply), report to
`data/checks/<name>/gold_bigquery_<publish_id>.json` (or `data/checks/_gold_publish_refused/<publish_id>.json` when the
URI was refused before `<name>` could be parsed; every attempt writes a report). Output, in this fixed order, one
`<check>: <value>` line each, `-` for a step that did not run: `status`, `publish_id`, `gold_uri`, `metadata
gold_fingerprint`, `champion gold_fingerprint`, `load_job_id`, `data_tests` (`passed` or the GOLD label), `staging_fingerprint`,
`publish_job_id`, `serving_num_rows`, `schema_matches`, `partitioning_matches`, `description_names_fingerprint`,
`serving_fingerprint`, `bytes_billed audit`, `bytes_billed publish`; then `message`, the diagnostic lines, and `report:
<path>`. Exit codes: 0
`published` / `dry_run_passed`; 1 `refused`; 3 `audit_failed` / `content_mismatch` (not published); 4 `verify_failed`
(published, check failed); 5 `permission_denied`, `unknown`, or an unexpected Google API/auth/network error (prints "outcome
unknown or checks could not run; rerun the CLI: the publish is idempotent").

`scripts/gold_seeded_check.py [--impersonate-service-account EMAIL]` (same default SA as the publish CLI).

`scripts/gold_access_check.py [--gold-sa EMAIL] [--scorer-sa EMAIL] [--monitor-sa EMAIL]` (thin CLI; the logic is
`src/readmission_risk/warehouse/gold_access_check.py::run_access_checks(make_client: Callable[[str], Client]) ->
list[ProbeResult]` with `ProbeResult(sa, probe, outcome: "denied" | "allowed" | "error", detail)`; `make_client(email)` builds
a BigQuery client impersonating `email` via `gcp.configure` + `gcp.bigquery_client()`). Live proof of the access limits,
slice 4's precedent. "Denied" means `google.api_core.exceptions.Forbidden`, or a `BadRequest` whose message contains
"Access Denied" or "Permission" (BigQuery reports some DML refusals that way); any other exception is `error`. Each probe must be
denied; an allowed probe is undone where it changed something:
- as `gold-publisher`: `create_table("<project>.serving.gold_access_probe")` (no `tables.create` on `serving`; if it
  succeeds, the table is deleted at once and reported); `DELETE FROM serving.predictions WHERE as_of_date = DATE '1900-01-01'`
  (only `dataViewer` there; matches no row even if allowed); `client.get_table("serving.scoring_runs")` (no grant);
- as `scorer` and as `monitor`: `SELECT COUNT(*) FROM serving.gold WHERE index_stop >= TIMESTAMP '0001-01-01'` (no read of
  gold, which holds labels).
Exit precedence: 3 if any probe was `allowed` (a FAIL, wins over everything), else 5 if any was `error`, else 0. The CLI
prints one line per probe and writes `data/checks/gold_access_check/<UTC timestamp>.json`. The operator already holds
token-creator on `scorer` and `monitor` (slices 4/7).

Make (Makefile, next to `score-bq`): new variables `GOLD_SA ?= gold-publisher@synthea-readmission-risk.iam.gserviceaccount.com`
and `PUBLISH_ARGS ?=`; targets, all reusing the existing `GOLD_URI` (same default `…/v2/main_200k`):
- `gold-publish` (LIVE: replaces `serving.gold` unless `PUBLISH_ARGS=--dry-run`): `$(BIN)/python scripts/publish_gold_to_bigquery.py --gold-uri $(GOLD_URI) --impersonate-service-account $(GOLD_SA) $(PUBLISH_ARGS)`;
- `gold-seeded-check` (LIVE, scratch_gold only): `… scripts/gold_seeded_check.py --impersonate-service-account $(GOLD_SA)`;
- `gold-access-check` (LIVE, expected-denied probes): `… scripts/gold_access_check.py --gold-sa $(GOLD_SA) --scorer-sa $(SCORER_SA) --monitor-sa $(MONITOR_SA)`.
`tests/test_tooling.py`: the three targets join `EXPECTED_TARGETS`; `GOLD_SA` and `PUBLISH_ARGS` join `MAKEFILE_VARIABLES`; a
new `test_make_gold_targets_run_as_the_gold_publisher_sa` mirrors `test_make_score_bq_and_m5_check_run_as_the_scorer_sa`
(dry-run `make -n` output names the SA and `$(GOLD_URI)`). They have no required variable, so the refusal test is unchanged.

### 7. Seeded check (`warehouse/gold_seeded_check.py`, the M5 standard for GOLD)

`seeded_gold_check(client) -> dict[str, DataTestResult]` (keys `"clean"`, `"GOLD-01"`..`"GOLD-07"`). For each key: create
`scratch_gold.gold_seed_<uuid8>_<slug>` (`slug = key.lower().replace("-", "_")`, e.g. `gold_01`, like `seeded_check.copy_name`)
with `gold.json`'s schema (unpartitioned, like staging) and insert, with ONE DML `INSERT INTO … (<columns>) VALUES (…), …`
statement per table, a fixed 10-row literal fixture (`FIXTURE_ROWS`, a module constant: encounter ids `seed-e01`..`seed-e10`, 2 positives (`seed-e01`, `seed-e02`), all
GOLD-05 columns set, `index_stop` 2019-01-01..2019-10-01 one per month), plus `scratch_gold.gold_seed_<uuid8>_<slug>_pred` with
`predictions.json`'s schema (unpartitioned) holding ONE row (one `INSERT … VALUES`) (`as_of_date` 2020-01-01, `encounter_id` `seed-e03`, `gold_fingerprint`
`"seedfp"`, other columns valid constants). Parameters: `n_rows = 10`, `n_positive = 2`, `predicted_fingerprint = "seedfp"`.
One mutation per ID, each breaking exactly that assertion while every earlier one holds:

| ID | Mutation (on the copy) |
|---|---|
| GOLD-01 | `UPDATE … SET index_stop = NULL WHERE encounter_id = 'seed-e10'` |
| GOLD-02 | `DELETE … WHERE encounter_id = 'seed-e10'` (a negative) |
| GOLD-03 | `UPDATE … SET is_readmitted = 1 WHERE encounter_id = 'seed-e10'` |
| GOLD-04 | `UPDATE … SET encounter_id = 'seed-e09' WHERE encounter_id = 'seed-e10'` (count unchanged) |
| GOLD-05 | `UPDATE … SET label_horizon = NULL WHERE encounter_id = 'seed-e10'` |
| GOLD-06 | `UPDATE … SET is_readmitted = 2 WHERE encounter_id = 'seed-e01'` and `UPDATE … SET is_readmitted = 0 WHERE encounter_id = 'seed-e02'` (sum stays 2) |
| GOLD-07 | `UPDATE <pred> SET encounter_id = 'seed-absent' WHERE as_of_date = DATE '2020-01-01'` |

`render_script` accepts the `scratch_gold` predictions copy by its rule in §4 (a `scratch_gold` table or exactly
`serving.predictions`). Pass: `clean` passed and every ID failed with exactly its own label. Tables dropped in `finally`. On a
non-ASSERT error the function raises `GoldSeededCheckError(message, results)` (a `RuntimeError` carrying the results so far,
like `seeded_check.SeededCheckError`). The CLI prints one line per key (partial ones too), writes
`data/checks/gold_seeded_check/<UTC timestamp>.json` (`{key: {passed, failure}}` + git head + `error` if any) in every case,
and exits 0 iff all as expected, 3 if any key was not as expected, 5 on `GoldSeededCheckError`. Cost: ≈ 8 × (2 inserts + ≤ 2 mutations + 1 test)
≈ 40 small queries (10 MB minimum billing each, < CHF 0.01 in total).

## Pipeline flow

`make gold-publish` → `gold-publisher` (impersonated) lists `v2/main_200k/` (validated) → reads metadata + champion manifest →
load job (Parquet → `scratch_gold.gold_staging_<id>`) → GOLD-01..07 on staging → staging content fingerprint = `80edd13d…`
→ one transaction replaces `serving.gold` → verify (row count, schema, partitioning, description, content fingerprint) → report
→ staging dropped. Re-running it is an idempotent full replace (probe 2).

## Data handling

- No change to features, labels, the split or any model input; no code under `pipeline/` changes; the published rows are the
  exact Parquet the scorer, monitor and gate use, and the content fingerprint proves it.
- `serving.gold` holds label columns; readable only by `gold-publisher` and the operator; the scorer, monitor and dashboard SAs
  get nothing on it (slice 2's views expose no label columns).
- GOLD-07 protects the predictions join: gold cannot be replaced by a population the champion's predictions do not refer to.
- Reproducibility: the publish is deterministic and idempotent; every check is order-independent.

## Failure modes

| Case | Behaviour |
|---|---|
| URI outside `gs://…-gold/v2/`, bad name, unexpected object under the prefix, no Parquet, missing/invalid metadata, non-rowhash-v1 definition, other label definition | `refused`, exit 1, nothing created |
| Metadata fingerprint ≠ `SERVING_GOLD_FINGERPRINT`, or ≠ the champion manifest's (e.g. `GOLD_URI=…/v2/pilot_20k`) | `refused`, exit 1, before any BigQuery job |
| Champion manifest missing/invalid or sha256 ≠ champion.json | `refused`, exit 1 |
| `Forbidden`/`PermissionError` at any step (listing, metadata, manifest, load, audit, publish, verify) | `permission_denied`, exit 5 with the IAM-propagation hint; nothing published unless the step was verify |
| Load job error other than Forbidden (schema/type mismatch) | `refused`, exit 1, job errors printed; staging dropped |
| A GOLD assertion fails | `audit_failed`, exit 3; `serving.gold` untouched |
| Staging fingerprint ≠ metadata | `content_mismatch`, exit 3, with the column diagnostic; untouched |
| Transaction error | `refused` exit 1 (rolled back, confirmed by `resolve_job`) or `unknown` exit 5 |
| Verify fails after a committed publish | `verify_failed`, exit 4; table holds the audited rows; fix and rerun |
| Network/auth error anywhere else | exit 5; rerun (idempotent) |
| Concurrent publishes | assumed, not measured: BigQuery aborts one of two concurrent transactions that change the same table ("Transaction is aborted due to concurrent update"), so that run ends `unknown` (exit 5, rerun; via `resolve_job` = False and the concurrent-update rule of step 5) and the other publishes; both carry the same gold, so the result is the same either way. Not guarded; no test asserts the ordering |
| Empty gold (0 rows) | GOLD-07 fails (no predicted encounter present) → `audit_failed` |

## Verification criteria

Offline (pytest, no GCP; `readmission_risk.gcp` factories raise unless faked):
1. `tests/test_infra.py`: `test_bigquery_tables_partitioned_require_filter_and_deletion_protected` gains
   `("gold", "index_stop")` with the partition type parameterised (`"MONTH"` for gold, `"DAY"` for the other three) and checks
   `clustering = ["admission_reason_code"]`; new `test_every_bigquery_table_is_partitioned_filtered_and_protected` (a generic
   loop over every `google_bigquery_table`); `scratch_gold`'s 1-day expiry and no `access` block; new
   `test_gold_publisher_grants_are_exactly` (the 6 resource grants of §2 and the one impersonation binding; nothing on the
   `serving` dataset; no WIF binding); new `test_gold_table_description_names_the_gold_fingerprint`
   (`SERVING_GOLD_FINGERPRINT` and `LABEL_DEFINITION` verbatim); `test_iam_is_additive_only` 40 → 47; `dataproc_runtime`
   counts unchanged (8).
2. `tests/warehouse/test_gold_schema.py`: `gold.json` names == `GOLD_TABLE_COLUMNS` (order) and == `EXPECTED_GOLD_COLUMNS`
   (set); all NULLABLE; types == the `GOLD_COLUMN_KINDS` mapping; descriptions non-empty.
3. `tests/warehouse/test_gold_sql.py`: shape rules; GOLD-01..07 contiguous, unique; the GOLD-05 column list in the SQL equals
   `GOLD_NOT_NULL_COLUMNS`, which equals `models.data._REQUIRED_NONNULL_COLUMNS` minus `encounter_id`/`index_stop` (so a
   loader change fails the test); `run_data_tests` passes `job_id` through and caps with `max_bytes_billed`; the predictions partition predicate present; `render_script` refuses a staging table outside
   `scratch_gold`, a foreign project, a missing or superfluous `predictions_table`; `run_data_tests` sends exactly the three
   typed parameters and refuses wrong keyword combinations; pass / GOLD verdict / re-raise via the fake client.
4. `tests/warehouse/test_gold_publish.py`. Fixture (module-level, in `tests/warehouse/gold_fixture.py`): a 6-row gold frame
   in `GOLD_TABLE_COLUMNS` order (2 positives, one row before 1960, all GOLD-05 columns set, one NULL `admission_reason_code`),
   written with pyarrow as `part-00000.parquet` (with `index_stop` etc. as `timestamp[ns]`, like Spark's), its
   `_gold_metadata.json` built with `metadata_to_json(GoldMetadata(..., gold_fingerprint=rowhash_fingerprint(frame),
   gold_fingerprint_definition="spark-rowhash-v1", ...))`, both put into the fake GCS under `v2/fixture/`; a fake champion
   manifest `{"gold_fingerprint": <that fingerprint>, ...}` under the bundles bucket and a `champion.json` in `tmp_path` whose
   `sha256` is the manifest bytes' sha256; the request passes `expected_fingerprint=<that fingerprint>`. (Fake BigQuery: `fake_bigquery.py` gains `load_table_from_uri` (records uris,
   destination, config; returns a job whose `result()` may raise), `get_table` (canned table objects), `list_rows(...).to_arrow(...)`
   (canned Arrow tables) and keeps `query`/`get_job`/`delete_table`; canned tables are REAL `google.cloud.bigquery.Table`
   objects built in the test (schema, `time_partitioning`, `require_partition_filter`, `clustering_fields`, `description`; `num_rows`
   set through `table._properties["numRows"]`), so the verify step reads the real client's attributes; fake storage from `tests/bundles/fake_gcs.py`):
   - each `refused` input case, incl. metadata fingerprint ≠ `expected_fingerprint`, champion fingerprint ≠ metadata, other
     label definition, v1 fingerprint definition, metadata `NotFound` after listing; `_classify` gets one test per table
     row E1-E6 (incl. `RefreshError` → `permission_denied`, `ServiceUnavailable` and a plain `OSError` → `unknown`, a
     fingerprint `ValueError` → `content_mismatch`); publish `.result()` raising `Forbidden` with `resolve_job` True →
     verified and `published`; an unparsable URI writes the `_gold_publish_refused` report; load
     error; GOLD failure → `audit_failed`, no publish query sent; one changed cell →
     `content_mismatch`, no publish; `dry_run` sends no publish query;
   - the publish script text (DELETE predicate, explicit column lists, job id, `job_retry=None`, the 1 GiB cap);
   - publish raising + `resolve_job` True/False/None → continue / `refused` / `unknown`;
   - verify: each of row count, schema, partitioning, description, fingerprint mismatched → `verify_failed`; all good →
     `published`;
   - staging always dropped (also on exceptions); INT64-vs-int32 and tz-aware timestamps give the Parquet frame's fingerprint;
   - the diagnostic: a changed float by 1 ulp → `max_ulp=1`; a missing row → `only_in_source=1`; a raising download →
     `diagnostic unavailable: …`; the status is unchanged in each;
   - every GCS read went through the injected fake storage (the global `gcp.storage_client` factory raises in tests);
   - report JSON round-trips; CLI exit codes 0/1/3/4/5 and 2 (malformed SA email), and a missing-ADC client build →
     `permission_denied` report + exit 5 (all CLI tests live here; `tests/warehouse/test_cli.py` is unchanged);
   - step 6: a fingerprint `ValueError` → `verify_failed` (exit 4), never `content_mismatch`; `require_partition_filter`
     False on the canned table → `verify_failed`;
   - `run_data_tests(job_id=…)` sends `job_retry=None`; without `job_id` the call is unchanged;
   - a champion manifest without `gold_fingerprint`, or with a non-hex one → `refused`.
   `tests/bundles/test_gcs.py`: `read_file(..., client=fake)` uses the given client.
   `tests/warehouse/test_gold_access_check.py`: each probe's denied (`Forbidden`, and a `BadRequest` "Access Denied") /
   allowed / other-error case maps to `denied` / `allowed` (with the undo for `create_table`) / `error`; exit precedence
   3 > 5 > 0.
   `tests/pipeline/test_fingerprint.py`: `canonical_token is _token`; the known-answer vector is unchanged.
   `tests/warehouse/test_gold_publish.py` (diagnostic): a tz-aware vs naive timestamp and an int64 vs float64 count with equal
   values are NOT reported unequal.
   `tests/test_tooling.py`: as in §6.
5. `tests/warehouse/test_gold_seeded_check.py`: every GOLD ID has a mutation; every statement targets only `scratch_gold`
   tables; the fixture satisfies all seven assertions as computed in Python (a pure re-implementation over `FIXTURE_ROWS`);
   cleanup on error.
6. `tests/warehouse/test_gold_source.py`: unchanged tests pass; `list_gold_objects` returns the parts and metadata and refuses
   the same cases.
7. `tests/warehouse/test_bigquery.py`: `query_config` default cap unchanged; the override is applied.

Live (each owner-confirmed; Results recorded here):
- **L0** read-only: `serving.gold`/`scratch_gold`/`gold-publisher` absent; `terraform plan` = exactly 1 dataset, 1 table, 1 SA,
  7 IAM members.
- **L1** `terraform apply`.
- **L2** `make gold-seeded-check` (clean passes; GOLD-01..07 each fire with their own label); then `make gold-access-check`
  (every probe denied: no `tables.create` on `serving` for `gold-publisher`, no write to `serving.predictions`, no read of
  `serving.scoring_runs`; neither `scorer` nor `monitor` can read `serving.gold`). Not measured live, by design: an
  audit failure on the real publish path (step 1's fingerprint refusals stop any other population before a job runs; the
  audit-failure path is covered offline and by L2's seeded check); a refused publish is shown live by
  `make gold-publish GOLD_URI=gs://synthea-readmission-risk-gold/v2/pilot_20k` → `refused` (exit 1), `serving.gold` unchanged.
- **L3** `make gold-publish PUBLISH_ARGS=--dry-run` on `v2/main_200k`: `dry_run_passed` (GOLD-01..07 pass with 185,294 / 2,825
  and the champion `569fdab6…`'s gold fingerprint; staging fingerprint `80edd13d…`).
- **L4** `make gold-publish`: `published`; record job ids, bytes billed, the report.
- **L5** rerun `make gold-publish` once: `published` again (idempotence on the real table).
- **L6** `terraform plan` = No changes (the publish did not alter the table definition).
- **L7** billing for the slice's days (owner, console) and CI green on the PR into `v3`.

## Out-of-scope follow-ups

Connector-based staging, route (b), for the CV signal (Spark → Storage Write API into a NEW `scratch_gold` table; no MERGE;
the same audit + publish); slice 2's views must filter `index_stop` with constants; scheduling the publish; a SQL-side
fingerprint; the `serving.gold` description literal must change with any new population (GOLD-07 then also requires a new
champion first); renaming the `scorerTableWriter` title; v2 L16 (calendar reminder 2026-12-16 + billing), in parallel.

## Implementation and pre-commit review (2026-10-31)

Built as designed (revision 4 + the gate fix). `/eg-precommit-review`: 5 rounds, 15 findings fixed, 0 rebutted, round 5
`no findings`; the stop rule tripped after round 2 and the owner chose "keep working" before rounds 3, 4 and 5. All findings
were error-path classification and reporting (exit codes, messages, a leftover-table note), none in the data path:
staging-drop note on every path (and not at step 1); the access check survives a client that cannot be built; a report-write
failure keeps the exit code; an uncommitted transaction is `refused` only for a genuine `BadRequest`, else `unknown` (rerun);
quota/rate-limit 403s are `unknown`, not IAM; an unknown load notes that its staging table may appear later; a non-check
failure at verify names the committed job; docstring, `docs/setup.md` (exits 4 and 5) and CLAUDE.md (Architecture item 3)
corrected. Test gate: `ruff check .` clean; `pytest` 1,937 passed, 2 skipped (the Dataproc-runtime environment check and the
off-macOS lock guard). No live step has run yet (L0-L7 below are pending, each owner-confirmed).

## Results (live, owner-confirmed)

- **L0 (2026-10-31, owner):** `serving.gold`, `scratch_gold` and `gold-publisher` absent; `terraform plan -out` = `Plan: 10 to add,
  0 to change, 0 to destroy`, the 10 designed resources. Re-checked before L1 from the saved plan file: 0 change/destroy/replace
  markers; `serving.gold` MONTH on `index_stop`, `require_partition_filter`, clustering `admission_reason_code`, deletion
  protection; all six grants to `gold-publisher`, impersonation for the operator; `scratch_gold` 1-day expiry, us-central1.
  PR #41 merged into `v3` (`e4f3eac`), all four CI jobs green.
- **L1 (2026-10-31 15:45Z):** `terraform apply` of exactly that saved plan. All 10 resources in state; live: `serving.gold` 24
  fields, 0 rows, `timePartitioning {MONTH, index_stop}`, `requirePartitionFilter: true`, clustering `admission_reason_code`;
  `scratch_gold` `defaultTableExpirationMs` 86400000; `gold-publisher` enabled. A fresh `terraform plan -detailed-exitcode`
  right after: exit 0 (No changes). Plan file deleted. Noted: `infra/outputs.tf`'s `bigquery_datasets` output does not list
  `scratch_gold` (cosmetic; follow-up).
- **L2a (2026-10-31 15:57Z, ~12 min after L1): `make gold-seeded-check` → `GOLD M5 OK`.** The clean pair passed; GOLD-01..07 each
  failed on its own seeded bad row with its own label (first run of `gold.sql` against BigQuery: it parses, the predictions
  partition filter is accepted, and the stop-at-first-ASSERT order holds). `scratch_gold` empty afterwards. Report
  `data/checks/gold_seeded_check/20261031T155827Z.json`.
- **L2b (15:58Z): `make gold-access-check` → exit 0, all 5 probes denied (403):** `gold-publisher` cannot create a table in
  `serving` (`serving.gold_access_probe`: Not found afterwards, i.e. refused, not undone), cannot change `serving.predictions`
  ("Permission bigquery.tables.updateData denied"), cannot read `serving.scoring_runs`; `scorer` and `monitor` cannot read
  `serving.gold` (accessDenied, jobs `7daf5231-…`, `b6df7e2f-…`). `serving` still holds exactly gold, monitoring, predictions,
  scoring_runs. Report `data/checks/gold_access_check/20261031T155843Z.json`.
- **L3 (2026-10-31, run by the owner): `make gold-publish PUBLISH_ARGS=--dry-run` → `dry_run_passed`** (publish id
  `1e7151a97536`, load job `gold-load-1e7151a97536`). GOLD-01..07 passed on the 185,294 staging rows against the champion
  `569fdab6…`'s predictions; staging fingerprint = metadata = champion gold fingerprint `80edd13d…63f3` (assumption A1, the exact
  REST round-trip, holds at full size). Audit billed 34,603,008 bytes (33 MiB; estimate 30-75 MB, cap 1 GiB). Afterwards
  `serving.gold` 0 rows, `scratch_gold` empty.
- **L4 (16:09-16:13Z): `make gold-publish` → `published`, exit 0** (publish id `85a513d3eed6`, jobs `gold-load-85a513d3eed6`,
  `gold-audit-85a513d3eed6`, `gold-publish-85a513d3eed6`). Verify: `serving_num_rows` 185,294, schema, partitioning and
  description match, serving content fingerprint `80edd13d…63f3`. Bytes billed: audit 34,603,008, publish 52,428,800 (50 MiB).
  Independent operator query: 185,294 rows, **2,825 positives** (= `_gold_metadata.json`, PRD FR3/M2), 185,294 distinct
  encounters, 3,457 rows before 1960, range 1918-01-26..2026-09-25; `INFORMATION_SCHEMA.PARTITIONS`: 801 populated MONTH
  partitions + `__UNPARTITIONED__` 3,457 rows + `__NULL__` 0 rows. `scratch_gold` empty. Report
  `data/checks/main_200k/gold_bigquery_85a513d3eed6.json`.
- **L5 (16:24-16:28Z): `make gold-publish` again → `published`, exit 0** (publish id `1f105e813ec6`): identical counts,
  fingerprint and bytes billed; 185,294 rows (not doubled). Idempotence proven from the transactions' child jobs:
  `gold-publish-85a513d3eed6` (L4) DELETE 0 / INSERT 185,294; `gold-publish-1f105e813ec6` (L5) DELETE 185,294 (incl. the
  `__UNPARTITIONED__` rows) / INSERT 185,294.
- **L6: `terraform plan -detailed-exitcode` → exit 0, "No changes"** after both publishes: the publish never alters the table
  definition.
- **L7 (open, owner):** billing for 2026-10-31 (console > Billing > Reports, project `synthea-readmission-risk`). Expected: cents
  (two probes, three loads, ~250 MiB of query bytes billed in total).

**PRD M2 status:** FR2 recorded (probe 1, FAIL U1 → FR4); counts equal to `_gold_metadata.json` (185,294 / 2,825) and the
BigQuery content fingerprint equal to `80edd13d…63f3`; GOLD tests pass and each is shown to fire. The remaining M2 clause
("views return rows with features for the newest tick") belongs to slice 2.
