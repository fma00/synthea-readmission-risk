# v2 slice 4: score to BigQuery (FR17–FR18, part of FR20, the GCS bundle seam)

**Status:** implemented and applied 2026-10-11 (design revision 8; gate overridden by the owner after round 7, see the revision log; implementation, review and live outcomes in "Results"). Commits `68edbdd` (the slice) and `26a534b` (the champion repoint). Open: L-post after v2 merges. Branch: `v2-slice4-score-to-bigquery` off `v2` (at `eda740b`, PR #5 merged; CI on PR #4 green on all four jobs), merged into `v2` by PR (ruleset `require-pr-main-and-v2`, id 24876876).

## Why

PRD `notes/prds/v2-mlops-keyless-scheduled-scale-up-2026-10-09.md`, "Implementation hints" item 4: "a `bigquery_writer` injected into `score_run_date`; `scoring_runs`; `ASSERT` scripts; one manual run on 10k (end of the spine)". Requirements served:
- FR17: predictions in BigQuery, one partition per run date, replaced per run date (v1 semantics);
- FR18: `ASSERT` data tests, NULL fails, pass/fail recorded in `scoring_runs`, a seeded bad row visibly fails (M5);
- FR20 (part): `scoring_runs` as the source of truth, and an Actions job summary;
- FR21 (recording half): a failed or refused run leaves a row (the GitHub issue is slice 7);
- FR6/FR7 (carried GCS half): a Python `gs://` bundle reader and create-only writer; `champion.json` repointed;
- NFR-S3: a least-privilege scorer service account;
- the cost NFR: `maximum_bytes_billed` on every query.

It ends the PRD's "spine" phase: IaC, keyless CI, a validated artifact, BigQuery serving.

## Owner decisions taken in Step 0 and the design check (2026-10-11)

| # | Question | Decision |
|---|---|---|
| D1 | The "one manually dispatched run" vs the WIF condition (`refs/heads/main` only) | **Laptop now, dispatch later.** Slice 4's live run is from the laptop impersonating the new `scorer` service account (same SA, same code path as CI). `.github/workflows/score.yml` is added (dispatch-only, keyless); its first dispatch joins the open V-post TODO after v2 merges. The WIF condition is not touched. |
| D2 | Where the 10k gold table lives | **GCS, download then load.** `data/gold/full_run_2b` (7 Parquet part files + `_gold_metadata.json`, 1.2 MB) is uploaded to `gs://synthea-readmission-risk-gold/full_run_2b/`. The scorer downloads that prefix into a temporary directory and runs the **unchanged** `load_gold_table_with_metadata`. Guard 6 recomputes `pandas-csv-sha256-v1` over the loaded frame's content (`models/data.py:216-228`); byte-identical files (checked in L2) are sufficient for it to match. No `gcsfs`. Gold is not loaded into BigQuery (slice 6). |
| D3 | Scope of the Python GCS bundle store | **Reader + create-only writer.** The writer cannot read (`objectCreator`), so a 412 is reported as "already exists"; there is no idempotent byte comparison on GCS. |
| D4 | Repointing `deploy/champion.json` to `gs://` | **Repoint and document.** `bundle_uri` becomes `gs://synthea-readmission-risk-bundles/75a1448729ef561c596f5e850f5bedce50ed63355ca2de38dc724c635d24eef4`; `sha256` unchanged (`b61388f0…`). The default `make score` then needs ADC that can read the bundle bucket; offline scoring passes `--bundle bundles/<id>`. Tests never touch GCS (enforced, see Verification). |
| D5 | (critic #8) What a run whose data tests fail does to the live partition | **Write-audit-publish.** Load into a short-lived **staging** table in a `scratch` dataset, run `predictions.sql` on it, and only if it passes publish the staging rows onto `serving.predictions`, replacing that run date's rows. The BigQuery table gains a `scoring_run_id` column (the local Parquet schema 2 is unchanged), so every live row names the attempt that produced it. A failing run never touches the live partition. **Mechanism amended in r3** (critic round 2, #4): the publish is a DML transaction (`DELETE` the run date's rows + `INSERT … SELECT` from staging, `COMMIT`), not a copy job. Copy and load jobs need `bigquery.tables.create` on the destination *dataset*, which D6 deliberately does not grant on `serving`; DML needs only table-level `getData`/`updateData`. Same intent, atomic, a few MB of billed query instead of a free copy. |
| D6 | (critic #10) The scorer's BigQuery access | **Option A: scratch dataset + custom no-delete role.** Dataset `scratch` (default table expiration 1 day) holds staging and M5 tables; the scorer has `roles/bigquery.dataEditor` there. On `serving`, it has only a custom project role `scorerTableWriter` = {`bigquery.tables.get`, `bigquery.tables.getData`, `bigquery.tables.updateData`}, granted **per table** on `predictions` and `scoring_runs`: it cannot create or drop tables in `serving` or change their metadata. `bigquery.tables.update` was removed in r3: it would allow setting a table expiration, which is an indirect drop. Residual (no BigQuery role is append-only): `updateData` allows DML `DELETE`/`UPDATE` of rows, so the scorer could still remove `scoring_runs` rows; BigQuery time travel (7 days, default) recovers them (runbook in `docs/setup.md`). Proven live by negative checks (L10). |

Defaults stated in Step 0 and not objected to:
- E1: the new direct pins are `google-cloud-bigquery` and `google-cloud-storage` only.
- E2: a load job into staging (scratch) and DML into `serving` (r3), not the Storage Write API.
- E3: one `scoring_runs` row per attempt, written in a `finally`.
- E4: `ASSERT` scripts as packaged `.sql` files, with a scratch-table M5 proof.
- E5: one query-config factory carrying `maximum_bytes_billed`.

## Facts this design relies on

| # | Fact | Status |
|---|---|---|
| F1 | `ASSERT expression [AS description]`: "If `expression` evaluates to `FALSE` or `NULL`, the statement generates an error"; the description appears in the error message. **Only the top-level expression**: inside `COUNTIF(…)`, `WHERE`, `EXISTS`, a NULL predicate is silently dropped (= passes). Hence the rule in Interfaces > SQL. | verified (Google "Debugging statements" page, 2026-10-11); the aggregate caveat is standard SQL three-valued logic |
| F2 | For a column-partitioned table, a load into `table$YYYYMMDD` requires every row to belong to that partition ("Otherwise, an error occurs"). | verified (load-data-partitioned-tables page) |
| F3 | BigQuery supports multi-statement transactions (`BEGIN TRANSACTION … COMMIT TRANSACTION`) containing DML on the same table; a transaction that conflicts with a concurrent one on the same table is aborted (the job fails, nothing is applied). DML needs `bigquery.tables.getData` + `bigquery.tables.updateData` on the table, not dataset-level `tables.create` (unlike copy and load jobs, which list `tables.create` on the destination dataset, per the critic's reading of the IAM docs). | from documentation knowledge, not re-fetched this session; **measured live**: L0 (a transaction as `scorer`), L5 (other partitions untouched), L10 (the negative checks) |
| F4 | Batch load jobs are free (shared slot pool); on-demand queries, DML included, bill ≥ 10 MB per referenced table; 1 TiB/month query free tier. | secondary sources (Google's pricing page did not render); **NHV**, checked against the billing report (L9) |
| F5 | `Blob.upload_from_string(..., if_generation_match=0)` sends one upload request with the precondition and no prior GET. | **unverified**; measured live with request logging (L3). Fallback if a GET is observed: upload through the JSON API directly, as in slice 3; decided then with the owner |
| F6 | `maximum_bytes_billed` on a multi-statement script job caps the script's billed bytes. | **unverified**; measured live (L9). The scripts are written so only ONE statement reads tables (see SQL), which keeps the question small |
| F7 | Dry-run resolution against the current `.venv` (2026-10-11): `google-cloud-bigquery==3.46.1`, `google-cloud-storage==3.17.0`, plus `google-api-core`, `google-cloud-core`, `google-resumable-media`, `google-crc32c`, `googleapis-common-protos`, `grpcio`, `grpcio-status`, `proto-plus`. `google-auth` and `protobuf` are already pinned (via mlflow). | verified (`pip install --dry-run --report`) |
| F8 | Named query parameters work inside a multi-statement script; a failing `ASSERT` surfaces from `QueryJob.result()` as a `google.api_core.exceptions.GoogleAPICallError` subclass whose structured error message contains the description. The exact class and message layout are not documented, and a **syntax** error can quote a string literal (an assertion label) too. | **unverified**; probed live in L0 (a failing assertion AND a deliberately malformed script) right after the apply. The probe fixes `_ASSERT_MESSAGE_RE` (Interfaces > SQL) and becomes a recorded-message unit test |
| F9 | The scorer today: `score_run_date(config, *, loader, writer)` (`scoring/pipeline.py:73`) calls `writer(predictions, config.output_dir, run_date)` once as the last step (line 135) and returns `ScoringResult(partition_path=<writer's return>)`; `store._validate_predictions` (`scoring/store.py:55`) is the full predictions-frame contract; `bundle_guard._read_manifest` calls `local_bundle_path(uri)` **twice**: line 128 (existence/scheme) and line 164 (I3: the bundle's directory name equals its `bundle_id`); `bundles.store.local_bundle_path` refuses `gs://` ("arrive in v2 slice 4", `bundles/store.py:27`); `scripts/export_bundle.py::_printable_uri` (lines 25-31) resolves the returned URI as a local path. | verified |

## Scope

**In:**
1. `src/readmission_risk/gcp.py`: credentials (ADC, optional impersonation, quota-project handling) and lazily built clients.
2. GCS bundle store: `bundles/gcs.py` (reader + create-only writer) behind `bundles/store.py`; `bundle_guard`'s two `local_bundle_path` calls replaced by store-level helpers; `scripts/export_bundle.py` accepts and prints `gs://`.
3. Gold from GCS: `warehouse/gold_source.py::materialized_gold_dir`, used by both scoring CLIs.
4. `warehouse/` package: staging writer, data-test runner, publish (a DML transaction), `scoring_runs` recorder, the orchestration `score_to_bigquery`, the M5 seeded check, table schemas as JSON (single source for Python and Terraform), SQL files.
5. Two small additive changes to the scorer: `ModelBundle.champion_sha256`, and an `on_validated` callback in `score_run_date`.
6. `scripts/score_to_bigquery.py` (Typer, no logic), `make score-bq`; `scripts/seeded_data_test_check.py` (M5).
7. Terraform: BigQuery API; datasets `serving` and `scratch`; tables `predictions` and `scoring_runs`; custom role `scorerTableWriter`; service account `scorer` with its IAM and WIF binding to `score.yml`; operator impersonation of `scorer`.
8. `.github/workflows/score.yml` (dispatch-only, keyless).
9. `deploy/champion.json` → `gs://` (its own commit).
10. Lockfile: two new direct pins + transitive.
11. Docs: README (one sentence), `docs/scoring.md`, `docs/setup.md`, CLAUDE.md, PRD status lines.
12. Live checks L0–L10, each outward-facing step confirmed by the owner first.

**Out (named follow-ups):**
- the first dispatched `score.yml` run (L-post, after the merge, with V-post);
- scheduled ticks, the simulated clock, the `monitoring` table and GitHub issues (slice 7);
- the run ledger and badge (slice 7);
- gold in BigQuery and the Spark fingerprint (slices 5–6);
- the dashboard SA, its custom query quota and the call-sheet view (slice 8; the view serves the live partition as is: every live row was published by an attempt with `published AND predictions_tests_passed`. Note that such an attempt's status is not necessarily `succeeded`: a history-test failure, or an error after the publish, still publishes. A rule of "rows of `succeeded` runs only" would hide valid live partitions, so slice 8 must not use it);
- a lockfile-drift CI check;
- the `terraform fmt` pre-commit hook;
- the default compute SA's `roles/editor`;
- re-scoring on rollback;
- a `scoring_runs` data test over the current attempt's own row (the next attempt tests it).

## Surfaces touched

| File | Change |
|---|---|
| `src/readmission_risk/gcp.py` | **new** |
| `src/readmission_risk/bundles/gcs.py` | **new** |
| `src/readmission_risk/bundles/store.py` | new `check_bundle_uri`, `bundle_uri_leaf`; `gs://` dispatch in `list_bundle_entries`, `read_bundle_file`, `write_bundle`; `local_bundle_path`'s refusal message changes |
| `src/readmission_risk/scoring/bundle_guard.py` | line 128 → `check_bundle_uri(uri)`; line 164 → `bundle_uri_leaf(uri)`; `validate_bundle` sets `ModelBundle.champion_sha256`. Guards 1–14 and I1–I5 unchanged in what they check. |
| `src/readmission_risk/scoring/store.py` | `_validate_predictions` → public `validate_predictions`; new `predictions_arrow_table(predictions, run_date)` (validation + `pa.Table.from_pandas(..., schema=PREDICTION_SCHEMA)` + the existing ValueError mapping), which `write_partition` now calls |
| `src/readmission_risk/scoring/pipeline.py` | `ScoringResult.partition_path: Path | str`; `score_run_date(..., on_validated=None)` |
| `src/readmission_risk/warehouse/` | **new package**: `__init__.py`, `bigquery.py`, `staging.py`, `scoring_runs.py`, `data_tests.py`, `gold_source.py`, `run.py`, `seeded_check.py`, `schemas/predictions.json`, `schemas/scoring_runs.json`, `sql/predictions.sql`, `sql/scoring_runs.sql` |
| `scripts/score_to_bigquery.py`, `scripts/seeded_data_test_check.py` | **new** |
| `scripts/score_discharges.py` | `--gold-dir` typed `str` and wrapped in `materialized_gold_dir`; `--impersonate-service-account` |
| `scripts/export_bundle.py` | `--bundles-dir` typed `str`; `_printable_uri` returns a `gs://` URI unchanged (relative=True, no warning); `--impersonate-service-account` |
| `src/readmission_risk/models/bundle_export.py` | `bundles_root: Path | str`, passed through |
| `pyproject.toml` | `[tool.setuptools.package-data] readmission_risk = ["warehouse/sql/*.sql", "warehouse/schemas/*.json"]` |
| `requirements.in`, `requirements.txt` | two pins + transitive |
| `infra/apis.tf`, `infra/bigquery.tf` (**new**), `infra/iam.tf`, `infra/outputs.tf` | see Interfaces > Terraform |
| `.github/workflows/score.yml` | **new** |
| `deploy/champion.json` | `bundle_uri` → `gs://…` |
| `Makefile` | `score-bq`, `m5-check`; variables `GOLD_URI`, `SCORER_SA` |
| `tests/conftest.py` | autouse no-GCP guard |
| `tests/bundles/test_gcs.py` (**new**), `tests/bundles/fake_gcs.py` (**new**), `tests/warehouse/` (**new**, incl. `fake_bigquery.py`), `tests/bundles/test_store.py`, `tests/bundles/test_champion.py`, `tests/scoring/helpers.py`, `tests/scoring/test_pipeline.py`, `tests/scoring/test_cli.py`, `tests/models/test_export_cli.py`, `tests/test_infra.py`, `tests/test_tooling.py` | see Verification |
| `README.md` (line 112), `docs/scoring.md`, `docs/setup.md`, `CLAUDE.md`, PRD | docs |

## Interfaces

### `src/readmission_risk/gcp.py`

```python
PROJECT_ID = "synthea-readmission-risk"
LOCATION = "us-central1"
_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)
_SA_EMAIL_RE = r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z0-9-]+\.iam\.gserviceaccount\.com$"

def configure(*, impersonate_service_account: str | None) -> None
    # Process-wide; resets cached clients. ValueError on a bad email. Default state (never called): no impersonation.
def credentials() -> google.auth.credentials.Credentials
    # source = google.auth.default(scopes=_SCOPES)[0].
    # Quota project, by credential TYPE (critic r2 #3): ONLY a google.oauth2.credentials.Credentials (a user's ADC) gets
    #   with_quota_project(PROJECT_ID) when not impersonating (the operator is Owner here; the ADC file's own quota project
    #   is deliberately another project). Every other type -- the external_account credential google-github-actions/auth
    #   writes in CI, a service account -- is used UNCHANGED: setting x-goog-user-project would need
    #   serviceusage.services.use, which the scorer deliberately lacks.
    # Impersonation: impersonated_credentials.Credentials(source_credentials=<a user credential:
    #   source.with_quota_project(None); else source unchanged>, target_principal=sa, target_scopes=_SCOPES,
    #   lifetime=3600) -- mirrors CLOUDSDK_BILLING_QUOTA_PROJECT=LEGACY for the token exchange.
    # Consequence for D1: the laptop runs (impersonated) and CI (external_account, not impersonated) share every line
    #   after credentials(); the CI branch itself is first exercised at L-post, and its unit test covers the type rule.
    # DefaultCredentialsError -> FileNotFoundError("no Application Default Credentials: run `gcloud auth
    #   application-default login`, or score offline with --bundle bundles/<id> and a local --gold-dir").
def storage_client() -> google.cloud.storage.Client     # cached; Client(project=PROJECT_ID, credentials=credentials())
def bigquery_client() -> google.cloud.bigquery.Client   # cached; Client(project=PROJECT_ID, credentials=..., location=LOCATION)
```

`gcp.py` starts with `from __future__ import annotations` and has **no module-level `google` import**; neither do `bundles/gcs.py`, `warehouse/*.py` nor the `tests/conftest.py` guard (CI's `pipeline-dataproc-runtime` job loads `tests/conftest.py` on 3.12 under `requirements-dataproc-3.0.txt`, which has no `google-*` packages; `tests/conftest.py:16-18` states the same rule for mlflow). All `google.*` imports are inside function bodies. **Clients are looked up at call time**: every consumer does `from readmission_risk import gcp` and calls `gcp.storage_client()` / `gcp.bigquery_client()` inside its function body (never binds the function as a default argument or module global), so the test guard's monkeypatch of the module attributes reaches every call. Auth failures that only surface on the first request (`google.auth.exceptions.RefreshError`, `TransportError`; base class `GoogleAuthError`) are mapped where each call is made (below).

### `src/readmission_risk/bundles/gcs.py`

```python
def parse_gs_uri(uri: str) -> tuple[str, str]
    # "gs://bucket/a/b" -> ("bucket", "a/b"); one trailing "/" stripped. ValueError for: an empty bucket or object
    # prefix, an empty, "." or ".." segment, a bucket not matching ^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$.
def list_entries(uri: str) -> list[str]
    # client.bucket(bucket) (never get_bucket: objectCreator/objectViewer lack storage.buckets.get) .list_blobs(
    # prefix=prefix + "/"). Relative names; a deeper object "x/y" contributes "x" (I3's exact-entry check then refuses a
    # bundle with a subdirectory); a zero-byte placeholder whose relative name is "" (console "folder") is skipped.
    # Sorted, de-duplicated. No entry -> FileNotFoundError("bundle not found: no bundle at <uri>").
def read_file(uri: str, name: str) -> bytes
    # Same name validation as the local reader; bucket.blob(f"{prefix}/{name}").download_as_bytes().
    # NotFound -> FileNotFoundError("bundle at <uri> has no <name>").
def write_files(expected: dict[str, bytes], bundles_root: str, bundle_id: str) -> tuple[str, bool]
    # For each non-manifest file, then manifest.json LAST: blob.upload_from_string(data,
    # content_type="application/octet-stream", if_generation_match=0, retry=None).
    # retry=None on purpose: with if_generation_match=0, a retried upload whose first attempt had in fact succeeded would
    # 412 against its own object. A transient error therefore fails the export; re-running reports "already exists" for
    # the objects that landed.
    # PreconditionFailed (412) -> ValueError("bundle <id> already exists (at least <name>) at <uri>; bundles are never
    # overwritten, and this identity cannot read to compare; verify with a reader (docs/setup.md > Bundles in GCS)").
    # A part-way failure leaves non-manifest objects without manifest.json: the reader refuses that bundle (I1). Recovery
    # (docs/setup.md): a reader checks which objects exist; an Owner deletes the partial prefix.
    # Root parsing: bundles_root may be a bare bucket ("gs://synthea-readmission-risk-bundles") or bucket + prefix;
    # bucket, bundle_prefix = parse_gs_uri(f"{bundles_root.rstrip('/')}/{bundle_id}"), so objects are
    # "[<prefix>/]<bundle_id>/<name>".
    # Returns (f"{bundles_root.rstrip('/')}/{bundle_id}", True).
```

Error mapping inside `list_entries`/`read_file`: `NotFound` (object or bucket) → `FileNotFoundError`; `Forbidden`, any other `GoogleAPICallError`, and `GoogleAuthError` → `PermissionError`/`OSError` with the original message. `bundle_guard._store_call` already turns `OSError` into `"<key>: cannot read bundle at <uri>: …"` (a ValueError, so `refused`). In `write_files`, a `Forbidden` or `GoogleAuthError` propagates unchanged (the exporter's own failure).

### `src/readmission_risk/bundles/store.py` changes

- `check_bundle_uri(bundle_uri: str) -> None` (**new**). Empty → ValueError("empty bundle URI"). `gs://` → `parse_gs_uri` (its ValueError propagates). Another scheme → the existing "unsupported bundle URI scheme" ValueError. A local path → `local_bundle_path` must not raise, and FileNotFoundError is left to `list_bundle_entries`.
- `bundle_uri_leaf(bundle_uri: str) -> str` (**new**). The last path segment: for `gs://`, the last segment of `parse_gs_uri`'s prefix; for local, `local_bundle_path(uri).resolve().name` (exactly the current line-164 behaviour).
- `local_bundle_path` keeps its behaviour; only its gs refusal message becomes `"not a local bundle path: <uri>"`.
- `list_bundle_entries` / `read_bundle_file`: dispatch on a `gs://` prefix to `gcs.list_entries` / `gcs.read_file`. The `bundles.gcs` import is inside the branch.
- `write_bundle(files, manifest, bundles_root: Path | str)`: `_expected_bytes` first (unchanged). A `str` root starting with `gs://` goes to `gcs.write_files`; everything else goes to the existing local code (`Path(bundles_root)`).

`bundle_guard._read_manifest` gets two edits: line 128 becomes `_store_call("bundle not found", uri, lambda: check_bundle_uri(uri))`, and line 164 becomes `directory = bundle_uri_leaf(uri)`. Messages and keys are unchanged. `validate_bundle` passes `champion_sha256=expected_manifest_sha256` to `ModelBundle`, a new field `champion_sha256: str | None = None` placed after `model_bytes`. That value is None for `--bundle`; when set, it has already been proven equal to the manifest's sha256 by I2.

### `src/readmission_risk/scoring/pipeline.py` changes

- `score_run_date(config, *, loader=…, writer=…, on_validated: Callable[[ModelBundle, int], None] | None = None)`. It is called exactly once, with `(bundle, window_days)`, right after step 4 (the bundle validated, before the completeness guard), and never on a run refused earlier. Purpose: a refused run after validation (for example L7) can still record the bundle, the fingerprint and the window.
- `ScoringResult.partition_path: Path | str`. The local writer returns a `Path`; the staging writer returns a `bigquery://…` string.
- Nothing else changes; `scoring/` never imports `warehouse`, `gcp` or `google`.

### `src/readmission_risk/warehouse/gold_source.py`

```python
@contextmanager
def materialized_gold_dir(gold_uri: str) -> Iterator[Path]
    # No scheme -> yields Path(gold_uri) untouched (the loader does its own checks).
    # "gs://bucket/prefix" -> list_blobs(prefix=prefix + "/") via client.bucket(). Classification of each object's relative
    #   name: "" (placeholder) skipped; "_SUCCESS" and names matching r"^\..*\.crc$" ignored; "*.parquet" and
    #   "_gold_metadata.json" downloaded; ANYTHING ELSE (including a name containing "/") -> ValueError("unexpected
    #   object <name> under <uri>"). Before downloading: > 1,000 objects or > 512 MiB total -> ValueError. No parquet
    #   object -> FileNotFoundError("No Parquet files found in gold directory: <uri>").
    #   NotFound (bucket) -> FileNotFoundError; Forbidden / other GoogleAPICallError / GoogleAuthError -> ValueError(
    #   "cannot read gold at <uri>: <message>").
    #   Downloads go into tempfile.TemporaryDirectory(prefix="gold-"), removed on exit (also on error).
    # Another scheme -> ValueError.
```

### `src/readmission_risk/warehouse/schemas/*.json` (single source of truth)

These are BigQuery JSON schema arrays (`[{"name", "type", "mode", "description"}]`). Python reads them with `importlib.resources`; Terraform reads them with `file("${path.module}/../src/readmission_risk/warehouse/schemas/<name>.json")`. Each description is a non-empty one-line string written by the implementer; tests require that it is non-empty but do not compare its text.

**`predictions.json`**: `PREDICTION_COLUMNS` in order, then `scoring_run_id` (STRING, REQUIRED).
- Types follow `PREDICTION_SCHEMA`: `date32` → `DATE`, `timestamp[us, UTC]` → `TIMESTAMP`, `int32` → `INTEGER`, `bool` → `BOOLEAN`, `float64` → `FLOAT`, `string` → `STRING`.
- Mode is `NULLABLE` for the two `admission_reason_*` columns and `REQUIRED` otherwise.

**`scoring_runs.json`** has one row per attempt:

| column | type | mode | meaning |
|---|---|---|---|
| `scoring_run_id` | STRING | REQUIRED | `uuid4().hex`, generated first |
| `status` | STRING | REQUIRED | `succeeded` / `data_tests_failed` / `refused` / `failed` (precedence in Flow) |
| `started_at` | TIMESTAMP | REQUIRED | wall clock UTC, taken first |
| `finished_at` | TIMESTAMP | REQUIRED | `started_at + time.monotonic()` elapsed (immune to clock steps, so RUNS-03 cannot fail on a clock change) |
| `run_date` | DATE | NULLABLE | null only when `--run-date` does not parse (then `refused`) |
| `trigger` | STRING | REQUIRED | `laptop` unless `GITHUB_ACTIONS == "true"`, then `GITHUB_EVENT_NAME` |
| `github_run_url` | STRING | NULLABLE | `$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID` in Actions |
| `git_commit` | STRING | NULLABLE | `GITHUB_SHA` in Actions; else `git rev-parse HEAD` (null on failure) |
| `git_dirty` | BOOLEAN | NULLABLE | laptop: `git status --porcelain` non-empty; null in Actions or on failure |
| `python_version` | STRING | REQUIRED | `platform.python_version()` |
| `gold_uri` | STRING | REQUIRED | as given |
| `gold_fingerprint` | STRING | NULLABLE | from `on_validated` |
| `champion_sha256` | STRING | NULLABLE | `bundle.champion_sha256`, from `on_validated` |
| `model_bundle_id` | STRING | NULLABLE | from `on_validated` |
| `model_name` | STRING | NULLABLE | from `on_validated` |
| `predictions_table` | STRING | REQUIRED | `synthea-readmission-risk.serving.predictions` |
| `top_n` | INTEGER | REQUIRED | as requested |
| `window_days` | INTEGER | NULLABLE | from `on_validated` |
| `n_predictions` | INTEGER | NULLABLE | `len(result.scoring.predictions)` (the staging writer's post-condition already proved it equals the load's `output_rows`) |
| `n_top_n` | INTEGER | NULLABLE | `int(result.scoring.predictions["is_top_n"].sum())` |
| `history_tests_passed` | BOOLEAN | NULLABLE | `scoring_runs.sql`; null if not run |
| `predictions_tests_passed` | BOOLEAN | NULLABLE | `predictions.sql` on staging; null if not run |
| `published` | BOOLEAN | REQUIRED | the publish transaction committed |
| `data_test_failure` | STRING | NULLABLE | the first failing assertion, `"<ID>: <message>"` (history first) |
| `error_type` | STRING | NULLABLE | exception class name |
| `error_message` | STRING | NULLABLE | `str(exc)`, truncated to 4,000 characters |

### `src/readmission_risk/warehouse/bigquery.py`

```python
SERVING_DATASET = "serving"
SCRATCH_DATASET = "scratch"
MAX_BYTES_BILLED = 100 * 1024**2      # per query job
def load_schema(name: str) -> list[dict]                          # "predictions" | "scoring_runs"; ValueError otherwise
def to_bq_schema(fields: list[dict]) -> list[bigquery.SchemaField]
def table_id(dataset: str, table: str) -> str
    # f"{PROJECT_ID}.{dataset}.{table}"; dataset and table must match ^[A-Za-z_][A-Za-z0-9_]{0,1023}$, else ValueError
def query_config(**kwargs) -> bigquery.QueryJobConfig
    # THE only QueryJobConfig constructor in src/: maximum_bytes_billed=MAX_BYTES_BILLED, use_legacy_sql=False, **kwargs
```

### `src/readmission_risk/warehouse/staging.py` (write-audit-publish)

```python
def staging_table_id(scoring_run_id: str) -> str      # table_id(SCRATCH_DATASET, f"predictions_{scoring_run_id}")
def make_staging_writer(client, scoring_run_id: str) -> Callable[[pd.DataFrame, Path, date], str]
    # writer(predictions, output_dir, run_date) -- score_run_date's signature; output_dir is ignored.
    #  1. arrow = scoring.store.predictions_arrow_table(predictions, run_date)  # the local writer's full validation, before any call
    #  2. append the column with an explicit pa.field("scoring_run_id", pa.string(), nullable=False) (a default
    #     append_column field is nullable -> Parquet OPTIONAL -> the load into a REQUIRED column fails); Parquet (snappy)
    #     into io.BytesIO
    #  3. client.create_table(Table(staging_id, schema=to_bq_schema(load_schema("predictions"))) with
    #     time_partitioning=DAY on as_of_date; no clustering, no require_partition_filter (staging only needs a decorator
    #     target; the publish is DML with an explicit column list, so its spec need not match serving.predictions); the
    #     scratch dataset's 1-day default expiration applies. exists_ok=False.
    #  4. client.load_table_from_file(buf, f"{staging_id}${run_date:%Y%m%d}", rewind=True, job_config=LoadJobConfig(
    #     source_format=PARQUET, write_disposition=WRITE_TRUNCATE, create_disposition=CREATE_NEVER)).result()
    #  5. job.output_rows != len(predictions) -> RuntimeError
    #  returns f"bigquery://{staging_id}${run_date:%Y%m%d}"
def publish(client, scoring_run_id: str, run_date: date) -> None
    # One script job via query_config(query_parameters=[run_date DATE]):
    #   BEGIN TRANSACTION;
    #   DELETE FROM `<serving.predictions>` WHERE as_of_date = @run_date;
    #   INSERT INTO `<serving.predictions>` (<all 19 columns, from predictions.json, in order, EACH back-quoted>)
    #     SELECT <same back-quoted columns> FROM `<staging>` WHERE as_of_date = @run_date;
    #   COMMIT TRANSACTION;
    # .result() with NO client timeout (critic r2 #6). Bounded by the workflow's timeout-minutes / Ctrl-C.
    # The job is created with job_id = f"publish_{scoring_run_id}" (critic r5 #1), so its outcome can be fetched later.
def resolve_job(client, job_id: str, *, poll_seconds: float = 2.0, max_wait_seconds: float = 300.0,
                sleep=time.sleep, monotonic=time.monotonic) -> bool | None
    # NotFound from get_job -> False IMMEDIATELY (critic r6 #4): the job id is caller-chosen and nothing re-inserts it,
    # so NotFound means the job was never created (e.g. jobs.insert refused) -- not 'unknown', and no 5-minute stall.
    # Critic r4 #1 / r5 #1, #3: QueryJob.result() can raise CLIENT-side (a polling transport error, a token RefreshError, a
    # garbled response) while the job is still running or after it finished. This decides what really happened:
    # loop: job = client.get_job(job_id) (location from the client); state "DONE" -> return job.error_result is None;
    # otherwise sleep poll_seconds; give up after max_wait_seconds -> None. A get_job exception is retried within the same
    # wait budget; if every attempt failed -> None. Pure w.r.t. the run record; time.sleep / time.monotonic injectable.
def publish_landed(client, scoring_run_id: str, run_date: date) -> bool | None
    # Used ONLY when resolve_job returned None (the job could not be observed): SELECT COUNT(*) FROM
    # `<serving.predictions>` WHERE as_of_date = @run_date AND `scoring_run_id` = @scoring_run_id (query_config).
    # > 0 -> True (the transaction is all-or-nothing, and nothing else writes this id); 0 -> None, NOT False: a
    # not-yet-committed transaction also counts 0. A raising check -> None.
def drop_staging(client, scoring_run_id: str) -> None   # client.delete_table(staging_id, not_found_ok=True)
```

**Lint, BLE001 (critic r7 #3):** the four deliberate catch-alls carry `# noqa: BLE001 -- <reason>`: `score_to_bigquery`'s handler ("the run must be recorded whatever failed; the exception is kept and re-raised by the CLI"), `drop_staging` in the `finally` ("best effort; the 1-day expiration is the backstop"), the `get_job` retry in `resolve_job` ("any error is retried within the wait budget"), and the `publish_landed` count check ("a failed check means unknown, not an error"). No other blind `except`. **Lint (critic r6 #2):** ruff's `S608` (SQL built from strings) fires on every Python-built statement. Each such line carries `# noqa: S608 -- identifiers validated by table_id()/_TABLE_RE and back-quoted; values are query parameters`, per line and with the reason (the project's documented-ignore style), not a per-file ignore. **Generated SQL rule:** every column name in SQL built by Python (publish, `append_run`, `publish_landed`, `seeded_check`) is back-quoted, so a reserved word such as `partition` never breaks a statement; tests assert the quoted form. No BigQuery `.result()` in this package passes a client-side timeout (load, query, DML); the same reasoning applies to the `scoring_runs` insert.

### SQL: `src/readmission_risk/warehouse/sql/*.sql` and `data_tests.py`

**Shape rule** (keeps billing to one table read per script, and makes the NULL rule mechanical):
- A script is one `DECLARE c STRUCT<…> DEFAULT (SELECT AS STRUCT … FROM <table> WHERE …);`, the only statement that reads a table, followed only by `ASSERT <expression over c and parameters> AS '<ID>: <message>';` statements.
- Every row-level check inside the `SELECT` is written as `COUNTIF((<condition that must hold>) IS NOT TRUE) AS bad_<x>`. That counts a row where the condition is FALSE **or NULL**, so NULL counts as a failure inside aggregates too (F1). The matching assertion is `ASSERT c.bad_<x> = 0`.
- Aggregate facts that are NULL on an empty table (`MIN`, `MAX`) are asserted directly, so a NULL fails the top-level `ASSERT` (F1).
- The reserved word `partition` is always back-quoted (`` `partition` ``). The renderer back-quotes table ids.
- IDs are `PRED-NN` / `RUNS-NN`, unique. A script stops at its first failing `ASSERT`, so order is meaningful.

`predictions.sql`: `{table}` is the staging table (or an M5 copy); parameters `@run_date DATE`, `@scoring_run_id STRING`; it reads `WHERE as_of_date = @run_date`.

| ID | Assertion |
|---|---|
| PRED-01 | `c.n_rows >= 1` |
| PRED-02 | `c.min_rank = 1 AND c.max_rank = c.n_rows AND c.n_distinct_rank = c.n_rows` |
| PRED-03 | `c.n_distinct_encounter = c.n_rows` |
| PRED-04 | `c.bad_risk_score = 0` with `COUNTIF((risk_score BETWEEN 0 AND 1) IS NOT TRUE)` (NaN and NULL both counted) |
| PRED-05 | `c.bad_top_n_flag = 0` with `COUNTIF((is_top_n = (rank <= top_n)) IS NOT TRUE)` |
| PRED-06 | `c.n_distinct_bundle = 1 AND c.n_distinct_top_n = 1 AND c.n_distinct_window = 1 AND c.n_distinct_fingerprint = 1 AND c.n_distinct_label_definition = 1 AND c.n_distinct_reference_date = 1` |
| PRED-07 | `c.bad_order = 0` with a subquery `COUNTIF((prev IS NULL OR risk_score <= prev) IS NOT TRUE)` over `LAG(risk_score) OVER (ORDER BY rank) AS prev` (row 1 has no predecessor; this keeps the exact `COUNTIF((…) IS NOT TRUE)` form the static test requires) |
| PRED-08 | `c.bad_partition = 0` with `` COUNTIF((`partition` = 'test') IS NOT TRUE) `` |
| PRED-09 | `c.bad_window = 0` with `COUNTIF((discharge_time >= TIMESTAMP(DATE_SUB(@run_date, INTERVAL scoring_window_days - 1 DAY)) AND discharge_time < TIMESTAMP(DATE_ADD(@run_date, INTERVAL 1 DAY))) IS NOT TRUE)` (the `window_bounds` rule) |
| PRED-10 | `c.bad_run_id = 0` with `COUNTIF((scoring_run_id = @scoring_run_id) IS NOT TRUE)` |

`scoring_runs.sql`: `{table}` is `serving.scoring_runs` (or an M5 copy). It takes no parameters and reads `WHERE started_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 400 DAY)`, which also satisfies `require_partition_filter`. An empty table passes every assertion, because every assertion is a COUNTIF count compared with 0.

| ID | Assertion (`bad_* = 0`, each a `COUNTIF((…) IS NOT TRUE)` or a duplicate count) |
|---|---|
| RUNS-01 | `COUNT(*) - COUNT(DISTINCT scoring_run_id) = 0` |
| RUNS-02 | `status IN ('succeeded','data_tests_failed','refused','failed')` |
| RUNS-03 | `finished_at >= started_at` |
| RUNS-04 | `status != 'succeeded' OR (published AND history_tests_passed AND predictions_tests_passed AND n_predictions > 0 AND model_bundle_id IS NOT NULL)` |
| RUNS-05 | `status = 'succeeded' OR error_message IS NOT NULL OR data_test_failure IS NOT NULL` |
| RUNS-06 | `NOT published OR predictions_tests_passed` (nothing is ever published untested; a `failed` row with `published = TRUE`, from an error after the publish, is legal) |
| RUNS-07 | `status != 'refused' OR NOT published` (a refusal happens before any write) |

```python
@dataclass(frozen=True)
class DataTestResult:
    script: str
    passed: bool
    failure: str | None        # "<ID>: <message>"
_TABLE_RE = re.compile(r"synthea-readmission-risk\.[A-Za-z_][A-Za-z0-9_]{0,1023}\.[A-Za-z_][A-Za-z0-9_]{0,1023}")
def assertion_descriptions(name: str) -> dict[str, str]
    # Parsed from the packaged .sql: {"PRED-01": "PRED-01: <message>", ...}, i.e. every AS '<...>' literal, by ID.
def render_script(name: str, *, table: str) -> str
    # name in {"predictions", "scoring_runs"} (ValueError otherwise); table must _TABLE_RE.fullmatch (ValueError
    # otherwise), and is inserted back-quoted.
def classify_error(exc: BaseException, name: str) -> DataTestResult | None
    # The verdict rule below as a pure function: a DataTestResult(passed=False, ...) for an assertion failure of script
    # `name`, else None. run_data_tests, the L0 probe and the recorded-message unit tests all call it.
def run_data_tests(client, name: str, *, table: str, run_date: date | None = None,
                   scoring_run_id: str | None = None) -> DataTestResult
    # predictions requires both parameters (ValueError otherwise); scoring_runs accepts neither.
    # client.query(render_script(...), job_config=query_config(query_parameters=[...])).result()
    # Verdict rule (critic r2 #5): a GoogleAPICallError is a data-test FAILURE only if one of its structured messages
    # (exc.errors[i]["message"], else str(exc)) FULLY matches _ASSERT_MESSAGE_RE AND the captured description is EXACTLY
    # one of assertion_descriptions(name)'s values -> passed=False, failure=<that description>. Everything else --
    # including a syntax error that merely QUOTES a label literal -- propagates (-> `failed`).
_ASSERT_MESSAGE_RE  # module constant, fixed from L0's recorded message. Placeholder until L0:
                    #   r"(?:Query error: )?(?P<description>(?:PRED|RUNS)-\d{2}: [^\n]*?)(?: at \[\d+:\d+\])?"
                    # L0 records the real message of a failing ASSERT and of a malformed script; the constant is set so
                    # the first fully matches and the second does not, and both recorded strings become unit-test inputs.
```

### `src/readmission_risk/warehouse/scoring_runs.py`

```python
@dataclass
class RunRecord: <one attribute per scoring_runs column, same names and order; datetimes tz-aware UTC; run_date date|None>
def record_parameters(record: RunRecord) -> list[bigquery.ScalarQueryParameter]
    # One typed parameter per column, named like it, type from scoring_runs.json (STRING/TIMESTAMP/DATE/BOOL/INT64);
    # None passed as a typed NULL.
def append_run(client, record: RunRecord) -> None
    # A DML insert, not a load job (critic r2 #4: loads need dataset-level tables.create on serving, which D6 withholds):
    # client.query("INSERT INTO `<serving.scoring_runs>` (<26 columns, each back-quoted>) VALUES (@<col>, ...)",
    #   job_config=query_config(query_parameters=record_parameters(record))).result()   # no client timeout
    # job.num_dml_affected_rows != 1 -> RuntimeError. The job is created with job_id = f"record_{scoring_run_id}".
    # On a client-side exception from .result(): staging.resolve_job(client, f"record_{id}") -> True means the row
    # exists (recorded=True, no WARNING, no exit 4); False or None -> recorded=False as below (None adds "outcome
    # unknown" to the WARNING). No count fallback here: the scorer can read scoring_runs, but a None means the job could
    # not even be observed, and the WARNING tells the operator to check.
```

### `src/readmission_risk/warehouse/run.py` (the orchestration)

```python
@dataclass(frozen=True)
class BigQueryRunRequest:
    gold_uri: str
    run_date: str                       # "YYYYMMDD" as given; parsed in Flow step 2b (after the client is built), never at construction
    bundle_uri: str | None = None
    champion_path: Path | None = None
    top_n: int = DEFAULT_TOP_N
    window_days: int | None = None

@dataclass(frozen=True, eq=False)
class BigQueryRunResult:
    record: RunRecord                   # what was (or was attempted to be) appended
    scoring: ScoringResult | None       # None unless scoring returned
    data_tests: tuple[DataTestResult, ...]
    error: BaseException | None         # the refusal/failure, or the append error; None on success
    recorded: bool                      # the scoring_runs append succeeded

def score_to_bigquery(request: BigQueryRunRequest, *, client_factory=None,
                      score=score_run_date) -> BigQueryRunResult
    # client_factory None -> resolved INSIDE the call to gcp.bigquery_client (lazy, see gcp.py)
```

It never raises an `Exception`. A `BaseException` that is not an `Exception` (KeyboardInterrupt, SystemExit) is caught only to set a local `interrupted = True` and re-raised; the `finally` then still drops staging (best effort) but **skips** setting the status and the `scoring_runs` insert, so the interruption propagates unrecorded. Flow:
1. `scoring_run_id`, `started_at`, the monotonic start, and a `RunRecord` pre-filled with id, trigger, URL, git, python, `gold_uri`, `predictions_table`, `top_n`, `published=False`.
2. Inside `try`:
   - a. `client = client_factory()` FIRST, so even a bad `--run-date` can be recorded (critic r3 #1). If this raises, the run is `refused` (FileNotFoundError: no ADC) or `failed`, and it cannot be recorded (`recorded=False`).
   - b. `run_date = parse_yyyymmdd(request.run_date, "run_date")` (ValueError → `refused`, `run_date` null, no tests run; the row IS recorded).
   - c. `history = run_data_tests(client, "scoring_runs", table=serving.scoring_runs)`. This is the state **before** this attempt; it runs on every attempt; a failure does not stop scoring.
   - d. `with materialized_gold_dir(request.gold_uri) as gold_dir:` build `ScoringConfig(gold_dir=gold_dir, run_date=request.run_date, output_dir=Path("unused-by-bigquery"), bundle_uri=…, champion_path=…, top_n=…, window_days=…, show_observed_outcomes=False)` and call `score(config, writer=make_staging_writer(client, id), on_validated=<fills gold_fingerprint, champion_sha256, model_bundle_id, model_name, window_days>)`.
   - e. `preds = run_data_tests(client, "predictions", table=staging, run_date=run_date, scoring_run_id=id)`.
   - f. If `preds.passed`: set the local flag `publish_attempted = True`, then `publish(client, id, run_date)`; on success `published=True`. If `publish` raises: `outcome = resolve_job(client, f"publish_{id}")`, and if that is None, `outcome = publish_landed(client, id, run_date)`. `True` → `published=True`; `False` → `published=False`; `None` → `published=False` plus the WARNING "publish outcome unknown; check serving.predictions for scoring_run_id <id>", printed to stderr at once and kept as `publish_warning`. Step 3 then sets `error_message = str(exc) + "\n" + publish_warning` (the warning only when present), and the 4,000-character truncation applies to the combined string. The error then propagates to step 3. Staging is dropped only in the `finally`, i.e. after this resolution, so a still-running transaction is never deprived of its source. The history result does not gate publishing: it concerns the audit table, not these rows.
   - g. Status: `succeeded` iff history and predictions both passed and published; otherwise `data_tests_failed`. `data_test_failure` is the history failure if any, else the predictions failure.
3. **Once `publish_attempted` is True, any `Exception` → `failed`, whatever its type** (critic r5 #2: a `ValueError` subclass such as `requests.exceptions.JSONDecodeError` can come out of the publish path; `refused` must never co-occur with a publish). Before that, the order of the checks is: (i) an exception that is an instance of `google.api_core.exceptions.GoogleAPIError`, `google.auth.exceptions.GoogleAuthError` or `requests.exceptions.RequestException` → `failed`, even when it is also a `ValueError` subclass (critic r7 #5; those types are imported inside the handler, never at module level). The gold and bundle readers' *own* mapped ValueErrors ("cannot read gold …", "cannot read bundle …") are plain ValueErrors and still count as refusals. (ii) `ValueError`/`FileNotFoundError` → `refused`. (iii) any other `Exception` → `failed`. Either way the exception is stored in `error`, with `error_type` and `error_message` filled. An exception raised after a successful publish (none is expected) still yields `failed`, with `published=True` recorded truthfully.
4. `finally`:
   - `drop_staging` is best effort (exceptions are printed as a WARNING; the scratch dataset's 1-day expiration is the backstop).
   - Unless interrupted: `finished_at` is set, then `append_run(client, record)` runs if a client exists (else it is skipped and `recorded=False`).
   - Insert outcome (readiness r6 #2): `append_run` returns None or raises. A `RuntimeError` from its own affected-rows post-condition → `recorded=False` directly (the job ran; it did not insert exactly one row). Any other exception → `outcome = resolve_job(client, f"record_{id}")`: True → `recorded=True`, nothing printed; False → `recorded=False`; None → `recorded=False` with "outcome unknown" added to the WARNING.
   - On `recorded=False`: the WARNING `the run was not recorded in scoring_runs: <error>` is printed to stderr. If the status is `succeeded`, `error` becomes the insert error; for any other status the run's own `error` (None for `data_tests_failed`) is kept. The insert failure never changes the status.

### `scripts/score_to_bigquery.py`

**Options:**
- `--gold-dir` (str, required);
- `--run-date` (str, required);
- `--bundle` (str);
- `--champion` (Path);
- `--top-n`;
- `--window-days`;
- `--impersonate-service-account` (str);
- `--verbose`.

**What it does:**
- Calls `gcp.configure(...)`, then `score_to_bigquery`. A `ValueError` from `gcp.configure` (bad `--impersonate-service-account` email) is printed as `ERROR: <message>` and exits 1 **unrecorded** (no client exists yet). The same holds in `score_discharges.py`, `export_bundle.py` and `seeded_data_test_check.py`: each calls `gcp.configure` inside its existing `except (FileNotFoundError, ValueError)` → `ERROR:` / exit 1 path.
- On a scoring result, prints `format_report(dataclasses.replace(result.scoring, partition_path=<where>))`, where `<where>` is `bigquery://synthea-readmission-risk.serving.predictions (as_of_date = YYYY-MM-DD)` if published, else `nothing (not published; the staging table was dropped)`; the staging table's name is never printed as a destination (critic r2 #10, readiness r2 #8). `format_report` itself is unchanged.
- Always prints `Scoring run <id>: <status> (published: yes|no; recorded: yes|no)`.
- When `GITHUB_STEP_SUMMARY` is set, it appends:

```
## Scoring run <status>    (the raw enum value: succeeded / data_tests_failed / refused / failed)

| field | value |
|---|---|
| run date | YYYY-MM-DD, or "-" when --run-date did not parse |
| scoring run id | <id> |
| bundle | <first 12 of bundle id or "-"> |
| rows / Top-N | <n_predictions or "-"> / <n_top_n or "-"> |
| data tests | history <pass/fail/not run>, predictions <pass/fail/not run> |
| published | yes/no |
| recorded | yes/no |
| failure | <data_test_failure or error_type: first line of error_message, or "-"> |
```

**Exit codes:**

| Code | When | Output |
|---|---|---|
| 0 | `succeeded` and recorded | — |
| 1 | `refused` | `ERROR: <message>` on stderr; traceback with `--verbose` |
| 3 | `data_tests_failed` | `DATA TESTS FAILED: <failure>` on stderr |
| 4 | `succeeded` but not recorded | `ERROR: not recorded: …` |
| — | `failed` | the CLI re-raises `result.error`, giving a traceback and exit 1 |

Precedence: the status decides the code (1 / 3 / traceback); `recorded=False` changes it only for `succeeded` (→ 4). A non-recorded run of any other status keeps its code; the WARNING line from `score_to_bigquery` is the only extra output. The run line and the job summary are printed/written in every case except an interruption, before the exit or re-raise.

### `scripts/seeded_data_test_check.py` and `warehouse/seeded_check.py` (M5: every assertion is shown to fire)

Critic r6 #3: a data test that is only ever seen to pass proves nothing. M5 therefore drives **every** assertion ID to fail, each on its own fresh scratch copy, with one targeted mutation chosen so that every EARLIER assertion of the same script still passes (a script stops at its first failure).

```python
PREDICTION_MUTATIONS: dict[str, str]   # ID -> DML applied to a fresh copy (all rows have as_of_date = @run_date)
RUN_MUTATIONS: dict[str, str]          # ID -> DML applied to a fresh copy
class SeededCheckError(RuntimeError):
    results: dict[str, dict[str, DataTestResult]]   # what was gathered before the failure
def seeded_check(client, *, run_date: date) -> dict[str, dict[str, DataTestResult]]
    # Returns {"predictions": {"clean": r, "PRED-01": r, ..., "PRED-10": r},
    #          "scoring_runs": {"clean": r, "RUNS-01": r, ..., "RUNS-07": r}}.
    # Raises SeededCheckError (carrying the partial results) if a clean copy fails, a mutated copy passes, or fails with
    # another ID; also if the preconditions below do not hold.
```

Copies:
- Copy names (critic r7 #1): an assertion ID maps to a table-name token by lowercasing and replacing `-` with `_` (`PRED-01` → `pred_01`; the clean copies use `clean`), so names are `m5p_<token>_<hex8>` / `m5r_<token>_<hex8>` and pass `table_id()` and `_TABLE_RE`.
- predictions: `CREATE TABLE scratch.m5p_<token>_<hex8> PARTITION BY as_of_date AS SELECT * FROM serving.predictions WHERE as_of_date = @run_date` (a query: only `getData` on the source). Preconditions on the clean copy: exactly one distinct `scoring_run_id` (it is passed as `@scoring_run_id`), ≥ 2 rows, and rank 2's `risk_score` > 0. Otherwise: "publish a run for this date first".
- scoring_runs: `CREATE TABLE scratch.m5r_<token>_<hex8> AS SELECT * FROM serving.scoring_runs WHERE started_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 400 DAY)`. Precondition: at least one `succeeded` row (call its newest one N), else "run the scorer first".

The clean copy of each table must pass. Then each mutated copy must fail with exactly its own ID. All column names are back-quoted, and `{t}` is the copy:

| ID | Mutation (one statement) | Why earlier assertions still pass |
|---|---|---|
| PRED-01 | `DELETE FROM {t} WHERE as_of_date = @run_date` | first assertion |
| PRED-02 | `UPDATE {t} SET rank = rank + 1 WHERE rank = (SELECT MAX(rank) FROM {t} WHERE as_of_date = @run_date) AND as_of_date = @run_date` | n ≥ 1 |
| PRED-03 | `UPDATE {t} SET encounter_id = (SELECT encounter_id FROM {t} WHERE rank = 1 AND as_of_date = @run_date) WHERE rank = 2 AND as_of_date = @run_date` | ranks unchanged |
| PRED-04 | `UPDATE {t} SET risk_score = 1.5 WHERE rank = <max> …` | ranks, ids unchanged |
| PRED-05 | `UPDATE {t} SET is_top_n = NOT is_top_n WHERE rank = 1 …` | score unchanged |
| PRED-06 | `UPDATE {t} SET model_bundle_id = CONCAT(model_bundle_id, '-m5') WHERE rank = <max> …` | flags unchanged |
| PRED-07 | `UPDATE {t} SET risk_score = 0 WHERE rank = 1 …` (rank 2's score > 0, precondition) | 0 is in [0, 1]; constants unchanged |
| PRED-08 | ``UPDATE {t} SET `partition` = 'train' WHERE rank = <max> …`` | order unchanged |
| PRED-09 | `UPDATE {t} SET discharge_time = TIMESTAMP_ADD(TIMESTAMP(DATE_ADD(@run_date, INTERVAL 1 DAY)), INTERVAL 1 HOUR) WHERE rank = <max> …` | other columns unchanged |
| PRED-10 | `UPDATE {t} SET scoring_run_id = 'm5-other' WHERE rank = <max> …` | other columns unchanged |
| RUNS-01 | `INSERT INTO {t} SELECT * FROM {t} WHERE scoring_run_id = N.id` (a duplicate id) | first assertion |
| RUNS-02 | insert N with a fresh id and `status = 'bogus'` | ids unique |
| RUNS-03 | insert N with a fresh id and `finished_at = TIMESTAMP_SUB(started_at, INTERVAL 1 SECOND)` | status valid |
| RUNS-04 | insert N with a fresh id and `published = FALSE` (still `succeeded`) | times valid |
| RUNS-05 | insert N with a fresh id, `status = 'failed'`, `published = FALSE`, `error_message = NULL`, `data_test_failure = NULL` | not succeeded, so RUNS-04 does not apply |
| RUNS-06 | insert N with a fresh id, `status = 'failed'`, `published = TRUE`, `predictions_tests_passed = FALSE`, `error_message = 'm5'` | RUNS-05 satisfied by the error message |
| RUNS-07 | insert N with a fresh id, `status = 'refused'`, `published = TRUE`, `predictions_tests_passed = TRUE`, `error_message = 'm5'` | RUNS-06 satisfied |

`<max>` is always the subquery `(SELECT MAX(rank) FROM {t} WHERE as_of_date = @run_date)` (as in PRED-02), and `…` always stands for `AND as_of_date = @run_date` (it also satisfies a partition filter). `@run_date` is the only parameter of a mutation. "Insert N with …" is a single `INSERT INTO {t} SELECT * REPLACE (<fresh id> AS scoring_run_id, …) FROM {t} WHERE scoring_run_id = N.id`.

Cost and cleanup:
- 2 clean + 17 mutated copies; each needs one CTAS, at most one DML statement and one script run. That is about 55 queries at roughly 10–20 MB billed each, about 1 GB in total, inside the free tier and under the per-query cap.
- Every copy is dropped right after its check. A `finally` drops any copy that is left over, and the 1-day expiration is the backstop.

`scripts/seeded_data_test_check.py` takes:
- `--run-date` (required, YYYYMMDD);
- `--impersonate-service-account` (str);
- `--verbose`.

`make m5-check RUN_DATE=…` runs it with `--impersonate-service-account $(SCORER_SA)`, so L8 exercises the scorer's real access, which is limited to `scratch`. It prints one line per result in the order above, then the verdict:

```
predictions   clean  : pass
predictions   PRED-01: fail PRED-01: <message>
...
scoring_runs  RUNS-07: fail RUNS-07: <message>
M5 OK: all 17 assertions fired on their seeded bad row, and both clean copies passed
```

Exit codes:
- exit 0 only on "M5 OK";
- on `SeededCheckError`, the lines for the results gathered so far are printed, then `ERROR: <message>`, and exit 1.

### Terraform

- **`apis.tf`:** add `bigquery.googleapis.com`. Cloud Resource Manager is enabled now; the file's bootstrap comment gains "(first apply only)".
- **Every new resource below** carries `depends_on = [google_project_service.enabled]`, like every existing resource in `infra/` (the BigQuery API is enabled in the same apply). A new test, `test_every_resource_depends_on_enabled_apis`, asserts this for every `resource` block in `infra/*.tf` except `google_project_service.enabled` itself (the existing budgets already carry it too, `infra/budgets.tf:52,82`).
- **`bigquery.tf`** (new):
  - **`google_bigquery_dataset.serving`:** `dataset_id = "serving"`, `location = var.region`, `delete_contents_on_destroy = false`, labels `purpose = "serving"`, no `access` blocks (authoritative; they would fight `*_iam_member`).
    - `description`: `"Demo predictions and scoring telemetry from synthetic Synthea data; not for clinical use."`
  - **`google_bigquery_dataset.scratch`:** `dataset_id = "scratch"`, `default_table_expiration_ms = 86400000`, `delete_contents_on_destroy = true`, labels `purpose = "scratch"`.
    - `description`: `"Short-lived staging and data-test tables (expire after 1 day)."`
  - **`google_bigquery_table.predictions`:** `dataset_id = serving`, `table_id = "predictions"`, `schema = file(<predictions.json>)`, `time_partitioning { type = "DAY", field = "as_of_date" }`, `require_partition_filter = true`, `clustering = ["model_bundle_id"]`, `deletion_protection = true`, labels `prediction_schema_version = "2"`.
    - `description`: `DEMO_NOTICE`, hard-coded as `chomp(<<-EOT … EOT)` on ONE line inside the heredoc. `tests/test_infra.py` extracts the heredoc body, strips it, and requires equality with `scoring.constants.DEMO_NOTICE`.
  - **`google_bigquery_table.scoring_runs`:** `schema = file(<scoring_runs.json>)`, `time_partitioning { type = "DAY", field = "started_at" }`, `require_partition_filter = true`, `clustering = ["status"]`, `deletion_protection = true`.
  - **`google_project_iam_custom_role.scorer_table_writer`:** `role_id = "scorerTableWriter"`, `title = "Scorer table writer (no create/delete)"`, `permissions = ["bigquery.tables.get", "bigquery.tables.getData", "bigquery.tables.updateData"]` (no `tables.update`: it can set an expiration; no `tables.create`/`delete`). If a live DML statement needs another *table-level* permission, it is added only with the owner's confirmation and recorded; a need for any *dataset-level* permission on `serving` stops the slice for an owner decision (it would break D6).
- **`iam.tf`** additions, additive only:
  - **The account itself:** `google_service_account.scorer`, `account_id = "scorer"`.
  - **Bucket reads:** `google_storage_bucket_iam_member.scorer_reads_bundles` and `.scorer_reads_gold`, both `roles/storage.objectViewer` (`objects.get` and `list`). Unlike gha-smoke, the scorer must download objects.
  - **`serving` tables:** `google_bigquery_table_iam_member.scorer_writes_predictions` and `.scorer_writes_scoring_runs`, role `google_project_iam_custom_role.scorer_table_writer.id`.
  - **`scratch` dataset:** `google_bigquery_dataset_iam_member.scorer_edits_scratch`, `roles/bigquery.dataEditor`.
  - **Jobs:** `google_project_iam_member.scorer_runs_jobs`, `roles/bigquery.jobUser`.
  - **WIF:** `local.score_workflow_ref = "${var.github_repository}/.github/workflows/score.yml@refs/heads/main"` plus `local.score_principal_set`; `google_service_account_iam_member.scorer_wif` grants `roles/iam.workloadIdentityUser`.
  - **Operator:** `google_service_account_iam_member.operator_impersonates_scorer` grants `roles/iam.serviceAccountTokenCreator` to `var.operator_member`.
- **`outputs.tf`:** `scorer_service_account_email`, `bigquery_datasets` = `{ serving = google_bigquery_dataset.serving.dataset_id, scratch = google_bigquery_dataset.scratch.dataset_id }` (bare dataset ids).
- **Expected plan: 15 to add, 0 to change, 0 to destroy, 0 to import.**
  - Resources: API 1, datasets 2, tables 2, custom role 1, SA 1, bucket IAM 2, table IAM 2, dataset IAM 1, project IAM 1, SA IAM 2.
  - The implementation recounts from the final config before L1; any difference is explained in Results.
  - `tests/test_infra.py::test_iam_is_additive_only`'s `len(_iam_members()) == 4` becomes the new count of `*_iam_member` resources: 4 + 8 = **12**.

### `.github/workflows/score.yml`

```yaml
name: Score to BigQuery

# Keyless batch scoring (v2 slice 4): ... (header comment like smoke.yml's)
on:
  workflow_dispatch:
    inputs:
      run_date:
        description: "YYYYMMDD, the last day of the discharge window"
        required: true
        type: string

permissions: {}

concurrency:
  group: score
  cancel-in-progress: false

jobs:
  score:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: actions/checkout@<the smoke.yml SHA>  # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-python@<the ci.yml SHA>  # v7.0.0
        with:
          python-version: "3.14"
          # No pip cache in an id-token job (readiness r6 #8): zizmor's cache-poisoning audit, and a cache restored from
          # another ref must never feed a job holding cloud credentials. Hash-checked installs either way.
      - name: Install
        run: |
          python -m pip install -e .
          python -m pip install --require-hashes -r requirements.txt -r requirements-linux-extras.txt
      - uses: google-github-actions/auth@<the smoke.yml SHA>  # v3.0.0
        with:
          workload_identity_provider: <the smoke.yml value>
          service_account: scorer@synthea-readmission-risk.iam.gserviceaccount.com
      - name: Score
        env:
          RUN_DATE: ${{ inputs.run_date }}
        run: |
          python scripts/score_to_bigquery.py --gold-dir gs://synthea-readmission-risk-gold/full_run_2b --run-date "$RUN_DATE"
```

Notes:
- Block-style job permissions: `tests/test_infra.py:229-237` rejects a flow-style `id-token`.
- The input reaches the shell only through `env` (zizmor template injection).
- No Java: the scorer imports no pyspark.
- **`concurrency`:** at most one run executes. A newer dispatch replaces an older *pending* one, which is cancelled; laptop runs are not serialised at all. Overlapping runs are nevertheless safe: each tests its own staging rows (PRED-10), and the live partition is last-publish-wins (v1 semantics).

### `deploy/champion.json`

`champion_json("gs://synthea-readmission-risk-bundles/75a1448729ef561c596f5e850f5bedce50ed63355ca2de38dc724c635d24eef4", "b61388f0c564c5db4b9a53affcd8b4ab65c513b31eeb1d9b3fe2a2401422bf5b")`. It is committed on its own after L4a (the run with an explicit `--bundle gs://…`) succeeded; subject "Promote the v1 logistic-regression bundle from GCS". L4b then runs with the repointed champion.

### Makefile

- New variables: `GOLD_URI ?= gs://synthea-readmission-risk-gold/full_run_2b` and `SCORER_SA ?= scorer@synthea-readmission-risk.iam.gserviceaccount.com`.
- The `help` target's regex (`Makefile:42`, `grep -E '^[a-z-]+:.*## '`) becomes `'^[a-z0-9-]+:.*## '`, otherwise `m5-check` (a digit) is not listed and `test_make_help_lists_every_target` fails (critic r6 #1). The awk field width stays.
- `score-bq` and `m5-check` are added to `.PHONY`.
- New target `m5-check: ## Prove the data tests refuse a seeded bad row (M5) as the scorer SA` runs `$(BIN)/python scripts/seeded_data_test_check.py --run-date $(RUN_DATE) --impersonate-service-account $(SCORER_SA)`.
- New target `score-bq: ## Score RUN_DATE into BigQuery as the scorer SA (laptop; needs ADC)` runs `$(BIN)/python scripts/score_to_bigquery.py --gold-dir $(GOLD_URI) --run-date $(RUN_DATE) --impersonate-service-account $(SCORER_SA) $(SCORE_ARGS)`.

### Lockfile

- `requirements.in` gains `google-cloud-bigquery==3.46.1` and `google-cloud-storage==3.17.0`, each the newest release at least 7 days old on PyPI (the Dependabot cooldown); if a release is younger, use the newest older one. A comment states the rule.
- Recompile in the 3.12 lock venv with exactly `pip-compile --generate-hashes --output-file=requirements.txt requirements.in`. Do not add `--upgrade` and do not use `make lock-runtime`, which passes `--upgrade`.
- Accepted diff: the two pins, F7's eight transitive packages, and `# via` lines. Any other version change → stop and ask.
- Then `pip install --require-hashes` and `pip check` on 3.14.
- `requirements-linux-extras.txt` is re-examined. pip-compile hashes every wheel on PyPI, so only `platform_system`-conditional dependencies could be missing; the new packages' metadata is checked for such markers. CI's install is the proof.

## UX flow

```
laptop (make score-bq) / score.yml
  run_date parsed; client built
  scoring_runs.sql on history ─────────────────────── data test 1 (pre-state; never gates publishing)
  gold: gs://…gold/full_run_2b → tmp dir → load_gold_table_with_metadata (unchanged)
  champion.json → gs:// bundle → I1–I5 + guards 1–14 (unchanged) → on_validated → unpickle verified bytes
  completeness guard → score → build_predictions (unchanged)
  staging writer: scratch.predictions_<run id>$YYYYMMDD (load, WRITE_TRUNCATE)
  predictions.sql on staging ─────────────────────── data test 2 (gates publishing)
  passed → BEGIN; DELETE run date's rows; INSERT … SELECT from staging; COMMIT (serving.predictions)
  finally: drop staging; scoring_runs row (DML INSERT)
  stdout report, job summary, exit code
```

## Data handling

- No new data is created or split; the gold table, the split assignment and the label are untouched.
- The label is stored only on the laptop/runner and in the private gold bucket (the uploaded gold table contains `is_readmitted` and `label_horizon`; the bucket has public access prevention and only the operator and `scorer` can read it), never in BigQuery and never in predictions: predictions carry no label column, and the BigQuery CLI has no `--show-observed-outcomes` (it is forced False).
- Leakage: none introduced. The completeness guard still refuses any window that touches train or dropped rows.
- Reproducibility: the same gold content (D2), the same sha256-pinned bundle bytes and the same lockfile give identical predictions. L4 compares the BigQuery rows to the local Parquet for the same run date.
- Uploaded to GCP: synthetic gold (1.2 MB) and predictions (a few hundred rows per run date). No PHI exists (NFR-S5).
- Redaction rule (slice 3) for every committed record: `<BILLING_ACCOUNT_ID>`, `<EXISTING_BUDGET_ID>`, `<OPERATOR>`. `terraform plan` output is summarised, never pasted.

## Failure modes

| Situation | Behaviour |
|---|---|
| `--run-date` does not parse | `refused`, `run_date` null, no tests run, row recorded (the client is built before parsing). In `score.yml` there is no shell pre-check, so a CI typo is recorded too |
| No ADC / impersonation denied | `gcp.credentials` FileNotFoundError, or `GoogleAuthError` on the first call → `refused` (gold/bundle reads map it) or `failed` (BigQuery call); the row cannot be recorded with no working client → WARNING, `recorded=False` |
| Bundle missing / no manifest / extra object / subdirectory / bad sha | I1–I4 keyed ValueError (unchanged keys) → `refused` |
| Scorer lacks `objects.get` | `OSError` → "cannot read bundle" ValueError → `refused` |
| Writer re-uploads an existing bundle / is retried after a lost response | 412 → "already exists" ValueError; `retry=None` documented (D3) |
| Writer interrupted mid-upload | partial objects without `manifest.json`; reader refuses (I1); recovery runbook |
| Gold prefix empty / unexpected object / > 512 MiB / missing metadata | FileNotFoundError / ValueError → `refused` |
| Gold bucket missing / forbidden | FileNotFoundError / ValueError → `refused` |
| Gold content edited in GCS | guard 6 fingerprint mismatch → `refused` (bundle fields recorded only if guard 6 passed, i.e. none) |
| Window touches train rows (L7) | completeness guard → `refused`, bundle fields filled by `on_validated` |
| Predictions frame violates the contract | `validate_predictions` ValueError before any call → `refused` |
| Staging table already exists | `Conflict` → `failed` (ids are uuid4; would indicate a bug) |
| Parquet schema ≠ table schema | load `BadRequest` → `failed`; live partition untouched |
| Load returns fewer rows | RuntimeError → `failed`; live partition untouched |
| A predictions data test fails | not published, `data_tests_failed`, exit 3 |
| A history data test fails | scoring continues; published if predictions pass; `data_tests_failed`, exit 3. Remediation runbook (`docs/setup.md`): an Owner inspects, then `DELETE`s or repairs the offending row with `bq query --maximum_bytes_billed`, and records it in the slice note; time travel keeps the original for 7 days |
| Data-test script errors otherwise (permission, syntax, bytes cap) | propagates → `failed` |
| Publish transaction fails (incl. aborted by a concurrent transaction) | `failed`, `published=False`, live partition unchanged (nothing applied); no client timeout, so `published` reflects the job's final state |
| `scoring_runs` append fails | `recorded=False`, WARNING; a clean run exits 4 |
| Overlapping runs for one run date | each tests its own staging rows; last publish wins; both recorded |
| Query would bill > 100 MiB | `bytesBilledLimitExceeded` → `failed` |

## Verification

### Offline tests (no network, no credentials; `pytest` and CI)

Baseline: 768 collected = 767 passed + 1 skipped. Target: 767 + the new tests passed, 1 skipped.

**Guard against real calls**: `tests/conftest.py` gains an autouse fixture (no module-level `google` or `readmission_risk.gcp` import: it does `monkeypatch.setattr("readmission_risk.gcp.credentials", …)` by dotted string inside the fixture body, skipping silently if `readmission_risk.gcp` cannot be imported — it always can: `gcp.py` imports no `google` at module level) that monkeypatches `gcp.credentials`, `gcp.storage_client` and `gcp.bigquery_client` to raise `RuntimeError("tests must not call GCP")`. Opt-out: a test marked `@pytest.mark.real_gcp_module` (marker registered in `pyproject.toml`'s `[tool.pytest.ini_options] markers`) is not patched; only `tests/test_gcp.py` uses it, and it fakes `google.auth.default` / `impersonated_credentials` itself, so no network is reachable either. Tests that need GCS or BigQuery override the three attributes with fakes:
- `tests/bundles/fake_gcs.py`: buckets of blobs with generations. Its `bucket()`/`blob()`/`list_blobs`/`download_as_bytes`/`upload_from_string` raise the real `google.api_core.exceptions` classes, it records each call, and it raises on `get_bucket`, `reload`, `exists`.
- `tests/warehouse/fake_bigquery.py`: records `create_table`/`load_table_from_file`/`query`/`delete_table` (and raises on `copy_table`/`load_table_from_json`, which this design must not use) calls with their configs, and returns scripted results or exceptions.

**`tests/bundles/test_gcs.py`:**
- `test_parse_gs_uri_accepts_and_rejects` (parametrised).
- `test_list_entries_relative_names_subdirectory_and_placeholder`, `test_list_entries_empty_is_not_found`.
- `test_read_file_maps_not_found_forbidden_and_auth_errors`.
- `test_write_files_manifest_last_if_generation_match_zero_retry_none`.
- `test_write_files_existing_object_is_already_exists_and_nothing_replaced`.
- `test_write_files_never_reads`.
- `test_validate_bundle_reads_a_gcs_bundle_like_a_local_one`: the session bundle is copied into the fake; `validate_bundle("gs://fake-bucket/<id>")` returns the same `bundle_id`, `partition_of` and `model_bytes` as the local one, and `champion_sha256` is set when the expected sha is given. This covers the line-164 change.
- `test_gcs_integrity_cases`: missing manifest, an extra object, a subdirectory object, a wrong file sha256, and a bundle stored under a prefix that is not its id. Each raises the same keyed ValueError as its local twin.

**`tests/bundles/test_store.py`:** `test_check_bundle_uri`, `test_bundle_uri_leaf`; the existing `test_gs_and_other_schemes_are_refused` (`tests/bundles/test_store.py:133-141`) keeps only `local_bundle_path("gs://…")` (new message) and the other-scheme cases; its `list_bundle_entries("gs://bucket/x")` assertion MOVES to `tests/bundles/test_gcs.py::test_list_entries_empty_is_not_found` against the fake (it would otherwise hit the no-GCP guard). **`tests/scoring/helpers.py`:** case `I1-gs-uri` (`gs://b/x`) is kept deliberately and commented as "malformed gs:// URI (bucket name too short)". It still gives `"bundle not found: "` via `parse_gs_uri` without any client call (the autouse guard proves it).

**`tests/scoring/test_pipeline.py`:**
- `test_on_validated_called_once_after_validation_and_not_on_earlier_refusals` (bundle + window on a completeness refusal; no call on a gold refusal).
- `test_validate_bundle_sets_champion_sha256` (champion vs `--bundle`).
- `test_partition_path_may_be_a_string` (a stub writer returning a str).
- The existing subprocess import test is extended so that `google.cloud` is also not in `sys.modules` after importing every `scoring.*` and `bundles.*` module.

**`tests/warehouse/`:**
- `test_predictions_schema_json_is_prediction_schema_plus_run_id` (names, order, types, modes; descriptions non-empty).
- `test_scoring_runs_schema_json_matches_run_record_fields`.
- `test_staging_writer_validates_before_any_call`.
- `test_staging_writer_creates_partitioned_table_then_loads_decorator_write_truncate` (create spec, destination, config, Parquet read back equals the frame plus `scoring_run_id`; the Parquet field `scoring_run_id` is REQUIRED, i.e. `pq.read_schema(...).field('scoring_run_id').nullable is False`).
- `test_staging_writer_row_count_postcondition`.
- `test_publish_is_one_transaction_delete_then_insert_with_explicit_columns` (the rendered script: BEGIN/DELETE with `as_of_date = @run_date`/INSERT with the 19 back-quoted columns of `predictions.json` in order, `` `partition` `` included/COMMIT; `query_config` used; no timeout).
- `test_resolve_job_waits_for_done` (the fake job is RUNNING on the first two `get_job` calls, then DONE → True; DONE with `error_result` → False; never DONE within the budget → None; `get_job` raising throughout → None; sleep/monotonic injected, no real waiting).
- `test_publish_error_while_running_then_commits_is_published` (publish `.result()` raises, the fake job is RUNNING then DONE without error → `published=True`, status `failed`, staging deleted only AFTER the last `get_job`).
- `test_publish_error_job_failed_is_not_published`; `test_publish_unobservable_falls_back_to_count` (resolve None, count > 0 → True; count 0 → `published=False` + 'outcome unknown' WARNING).
- `test_value_error_after_publish_attempt_is_failed_not_refused` (publish raises a `ValueError` subclass, the job resolves DONE → status `failed`, `published=True`; RUNS-07 can never be violated by the flow).
- `test_append_error_after_commit_counts_as_recorded` (the insert `.result()` raises, `resolve_job('record_<id>')` → True → `recorded=True`, exit 0 for a succeeded run).
- `test_append_run_is_a_parameterised_insert_of_all_26_columns` (typed NULLs; affected-rows post-condition).
- `test_no_client_timeouts` (static: no `.result(timeout=` in `src/readmission_risk/warehouse/`).
- `test_query_config_sets_maximum_bytes_billed`.
- `test_only_query_config_builds_query_job_configs`: static; `QueryJobConfig(` appears in `src/` only in `warehouse/bigquery.py`, and every `.query(` call in `src/` passes `job_config=query_config(`.
- `test_sql_scripts_follow_the_shape_rule`, static:
  - one `DECLARE c STRUCT` reading the table, then only `ASSERT … AS '<ID>: …';` statements;
  - IDs unique and equal to the tables above;
  - every `COUNTIF(` has the form `COUNTIF((…) IS NOT TRUE`;
  - `partition` appears only back-quoted;
  - no `IFNULL`/`COALESCE`/`IS NOT FALSE` anywhere.
- `test_render_script_backquotes_and_refuses_bad_identifiers`.
- `test_run_data_tests_verdict_only_on_exact_assertion_message` (any `GoogleAPICallError` class; the L0-recorded real ASSERT message → failure; the L0-recorded real syntax-error message that quotes a label → propagates; an unknown ID or an altered description → propagates) and `test_run_data_tests_propagates_other_errors`; parameter rules; `test_assertion_descriptions_parsed_from_sql`.
- `test_score_to_bigquery_*`:
  - `succeeded` (call order: history query → gold → staging create/load → predictions query → publish transaction → delete staging → insert row);
  - `predictions_tests_fail_not_published`;
  - `history_tests_fail_still_published_status_data_tests_failed`;
  - `refused_bad_run_date_no_tests`;
  - `refused_after_validation_records_bundle_fields`;
  - `failed_load_records_and_keeps_live_untouched` (no publish call);
  - `publish_failure_failed_not_published`;
  - `error_after_publish_is_failed_with_published_true`;
  - `keyboard_interrupt_drops_staging_skips_insert_and_propagates`;
  - `insert_failure_on_data_tests_failed_keeps_status_and_none_error`;
  - `append_failure_after_success_sets_error_recorded_false`;
  - `append_failure_keeps_original_error`;
  - `no_client_skips_append`;
  - `finished_at_uses_monotonic` (wall clock patched backwards).
- `test_materialized_gold_dir_*`: local passthrough, downloads only parquet and metadata, ignores `_SUCCESS`/crc/placeholder, refuses unexpected/subdirectory/oversize, maps NotFound/Forbidden/auth, `fingerprint_identical_to_local`, temp dir removed on success and error.
- `test_seeded_check_*`: every ID in `PREDICTION_MUTATIONS`/`RUN_MUTATIONS` exactly equals `assertion_descriptions`' IDs (no assertion without a mutation); every mutation statement back-quotes its columns; with the fake client, clean pass + each ID failing with itself → OK, and each wrong outcome (clean fails, mutated passes, wrong ID, unmet precondition) → `SeededCheckError` carrying the partial results; leftover copies dropped in `finally`; the CLI prints partial results before `ERROR:`.
- `test_score_to_bigquery_cli_*`: exit codes 0/1/3/4 and the re-raise for `failed`, the printed run line, and the job summary's exact table.

**`tests/test_gcp.py`** (new, at `tests/` root next to `test_infra.py`, since `gcp.py` is a top-level module):
- `test_quota_project_only_for_user_credentials` (a fake `google.oauth2.credentials.Credentials` gets `with_quota_project(PROJECT_ID)`; a fake external-account and a service-account credential are returned unchanged; impersonation clears a user source's quota project);
- `test_configure_validates_email_and_resets_clients`;
- `test_missing_adc_is_file_not_found`;
- `test_gcp_imports_without_google` (subprocess with `sys.modules['google'] = None`: `import readmission_risk.gcp, readmission_risk.bundles.store, readmission_risk.warehouse.run` succeeds).

**`tests/scoring/test_cli.py`:** `--gold-dir gs://…` reaches `materialized_gold_dir` as the same string, with no `gs:/` collapse.

**`tests/models/test_export_cli.py`:** a `gs://` `--bundles-dir` prints the `gs://` URI and the champion JSON unchanged, with no "absolute path" warning (`export_bundle` stubbed).

**`tests/bundles/test_champion.py`:** the committed champion's `bundle_uri` must match `^gs://synthea-readmission-risk-bundles/[0-9a-f]{64}$`.

**`tests/test_infra.py`:**
- `test_score_workflow_matches_the_terraform_values`: the provider string, the SA email (`<scorer account_id>@<project_id>.iam.gserviceaccount.com`), and the gold URI = `gs://<google_storage_bucket.gold name>/full_run_2b` (exposed as a module-level helper `expected_gold_uri()` that `tests/test_tooling.py` imports);
- `test_scorer_sa_binding_is_keyed_to_the_score_workflow_on_main`;
- `test_scorer_grants_are_exactly`: objectViewer on bundles and gold, the custom role on the two serving tables, dataEditor on scratch, jobUser, workloadIdentityUser, operator tokenCreator, and nothing else;
- `test_custom_role_is_exactly_get_getdata_updatedata`;
- `test_bigquery_tables_partitioned_require_filter_and_deletion_protected`;
- `test_terraform_schemas_are_the_package_schemas`;
- `test_predictions_table_description_is_demo_notice`;
- `test_score_workflow_input_reaches_shell_only_via_env`;
- the IAM count is 12.

The existing id-token, SHA-pin, persist-credentials and no-keys tests iterate the workflows directory and cover `score.yml`.

**`test_seeded_check_*`** also asserts that every copy name passes `table_id()`.

**`tests/test_tooling.py`:** `score-bq` and `m5-check` added to `EXPECTED_TARGETS`; `GOLD_URI` and `SCORER_SA` added to `MAKEFILE_VARIABLES` (hence `SCRUBBED_ENV`); a new `test_gold_uri_default_matches_infra` reads the default with the regex `^GOLD_URI\s+\?=\s+(\S+)$` (multiline) on the Makefile text, so the new variables keep the block's column alignment and compares it with the gold URI `tests/test_infra.py` derives (`gs://<gold bucket>/full_run_2b`); `tests/bundles/test_champion.py`'s change lands in the champion-repoint commit, not before. `tests/test_docs.py`: no change expected; the README edit keeps its links.

**Gates:** `ruff check .`, `pytest`, `zizmor --offline .` (zero findings at the default persona), `make tf-validate`.

### Live checks (each outward-facing step announced and confirmed by the owner first)

Every gcloud/bq command runs with `export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk`, and impersonated ones also with `CLOUDSDK_BILLING_QUOTA_PROJECT=LEGACY`. Read-backs and comparisons are ad-hoc commands or snippets run from the scratchpad and recorded in Results, not committed code.

- **L1:** `terraform plan` → "15 to add, 0 to change, 0 to destroy". The owner confirms, then `apply`, then `plan` → "No changes".
- **L0 (after L1, before any data):** as `scorer`, the probe:
  1. Run `scoring_runs.sql` on the empty table. It must pass (proves parse, rendering, and that a script with no parameters works).
  2. Create an empty staging-shaped table in `scratch`, then run `predictions.sql` on it with both parameters. It must fail with `PRED-01`; record the exception class and full message (F8).
  3. Run a deliberately malformed copy of `predictions.sql` (one `ASSERT` with a missing parenthesis, label literal intact), rendered by hand and sent with `client.query(..., job_config=query_config(...))`, and pass the exception to `classify_error(exc, "predictions")`: it must NOT be classified as a verdict (F8). Record both messages verbatim; set `_ASSERT_MESSAGE_RE` and the recorded-message unit test from them.
  4. Run `publish` against that empty staging table for a far-past date (`1900-01-01`): the transaction commits with 0 rows deleted and inserted (proves the scorer's table-level grant suffices for DML in a transaction, F3).
  5. Drop that table.
  6. **Negative checks as `scorer`, while both serving tables are still EMPTY** (moved here from L10 so a check that unexpectedly succeeds cannot destroy data). Run as an ad-hoc Python snippet, NOT with `bq` (critic r7 #2: the `bq` identity was unspecified, and as the Owner `bq rm` would really delete): `gcp.configure(impersonate_service_account="scorer@synthea-readmission-risk.iam.gserviceaccount.com")`, `client = gcp.bigquery_client()`.
     - **Identity first:** `client.query("SELECT SESSION_USER() AS u", job_config=query_config())` must return the scorer's email. Otherwise stop: no probe runs.
     - **Probes:**
       - `client.create_table(Table("…serving.l0_probe", schema=[SchemaField("x","STRING")]))` (create);
       - `client.update_table(<get scoring_runs>, ["expires"])` with `expires = now + 1 h` (metadata);
       - `client.load_table_from_json([row], "…serving.scoring_runs", job_config=LoadJobConfig(schema=…, write_disposition=WRITE_APPEND)).result()`. The row has **every REQUIRED column** filled (so a permitted load cannot fail with 400 instead): `scoring_run_id = "l0-probe"`, `status = "l0_probe"` (deliberately RUNS-02-invalid), `started_at = finished_at = now`, `trigger = "l0_probe"`, `python_version`, `gold_uri = "l0-probe"`, `predictions_table`, `top_n = 1`, `published = false`.
       - `client.delete_table("…serving.predictions")` (delete; the table is empty).
     - **Pass criterion for each probe:** a `google.api_core.exceptions.Forbidden` whose message names the specific denied permission: `bigquery.tables.create` (create; also expected for the load if the critic's reading of the IAM docs holds), `bigquery.tables.update` (metadata) or `bigquery.tables.delete` (delete). Any other error (another 403 reason, 400, 404) is NOT a pass. It is recorded and stops the slice for review.
     - **The load probe** has one more accepted outcome: a Forbidden naming `bigquery.tables.updateData` is impossible (the role has it), so a refusal there means F3 holds.

     Fallbacks, decided BEFORE running:
     - a create that succeeds → the operator drops `serving.l0_probe`;
     - an expiration that succeeds → the operator clears it (`expires = None`);
     - a load that succeeds → the operator runs `DELETE FROM serving.scoring_runs WHERE scoring_run_id = 'l0-probe'`, and F3's premise is recorded as wrong (the DML design still stands);
     - a delete that succeeds → the operator runs `terraform apply`, which recreates the empty table (nothing lost).

     Any success also means D6's boundary is not what the role says: the slice stops for an owner decision before L2.
- **L2:** upload `data/gold/full_run_2b/{part-*.parquet,_gold_metadata.json}` to `gs://synthea-readmission-risk-gold/full_run_2b/` as the operator. Byte identity, checked two ways: (a) each object's `md5_hash` from `gcloud storage objects describe` equals `gcloud storage hash --hex` of the local file (GCS stores md5/crc32c, not sha256); (b) the prefix is downloaded with `gcloud storage cp` into the scratchpad and every file's `sha256sum` equals the local one's.
- **L3:** GCS writer proof as `bundle-writer`: `python scripts/export_bundle.py --experiment-name readmission-risk-v1 --model logistic_regression --bundles-dir gs://synthea-readmission-risk-bundles --impersonate-service-account bundle-writer@…`. Expected:
  - exit 1 with "already exists";
  - with HTTP debug logging (`http.client.HTTPConnection.debuglevel = 1` plus `urllib3` DEBUG), no GET on any object before the first upload request (F5);
  - the object's generation unchanged (operator `gcloud storage objects describe`).
- **L2b (local reference, no GCP):** `make score RUN_DATE=20250630 SCORE_ARGS="--bundle bundles/75a14… --output-dir <scratchpad>/ref"` → the reference Parquet that L3b and L4a compare against (value by value, the L4a method).
- **L3b (the writer's create path, live):** as `bundle-writer`, `python scripts/export_bundle.py --experiment-name readmission-risk-v1 --model logistic_regression --bundles-dir gs://synthea-readmission-risk-bundles/l3-probe --impersonate-service-account bundle-writer@…` → `created`, objects at `l3-probe/75a14…/{split_assignment.csv, model.pkl, manifest.json}`, manifest uploaded last (object creation times). Then, as `scorer`, `python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --run-date 20250630 --bundle gs://synthea-readmission-risk-bundles/l3-probe/75a14… --output-dir <scratchpad> --impersonate-service-account scorer@…` → succeeds (the reader validates a bundle this code wrote; its Parquet equals L2b's reference, column by column via `to_pylist()`). Cleanup as the operator: `gcloud storage rm --recursive --all-versions gs://synthea-readmission-risk-bundles/l3-probe/` (the bucket is versioned).
- **L4a:** as `scorer`, `make score-bq RUN_DATE=20250630 SCORE_ARGS="--bundle gs://synthea-readmission-risk-bundles/75a14…"` → `succeeded`, exit 0. Read-back as `scorer`: `client.query("SELECT * … WHERE as_of_date = '2025-06-30' ORDER BY rank", job_config=query_config()).to_arrow()`. Compare VALUES, not Arrow schemas (nullability flags differ between the REST read path and the local file): drop `scoring_run_id`, then for every column compare `to_pylist()` of the BigQuery column with `to_pylist()` of the local Parquet column (ints compared as Python ints) against L2b's reference (every column; float scores exactly equal). Staging table gone.
  - **L4b:** commit the champion repoint, then `make score-bq RUN_DATE=20250630` with no `--bundle` → `succeeded`, `champion_sha256` recorded.
- **L5:** re-run 20250630 → same row count, a new `scoring_run_id` on every row (replaced, not appended). Score 20250731; 20250630's partition is unchanged (row count + `BIT_XOR(FARM_FINGERPRINT(encounter_id))` and the distinct `scoring_run_id` before and after; F3).
- **L6:** `scoring_runs` holds the L4/L5 rows: all `succeeded`, `published`, both test flags TRUE, `trigger = laptop`, git commit set.
- **L7:** a refused run (`--run-date 20221215`, a window touching training rows) → exit 1, a `refused` row carrying the guard's message, with bundle id and window recorded.
- **L8 (M5):** `make m5-check RUN_DATE=20250630` (impersonating `scorer`) → both clean copies pass and all 17 mutated copies fail with exactly their own ID ("M5 OK"); no `m5p_`/`m5r_` table left in `scratch`. The output is recorded in Results as M5's "recorded run".
- **L9 (operator identity, `bq query --maximum_bytes_billed=104857600`):**
  - `region-us-central1.INFORMATION_SCHEMA.JOBS_BY_PROJECT` for this slice's jobs: every query/DML job's `total_bytes_billed` ≤ 100 MiB, and the load jobs bill 0.
  - F6 through the real code path: as `scorer`, `run_data_tests(client, "scoring_runs", table=serving.scoring_runs)` (no parameters needed) with `bigquery.MAX_BYTES_BILLED` monkeypatched to 1 MiB in an ad-hoc snippet must raise a bytes-billed error (`bytesBilledLimitExceeded` in the job error) rather than return a verdict. If it is not refused, the cap does not reach the script's statements: record that, and decide with the owner.
  - After 24 h, the billing report shows no BigQuery or Storage charge above zero beyond the free tier (F4).
- **L10:** `scorer` holds exactly the designed bindings and no user-managed keys. The `serving` dataset ACL does not list `scorer`; the two serving tables' IAM lists it with `scorerTableWriter`; `scratch` lists it as editor. The negative checks ran in L0 step 6, on empty tables; L10 repeats only the create probe (`bq mk --table serving.l10_probe`, must be refused) now that the tables hold data.
- **L-post** (after v2 merges, added to the V-post TODO): `gh workflow run score.yml --ref main -f run_date=20250630` → green, with a `scoring_runs` row carrying `trigger = workflow_dispatch` and the run URL.

## Results

Recorded under the redaction rule. Implementation 2026-10-11 on branch `v2-slice4-score-to-bigquery` (off `v2` at `eda740b`).

**Design check.** Rounds 1-7, then an owner override:
- Round 1 ran comprehension (passed), the critic and readiness. Rounds 2-7 ran the critic and readiness.
- Readiness closed once, in round 5 (on r5), and reopened on text added later.
- The critic's gap counts were 21, 10, 6, 5, 3, 5, 5. The three-revision cap was reached after round 4. The owner chose further rounds twice, then "fix all 10, then implement" after round 7 (r8).
- Neither pass formally closed on r8.

**Implementation deviations from r8 (all small, none changes a decision):**
- The SQL scripts use `DECLARE c DEFAULT (SELECT AS STRUCT ...)`, with the struct type inferred, rather than spelling out `STRUCT<...>`.
- `resolve_job`'s `sleep`/`monotonic` default to None and are resolved per call, so tests can patch the clock.
- `google-cloud-storage` is pinned at **3.16.0**, not 3.17.0: 3.17.0 was 3 days old, and the design's 7-day rule applies.
- Both fixed-id jobs (publish, record) pass `job_retry=None`. google-cloud-bigquery 3.46 warns that a `job_id` without it will become an error, which was seen live in L0.
- BLE001 `noqa` sites: `run.py` (the run handler, the `drop_staging` catch, and the `_record` catch, which the job id resolves) and `staging.py` (`resolve_job`'s retry and `publish_landed`). `seeded_check`'s cleanup uses `contextlib.suppress`.
- `seeded_check` creates copies with CTAS throughout; no copy jobs are used anywhere.

**Pre-commit review.** 4 rounds. The stop condition tripped after rounds 2, 3 and 4; the owner chose "keep working" twice, then "accept".
- **Fixed, round 1:** a transient GCS error behind a reader's mapped ValueError was recorded as `refused`; `_classify` now walks the cause chain.
- **Fixed, round 1:** the gold listing is bounded (`max_results`).
- **Fixed, round 2:** no ADC and a denied impersonation stay `refused`.
- **Fixed, round 3:** a google-auth `RefreshError` is judged by its HTTP response body, because impersonated credentials never set `retryable`.
- **Fixed, round 4:** 408 is transient (google-auth's `DEFAULT_RETRYABLE_STATUS_CODES`).
- **Rebutted:** the champion repoint is a separate commit (D4); the reviewer accepted this.
- The round-4 fix was not re-reviewed (owner's choice).

**Tool gates.** `ruff check .` clean; `zizmor --offline .` no findings (9 suppressed below the default persona: 7 before, plus 2 pedantic notes on `score.yml`); `make tf-validate` valid; `pytest` **917 passed, 1 skipped** (was 767 + 1), after the round-4 fix and before the champion-test change; the champion test and docs tests (114) pass after the repoint.

**Live checks** (as the `scorer` service account unless noted):
- **L1:** `Plan: 15 to add, 0 to change, 0 to destroy` → `Apply complete! Resources: 15 added, 0 changed, 0 destroyed.` → "No changes". The first impersonation was refused for about 64 s (IAM propagation); the error was a `RefreshError` whose body held `{"error": {"code": 403, "status": "PERMISSION_DENIED"}}`, the exact shape `_refresh_error_is_permanent` parses.
- **L0:** identity `scorer@…` confirmed. Results:
  - **`scoring_runs.sql` on the empty table:** passed.
  - **F8, a failing ASSERT:** `BadRequest`, structured message `Query error: PRED-01: the run date has no prediction rows at [40:1]`, which matches the placeholder `_ASSERT_MESSAGE_RE` unchanged.
  - **F8, a malformed script:** `Syntax error: Expected "," but got keyword AS at [40:23]`, not taken as a verdict. Both messages are pinned in `tests/warehouse/test_data_tests.py`.
  - **F3, the publish transaction as the scorer:** `DONE`, no error.
  - **Negative checks on the empty tables:** every one refused with 403, naming the permission:
    - create → `bigquery.tables.create` on the dataset;
    - expiration → `bigquery.tables.update`;
    - **load job into `scoring_runs` → `bigquery.tables.create` on the dataset** (the critic's reading confirmed: loads into `serving` are impossible under D6, so the DML design was required);
    - delete → `bigquery.tables.delete`.
- **L2 (operator):** 8 objects uploaded to `gs://synthea-readmission-risk-gold/full_run_2b/`; each object's md5 equals the local file's, and after a fresh download each sha256 equals the local file's.
- **L2b:** the local reference, 42 rows; its Top-10 matches the README sample.
- **L3:** as `bundle-writer`, re-exporting the v1 logistic-regression bundle to the bucket root gave exit 1, "already exists (at least model.pkl)" (412). **F5:** no GET on any object before the upload. One bucket-metadata `GET /storage/v1/b/<bucket>` was sent and refused (403) and was ignored by the client; nothing was read, and its origin was not traced (an observation, not a defect). The manifest's generation was unchanged.
- **L3b:** a fresh export to `gs://…/l3-probe/` gave `created`; the objects were created model.pkl → split_assignment.csv → manifest.json, the manifest last by generation. The scorer validated and scored from that bundle, and its 42 predictions are identical, value for value, to L2b. Then `l3-probe/` was deleted with all versions.
- **L4a:** `make score-bq RUN_DATE=20250630 SCORE_ARGS="--bundle gs://…/75a14…"` → `succeeded (published: yes; recorded: yes)`. The read-back has 42 rows, all 18 columns identical to L2b, one `scoring_run_id`, and `scratch` is empty.
- **Commits:** `68edbdd` (the slice) and `26a534b` (the champion → `gs://`, same pin), on the owner's instruction.
- **L4b:** `make score-bq RUN_DATE=20250630` with no `--bundle` → `succeeded`, `champion_sha256` = `b61388f0…`. The partition was replaced: still 42 rows, all under the new run id.
- **L5:** scoring 20250731 (44 rows) left 20250630 unchanged: the row count, `BIT_XOR(FARM_FINGERPRINT(encounter_id))` and the run id are all equal before and after.
- **L6:** 4 rows (L4a, L4b, L5 and the refused L7), all with `trigger = laptop`. L4a records commit `eda740b` with `git_dirty = true` (run before the commit) and no champion (`--bundle`); the later rows record `26a534b`, clean, champion `b61388f0`.
- **L7:** `--run-date 20221215` → exit 1, `refused (published: no; recorded: yes)`. The refused row still carries the bundle and the window (`on_validated`).
- **L8 (M5):** `make m5-check RUN_DATE=20250630` → both clean copies pass, and all 17 mutated copies fail with exactly their own ID ("M5 OK"); `scratch` is empty afterwards. This is PRD M5's recorded run.
- **L9 (operator):**
  - **F6:** `run_data_tests` with a 1 MiB cap is refused ("Query exceeded limit for bytes billed").
  - **Bytes billed:** today's scorer jobs billed at most 20 MiB per job (the scripts) and about 755 MB in total, mostly M5's 19 CTAS copies; the load jobs billed nothing.
  - **F4: done 2026-10-12.** The owner read the billing report (Cloud console > Billing > Reports, project
    `synthea-readmission-risk`, from 2026-10-11): **CHF 0** for slice 4's runs, so the BigQuery load jobs, DML and queries
    and the Storage use all stayed within the free tiers, as projected.
- **L10 (operator):** the live IAM matches the design:
  - `scorer`: 0 user-managed keys;
  - on the account: `serviceAccountTokenCreator` → `<OPERATOR>` and `workloadIdentityUser` → `score.yml@refs/heads/main`;
  - `objectViewer` on the bundle and gold buckets;
  - project `bigquery.jobUser`;
  - **no entry in the `serving` ACL**, `WRITER` on `scratch`;
  - `scorerTableWriter` on exactly the two serving tables.

**Open:** L-post (the first dispatched `score.yml`, after v2 merges into `main`, together with slice 3's V-post). The F4 billing check is done (CHF 0, 2026-10-12).

**Follow-ups found during the live checks, resolved the same day:**
- **MLflow telemetry.** Laptop runs of the bundle exporter (and of training and the README assets) contacted `config.mlflow-telemetry.io`. MLflow disables its telemetry by itself only under pytest and in CI. `src/readmission_risk/__init__.py` now does `os.environ.setdefault("MLFLOW_DISABLE_TELEMETRY", "true")` before any `mlflow` import, so an explicit `MLFLOW_DISABLE_TELEMETRY=false` still wins.
  - Tests: `tests/test_gcp.py::test_importing_the_package_opts_out_of_mlflow_telemetry` runs a subprocess without the CI/test markers and with sockets blocked, and checks that no telemetry client exists. A mutation check confirmed it fails without the opt-out. `test_an_explicit_mlflow_telemetry_choice_wins` covers the override.
  - Live: a traced re-export made 0 telemetry requests.
- **The bucket-metadata GET in L3.** Traced to google-cloud-storage 3.16's "App-centric Observability" `BucketMetadataCache`. `_helpers.create_trace_span_helper` queues a background `get_bucket` on the first touch of every bucket, *before* checking whether its OpenTelemetry tracing is on (it is off by default here, so the metadata is never used). On a 403 it caches a fallback. Harmless, but a refused request each time.
  - Fix: `gcp.storage_client()` sets the library's own switch, `DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA=true` (`setdefault`).
  - Test: `test_storage_client_switches_off_the_unused_bucket_metadata_lookup`.
  - Live: a traced scoring run from the GCS champion sent only the object listing, no `GET /storage/v1/b/<bucket>?projection=noAcl`.
- Trade-off, stated: both are process-environment defaults set by the package. That is acceptable for this single-owner application package, and `setdefault` never overrides an explicit setting.

## Teardown additions (docs/setup.md > Teardown checklist)

Steps in order:
1. Set `deletion_protection = false` on both tables and apply.
2. `serving` (`delete_contents_on_destroy = false`) must be emptied first; `scratch` deletes itself.
3. Delete the custom role (custom roles stay soft-deleted for 7–37 days; the same `role_id` cannot be reused in that time).

## Out-of-scope follow-ups

The items under Scope > Out.

## Revision log

- r1 (2026-10-11): first draft after Step 0 (D1–D4).
- r2 (2026-10-11): round 1. Comprehension passed. The critic raised 21 gaps and readiness 14 questions; all are addressed:
  - `bundle_guard.py:164` via `bundle_uri_leaf` (critic 1, readiness 1);
  - the NULL rule rewritten (`COUNTIF((…) IS NOT TRUE)`, a single-read script shape) and its static test fixed (critic 2);
  - `partition` back-quoted, plus probe L0 (critic 3);
  - history-test semantics, a monotonic `finished_at` and a remediation runbook (critic 4);
  - `score_to_bigquery` returns a result instead of raising, with exit codes (critic 5, readiness 13);
  - `on_validated` and `ModelBundle.champion_sha256` (critic 6, readiness 3, 6);
  - `run_date` parsed first (critic 7, readiness 4);
  - owner decisions D5 (write-audit-publish + `scoring_run_id`) and D6 (scratch dataset + custom no-delete role) (critic 8, 10);
  - the exporter's `gs://` printing (critic 9, readiness 2);
  - auth/quota mapping (critic 11, readiness 5, 8);
  - `retry=None` and `client.bucket()` (critic 12);
  - the `BigQueryRunRequest` input (critic 13, readiness 13);
  - gold and listing edge cases (critic 14);
  - F8, with the label regex matched on any API error class (critic 15, readiness 7);
  - the infra test count, block-style permissions and the autouse no-GCP guard (critic 16, 17);
  - the L4/L9 methods and identities (critic 18);
  - concurrency wording (critic 19);
  - README and the DEMO_NOTICE test (critic 20, readiness 11);
  - line numbers, test counts and the fingerprint wording (critic 21);
  - schema descriptions (readiness 10), the exact test changes (readiness 12), the job-summary layout (readiness 14), and the M5 INSERT (readiness 9).
- r3 (2026-10-11): round 2. The critic raised 10 gaps and readiness 8 questions; all are addressed:
  - copy and load jobs into `serving` replaced by DML (a publish transaction; a parameterised `INSERT` for `scoring_runs`), because they need dataset-level `tables.create`; D5's mechanism amended with D6 unchanged; staging's spec no longer has to match (critic 1, 4; readiness 1);
  - `scoring_run_id` declared non-nullable in staging Parquet, with a test (critic 2);
  - quota project set only for user credentials, plus `tests/test_gcp.py` (critic 3);
  - the data-test verdict needs an exact assertion-message match, fixed from an L0 probe that includes a malformed script (critic 5);
  - RUNS-06 made consistent with the "failed after publish" path, RUNS-07 added, and no client timeouts so `published`/`recorded` are definitive (critic 6; readiness 3);
  - `tables.update` removed from the custom role, with live negative checks in L10 (critic 7);
  - M5 options, output and `make m5-check` (critic 8; readiness 6);
  - no module-level `google` imports, `from __future__ import annotations`, and a no-google import test (critic 9);
  - the L4a value comparison, and the CLI printing the live destination, never staging (critic 10; readiness 8);
  - `KeyboardInterrupt` handling (readiness 4);
  - exit-code precedence (readiness 2);
  - summary placeholders and heading (readiness 5);
  - the `render_script` table regex (readiness 7);
  - the `configure` default, and the champion test landing in the repoint commit (readiness, minor).
- r4 (2026-10-11): round 3. The critic raised 6 gaps and readiness 5 questions; all are addressed:
  - the client is built before `--run-date` is parsed, so a bad date is recorded; `score.yml`'s shell pre-check is dropped (critic 1);
  - `predictions_arrow_table(predictions, run_date)` (critic 2; readiness 1);
  - the gs case of `test_gs_and_other_schemes_are_refused` moves to the fake (critic 3);
  - the negative checks move into L0, on empty tables, each with a fallback decided in advance (critic 4);
  - L3b exercises the writer's create path and the reader on a bundle that path wrote (critic 5);
  - `n_predictions` and `n_top_n` come from the predictions frame (critic 6);
  - root parsing for bucket-only bundle roots (readiness 2);
  - the `real_gcp_module` marker opt-out (readiness 3);
  - clients looked up at call time, `client_factory=None` (readiness 4);
  - `classify_error`, used by the L0 probe (readiness 5);
  - the DEMO_NOTICE heredoc `chomp` (readiness, minor).
- r5 (2026-10-11): round 4. The critic raised 5 gaps and readiness 4 questions; the owner chose "fix all 9, one more round" at the 3-revision cap. Changes:
  - `publish_landed` resolves a publish error raised after the commit, with three tests (critic 1);
  - the slice-8 view rule restated (critic 2);
  - L2 compares md5 and sha256 after a download (critic 3);
  - L9 checks the cap through `run_data_tests` on `scoring_runs.sql` (critic 4);
  - `depends_on` on every new resource, with a test (critic 5);
  - generated SQL back-quotes every column (readiness 1);
  - `seeded_check`'s keys named (readiness 2);
  - the stale `run_date` comment fixed (readiness 3);
  - the gold-URI test target named, and the Makefile default checked against it (readiness 4).
- r6 (2026-10-11): round 5. Readiness closed ("implementation ready"); the critic raised 3 gaps, all in client-side error handling. Changes:
  - jobs with known ids plus `resolve_job`, which polls until DONE before deciding `published`/`recorded`; the count check becomes a fallback whose 0 means "unknown"; staging is dropped only after the publish is resolved (critic r5 #1);
  - once a publish is attempted, every exception is `failed` (critic r5 #2);
  - the insert outcome is resolved the same way (critic r5 #3);
  - five tests added.
- r7 (2026-10-11): round 6. The critic raised 5 gaps and readiness 8 questions; the owner chose "fix all 13, one more round". Changes:
  - the Makefile help regex `[a-z0-9-]`, `.PHONY`, and the test_tooling constants (critic 1; readiness 5);
  - per-line `# noqa: S608` with the reason (critic 2);
  - M5 now fires every one of the 17 assertions on its own mutated copy, with a mapping test (critic 3);
  - `resolve_job`: NotFound → False at once, a 300 s budget, injectable `sleep`/`monotonic` (critic 4; readiness 3);
  - an L2b reference before L3b (critic 5);
  - the `error_message` combination rule (readiness 1);
  - the insert outcome contract (readiness 2);
  - `SeededCheckError` with partial results (readiness 4);
  - output map values (readiness 6);
  - PRED-07 rewritten into the exact `COUNTIF((…) IS NOT TRUE)` form (readiness 7);
  - no pip cache in the id-token job (readiness 8).
- r8 (2026-10-11): round 7. The critic raised 4 gaps plus 1 minor, and readiness 5 questions. The owner chose "fix all 10, then implement", **overriding the gate: neither pass formally closed on r8**. Changes:
  - M5 copy-name tokens (`pred_01`), with a test (critic 1; readiness 1);
  - L0 negative probes run in Python, impersonated, with a `SESSION_USER()` identity check, permission-specific pass criteria and a complete probe row (critic 2);
  - per-line `# noqa: BLE001` at the four catch-alls (critic 3);
  - the label statement corrected (critic 4);
  - google/requests exception types → `failed` before the refusal rule (critic 5);
  - the unused `sleep` dropped (readiness 2);
  - the `GOLD_URI` regex allows alignment (readiness 3);
  - the `configure` error path in all 4 CLIs (readiness 4);
  - `<max>` and `…` defined (readiness 5).

  Round history: critic 21 → 10 → 6 → 5 → 3 → 5 → 5 gaps; readiness closed once (round 5, on r5) and reopened on later text.
