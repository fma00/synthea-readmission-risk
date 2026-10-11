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
