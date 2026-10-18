"""M5 (PRD): every data-test assertion is shown to fire. Each assertion ID gets a fresh scratch copy of real rows and ONE
targeted mutation that breaks exactly that assertion while every earlier one still holds (a script stops at its first
failing ASSERT); the clean copy of each table must pass. Copies live in the scratch dataset and are dropped right after
their check. See notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md (Interfaces > seeded_check.py).
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import date

from .bigquery import SCRATCH_DATASET, query_config, quoted, table_id
from .data_tests import DataTestResult, assertion_descriptions, run_data_tests
from .scoring_runs import scoring_runs_table_id
from .staging import predictions_table_id

_MAX_RANK = "(SELECT MAX(`rank`) FROM {t} WHERE `as_of_date` = @run_date)"
_DAY = "AND `as_of_date` = @run_date"

# ID -> one DML statement on a fresh copy {t}; @run_date is the only parameter.
PREDICTION_MUTATIONS: dict[str, str] = {
    "PRED-01": "DELETE FROM {t} WHERE `as_of_date` = @run_date",
    "PRED-02": f"UPDATE {{t}} SET `rank` = `rank` + 1 WHERE `rank` = {_MAX_RANK} {_DAY}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "PRED-03": "UPDATE {t} SET `encounter_id` = (SELECT `encounter_id` FROM {t} WHERE `rank` = 1 "  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    f"{_DAY}) WHERE `rank` = 2 {_DAY}",
    "PRED-04": f"UPDATE {{t}} SET `risk_score` = 1.5 WHERE `rank` = {_MAX_RANK} {_DAY}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "PRED-05": f"UPDATE {{t}} SET `is_top_n` = NOT `is_top_n` WHERE `rank` = 1 {_DAY}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "PRED-06": f"UPDATE {{t}} SET `model_bundle_id` = CONCAT(`model_bundle_id`, '-m5') WHERE `rank` = {_MAX_RANK} {_DAY}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "PRED-07": f"UPDATE {{t}} SET `risk_score` = 0 WHERE `rank` = 1 {_DAY}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "PRED-08": f"UPDATE {{t}} SET `partition` = 'train' WHERE `rank` = {_MAX_RANK} {_DAY}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "PRED-09": "UPDATE {t} SET `discharge_time` = TIMESTAMP_ADD(TIMESTAMP(DATE_ADD(@run_date, INTERVAL 1 DAY)), "  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    f"INTERVAL 1 HOUR) WHERE `rank` = {_MAX_RANK} {_DAY}",
    "PRED-10": f"UPDATE {{t}} SET `scoring_run_id` = 'm5-other' WHERE `rank` = {_MAX_RANK} {_DAY}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "PRED-11": f"UPDATE {{t}} SET `gold_fingerprint_definition` = NULL WHERE `rank` = {_MAX_RANK} {_DAY}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
}

_INSERT_N = "INSERT INTO {t} SELECT * REPLACE ({replace}) FROM {t} WHERE `scoring_run_id` = @n_id"
# ID -> one DML statement on a fresh copy {t}; parameters @n_id (the newest succeeded run) and @fresh_id.
RUN_MUTATIONS: dict[str, str] = {
    "RUNS-01": "INSERT INTO {t} SELECT * FROM {t} WHERE `scoring_run_id` = @n_id",
    "RUNS-02": _INSERT_N.replace("{replace}", "@fresh_id AS `scoring_run_id`, 'bogus' AS `status`"),
    "RUNS-03": _INSERT_N.replace(
        "{replace}", "@fresh_id AS `scoring_run_id`, TIMESTAMP_SUB(`started_at`, INTERVAL 1 SECOND) AS `finished_at`"
    ),
    "RUNS-04": _INSERT_N.replace("{replace}", "@fresh_id AS `scoring_run_id`, FALSE AS `published`"),
    "RUNS-05": _INSERT_N.replace(
        "{replace}",
        "@fresh_id AS `scoring_run_id`, 'failed' AS `status`, FALSE AS `published`, "
        "CAST(NULL AS STRING) AS `error_message`, CAST(NULL AS STRING) AS `data_test_failure`",
    ),
    "RUNS-06": _INSERT_N.replace(
        "{replace}",
        "@fresh_id AS `scoring_run_id`, 'failed' AS `status`, TRUE AS `published`, "
        "FALSE AS `predictions_tests_passed`, 'm5' AS `error_message`",
    ),
    "RUNS-07": _INSERT_N.replace(
        "{replace}",
        "@fresh_id AS `scoring_run_id`, 'refused' AS `status`, TRUE AS `published`, "
        "TRUE AS `predictions_tests_passed`, 'm5' AS `error_message`",
    ),
}


class SeededCheckError(RuntimeError):
    def __init__(self, message: str, results: dict[str, dict[str, DataTestResult]]):
        super().__init__(message)
        self.results = results


def copy_name(kind: str, assertion_id: str) -> str:
    """kind 'p' or 'r'; 'PRED-01' -> scratch.m5p_pred_01_<hex8>; 'clean' -> m5p_clean_<hex8>."""
    token = assertion_id.lower().replace("-", "_")
    return table_id(SCRATCH_DATASET, f"m5{kind}_{token}_{uuid.uuid4().hex[:8]}")


def seeded_check(client, *, run_date: date) -> dict[str, dict[str, DataTestResult]]:
    from google.cloud import bigquery

    results: dict[str, dict[str, DataTestResult]] = {"predictions": {}, "scoring_runs": {}}
    live: set[str] = set()
    day = bigquery.ScalarQueryParameter("run_date", "DATE", run_date)

    def run(sql: str, *params) -> list:
        return list(client.query(sql, job_config=query_config(query_parameters=list(params))).result())

    def drop(name: str) -> None:
        client.delete_table(name, not_found_ok=True)
        live.discard(name)

    def copy_predictions(assertion_id: str) -> str:
        name = copy_name("p", assertion_id)
        live.add(name)
        run(
            f"CREATE TABLE {quoted(name)} PARTITION BY `as_of_date` AS "  # noqa: S608 -- identifiers validated by table_id() and back-quoted; values are query parameters
            f"SELECT * FROM {quoted(predictions_table_id())} WHERE `as_of_date` = @run_date",
            day,
        )
        return name

    def copy_runs(assertion_id: str) -> str:
        name = copy_name("r", assertion_id)
        live.add(name)
        run(
            f"CREATE TABLE {quoted(name)} AS SELECT * FROM {quoted(scoring_runs_table_id())} "  # noqa: S608 -- identifiers validated by table_id() and back-quoted
            "WHERE `started_at` >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 400 DAY)"
        )
        return name

    def expect(table: str, kind: str, label: str, result: DataTestResult) -> None:
        results[kind][label] = result
        drop(table)
        if label == "clean" and not result.passed:
            raise SeededCheckError(f"the clean {kind} copy failed its data tests: {result.failure}", results)
        if label != "clean" and (result.passed or not (result.failure or "").startswith(f"{label}: ")):
            got = "passed" if result.passed else f"failed with {result.failure}"
            raise SeededCheckError(f"the {label} mutation should fail with {label}; it {got}", results)

    try:
        # predictions
        clean = copy_predictions("clean")
        rows = run(
            "SELECT COUNT(DISTINCT `scoring_run_id`) AS n_ids, ANY_VALUE(`scoring_run_id`) AS run_id, COUNT(*) AS n, "  # noqa: S608 -- identifier validated by table_id() and back-quoted
            f"MAX(IF(`rank` = 2, `risk_score`, NULL)) AS second FROM {quoted(clean)} WHERE `as_of_date` = @run_date",
            day,
        )[0]
        if rows["n_ids"] != 1 or rows["n"] < 2 or not (rows["second"] or 0) > 0:
            raise SeededCheckError(
                f"publish a run for {run_date} first (need one scoring_run_id, >= 2 rows and rank 2's risk_score > 0)",
                results,
            )
        run_id = rows["run_id"]
        expect(clean, "predictions", "clean",
               run_data_tests(client, "predictions", table=clean, run_date=run_date, scoring_run_id=run_id))  # fmt: skip
        for assertion_id in assertion_descriptions("predictions"):
            copy = copy_predictions(assertion_id)
            run(PREDICTION_MUTATIONS[assertion_id].replace("{t}", quoted(copy)), day)
            expect(copy, "predictions", assertion_id,
                   run_data_tests(client, "predictions", table=copy, run_date=run_date, scoring_run_id=run_id))  # fmt: skip

        # scoring_runs
        clean = copy_runs("clean")
        newest = run(
            f"SELECT `scoring_run_id` FROM {quoted(clean)} WHERE `status` = 'succeeded' "  # noqa: S608 -- identifier validated by table_id() and back-quoted
            "ORDER BY `started_at` DESC LIMIT 1"
        )
        if not newest:
            raise SeededCheckError("run the scorer first (no succeeded scoring run in the last 400 days)", results)
        n_id = bigquery.ScalarQueryParameter("n_id", "STRING", newest[0]["scoring_run_id"])
        expect(clean, "scoring_runs", "clean", run_data_tests(client, "scoring_runs", table=clean))
        for assertion_id in assertion_descriptions("scoring_runs"):
            copy = copy_runs(assertion_id)
            fresh = bigquery.ScalarQueryParameter("fresh_id", "STRING", f"m5-{uuid.uuid4().hex}")
            statement = RUN_MUTATIONS[assertion_id].replace("{t}", quoted(copy))
            run(statement, *([n_id] if "@fresh_id" not in statement else [n_id, fresh]))
            expect(copy, "scoring_runs", assertion_id, run_data_tests(client, "scoring_runs", table=copy))
        return results
    finally:
        for name in sorted(live):
            # best effort; the scratch dataset's 1-day expiration is the backstop
            with contextlib.suppress(Exception):
                client.delete_table(name, not_found_ok=True)


# ---------------------------------------------------------------------------------------------- monitoring (v2 slice 7)
# serving.monitoring is empty before the first tick, so its M5 copies are seeded with SYNTHETIC rows: a valid base row
# plus four valid companions (a failed, a refused, a clock-stage and an incomplete tick) that prove every check is
# NULL-safe on such rows. Each mutation touches only the base row. Run as the monitor service account (make m5-monitoring).
_MONITORING_BUNDLE = "569fdab6c17d433542b2cd6b531ed8efc5677fb37b00a792d84f79803c6a0f72"
_SQL_TYPES = {"STRING": "STRING", "INTEGER": "INT64", "FLOAT": "FLOAT64", "BOOLEAN": "BOOL", "TIMESTAMP": "TIMESTAMP", "DATE": "DATE"}
MONITORING_BASE_ROW: dict[str, str] = {
    "monitoring_run_id": "'m5base'", "status": "'succeeded'", "started_at": "CURRENT_TIMESTAMP()",
    "finished_at": "CURRENT_TIMESTAMP()", "sim_date": "DATE '2023-08-14'", "cadence": "'catch_up'", "trigger": "'laptop'",
    "history_tests_passed": "TRUE", "scoring_run_id": "'m5run'", "model_bundle_id": f"'{_MONITORING_BUNDLE}'",
    "verdict": "'ok'", "n_matured": "2000", "observed": "40", "expected": "40.0", "oe_ratio": "1.0", "oe_ci_low": "0.7144",
    "oe_ci_high": "1.3617", "coverage_required": "18", "coverage_found": "18", "min_positives_floor": "20",
    "band_low": "0.8", "band_high": "1.25",
}  # fmt: skip
_METRICS = ("verdict", "n_matured", "observed", "expected", "oe_ratio", "oe_ci_low", "oe_ci_high", "coverage_required", "coverage_found")
MONITORING_COMPANIONS: dict[str, dict[str, str]] = {
    "m5failed": {"status": "'failed'", "failed_stage": "'score'", **dict.fromkeys(_METRICS, None)},
    "m5refused": {"status": "'refused'", "failed_stage": "'evaluate'", **dict.fromkeys(_METRICS, None)},
    "m5clock": {"status": "'refused'", "failed_stage": "'clock'", "sim_date": None, "cadence": None,
                "history_tests_passed": None, **dict.fromkeys(_METRICS, None)},
    "m5incomplete": {"verdict": "'incomplete_cohort'", "n_matured": "0", "observed": "0", "expected": "0.0", "oe_ratio": None,
                     "oe_ci_low": None, "oe_ci_high": None, "coverage_found": "4", "scoring_run_id": "'m5run2'",
                     "sim_date": "DATE '2023-04-17'"},
}  # fmt: skip
_ONLY_BASE = "WHERE `monitoring_run_id` = 'm5base'"
MONITORING_MUTATIONS: dict[str, str] = {
    "MON-01": f"INSERT INTO {{t}} SELECT * FROM {{t}} {_ONLY_BASE}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "MON-02": f"UPDATE {{t}} SET `status` = 'done' {_ONLY_BASE}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "MON-03": f"UPDATE {{t}} SET `finished_at` = TIMESTAMP_SUB(`started_at`, INTERVAL 1 SECOND) {_ONLY_BASE}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "MON-04": f"UPDATE {{t}} SET `verdict` = NULL {_ONLY_BASE}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "MON-05": f"UPDATE {{t}} SET `scoring_run_id` = NULL {_ONLY_BASE}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "MON-06": f"UPDATE {{t}} SET `sim_date` = DATE '2023-08-15' {_ONLY_BASE}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "MON-07": f"UPDATE {{t}} SET `oe_ci_low` = `oe_ratio` + 1 {_ONLY_BASE}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "MON-08": f"UPDATE {{t}} SET `verdict` = 'alert' {_ONLY_BASE}",  # O/E 1.0 is inside its CI: an inconsistent alert  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "MON-09": f"UPDATE {{t}} SET `verdict` = 'great' {_ONLY_BASE}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "MON-10": f"UPDATE {{t}} SET `sim_date` = DATE '2026-09-14' {_ONLY_BASE}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "MON-11": f"UPDATE {{t}} SET `cadence` = 'hourly' {_ONLY_BASE}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
    "MON-12": f"UPDATE {{t}} SET `failed_stage` = 'score' {_ONLY_BASE}",  # noqa: S608 -- static template; {t} becomes a back-quoted table_id()
}


def monitoring_struct(monitoring_run_id: str, overrides: dict[str, str | None] | None = None) -> str:
    """One STRUCT literal in schema order; every NULL is typed (an untyped NULL in a STRUCT would be INT64)."""
    from .bigquery import load_schema

    values = {**MONITORING_BASE_ROW, **(overrides or {}), "monitoring_run_id": f"'{monitoring_run_id}'"}
    parts = []
    for field in load_schema("monitoring"):
        value = values.get(field["name"])
        parts.append(f"{value if value is not None else 'CAST(NULL AS ' + _SQL_TYPES[field['type']] + ')'} AS `{field['name']}`")
    return "STRUCT(" + ", ".join(parts) + ")"


def monitoring_clean_rows_sql() -> str:
    rows = [monitoring_struct("m5base")] + [monitoring_struct(rid, o) for rid, o in MONITORING_COMPANIONS.items()]
    return "[" + ", ".join(rows) + "]"


def seeded_monitoring_check(client) -> dict[str, dict[str, DataTestResult]]:
    """{"monitoring": {"clean": ..., "MON-01": ...}} (the same nested shape as seeded_check's result)."""
    from readmission_risk.monitoring.store import monitoring_table_id

    results: dict[str, dict[str, DataTestResult]] = {"monitoring": {}}
    live: set[str] = set()

    def run(sql: str) -> None:
        client.query(sql, job_config=query_config()).result()

    def seeded_copy(label: str) -> str:
        name = copy_name("m", label)
        live.add(name)
        run(
            f"CREATE TABLE {quoted(name)} PARTITION BY DATE(`started_at`) AS SELECT * FROM {quoted(monitoring_table_id())} "  # noqa: S608 -- identifiers validated by table_id() and back-quoted
            "WHERE `started_at` >= TIMESTAMP '2026-10-18' AND FALSE"
        )
        run(f"INSERT INTO {quoted(name)} SELECT * FROM UNNEST({monitoring_clean_rows_sql()})")  # noqa: S608 -- static literals
        return name

    def expect(name: str, label: str) -> None:
        result = run_data_tests(client, "monitoring", table=name)
        results["monitoring"][label] = result
        client.delete_table(name, not_found_ok=True)
        live.discard(name)
        if label == "clean" and not result.passed:
            raise SeededCheckError(f"the clean monitoring copy failed its data tests: {result.failure}", results)
        if label != "clean" and (result.passed or not (result.failure or "").startswith(f"{label}: ")):
            got = "passed" if result.passed else f"failed with {result.failure}"
            raise SeededCheckError(f"the {label} mutation should fail with {label}; it {got}", results)

    try:
        expect(seeded_copy("clean"), "clean")
        for assertion_id in assertion_descriptions("monitoring"):
            name = seeded_copy(assertion_id)
            run(MONITORING_MUTATIONS[assertion_id].replace("{t}", quoted(name)))
            expect(name, assertion_id)
        return results
    finally:
        for name in sorted(live):
            with contextlib.suppress(Exception):
                client.delete_table(name, not_found_ok=True)
