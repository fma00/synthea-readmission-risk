-- dashboard.v_call_sheet (v3 slice 2; notes/eg-new-feature/v3-slice2-authorized-views-2026-10-31.md, Interfaces section 3).
-- The Top-N call sheet: serving.predictions joined to a few serving.gold features, no label or demographic column. The reason
-- rate is an aggregate over gold discharges whose labels were settled before the champion's test window (owner decision D2);
-- a rate over fewer than 30 rows is withheld (the count stays). discharge_time is gold's index_stop, so a query constraining it
-- and as_of_date with constants can prune both filter-required base tables. Rendered by Terraform's templatefile.
WITH reason_rates AS (
  SELECT IFNULL(admission_reason_code, 'MISSING') AS reason_key, COUNT(*) AS n, AVG(is_readmitted) AS rate
  FROM `${gold}`
  WHERE index_stop < TIMESTAMP '2023-01-01 00:00:00+00' AND label_horizon < TIMESTAMP '2023-01-01 00:00:00+00'
  GROUP BY reason_key
)
SELECT
  p.as_of_date,
  p.rank,
  p.is_top_n,
  p.top_n,
  p.patient_id,
  p.encounter_id,
  g.index_stop AS discharge_time,
  p.admission_reason_code,
  p.admission_reason_description,
  p.risk_score,
  g.age_at_index_years,
  TIMESTAMP_DIFF(g.index_stop, g.index_start, MICROSECOND) / 86400000000 AS length_of_stay_days,
  g.prior_inpatient_count,
  g.prior_emergency_count,
  g.active_condition_count,
  r.n AS reason_history_n,
  IF(r.n >= 30, r.rate, NULL) AS reason_history_rate,
  p.model_bundle_id,
  p.scoring_run_id,
  p.scoring_window_days
FROM `${predictions}` AS p
JOIN `${gold}` AS g ON g.encounter_id = p.encounter_id
LEFT JOIN reason_rates AS r ON r.reason_key = IFNULL(p.admission_reason_code, 'MISSING')
WHERE p.gold_fingerprint = '80edd13dcc967d69b6fa4cfa17b4d7fa40188f8e7e17b2031b833332a59c63f3'
