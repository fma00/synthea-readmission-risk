"""The gold publish (v3 slice 1, PRD FR4/FR5, owner decisions D6-D8): write-audit-publish of a v2 gold table from GCS into
serving.gold. A BigQuery load job copies the gold Parquet into a staging table in scratch_gold; the GOLD data tests and a
spark-rowhash-v1 content fingerprint audit it; ONE DML transaction replaces serving.gold; a verify step proves serving.gold
now holds exactly the audited rows. The connector's direct overwrite is not used: BigQuery rejects its unfiltered MERGE on a
filter-required table (probe 1). google.* is imported inside the functions. See
notes/eg-new-feature/v3-slice1-gold-bigquery-2026-10-31.md (Interfaces section 5).
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from readmission_risk.bundles.champion import read_champion
from readmission_risk.bundles.gcs import read_file
from readmission_risk.bundles.manifest import HEX64_RE, sha256_hex
from readmission_risk.models.data import _REQUIRED_NONNULL_COLUMNS
from readmission_risk.pipeline.fingerprint import (
    GOLD_COLUMN_KINDS,
    canonical_token,
    rowhash_fingerprint,
)
from readmission_risk.pipeline.gold_metadata import (
    FINGERPRINT_DEFINITION_ROWHASH_V1,
    GOLD_METADATA_FILENAME,
    LABEL_DEFINITION,
    parse_gold_metadata,
)

from .bigquery import (
    GOLD_PUBLISH_MAX_BYTES_BILLED,
    GOLD_TABLE,
    SCRATCH_GOLD_DATASET,
    SERVING_DATASET,
    SERVING_GOLD_FINGERPRINT,
    column_names,
    load_schema,
    query_config,
    quoted,
    table_id,
    to_bq_schema,
)
from .data_tests import DataTestResult, run_data_tests
from .gold_source import list_gold_objects
from .staging import resolve_job

# GOLD-05: the columns the loader (trainer, scorer, gate) requires non-null, minus the two keys GOLD-04/01 check.
GOLD_NOT_NULL_COLUMNS: tuple[str, ...] = tuple(c for c in _REQUIRED_NONNULL_COLUMNS if c not in ("encounter_id", "index_stop"))
GOLD_URI_PREFIX = "gs://synthea-readmission-risk-gold/v2/"
DEFAULT_GOLD_URI = GOLD_URI_PREFIX + "main_200k"
PUBLISH_ID_RE = re.compile(r"[0-9a-f]{12}")
_NAME_RE = re.compile(r"[a-z0-9_]{1,64}")
_LOAD_REFUSAL_REASONS = {"invalid", "invalidQuery", "badRequest"}
# status -> CLI exit code (Interfaces section 6)
EXIT_CODES = {
    "published": 0,
    "dry_run_passed": 0,
    "refused": 1,
    "audit_failed": 3,
    "content_mismatch": 3,
    "verify_failed": 4,
    "permission_denied": 5,
    "unknown": 5,
}
IAM_HINT = "IAM may still be propagating (~8 min after a terraform apply): rerun"


@dataclass(frozen=True)
class GoldPublishRequest:
    gold_uri: str  # stored as given; step 1 refuses anything but gs://synthea-readmission-risk-gold/v2/<name>
    champion_path: Path = Path("deploy/champion.json")
    expected_fingerprint: str = SERVING_GOLD_FINGERPRINT  # tests pass their fixture's; production never overrides it
    dry_run: bool = False  # everything up to and incl. the audit; no transaction (staging dropped)


@dataclass(frozen=True)
class GoldPublishResult:
    status: str
    publish_id: str
    gold_uri: str
    message: str
    metadata: dict = field(default_factory=dict)  # literal GoldMetadata field names
    champion_gold_fingerprint: str | None = None
    load_job_id: str | None = None
    publish_job_id: str | None = None
    data_tests: DataTestResult | None = None
    staging_fingerprint: str | None = None
    serving_fingerprint: str | None = None
    serving_num_rows: int | None = None
    schema_matches: bool | None = None
    partitioning_matches: bool | None = None
    description_names_fingerprint: bool | None = None
    bytes_billed: dict = field(default_factory=lambda: {"audit": None, "publish": None})
    diagnostic: list[str] = field(default_factory=list)


class _Stop(Exception):
    """Ends the publish with a given status (never escapes publish_gold)."""

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def gold_name(gold_uri: object) -> str | None:
    """'<name>' of gs://synthea-readmission-risk-gold/v2/<name> (one trailing '/' allowed), else None."""
    if not isinstance(gold_uri, str) or not gold_uri.startswith(GOLD_URI_PREFIX):
        return None
    name = gold_uri[len(GOLD_URI_PREFIX) :].removesuffix("/")
    return name if _NAME_RE.fullmatch(name) else None


def staging_table_id(publish_id: str) -> str:
    return table_id(SCRATCH_GOLD_DATASET, f"gold_staging_{publish_id}")


def serving_gold_table_id() -> str:
    return table_id(SERVING_DATASET, GOLD_TABLE)


def _chain(exc: BaseException) -> list[BaseException]:
    """exc and every exception it was raised from or during (as warehouse.run._chain; that module imports the scorer)."""
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and all(current is not s for s in seen):
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def _load_reasons(exc: BaseException) -> set[str]:
    errors = getattr(exc, "errors", None) or []
    return {e.get("reason") for e in errors if isinstance(e, dict) and e.get("reason")}


def classify(exc: BaseException, step: str) -> str:
    """The status of an exception raised at `step` ("inputs", "load", "audit", "audit_fingerprint", "verify"): table E1-E6
    of the design note, checked against the exception AND its cause/context chain, E1 first (bundles.gcs.read_file remaps a
    RefreshError into a plain OSError raised from it)."""
    from google.api_core import exceptions as api
    from google.auth import exceptions as auth

    chain = _chain(exc)
    if _is_quota(chain):
        return "unknown"  # E6: a 403 quotaExceeded/rateLimitExceeded is not a permission problem; rerun later
    if any(isinstance(e, (api.Forbidden, PermissionError, auth.RefreshError, auth.DefaultCredentialsError)) for e in chain):
        return "permission_denied"  # E1
    if step == "inputs" and isinstance(exc, (ValueError, FileNotFoundError, api.NotFound)):
        return "refused"  # E2
    if step == "load" and (isinstance(exc, api.BadRequest) or _load_reasons(exc) & _LOAD_REFUSAL_REASONS):
        return "refused"  # E3
    if step == "audit_fingerprint" and isinstance(exc, ValueError):
        return "content_mismatch"  # E5
    if step == "verify" and isinstance(exc, ValueError):
        return "verify_failed"  # E5b
    return "unknown"  # E6


def _message_for(status: str, step: str, exc: BaseException) -> str:
    if status == "permission_denied":
        return f"permission/credentials refused at {step}: {exc}; {IAM_HINT}"
    if status == "unknown":
        return f"could not run at {step}: {type(exc).__name__}: {exc}; rerun (the publish is idempotent)"
    return f"{status} at {step}: {exc}"


_QUOTA_REASONS = {"quotaExceeded", "rateLimitExceeded"}


def _is_quota(chain: list[BaseException]) -> bool:
    """BigQuery reports quota and rate limits as 403 Forbidden: a rerun later fixes them, IAM does not."""
    return any(_load_reasons(e) & _QUOTA_REASONS for e in chain)


def _uncommitted_is_bad_input(exc: BaseException) -> bool:
    """For a transaction that did NOT commit: True only for a genuine BadRequest (the statement itself was rejected), not
    BigQuery aborting one of two concurrent transactions. Everything else (network outages surfacing as RetryError,
    ConnectionError or timeouts, server errors, rate limits) is something a rerun can fix."""
    from google.api_core import exceptions as api

    chain = _chain(exc)
    if any("concurrent update" in str(e).lower() for e in chain) or _is_quota(chain):
        return False
    return any(isinstance(e, api.BadRequest) for e in chain)


def publish_script(staging: str) -> str:
    """The publish transaction (probe 2's statement with explicit column lists, like staging.publish_script). The DELETE's
    constant predicate is a pruning filter (serving.gold requires one) that matches every non-NULL row, incl. the
    __UNPARTITIONED__ pre-1960 rows; NULL rows cannot exist there (only GOLD-01-audited staging is ever inserted)."""
    live, columns = quoted(serving_gold_table_id()), ", ".join(quoted(c) for c in column_names("gold"))
    return (
        "BEGIN TRANSACTION;\n"  # noqa: S608 -- identifiers validated by table_id() and back-quoted; no values
        f"DELETE FROM {live} WHERE `index_stop` >= TIMESTAMP '0001-01-01';\n"
        f"INSERT INTO {live} ({columns})\n"
        f"  SELECT {columns} FROM {quoted(staging)};\n"
        "COMMIT TRANSACTION;\n"
    )


def _read_frame(client, table: str):
    """Every row of `table` via tabledata.list (reads all partitions, needs no partition filter, bills no query bytes)."""
    return client.list_rows(client.get_table(table)).to_arrow(create_bqstorage_client=False).to_pandas()


def _same(column: str, kind: str, a: object, b: object) -> bool:
    try:
        return canonical_token(column, kind, a) == canonical_token(column, kind, b)
    except ValueError:
        return False


def column_diagnostic(bq_frame, source_frame) -> list[str]:
    """Which columns differ between BigQuery's rows and the source Parquet, joined on encounter_id; cells compared by the
    fingerprint's canonical token. The last line names the kind: float_serialization (only age_at_index_years, all
    differences <= 1 ulp: a harness defect, assumption A1) or content."""
    merged = bq_frame.merge(source_frame, on="encounter_id", how="outer", suffixes=("_bq", "_src"), indicator=True)
    only_bq = int((merged["_merge"] == "left_only").sum())
    only_src = int((merged["_merge"] == "right_only").sum())
    both = merged[merged["_merge"] == "both"]
    lines = [f"only_in_bigquery={only_bq} only_in_source={only_src}"]
    listed: list[str] = []
    ulp_ok = True
    for column in sorted(GOLD_COLUMN_KINDS):
        if column == "encounter_id":
            continue
        kind = GOLD_COLUMN_KINDS[column]
        pairs = [
            (a, b)
            for a, b in zip(both[f"{column}_bq"].astype(object), both[f"{column}_src"].astype(object), strict=True)
            if not _same(column, kind, a, b)
        ]
        if not pairs:
            continue
        listed.append(column)
        if column != "age_at_index_years":
            lines.append(f"column={column} unequal={len(pairs)}")
            continue
        ulps, other = [], 0
        for a, b in pairs:
            try:
                x, y = np.float64(a), np.float64(b)
            except (TypeError, ValueError):
                other += 1
                continue
            if np.isfinite(x) and np.isfinite(y) and np.signbit(x) == np.signbit(y):
                ulps.append(abs(int(x.view(np.int64)) - int(y.view(np.int64))))
            else:
                other += 1
        max_ulp = max(ulps) if ulps else None
        lines.append(f"column={column} unequal={len(pairs)} max_ulp={'-' if max_ulp is None else max_ulp} other={other}")
        ulp_ok = other == 0 and max_ulp is not None and max_ulp <= 1
    floating = listed == ["age_at_index_years"] and ulp_ok and only_bq == 0 and only_src == 0
    lines.append(f"kind={'float_serialization' if floating else 'content'}")
    return lines


def _source_frame(wanted: list[tuple[str, object]]):
    import pyarrow.parquet as pq

    with tempfile.TemporaryDirectory(prefix="gold-diag-") as tmp:
        for name, blob in wanted:
            if name.endswith(".parquet"):
                blob.download_to_filename(str(Path(tmp) / name))
        return pq.read_table(tmp).to_pandas()


def _diagnose(bq_frame, wanted) -> list[str]:
    try:
        return column_diagnostic(bq_frame, _source_frame(wanted))
    except Exception as exc:  # noqa: BLE001 -- a diagnostic never changes the status
        return [f"diagnostic unavailable: {type(exc).__name__}: {exc}"]


def _schema_matches(table) -> bool:
    normal = {"FLOAT64": "FLOAT", "INT64": "INTEGER", "BOOL": "BOOLEAN"}
    got = [(f.name, normal.get(f.field_type, f.field_type), f.mode) for f in table.schema]
    return got == [(f["name"], f["type"], f["mode"]) for f in load_schema("gold")]


def _partitioning_matches(table) -> bool:
    tp = table.time_partitioning
    return (
        tp is not None
        and tp.type_ == "MONTH"
        and tp.field == "index_stop"
        # the TABLE-level property Terraform sets (time_partitioning.require_partition_filter is deprecated and None here)
        and table.require_partition_filter is True
        and list(table.clustering_fields or []) == ["admission_reason_code"]
    )


def _job_bytes(client, job_id: str) -> int | None:
    try:
        return getattr(client.get_job(job_id), "total_bytes_billed", None)
    except Exception:  # noqa: BLE001 -- bytes are informational
        return None


def _inputs(storage, request: GoldPublishRequest, state: dict):
    """Step 1 (E2: every ValueError/FileNotFoundError here is a refusal). Returns (gold_uri, wanted, metadata, champion_fp)."""
    name = gold_name(request.gold_uri)
    if name is None:
        raise ValueError(f"gold URI must be {GOLD_URI_PREFIX}<name> with <name> matching [a-z0-9_]{{1,64}}; got {request.gold_uri!r}")
    gold_uri = GOLD_URI_PREFIX + name
    wanted = list_gold_objects(storage, gold_uri)
    if GOLD_METADATA_FILENAME not in {n for n, _ in wanted}:
        raise FileNotFoundError(f"no {GOLD_METADATA_FILENAME} under {gold_uri}")
    metadata_uri = f"{gold_uri}/{GOLD_METADATA_FILENAME}"
    metadata = parse_gold_metadata(read_file(gold_uri, GOLD_METADATA_FILENAME, client=storage).decode("utf-8"), metadata_uri)
    state["metadata"] = {
        k: getattr(metadata, k)
        for k in ("gold_fingerprint", "gold_fingerprint_definition", "n_rows", "n_positive", "reference_date", "label_definition")
    }
    if metadata.gold_fingerprint_definition != FINGERPRINT_DEFINITION_ROWHASH_V1:
        raise ValueError(f"only {FINGERPRINT_DEFINITION_ROWHASH_V1} gold is publishable; got {metadata.gold_fingerprint_definition!r}")
    if metadata.label_definition != LABEL_DEFINITION:
        raise ValueError(f"label definition {metadata.label_definition!r} is not {LABEL_DEFINITION!r}")
    if metadata.gold_fingerprint != request.expected_fingerprint:
        raise ValueError(
            f"gold fingerprint {metadata.gold_fingerprint} is not the one serving.gold is defined for "
            f"({request.expected_fingerprint}; warehouse.bigquery.SERVING_GOLD_FINGERPRINT and infra/bigquery.tf)"
        )
    champion = read_champion(request.champion_path)
    manifest_bytes = read_file(champion.bundle_uri, "manifest.json", client=storage)
    if sha256_hex(manifest_bytes) != champion.sha256:
        raise ValueError(f"the champion manifest at {champion.bundle_uri} does not match {request.champion_path}'s sha256")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"the champion manifest at {champion.bundle_uri} is not UTF-8 JSON: {exc}") from exc
    champion_fp = manifest.get("gold_fingerprint") if isinstance(manifest, dict) else None
    if not isinstance(champion_fp, str) or not HEX64_RE.fullmatch(champion_fp):
        raise ValueError(f"the champion manifest has no 64-hex gold_fingerprint; got {champion_fp!r}")
    state["champion_gold_fingerprint"] = champion_fp
    if champion_fp != metadata.gold_fingerprint:
        raise ValueError(f"the champion was trained on gold {champion_fp}, not on this gold ({metadata.gold_fingerprint})")
    return gold_uri, wanted, metadata, champion_fp


def _load(client, gold_uri: str, wanted, staging: str, publish_id: str, state: dict) -> None:
    """Step 2 (E3): the Parquet parts, in name order, into a new staging table with gold.json's schema."""
    from google.cloud import bigquery

    state["load_job_id"] = f"gold-load-{publish_id}"
    load = client.load_table_from_uri(
        sorted(f"{gold_uri}/{n}" for n, _ in wanted if n.endswith(".parquet")),
        staging,
        job_id=state["load_job_id"],
        job_config=bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            schema=to_bq_schema(load_schema("gold")),
            write_disposition=bigquery.WriteDisposition.WRITE_EMPTY,
            create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        ),
    )
    load.result()


def _audit(client, staging: str, metadata, champion_fp: str, publish_id: str, state: dict) -> None:
    """Step 3 (E4): GOLD-01..07 on the staging table; raises _Stop("audit_failed") on a failing assertion."""
    audit_job_id = f"gold-audit-{publish_id}"
    state["data_tests"] = run_data_tests(
        client,
        "gold",
        table=staging,
        predictions_table=table_id(SERVING_DATASET, "predictions"),
        n_rows=metadata.n_rows,
        n_positive=metadata.n_positive,
        predicted_fingerprint=champion_fp,
        max_bytes_billed=GOLD_PUBLISH_MAX_BYTES_BILLED,
        job_id=audit_job_id,
    )
    state["bytes_billed"]["audit"] = _job_bytes(client, audit_job_id)
    if not state["data_tests"].passed:
        raise _Stop("audit_failed", f"not published: {state['data_tests'].failure}")


def _audit_fingerprint(client, staging: str, metadata, wanted, state: dict) -> None:
    """Step 3, second half (E5): the staging rows' content fingerprint must equal the metadata's."""
    frame = _read_frame(client, staging)
    state["staging_fingerprint"] = rowhash_fingerprint(frame)
    if state["staging_fingerprint"] != metadata.gold_fingerprint:
        state["diagnostic"] = _diagnose(frame, wanted)
        raise _Stop(
            "content_mismatch",
            f"not published: the staging rows' fingerprint {state['staging_fingerprint']} is not the metadata's "
            f"{metadata.gold_fingerprint}",
        )


def _publish(client, staging: str, publish_id: str, state: dict) -> None:
    """Step 5: ONE transaction replaces serving.gold. On any client-side error the job's real outcome decides first
    (resolve_job): committed -> continue to verify; not committed -> permission_denied (E1), refused only for a genuine
    BadRequest that is not a concurrent-update abort or a quota error, otherwise unknown (rerun); unobservable -> unknown."""
    state["publish_job_id"] = f"gold-publish-{publish_id}"
    try:
        job = client.query(
            publish_script(staging),
            job_config=query_config(max_bytes_billed=GOLD_PUBLISH_MAX_BYTES_BILLED),
            job_id=state["publish_job_id"],
            job_retry=None,
        )
        job.result()  # no client timeout
    except Exception as exc:
        landed = resolve_job(client, state["publish_job_id"])
        if landed is None:
            raise _Stop("unknown", f"publish outcome unknown: check job {state['publish_job_id']} ({exc}); rerun") from exc
        if landed is False:
            if classify(exc, "publish") == "permission_denied":
                raise _Stop("permission_denied", _message_for("permission_denied", "publish", exc)) from exc
            if not _uncommitted_is_bad_input(exc):
                raise _Stop(
                    "unknown",
                    f"not published: job {state['publish_job_id']} did not commit ({exc}); rerun (the publish is idempotent)",
                ) from exc
            raise _Stop("refused", f"not published: the transaction did not commit: {exc}") from exc
        state["bytes_billed"]["publish"] = _job_bytes(client, state["publish_job_id"])
        return
    state["bytes_billed"]["publish"] = getattr(job, "total_bytes_billed", None)


def _verify(client, metadata, wanted, state: dict) -> None:
    """Step 6 (E5b): serving.gold now holds exactly the audited rows; raises _Stop("verify_failed") naming the checks."""
    serving = serving_gold_table_id()
    table = client.get_table(serving)
    state["serving_num_rows"] = table.num_rows
    state["schema_matches"] = _schema_matches(table)
    state["partitioning_matches"] = _partitioning_matches(table)
    state["description_names_fingerprint"] = metadata.gold_fingerprint in (table.description or "")
    frame = _read_frame(client, serving)
    state["serving_fingerprint"] = rowhash_fingerprint(frame)
    failed = [
        check
        for check, ok in (
            ("serving_num_rows", state["serving_num_rows"] == metadata.n_rows),
            ("schema_matches", state["schema_matches"]),
            ("partitioning_matches", state["partitioning_matches"]),
            ("description_names_fingerprint", state["description_names_fingerprint"]),
            ("serving_fingerprint", state["serving_fingerprint"] == metadata.gold_fingerprint),
        )
        if not ok
    ]
    if failed:
        if state["serving_fingerprint"] != metadata.gold_fingerprint:
            state["diagnostic"] = _diagnose(frame, wanted)
        raise _Stop("verify_failed", f"published, but verify failed: {', '.join(failed)}")


def publish_gold(client, storage, request: GoldPublishRequest, *, publish_id: str | None = None) -> GoldPublishResult:
    """Load -> audit -> one transaction -> verify. Never raises for a classified failure: returns a result whose status
    maps to an exit code (EXIT_CODES). ValueError before any I/O for a malformed publish_id (a programming error)."""
    if publish_id is None:
        publish_id = uuid.uuid4().hex[:12]
    elif not isinstance(publish_id, str) or not PUBLISH_ID_RE.fullmatch(publish_id):
        raise ValueError(f"publish_id must be 12 lowercase hex characters; got {publish_id!r}")

    state: dict = {"bytes_billed": {"audit": None, "publish": None}, "diagnostic": []}
    staging = staging_table_id(publish_id)
    notes: list[str] = []
    step = "inputs"

    def finish(status: str, message: str) -> GoldPublishResult:
        if notes:
            message = f"{message} ({'; '.join(notes)})"
        return GoldPublishResult(status=status, publish_id=publish_id, gold_uri=request.gold_uri, message=message, **state)

    try:
        gold_uri, wanted, metadata, champion_fp = _inputs(storage, request, state)
        step = "load"
        _load(client, gold_uri, wanted, staging, publish_id, state)
        step = "audit"
        _audit(client, staging, metadata, champion_fp, publish_id, state)
        step = "audit_fingerprint"
        _audit_fingerprint(client, staging, metadata, wanted, state)
        if not request.dry_run:
            step = "publish"
            _publish(client, staging, publish_id, state)
            step = "verify"
            _verify(client, metadata, wanted, state)
        if request.dry_run:
            outcome = ("dry_run_passed", "dry run: audit passed; serving.gold untouched")
        else:
            outcome = (
                "published",
                f"published {metadata.n_rows} rows into {serving_gold_table_id()} (fingerprint {metadata.gold_fingerprint})",
            )
    except _Stop as stop:
        outcome = (stop.status, stop.message)
    except Exception as exc:  # noqa: BLE001 -- every failure maps to a status (table E1-E6)
        status = classify(exc, step)
        message = _message_for(status, step, exc)
        if step == "verify":  # the transaction has already committed: an exit 5 here must not read as "nothing published"
            message = (
                f"the transaction committed (job {state.get('publish_job_id')}); serving.gold may already hold the new rows: "
                f"{message}"
            )
        outcome = (status, message)
    # dropped BEFORE the result is built, so a failed drop is reported on every path (the leftover holds labelled rows);
    # skipped when the run stopped at its inputs: no load job was started, so no staging table can exist
    if step != "inputs":
        try:
            client.delete_table(staging, not_found_ok=True)
        except Exception as exc:  # noqa: BLE001 -- the dataset's 1-day expiry removes a leftover
            notes.append(f"staging drop failed ({exc}); it expires within 1 day")
        if step == "load" and outcome[0] == "unknown":
            # the client stopped waiting but the load job may still finish server-side and create the table after the drop
            notes.append(f"load job {state.get('load_job_id')} outcome unknown: {staging} may appear later; it expires within 1 day")
    return finish(*outcome)


def failed_result(request: GoldPublishRequest, publish_id: str, *, status: str, message: str) -> GoldPublishResult:
    """The CLI's result for a failure before publish_gold ran (e.g. no credentials)."""
    return GoldPublishResult(status=status, publish_id=publish_id, gold_uri=request.gold_uri, message=message)


def report_path(result: GoldPublishResult, root: Path = Path("data/checks")) -> Path:
    name = gold_name(result.gold_uri)
    if name is None:
        return root / "_gold_publish_refused" / f"{result.publish_id}.json"
    return root / name / f"gold_bigquery_{result.publish_id}.json"


def _git_head() -> str | None:
    """As warehouse.run._git_state's commit (that module imports the scorer)."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return os.environ.get("GITHUB_SHA") or None
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def write_report(result: GoldPublishResult, path: Path) -> None:
    document = dataclasses.asdict(result)
    document["checked_at"] = datetime.now(UTC).isoformat()
    document["git_head"] = _git_head()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True, default=str) + "\n")


def summary_lines(result: GoldPublishResult, report: object = None) -> list[str]:
    """The CLI's output, in a fixed order; '-' for a step that did not run."""

    def show(value: object) -> str:
        return "-" if value is None else str(value)

    tests = result.data_tests
    rows = [
        ("status", result.status),
        ("publish_id", result.publish_id),
        ("gold_uri", result.gold_uri),
        ("metadata gold_fingerprint", result.metadata.get("gold_fingerprint")),
        ("champion gold_fingerprint", result.champion_gold_fingerprint),
        ("load_job_id", result.load_job_id),
        ("data_tests", None if tests is None else ("passed" if tests.passed else tests.failure)),
        ("staging_fingerprint", result.staging_fingerprint),
        ("publish_job_id", result.publish_job_id),
        ("serving_num_rows", result.serving_num_rows),
        ("schema_matches", result.schema_matches),
        ("partitioning_matches", result.partitioning_matches),
        ("description_names_fingerprint", result.description_names_fingerprint),
        ("serving_fingerprint", result.serving_fingerprint),
        ("bytes_billed audit", result.bytes_billed.get("audit")),
        ("bytes_billed publish", result.bytes_billed.get("publish")),
    ]
    lines = [f"{label}: {show(value)}" for label, value in rows]
    lines.append(f"message: {result.message}")
    lines.extend(result.diagnostic)
    if report is not None:
        lines.append(f"report: {report}")
    return lines
