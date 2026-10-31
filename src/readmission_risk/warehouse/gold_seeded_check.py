"""M5 for the GOLD data tests (v3 slice 1): every GOLD assertion is shown to fire. Each key gets a fresh pair of tables in
scratch_gold (a 10-row literal gold fixture and a 1-row literal predictions table), ONE targeted mutation that breaks
exactly that assertion while every earlier one still holds (a script stops at its first failing ASSERT), and a run of
gold.sql; the clean pair must pass. Tables are dropped right after their check. Run as gold-publisher (make
gold-seeded-check). See notes/eg-new-feature/v3-slice1-gold-bigquery-2026-10-31.md (Interfaces section 7).
"""

from __future__ import annotations

import contextlib
import uuid

from readmission_risk.pipeline.big_join import GOLD_TABLE_COLUMNS
from readmission_risk.pipeline.fingerprint import GOLD_COLUMN_KINDS

from .bigquery import (
    SCRATCH_GOLD_DATASET,
    load_schema,
    query_config,
    quoted,
    table_id,
    to_bq_schema,
)
from .data_tests import DataTestResult, assertion_descriptions, run_data_tests

SEED_FINGERPRINT = "seedfp"
N_ROWS, N_POSITIVE = 10, 2


def _fixture_row(i: int) -> dict:
    """Row i (1..10): seed-e01..seed-e10, positives seed-e01 and seed-e02, index_stop one per month of 2019."""
    values: dict = {}
    for column in GOLD_TABLE_COLUMNS:
        kind = GOLD_COLUMN_KINDS[column]
        if kind == "string":
            values[column] = f"seed-{column}-{i:02d}"
        elif kind == "int":
            values[column] = i
        elif kind == "double":
            values[column] = 40.0 + i
        else:
            values[column] = f"2019-{i:02d}-15 12:00:00+00"
    values["encounter_id"] = f"seed-e{i:02d}"
    values["patient_id"] = f"seed-p{i:02d}"
    values["is_readmitted"] = 1 if i <= 2 else 0
    values["index_start"] = f"2019-{i:02d}-10 08:00:00+00"
    values["index_stop"] = f"2019-{i:02d}-14 08:00:00+00"
    values["label_horizon"] = f"2019-{i:02d}-28 08:00:00+00"
    return values


FIXTURE_ROWS: tuple[dict, ...] = tuple(_fixture_row(i) for i in range(1, N_ROWS + 1))

# ID -> DML statements on a fresh pair: {t} the gold copy, {p} its predictions copy. Each breaks exactly that assertion.
MUTATIONS: dict[str, tuple[str, ...]] = {
    "GOLD-01": ("UPDATE {t} SET `index_stop` = NULL WHERE `encounter_id` = 'seed-e10'",),
    "GOLD-02": ("DELETE FROM {t} WHERE `encounter_id` = 'seed-e10'",),  # a negative: GOLD-01 still holds
    "GOLD-03": ("UPDATE {t} SET `is_readmitted` = 1 WHERE `encounter_id` = 'seed-e10'",),
    "GOLD-04": ("UPDATE {t} SET `encounter_id` = 'seed-e09' WHERE `encounter_id` = 'seed-e10'",),  # count unchanged
    "GOLD-05": ("UPDATE {t} SET `label_horizon` = NULL WHERE `encounter_id` = 'seed-e10'",),
    "GOLD-06": (  # the positive count stays 2
        "UPDATE {t} SET `is_readmitted` = 2 WHERE `encounter_id` = 'seed-e01'",
        "UPDATE {t} SET `is_readmitted` = 0 WHERE `encounter_id` = 'seed-e02'",
    ),
    "GOLD-07": ("UPDATE {p} SET `encounter_id` = 'seed-absent' WHERE `as_of_date` = DATE '2020-01-01'",),
}


class GoldSeededCheckError(RuntimeError):
    def __init__(self, message: str, results: dict[str, DataTestResult]):
        super().__init__(message)
        self.results = results


def _literal(value: object, bq_type: str) -> str:
    if value is None:
        return "NULL"
    if bq_type == "STRING":
        if "'" in str(value) or "\\" in str(value):
            raise ValueError(f"fixture strings must not contain quotes or backslashes: {value!r}")
        return f"'{value}'"
    if bq_type == "TIMESTAMP":
        return f"TIMESTAMP '{value}'"
    if bq_type == "DATE":
        return f"DATE '{value}'"
    if bq_type == "BOOLEAN":
        return "TRUE" if value else "FALSE"
    return repr(value)


def insert_sql(table: str, schema_name: str, rows: list[dict]) -> str:
    fields = load_schema(schema_name)
    columns = ", ".join(quoted(f["name"]) for f in fields)
    values = ",\n  ".join("(" + ", ".join(_literal(row[f["name"]], f["type"]) for f in fields) + ")" for row in rows)
    return f"INSERT INTO {quoted(table)} ({columns}) VALUES\n  {values}"


def prediction_row() -> dict:
    """One valid serving.predictions row referring to seed-e03 under SEED_FINGERPRINT."""
    defaults = {"STRING": "seed", "INTEGER": 1, "FLOAT": 0.5, "BOOLEAN": True, "DATE": "2020-01-01", "TIMESTAMP": "2020-01-01 00:00:00+00"}
    row = {f["name"]: defaults[f["type"]] for f in load_schema("predictions")}
    row.update(as_of_date="2020-01-01", encounter_id="seed-e03", gold_fingerprint=SEED_FINGERPRINT, rank=1, top_n=1, is_top_n=True)
    return row


def seed_names(token: str, key: str) -> tuple[str, str]:
    """'GOLD-01' -> (scratch_gold.gold_seed_<token>_gold_01, ..._pred); 'clean' -> ..._clean (like seeded_check.copy_name)."""
    slug = key.lower().replace("-", "_")
    gold = table_id(SCRATCH_GOLD_DATASET, f"gold_seed_{token}_{slug}")
    return gold, f"{gold}_pred"


def expected(results: dict[str, DataTestResult]) -> bool:
    """The clean pair passed and every GOLD assertion failed with exactly its own label."""
    ids = list(assertion_descriptions("gold"))
    if set(results) != {"clean", *ids} or not results["clean"].passed:
        return False
    return all(not results[i].passed and (results[i].failure or "").startswith(f"{i}: ") for i in ids)


def seeded_gold_check(client) -> dict[str, DataTestResult]:
    """{"clean": ..., "GOLD-01": ..., ...}. GoldSeededCheckError (carrying the results so far) on any non-ASSERT error."""
    from google.cloud import bigquery

    if set(MUTATIONS) != set(assertion_descriptions("gold")):
        raise ValueError("every GOLD assertion needs exactly one mutation")
    token = uuid.uuid4().hex[:8]
    results: dict[str, DataTestResult] = {}
    live: set[str] = set()

    def run(sql: str) -> None:
        client.query(sql, job_config=query_config()).result()

    try:
        for key in ("clean", *assertion_descriptions("gold")):
            gold, pred = seed_names(token, key)
            for name, schema_name in ((gold, "gold"), (pred, "predictions")):
                client.create_table(bigquery.Table(name, schema=to_bq_schema(load_schema(schema_name))))
                live.add(name)
            run(insert_sql(gold, "gold", list(FIXTURE_ROWS)))
            run(insert_sql(pred, "predictions", [prediction_row()]))
            for statement in MUTATIONS.get(key, ()):
                run(statement.replace("{t}", quoted(gold)).replace("{p}", quoted(pred)))
            results[key] = run_data_tests(
                client, "gold", table=gold, predictions_table=pred,
                n_rows=N_ROWS, n_positive=N_POSITIVE, predicted_fingerprint=SEED_FINGERPRINT,
            )  # fmt: skip
            for name in (gold, pred):
                client.delete_table(name, not_found_ok=True)
                live.discard(name)
    except Exception as exc:
        raise GoldSeededCheckError(f"{type(exc).__name__}: {exc}", results) from exc
    finally:
        for name in sorted(live):  # best effort; the dataset's 1-day expiration is the backstop
            with contextlib.suppress(Exception):
                client.delete_table(name, not_found_ok=True)
    return results
