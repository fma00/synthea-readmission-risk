-- Data tests for the serving.monitoring table (v2 slice 7, PRD FR18), run on the table's state BEFORE each evaluate insert.
-- {table} is serving.monitoring (or an M5 copy), rendered back-quoted; no parameters. An empty table passes.
-- Same shape rule as scoring_runs.sql: one reading DECLARE, ASSERTs over `c` only, row checks as
-- COUNTIF((<condition that must hold>) IS NOT TRUE). Every condition is written so it is never NULL on a valid failed,
-- refused, clock-stage or incomplete row. The literals (epoch, grid anchor, last sim date) equal deploy/monitoring.json
-- (tests/warehouse/test_monitoring_sql.py). A script stops at its first failing ASSERT; warehouse.seeded_check relies on
-- the order.
DECLARE c DEFAULT (
  SELECT AS STRUCT
    COUNT(*) - COUNT(DISTINCT `monitoring_run_id`) AS n_duplicate_ids,
    COUNTIF((`status` IN ('succeeded', 'refused', 'failed')) IS NOT TRUE) AS bad_status,
    COUNTIF((`finished_at` >= `started_at`) IS NOT TRUE) AS bad_times,
    COUNTIF(((`status` = 'succeeded') = (`verdict` IS NOT NULL)) IS NOT TRUE) AS bad_verdict_presence,
    COUNTIF((
      `status` != 'succeeded'
      OR (`sim_date` IS NOT NULL AND `cadence` IS NOT NULL AND `scoring_run_id` IS NOT NULL AND `model_bundle_id` IS NOT NULL
          AND `n_matured` IS NOT NULL AND `coverage_required` IS NOT NULL AND `coverage_found` IS NOT NULL
          AND `history_tests_passed`)
    ) IS NOT TRUE) AS bad_succeeded,
    COUNTIF((`sim_date` IS NULL OR MOD(DATE_DIFF(`sim_date`, DATE '2023-04-17', DAY), 7) = 0) IS NOT TRUE) AS bad_grid,
    COUNTIF((`oe_ratio` IS NULL OR (`oe_ci_low` <= `oe_ratio` AND `oe_ratio` <= `oe_ci_high`)) IS NOT TRUE) AS bad_ci,
    COUNTIF((
      `verdict` IS NULL OR `verdict` != 'alert'
      OR (`observed` >= `min_positives_floor` AND (`oe_ci_high` < 1 OR `oe_ci_low` > 1)
          AND (`oe_ratio` < `band_low` OR `oe_ratio` > `band_high`) AND `coverage_found` = `coverage_required`)
    ) IS NOT TRUE) AS bad_alert,
    COUNTIF((`verdict` IS NULL OR `verdict` IN ('ok', 'alert', 'insufficient_data', 'incomplete_cohort')) IS NOT TRUE) AS bad_verdict,
    COUNTIF((`sim_date` IS NULL OR `sim_date` BETWEEN DATE '2023-04-17' AND DATE '2026-09-07') IS NOT TRUE) AS bad_range,
    COUNTIF((`cadence` IS NULL OR `cadence` IN ('burn_in', 'weekly', 'dispatch', 'catch_up')) IS NOT TRUE) AS bad_cadence,
    COUNTIF((
      (`status` = 'succeeded') = (`failed_stage` IS NULL)
      AND (`failed_stage` IS NULL OR `failed_stage` IN ('clock', 'score', 'evaluate'))
    ) IS NOT TRUE) AS bad_stage
  FROM {table}
  WHERE `started_at` >= TIMESTAMP '2026-10-18'
);

ASSERT c.n_duplicate_ids = 0 AS 'MON-01: monitoring_run_id is not unique';
ASSERT c.bad_status = 0 AS 'MON-02: a status is not one of succeeded, refused, failed';
ASSERT c.bad_times = 0 AS 'MON-03: a tick finished before it started';
ASSERT c.bad_verdict_presence = 0 AS 'MON-04: a verdict is present on an unsuccessful tick or missing on a succeeded one';
ASSERT c.bad_succeeded = 0 AS 'MON-05: a succeeded tick lacks its run, cohort counts or a passed history test';
ASSERT c.bad_grid = 0 AS 'MON-06: a sim_date is off the weekly grid';
ASSERT c.bad_ci = 0 AS 'MON-07: an observed/expected ratio lies outside its own interval';
ASSERT c.bad_alert = 0 AS 'MON-08: an alert does not meet the pre-registered rule with full coverage';
ASSERT c.bad_verdict = 0 AS 'MON-09: a verdict is not one of ok, alert, insufficient_data, incomplete_cohort';
ASSERT c.bad_range = 0 AS 'MON-10: a sim_date is outside the simulated range';
ASSERT c.bad_cadence = 0 AS 'MON-11: a cadence is not one of burn_in, weekly, dispatch, catch_up';
ASSERT c.bad_stage = 0 AS 'MON-12: failed_stage is inconsistent with the status or unknown';
