"""The gold publish (v3 slice 1): load -> audit -> one transaction -> verify, every failure's status (table E1-E6), the
content-mismatch diagnostic, the report and the CLI. Fake GCS and fake BigQuery only (tests/warehouse/gold_fixture.py)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import requests
from google.api_core import exceptions as api
from google.auth.exceptions import DefaultCredentialsError, RefreshError, TransportError
from typer.testing import CliRunner

from readmission_risk.bundles.gcs import read_file
from readmission_risk.warehouse import gold
from readmission_risk.warehouse.bigquery import (
    GOLD_PUBLISH_MAX_BYTES_BILLED,
    SERVING_GOLD_FINGERPRINT,
    column_names,
)
from readmission_risk.warehouse.gold import (
    EXIT_CODES,
    GoldPublishRequest,
    classify,
    column_diagnostic,
    failed_result,
    gold_name,
    publish_gold,
    publish_script,
    report_path,
    summary_lines,
    write_report,
)
from tests.warehouse.fake_bigquery import FakeJob, assertion_error
from tests.warehouse.gold_fixture import (
    GOLD_BUCKET,
    GOLD_URI,
    NAME,
    PUBLISH_ID,
    as_bigquery_returns,
    build_world,
    gold_frame,
    parquet_bytes,
)

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
SERVING = "synthea-readmission-risk.serving.gold"


def _publish(world, **request):
    return publish_gold(world.bq, world.storage, world.request(**request), publish_id=PUBLISH_ID)


def _set_cell(column: str, row: int, value):
    def mutate(arrow: pa.Table) -> pa.Table:
        values = arrow.column(column).to_pylist()
        values[row] = value
        index = arrow.column_names.index(column)
        return arrow.set_column(index, column, pa.array(values, type=arrow.schema.field(column).type))

    return mutate


# ---------------------------------------------------------------------------------------------------------- happy path
def test_publish_loads_audits_publishes_and_verifies(tmp_path):
    world = build_world(tmp_path)
    result = _publish(world)
    assert result.status == "published" and EXIT_CODES[result.status] == 0
    assert result.staging_fingerprint == result.serving_fingerprint == world.fingerprint
    assert result.champion_gold_fingerprint == world.fingerprint
    assert result.serving_num_rows == 6
    assert result.schema_matches and result.partitioning_matches and result.description_names_fingerprint
    assert result.data_tests.passed
    assert result.metadata == {
        "gold_fingerprint": world.fingerprint,
        "gold_fingerprint_definition": "spark-rowhash-v1",
        "n_rows": 6,
        "n_positive": 2,
        "reference_date": "20260916",
        "label_definition": "unplanned_readmission_v1",
    }
    assert result.bytes_billed == {"audit": None, "publish": 20971520}
    # the load: exactly the Parquet parts, a new staging table in scratch_gold, gold.json's schema, never into serving
    (load,) = [c for c in world.bq.calls if c[0] == "load_uri"]
    _, uris, destination, config, job_id = load
    assert uris == [f"{GOLD_URI}/part-00000.parquet"]
    assert destination == world.staging_name() and job_id == f"gold-load-{PUBLISH_ID}"
    assert [f.name for f in config.schema] == column_names("gold")
    assert config.source_format == "PARQUET" and config.write_disposition == "WRITE_EMPTY"
    # the audit ran on staging with a fixed job id; the transaction came after it; staging was dropped
    audit, transaction = world.queries()
    assert "GOLD-07" in audit and world.staging_name() in audit and "`synthea-readmission-risk.serving.predictions`" in audit
    assert transaction == publish_script(world.staging_name())
    audit_call = next(c for c in world.bq.calls if c[0] == "query" and c[1] == audit)
    assert audit_call[3] == f"gold-audit-{PUBLISH_ID}" and audit_call[2].maximum_bytes_billed == GOLD_PUBLISH_MAX_BYTES_BILLED
    publish_call = next(c for c in world.bq.calls if c[0] == "query" and c[1] == transaction)
    assert publish_call[3] == f"gold-publish-{PUBLISH_ID}" and publish_call[2].maximum_bytes_billed == GOLD_PUBLISH_MAX_BYTES_BILLED
    assert ("delete_table", world.staging_name(), True) in world.bq.calls


def test_publish_script_is_one_transaction_with_a_constant_partition_predicate():
    script = publish_script("synthea-readmission-risk.scratch_gold.gold_staging_abcdef012345")
    columns = ", ".join(f"`{c}`" for c in column_names("gold"))
    assert script == (
        "BEGIN TRANSACTION;\n"  # noqa: S608 -- the expected text of a static template
        f"DELETE FROM `{SERVING}` WHERE `index_stop` >= TIMESTAMP '0001-01-01';\n"
        f"INSERT INTO `{SERVING}` ({columns})\n"
        f"  SELECT {columns} FROM `synthea-readmission-risk.scratch_gold.gold_staging_abcdef012345`;\n"
        "COMMIT TRANSACTION;\n"
    )


def test_dry_run_audits_but_never_publishes(tmp_path):
    world = build_world(tmp_path)
    result = _publish(world, dry_run=True)
    assert result.status == "dry_run_passed" and EXIT_CODES[result.status] == 0
    assert not any(q.startswith("BEGIN TRANSACTION") for q in world.queries())
    assert result.staging_fingerprint == world.fingerprint and result.serving_fingerprint is None


def test_publish_id_is_generated_or_validated(tmp_path):
    world = build_world(tmp_path)
    result = publish_gold(world.bq, world.storage, world.request(dry_run=True))
    assert gold.PUBLISH_ID_RE.fullmatch(result.publish_id)
    calls = len(world.bq.calls)
    with pytest.raises(ValueError, match="12 lowercase hex"):
        publish_gold(world.bq, world.storage, world.request(), publish_id="NOT-HEX")
    assert len(world.bq.calls) == calls  # refused before any I/O


# ------------------------------------------------------------------------------------------------ step 1: refusals (E2)
@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda w: {"gold_uri": "gs://other-bucket/v2/fixture"}, "gold URI must be"),
        (lambda w: {"gold_uri": "gs://synthea-readmission-risk-gold/v2/Fixture"}, "gold URI must be"),
        (lambda w: {"gold_uri": "gs://synthea-readmission-risk-gold/full_run_2b"}, "gold URI must be"),
        (lambda w: {"expected_fingerprint": "0" * 64}, "is not the one serving.gold is defined for"),
    ],
)
def test_request_refusals(tmp_path, change, message):
    world = build_world(tmp_path)
    result = _publish(world, **change(world))
    assert result.status == "refused" and EXIT_CODES["refused"] == 1, result.message
    assert message in result.message
    assert not [c for c in world.bq.calls if c[0] in ("load_uri", "query")]


def test_refuses_v1_gold_and_another_label_definition(tmp_path):
    v1 = build_world(tmp_path / "v1", metadata_version=1)
    assert _publish(v1).status == "refused" and "only spark-rowhash-v1 gold" in _publish(v1).message
    other = build_world(tmp_path / "label", label_definition="all_cause_v0")
    result = _publish(other)
    assert result.status == "refused" and "label definition 'all_cause_v0'" in result.message


def test_refuses_champion_mismatches(tmp_path):
    world = build_world(tmp_path)
    world.put_manifest({"gold_fingerprint": "1" * 64})
    assert "the champion was trained on gold 1111" in _publish(world).message
    world.put_manifest({"bundle_id": "x"})
    result = _publish(world)
    assert result.status == "refused" and "no 64-hex gold_fingerprint" in result.message
    world.put_manifest({"gold_fingerprint": "XYZ"})
    assert "no 64-hex gold_fingerprint" in _publish(world).message
    world.put_manifest(b"\xff not json")
    assert "is not UTF-8 JSON" in _publish(world).message
    world.put_manifest({"gold_fingerprint": world.fingerprint})
    world.storage.put("synthea-readmission-risk-bundles", "bundle1/manifest.json", b'{"gold_fingerprint": "tampered"}')
    result = _publish(world)
    assert result.status == "refused" and "does not match" in result.message


def test_refuses_layout_problems_and_missing_metadata(tmp_path):
    world = build_world(tmp_path)
    world.storage.put(GOLD_BUCKET, f"v2/{NAME}/notes.txt", b"x")
    assert "unexpected object 'notes.txt'" in _publish(world).message
    del world.storage.buckets[GOLD_BUCKET][f"v2/{NAME}/notes.txt"]
    del world.storage.buckets[GOLD_BUCKET][f"v2/{NAME}/_gold_metadata.json"]
    result = _publish(world)
    assert result.status == "refused" and "no _gold_metadata.json" in result.message
    empty = build_world(tmp_path / "empty")
    assert _publish(empty, gold_uri="gs://synthea-readmission-risk-gold/v2/nothing_here").status == "refused"


def test_metadata_vanishing_between_listing_and_download_is_refused(tmp_path, monkeypatch):
    world = build_world(tmp_path)
    real = gold.read_file

    def vanish(uri, name, *, client=None):
        if name == "_gold_metadata.json":
            raise FileNotFoundError(f"bundle at {uri} has no {name}")
        return real(uri, name, client=client)

    monkeypatch.setattr(gold, "read_file", vanish)
    assert _publish(world).status == "refused"


# ---------------------------------------------------------------------------------- permissions and errors (E1, E6)
def test_forbidden_storage_is_permission_denied_with_the_iam_hint(tmp_path):
    world = build_world(tmp_path)
    world.storage.forbidden = True
    result = _publish(world)
    assert result.status == "permission_denied" and EXIT_CODES["permission_denied"] == 5
    assert "IAM may still be propagating" in result.message


def test_a_refresh_error_is_permission_denied_also_when_read_file_remapped_it(tmp_path):
    world = build_world(tmp_path)
    world.storage.auth_error = RefreshError("token refresh failed")
    assert _publish(world).status == "permission_denied"  # at the listing: unwrapped
    # through bundles.gcs.read_file, which turns it into a plain OSError raised from the RefreshError
    with pytest.raises(OSError) as wrapped:
        read_file(GOLD_URI, "_gold_metadata.json", client=world.storage)
    assert type(wrapped.value) is OSError
    assert classify(wrapped.value, "inputs") == "permission_denied"


def test_load_errors(tmp_path):
    world = build_world(tmp_path)
    world.bq.on_load_uri = lambda *a: FakeJob(raise_on_result=api.BadRequest("Provided Schema does not match", errors=[{"reason": "invalid"}]))
    assert _publish(world).status == "refused"
    world.bq.on_load_uri = lambda *a: FakeJob(raise_on_result=api.Forbidden("403 bigquery.tables.create denied"))
    assert _publish(world).status == "permission_denied"
    world.bq.on_load_uri = lambda *a: FakeJob(raise_on_result=api.ServiceUnavailable("503 backend error"))
    result = _publish(world)
    assert result.status == "unknown" and "rerun (the publish is idempotent)" in result.message
    assert all(c[1] == world.staging_name() for c in world.bq.calls if c[0] == "delete_table")


@pytest.mark.parametrize(
    ("exc", "step", "status"),
    [
        (api.Forbidden("403"), "audit", "permission_denied"),  # E1
        (PermissionError("denied"), "inputs", "permission_denied"),
        (RefreshError("refresh"), "verify", "permission_denied"),
        (DefaultCredentialsError("no ADC"), "load", "permission_denied"),
        (ValueError("bad layout"), "inputs", "refused"),  # E2
        (FileNotFoundError("missing"), "inputs", "refused"),
        (api.NotFound("404"), "inputs", "refused"),
        (api.BadRequest("schema"), "load", "refused"),  # E3
        (ValueError("holds a bool"), "audit_fingerprint", "content_mismatch"),  # E5
        (ValueError("holds a bool"), "verify", "verify_failed"),  # E5b
        (api.ServiceUnavailable("503"), "inputs", "unknown"),  # E6
        (TransportError("connection reset"), "audit", "unknown"),
        (OSError("cannot read manifest.json of bundle"), "inputs", "unknown"),
        (api.InternalServerError("500"), "load", "unknown"),
        (ValueError("x"), "audit", "unknown"),
        (api.Forbidden("403 Exceeded rate limits", errors=[{"reason": "rateLimitExceeded"}]), "load", "unknown"),  # not IAM
    ],
)
def test_classification_table(exc, step, status):
    assert classify(exc, step) == status


# ------------------------------------------------------------------------------------------------- step 3: the audit
def test_a_failing_assertion_is_audit_failed_and_nothing_is_published(tmp_path):
    world = build_world(tmp_path)

    def fail(sql):
        raise assertion_error("GOLD-02: the row count differs from the gold metadata")

    world.audit = fail
    result = _publish(world)
    assert result.status == "audit_failed" and EXIT_CODES["audit_failed"] == 3
    assert result.data_tests.failure == "GOLD-02: the row count differs from the gold metadata"
    assert not any(q.startswith("BEGIN TRANSACTION") for q in world.queries())
    assert ("delete_table", world.staging_name(), True) in world.bq.calls


def test_a_changed_cell_is_content_mismatch_with_a_diagnostic(tmp_path):
    world = build_world(tmp_path)
    world.mutate_staging = _set_cell("gender", 2, "X")
    result = _publish(world)
    assert result.status == "content_mismatch" and "not published" in result.message
    assert result.diagnostic == ["only_in_bigquery=0 only_in_source=0", "column=gender unequal=1", "kind=content"]
    assert not any(q.startswith("BEGIN TRANSACTION") for q in world.queries())


def test_a_one_ulp_float_difference_is_classified_float_serialization(tmp_path):
    world = build_world(tmp_path)
    world.mutate_staging = _set_cell("age_at_index_years", 1, float(np.nextafter(70.5, 100)))
    result = _publish(world)
    assert result.status == "content_mismatch"  # never relaxed: the download would be changed instead
    assert result.diagnostic[-2:] == ["column=age_at_index_years unequal=1 max_ulp=1 other=0", "kind=float_serialization"]


def test_an_unexpected_cell_type_is_content_mismatch(tmp_path):
    world = build_world(tmp_path)

    def to_bool(arrow):
        index = arrow.column_names.index("is_readmitted")
        return arrow.set_column(index, "is_readmitted", pa.array([True, True, False, False, False, False]))

    world.mutate_staging = to_bool
    result = _publish(world)
    assert result.status == "content_mismatch" and "holds a bool" in result.message


def test_the_diagnostic_is_dtype_and_time_zone_proof_and_never_changes_the_status(tmp_path, monkeypatch):
    frame = build_world(tmp_path).frame
    bq = as_bigquery_returns(pa.Table.from_pandas(frame, preserve_index=False)).to_pandas()
    assert column_diagnostic(bq, frame) == ["only_in_bigquery=0 only_in_source=0", "kind=content"]
    missing = column_diagnostic(bq.iloc[1:], frame)
    assert missing[0] == "only_in_bigquery=0 only_in_source=1"
    world = build_world(tmp_path / "w")
    world.mutate_staging = _set_cell("gender", 0, "X")
    monkeypatch.setattr(gold, "_source_frame", lambda wanted: (_ for _ in ()).throw(OSError("download failed")))
    result = _publish(world)
    assert result.status == "content_mismatch"
    assert result.diagnostic == ["diagnostic unavailable: OSError: download failed"]


# ----------------------------------------------------------------------------------------------- step 5: the publish
def test_a_committed_transaction_whose_result_raised_continues_to_verify(tmp_path, monkeypatch):
    world = build_world(tmp_path)
    world.publish = lambda: FakeJob(raise_on_result=api.Forbidden("403 while polling"))
    monkeypatch.setattr(gold, "resolve_job", lambda client, job_id: True)
    result = _publish(world)
    assert result.status == "published" and result.serving_fingerprint == world.fingerprint


@pytest.mark.parametrize(
    ("error", "landed", "status"),
    [
        (api.BadRequest("Query error: column count mismatch"), False, "refused"),
        (api.Forbidden("403 updateData denied"), False, "permission_denied"),
        # a transient cause did not commit: "unknown" (rerun), never "refused" (exit 1 means bad input)
        (api.ServiceUnavailable("503"), False, "unknown"),
        (api.BadRequest("Transaction is aborted due to concurrent update against table serving.gold"), False, "unknown"),
        # round 2: a network outage surfaces as RetryError(cause=ConnectionError); quota is a 403 but not a permission
        (api.RetryError("Deadline of 600s exceeded", cause=requests.exceptions.ConnectionError("reset")), False, "unknown"),
        (api.Forbidden("403 Quota exceeded", errors=[{"reason": "quotaExceeded"}]), False, "unknown"),
        (api.ServiceUnavailable("503"), None, "unknown"),
    ],
)
def test_an_uncommitted_or_unknown_transaction(tmp_path, monkeypatch, error, landed, status):
    world = build_world(tmp_path)
    world.copy_on_publish = False
    world.publish = lambda: FakeJob(raise_on_result=error)
    monkeypatch.setattr(gold, "resolve_job", lambda client, job_id: landed)
    result = _publish(world)
    assert result.status == status
    if status == "unknown":
        assert f"gold-publish-{PUBLISH_ID}" in result.message


# ------------------------------------------------------------------------------------------------ step 6: the verify
@pytest.mark.parametrize(
    ("overrides", "check"),
    [
        ({"require_partition_filter": False}, "partitioning_matches"),
        ({"clustering_fields": ["encounter_id"]}, "partitioning_matches"),
        ({"description": "no fingerprint here"}, "description_names_fingerprint"),
    ],
)
def test_verify_failures_after_the_commit(tmp_path, overrides, check):
    world = build_world(tmp_path)
    world.serving_overrides = overrides
    result = _publish(world)
    assert result.status == "verify_failed" and EXIT_CODES["verify_failed"] == 4
    assert check in result.message and "published, but verify failed" in result.message


def test_verify_compares_rows_count_and_content(tmp_path):
    world = build_world(tmp_path)
    world.mutate_serving = lambda arrow: arrow.slice(1)
    result = _publish(world)
    assert result.status == "verify_failed"
    assert "serving_num_rows" in result.message and "serving_fingerprint" in result.message
    assert result.diagnostic[0] == "only_in_bigquery=0 only_in_source=1"


def test_a_fingerprint_value_error_at_verify_is_verify_failed(tmp_path):
    world = build_world(tmp_path)

    def to_bool(arrow):
        index = arrow.column_names.index("is_readmitted")
        return arrow.set_column(index, "is_readmitted", pa.array([True, True, False, False, False, False]))

    world.mutate_serving = to_bool
    result = _publish(world)
    assert result.status == "verify_failed" and "holds a bool" in result.message


def test_a_non_check_failure_at_verify_says_the_transaction_committed(tmp_path):
    """Exit 5 at verify must not read as "nothing published": the transaction already committed (round-4 review)."""
    world = build_world(tmp_path)

    def commit_then_lose_credentials():
        world.bq.raise_on_get_table = RefreshError("token refresh failed")
        return FakeJob()

    world.publish = commit_then_lose_credentials
    result = _publish(world)
    assert result.status == "permission_denied" and EXIT_CODES[result.status] == 5
    assert result.message.startswith(f"the transaction committed (job gold-publish-{PUBLISH_ID}); serving.gold may already hold")


def test_a_failing_staging_drop_is_noted_not_fatal(tmp_path):
    world = build_world(tmp_path)
    world.bq.raise_on_delete = api.ServiceUnavailable("503")
    result = _publish(world)
    assert result.status == "published" and "staging drop failed" in result.message


def test_no_staging_drop_when_the_run_stopped_at_its_inputs(tmp_path):
    """No load job ran, so no staging table can exist: no drop call and no false 'staging drop failed' note (round 2)."""
    world = build_world(tmp_path)
    world.bq.raise_on_delete = api.Forbidden("403")
    world.storage.auth_error = RefreshError("token refresh failed")
    result = _publish(world)
    assert result.status == "permission_denied" and "staging drop failed" not in result.message
    assert "delete_table" not in world.bq.kinds()


def test_a_failing_staging_drop_is_noted_also_when_the_publish_failed(tmp_path):
    """The leftover staging table holds labelled rows: a failed run must say so too (round-1 review finding)."""
    world = build_world(tmp_path)
    world.bq.raise_on_delete = api.ServiceUnavailable("503")

    def fail(sql):
        raise assertion_error("GOLD-02: the row count differs from the gold metadata")

    world.audit = fail
    result = _publish(world)
    assert result.status == "audit_failed" and "staging drop failed" in result.message
    world.audit = lambda sql: FakeJob()
    world.bq.on_load_uri = lambda *a: FakeJob(raise_on_result=api.ServiceUnavailable("503"))
    result = _publish(world)
    assert result.status == "unknown" and "staging drop failed" in result.message


def test_an_unknown_load_says_its_staging_table_may_appear_later(tmp_path):
    """The client stopped waiting; the server-side load may still create the table after the drop (round-3 review)."""
    world = build_world(tmp_path)
    world.bq.on_load_uri = lambda *a: FakeJob(raise_on_result=api.ServiceUnavailable("503 while polling"))
    result = _publish(world)
    assert result.status == "unknown"
    assert f"load job gold-load-{PUBLISH_ID} outcome unknown: {world.staging_name()} may appear later" in result.message


# -------------------------------------------------------------------------------------------------- report and output
def test_report_paths_and_round_trip(tmp_path):
    world = build_world(tmp_path)
    result = _publish(world)
    path = report_path(result, tmp_path / "checks")
    assert path == tmp_path / "checks" / NAME / f"gold_bigquery_{PUBLISH_ID}.json"
    write_report(result, path)
    document = json.loads(path.read_text())
    assert document["status"] == "published" and document["data_tests"]["passed"] is True
    assert {"checked_at", "git_head"} <= set(document)
    refused = failed_result(GoldPublishRequest(gold_uri="not a uri"), PUBLISH_ID, status="refused", message="x")
    assert report_path(refused, tmp_path) == tmp_path / "_gold_publish_refused" / f"{PUBLISH_ID}.json"


def test_gold_name():
    assert gold_name("gs://synthea-readmission-risk-gold/v2/main_200k") == "main_200k"
    assert gold_name("gs://synthea-readmission-risk-gold/v2/main_200k/") == "main_200k"
    for bad in ("gs://synthea-readmission-risk-gold/v2/", "gs://synthea-readmission-risk-gold/v2/a/b", "gs://x/v2/a", None):
        assert gold_name(bad) is None


def test_summary_lines_have_a_fixed_order(tmp_path):
    world = build_world(tmp_path)
    lines = summary_lines(_publish(world, dry_run=True), Path("r.json"))
    assert [line.split(":", 1)[0] for line in lines] == [
        "status", "publish_id", "gold_uri", "metadata gold_fingerprint", "champion gold_fingerprint", "load_job_id",
        "data_tests", "staging_fingerprint", "publish_job_id", "serving_num_rows", "schema_matches", "partitioning_matches",
        "description_names_fingerprint", "serving_fingerprint", "bytes_billed audit", "bytes_billed publish", "message", "report",
    ]  # fmt: skip
    assert "publish_job_id: -" in lines and "data_tests: passed" in lines


def test_production_constants():
    assert GoldPublishRequest(gold_uri=GOLD_URI).expected_fingerprint == SERVING_GOLD_FINGERPRINT
    assert gold.DEFAULT_GOLD_URI == "gs://synthea-readmission-risk-gold/v2/main_200k"


def test_parquet_fixture_is_spark_like():
    frame = gold_frame()
    schema = pq.read_schema(pa.BufferReader(parquet_bytes(frame)))
    assert schema.field("index_stop").type == pa.timestamp("ns") and schema.field("is_readmitted").type == pa.int32()
    assert pd.isna(frame.loc[1, "admission_reason_code"])


# ------------------------------------------------------------------------------------------------------------- the CLI
@pytest.fixture(scope="module")
def cli():
    spec = importlib.util.spec_from_file_location("publish_gold_cli", _SCRIPTS / "publish_gold_to_bigquery.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _invoke(cli, monkeypatch, tmp_path, world, *args):
    monkeypatch.setattr("readmission_risk.gcp.bigquery_client", lambda: world.bq)
    monkeypatch.setattr("readmission_risk.gcp.storage_client", lambda: world.storage)
    monkeypatch.setattr(cli, "REPORT_ROOT", tmp_path / "checks")
    monkeypatch.setattr(cli, "new_publish_id", lambda: PUBLISH_ID)
    return CliRunner().invoke(cli.app, ["--gold-uri", GOLD_URI, "--champion", str(world.champion_path), *args])


def test_cli_publishes_and_writes_the_report(cli, monkeypatch, tmp_path):
    world = build_world(tmp_path)
    monkeypatch.setattr(cli, "EXPECTED_FINGERPRINT", world.fingerprint)
    out = _invoke(cli, monkeypatch, tmp_path, world)
    assert out.exit_code == 0, out.output
    assert "status: published" in out.output
    assert (tmp_path / "checks" / NAME / f"gold_bigquery_{PUBLISH_ID}.json").is_file()


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        (lambda w: setattr(w.storage, "forbidden", True), 5),
        (lambda w: w.put_manifest({"gold_fingerprint": "1" * 64}), 1),
        (lambda w: setattr(w, "audit", lambda sql: (_ for _ in ()).throw(assertion_error("GOLD-04: encounter_id is not unique or is NULL"))), 3),
        (lambda w: setattr(w, "serving_overrides", {"description": "x"}), 4),
    ],
)
def test_cli_exit_codes(cli, monkeypatch, tmp_path, setup, code):
    world = build_world(tmp_path)
    setup(world)
    monkeypatch.setattr(cli, "EXPECTED_FINGERPRINT", world.fingerprint)
    out = _invoke(cli, monkeypatch, tmp_path, world)
    assert out.exit_code == code, out.output
    assert "report: " in out.output


def test_cli_missing_credentials_writes_a_permission_denied_report(cli, monkeypatch, tmp_path):
    world = build_world(tmp_path)

    def no_adc():
        raise FileNotFoundError("no Application Default Credentials: run gcloud auth application-default login")

    monkeypatch.setattr("readmission_risk.gcp.bigquery_client", no_adc)
    monkeypatch.setattr("readmission_risk.gcp.storage_client", lambda: world.storage)
    monkeypatch.setattr(cli, "REPORT_ROOT", tmp_path / "checks")
    monkeypatch.setattr(cli, "new_publish_id", lambda: PUBLISH_ID)
    out = CliRunner().invoke(cli.app, ["--gold-uri", GOLD_URI, "--champion", str(world.champion_path)])
    assert out.exit_code == 5 and "status: permission_denied" in out.output
    assert json.loads((tmp_path / "checks" / NAME / f"gold_bigquery_{PUBLISH_ID}.json").read_text())["status"] == "permission_denied"


def test_cli_keeps_the_exit_code_when_the_report_cannot_be_written(cli, monkeypatch, tmp_path):
    """A replaced serving.gold must not be reported as exit 1 because data/checks is unwritable (round-1 review finding)."""
    world = build_world(tmp_path)
    monkeypatch.setattr(cli, "EXPECTED_FINGERPRINT", world.fingerprint)

    def unwritable(result, path):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(cli, "write_report", unwritable)
    out = _invoke(cli, monkeypatch, tmp_path, world)
    assert out.exit_code == 0, out.output
    assert "status: published" in out.output and "report: <not written: read-only file system>" in out.output


def test_cli_malformed_service_account_is_a_usage_error(cli, monkeypatch, tmp_path):
    world = build_world(tmp_path)
    out = _invoke(cli, monkeypatch, tmp_path, world, "--impersonate-service-account", "not-an-email")
    assert out.exit_code == 2
    assert not (tmp_path / "checks").exists()
