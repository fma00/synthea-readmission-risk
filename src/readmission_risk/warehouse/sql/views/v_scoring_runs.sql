-- dashboard.v_scoring_runs (v3 slice 2; notes/eg-new-feature/v3-slice2-authorized-views-2026-10-31.md, Interfaces section 3):
-- every scoring attempt, without the free-text error message and the internal gold/predictions paths (owner decision D4). Rendered by Terraform's templatefile.
SELECT
  scoring_run_id,
  status,
  started_at,
  finished_at,
  run_date,
  trigger,
  github_run_url,
  git_commit,
  git_dirty,
  python_version,
  gold_fingerprint,
  champion_sha256,
  model_bundle_id,
  model_name,
  top_n,
  window_days,
  n_predictions,
  n_top_n,
  history_tests_passed,
  predictions_tests_passed,
  published,
  data_test_failure,
  error_type,
  gold_fingerprint_definition
FROM `${scoring_runs}`
