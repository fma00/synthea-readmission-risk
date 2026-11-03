-- Data tests for a gold STAGING table before it is published into serving.gold (v3 slice 1, PRD FR5, owner decision D8).
-- {table} is the staging table in scratch_gold (unpartitioned, created by the load job: no partition filter is needed and
-- NULL partition keys are countable); {predictions_table} is serving.predictions (or a seeded copy in scratch_gold), rendered
-- back-quoted by warehouse.data_tests; parameters @n_rows INT64, @n_positive INT64 (from _gold_metadata.json) and
-- @predicted_fingerprint STRING (the champion's gold fingerprint).
-- Shape rule: the DECLARE is the only statement that reads a table; every ASSERT reads only `c`. An ASSERT fails on FALSE
-- and on NULL (BigQuery), but inside COUNTIF a NULL condition is silently skipped, so every row check is written as
-- COUNTIF((<condition that must hold>) IS NOT TRUE): a FALSE or NULL condition counts as a bad row.
-- A script stops at its first failing ASSERT; the order is relied on by warehouse.gold_seeded_check (M5 for GOLD).
-- GOLD-05's columns are warehouse.gold.GOLD_NOT_NULL_COLUMNS (= the loader's required non-null columns minus the two keys
-- GOLD-04/01 check); tests/warehouse/test_gold_sql.py keeps the two equal.
DECLARE c DEFAULT (
  SELECT AS STRUCT
    COUNTIF((`index_stop` IS NOT NULL) IS NOT TRUE) AS null_index_stop,
    COUNT(*) AS n_rows,
    SUM(`is_readmitted`) AS n_positive,
    COUNT(DISTINCT `encounter_id`) AS n_distinct_encounter,
    COUNTIF((`encounter_id` IS NOT NULL) IS NOT TRUE) AS null_encounter,
    COUNTIF((
      `patient_id` IS NOT NULL
      AND `index_start` IS NOT NULL
      AND `is_readmitted` IS NOT NULL
      AND `label_horizon` IS NOT NULL
      AND `prior_encounter_count` IS NOT NULL
      AND `prior_inpatient_count` IS NOT NULL
      AND `prior_emergency_count` IS NOT NULL
      AND `active_condition_count` IS NOT NULL
      AND `active_medication_count` IS NOT NULL
      AND `procedure_count_window` IS NOT NULL
      AND `active_careplan_count` IS NOT NULL
      AND `observation_count_window` IS NOT NULL
      AND `age_at_index_years` IS NOT NULL
    ) IS NOT TRUE) AS bad_not_null,
    COUNTIF((`is_readmitted` IN (0, 1)) IS NOT TRUE) AS bad_label,
    (
      SELECT COUNT(*)
      FROM {predictions_table} AS p
      WHERE p.`as_of_date` BETWEEN DATE '2000-01-01' AND DATE '2100-12-31'
        AND p.`gold_fingerprint` = @predicted_fingerprint
    ) AS n_predicted,
    (
      SELECT COUNT(*)
      FROM {predictions_table} AS p
      WHERE p.`as_of_date` BETWEEN DATE '2000-01-01' AND DATE '2100-12-31'
        AND p.`gold_fingerprint` = @predicted_fingerprint
        AND NOT EXISTS (SELECT 1 FROM {table} AS g WHERE g.`encounter_id` = p.`encounter_id`)
    ) AS missing_predicted
  FROM {table}
);

ASSERT c.null_index_stop = 0 AS 'GOLD-01: an index_stop (the partition key) is NULL';
ASSERT c.n_rows = @n_rows AS 'GOLD-02: the row count differs from the gold metadata';
ASSERT c.n_positive = @n_positive AS 'GOLD-03: the positive count differs from the gold metadata';
ASSERT c.n_distinct_encounter = c.n_rows AND c.null_encounter = 0 AS 'GOLD-04: encounter_id is not unique or is NULL';
ASSERT c.bad_not_null = 0 AS 'GOLD-05: a column the loader requires non-null is NULL';
ASSERT c.bad_label = 0 AS 'GOLD-06: an is_readmitted is not 0 or 1';
ASSERT c.n_predicted >= 1 AND c.missing_predicted = 0 AS 'GOLD-07: the champion has no predictions on this gold, or one of its predicted encounters is missing';
