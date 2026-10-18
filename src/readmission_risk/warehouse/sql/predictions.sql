-- Data tests for one run date's prediction rows (v2 slice 4, PRD FR18). {table} is the staging table (or an M5 copy),
-- rendered back-quoted by warehouse.data_tests; parameters @run_date DATE and @scoring_run_id STRING.
-- Shape rule: the DECLARE is the only statement that reads a table; every ASSERT reads only `c`. An ASSERT fails on FALSE
-- and on NULL (BigQuery), but inside COUNTIF a NULL condition is silently skipped, so every row check is written as
-- COUNTIF((<condition that must hold>) IS NOT TRUE): a FALSE or NULL condition counts as a bad row.
-- A script stops at its first failing ASSERT; the order is relied on by warehouse.seeded_check (M5).
DECLARE c DEFAULT (
  SELECT AS STRUCT
    COUNT(*) AS n_rows,
    MIN(`rank`) AS min_rank,
    MAX(`rank`) AS max_rank,
    COUNT(DISTINCT `rank`) AS n_distinct_rank,
    COUNT(DISTINCT `encounter_id`) AS n_distinct_encounter,
    COUNTIF((`risk_score` BETWEEN 0 AND 1) IS NOT TRUE) AS bad_risk_score,
    COUNTIF((`is_top_n` = (`rank` <= `top_n`)) IS NOT TRUE) AS bad_top_n_flag,
    COUNT(DISTINCT `model_bundle_id`) AS n_distinct_bundle,
    COUNT(DISTINCT `top_n`) AS n_distinct_top_n,
    COUNT(DISTINCT `scoring_window_days`) AS n_distinct_window,
    COUNT(DISTINCT `gold_fingerprint`) AS n_distinct_fingerprint,
    COUNT(DISTINCT `label_definition`) AS n_distinct_label_definition,
    COUNT(DISTINCT `gold_reference_date`) AS n_distinct_reference_date,
    (
      SELECT COUNTIF((`prev` IS NULL OR `risk_score` <= `prev`) IS NOT TRUE)
      FROM (
        SELECT `risk_score`, LAG(`risk_score`) OVER (ORDER BY `rank`) AS `prev`
        FROM {table}
        WHERE `as_of_date` = @run_date
      )
    ) AS bad_order,
    COUNTIF((`partition` = 'test') IS NOT TRUE) AS bad_partition,
    COUNTIF((
      `discharge_time` >= TIMESTAMP(DATE_SUB(@run_date, INTERVAL `scoring_window_days` - 1 DAY))
      AND `discharge_time` < TIMESTAMP(DATE_ADD(@run_date, INTERVAL 1 DAY))
    ) IS NOT TRUE) AS bad_window,
    COUNTIF((`scoring_run_id` = @scoring_run_id) IS NOT TRUE) AS bad_run_id,
    COUNT(DISTINCT `gold_fingerprint_definition`) AS n_distinct_fp_definition,
    COUNTIF((`gold_fingerprint_definition` IN ('pandas-csv-sha256-v1', 'spark-rowhash-v1')) IS NOT TRUE) AS bad_fp_definition
  FROM {table}
  WHERE `as_of_date` = @run_date
);

ASSERT c.n_rows >= 1 AS 'PRED-01: the run date has no prediction rows';
ASSERT c.min_rank = 1 AND c.max_rank = c.n_rows AND c.n_distinct_rank = c.n_rows AS 'PRED-02: rank is not exactly 1..n';
ASSERT c.n_distinct_encounter = c.n_rows AS 'PRED-03: encounter_id is not unique';
ASSERT c.bad_risk_score = 0 AS 'PRED-04: a risk_score is outside [0, 1], NaN or null';
ASSERT c.bad_top_n_flag = 0 AS 'PRED-05: is_top_n disagrees with rank <= top_n';
ASSERT c.n_distinct_bundle = 1 AND c.n_distinct_top_n = 1 AND c.n_distinct_window = 1 AND c.n_distinct_fingerprint = 1 AND c.n_distinct_label_definition = 1 AND c.n_distinct_reference_date = 1 AS 'PRED-06: a run-level column is not constant';
ASSERT c.bad_order = 0 AS 'PRED-07: risk_score increases with rank';
ASSERT c.bad_partition = 0 AS 'PRED-08: a row is not from the test partition';
ASSERT c.bad_window = 0 AS 'PRED-09: a discharge_time is outside the scoring window';
ASSERT c.bad_run_id = 0 AS 'PRED-10: a row belongs to another scoring run';
ASSERT c.n_distinct_fp_definition = 1 AND c.bad_fp_definition = 0 AS 'PRED-11: gold_fingerprint_definition is missing, unknown or not constant';
