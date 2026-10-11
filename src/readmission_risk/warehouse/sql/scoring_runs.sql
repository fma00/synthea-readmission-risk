-- Data tests for the scoring_runs telemetry table (v2 slice 4, PRD FR18), run on the table's state BEFORE each attempt.
-- {table} is serving.scoring_runs (or an M5 copy), rendered back-quoted; no parameters. An empty table passes.
-- Same shape rule as predictions.sql: one reading DECLARE, ASSERTs over `c` only, row checks as
-- COUNTIF((<condition that must hold>) IS NOT TRUE) so a NULL counts as a failure.
DECLARE c DEFAULT (
  SELECT AS STRUCT
    COUNT(*) - COUNT(DISTINCT `scoring_run_id`) AS n_duplicate_ids,
    COUNTIF((`status` IN ('succeeded', 'data_tests_failed', 'refused', 'failed')) IS NOT TRUE) AS bad_status,
    COUNTIF((`finished_at` >= `started_at`) IS NOT TRUE) AS bad_times,
    COUNTIF((
      `status` != 'succeeded'
      OR (`published` AND `history_tests_passed` AND `predictions_tests_passed` AND `n_predictions` > 0 AND `model_bundle_id` IS NOT NULL)
    ) IS NOT TRUE) AS bad_succeeded,
    COUNTIF((`status` = 'succeeded' OR `error_message` IS NOT NULL OR `data_test_failure` IS NOT NULL) IS NOT TRUE) AS bad_unexplained,
    COUNTIF((NOT `published` OR `predictions_tests_passed`) IS NOT TRUE) AS bad_untested_publish,
    COUNTIF((`status` != 'refused' OR NOT `published`) IS NOT TRUE) AS bad_refused_publish
  FROM {table}
  WHERE `started_at` >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 400 DAY)
);

ASSERT c.n_duplicate_ids = 0 AS 'RUNS-01: scoring_run_id is not unique';
ASSERT c.bad_status = 0 AS 'RUNS-02: a status is not one of succeeded, data_tests_failed, refused, failed';
ASSERT c.bad_times = 0 AS 'RUNS-03: a run finished before it started';
ASSERT c.bad_succeeded = 0 AS 'RUNS-04: a succeeded run is not published, tested and non-empty';
ASSERT c.bad_unexplained = 0 AS 'RUNS-05: an unsuccessful run carries no error or data-test failure';
ASSERT c.bad_untested_publish = 0 AS 'RUNS-06: a run published rows that did not pass their data tests';
ASSERT c.bad_refused_publish = 0 AS 'RUNS-07: a refused run published rows';
