# v2 slice 7: weekly monitoring tick on a simulated clock, run ledger, failure issues, accelerated burn-in, and v2.0

Status: design revision 6 (2026-10-18), after design-check round 6 (readiness closed in rounds 5 and 6; round-6 critic gaps addressed here); earlier: after design-check round 5 (3-revision cap reached after round 4; owner chose "apply fixes and run one more round"; round 5: readiness closed, critic 1 blocking + 12 minor, all addressed here). PRD:
`notes/prds/v2-mlops-keyless-scheduled-scale-up-2026-10-09.md` (FR19, FR20, FR21, M3, OQ1, OQ3, risk row "Ledger bot push
rejected by branch protection"). Pre-registered rule: `prereg.yaml` `monitoring:` (lines 94-98), FINAL, never edited by
this slice. "Kill trigger" below means FR19's: *if the tick is not idempotent by week 5, ship scheduled scoring + data
tests only*.

## Owner decisions (Step 0, 2026-10-18)

- **D1 Merge timing and release scope.** v2 merges into `main` during this slice, after slice 7 is built and merged into
  `v2`. **v2.0 = slices 1-7 + M3.** Slice 8 (Streamlit dashboard FR22, model card / README overhaul FR23-FR24, the
  BigQuery gold direct-write + connector MERGE test moved there from FR14) **moves to a v3.0 scope**. Every document that
  states the old scope is updated in this slice (Surfaces > Documentation). This replaces the 2026-10-11/12 decision
  "merge v2 into main only at v2.0, together with V-post and L-post": the merge now starts v2.0's release step; V-post,
  L-post, the burn-in and M3 run on `main` after it; the `v2.0` tag is set only once M3 is green. Merge commit, not squash,
  so the prereg commit `c9427b2` keeps its hash.
- **D2 Champion = the 200k logistic regression, promoted by a disclosed, post-hoc owner override of the pre-registered
  gate.** `deploy/champion.json` moves from `75a1448729ef…` (v1 LR, 10k `full_run_2b`) to
  `569fdab6c17d433542b2cd6b531ed8efc5677fb37b00a792d84f79803c6a0f72` (LR, MLflow run `a9f32f4b7c584f4c8fe992748410789f`,
  experiment `readmission-risk-v2-main200k`, trained on `main_200k`, fingerprint `80edd13d…63f3`, `spark-rowhash-v1`). The
  gate (6b, `prereg.yaml` `phase_b`, 2026-10-17) REFUSED it: ROC-AUC vs champion lower bound +0.0012 (pass), vs reason-code
  lookup −0.0033 (fail), Brier non-inferior (pass). **Stated plainly: overriding a pre-registered REFUSE after seeing it is
  a post-hoc decision; the pre-registration did not foresee it.** What is NOT post-hoc is the owner's reason: the 10k
  population was a technical feasibility run, which is on record before any 200k data existed: CLAUDE.md's slice-3 caveat
  (1), added in commit `74accd7` (2026-09-20), "use the v2 population (generated with a NEW seed …, and sized for
  statistical power) as the untouched validation". That caveat names the v2 population as the validation population; it
  does not say to promote regardless of the gate, and the doc does not claim it does. Supporting facts from the gate
  itself: the challenger beats the champion it replaces, and the v1 champion does not satisfy the lookup criterion either
  (v1 test AUC 0.869 vs lookup 0.873 on its own window). `prereg.yaml` is untouched: its decision stays REFUSE and
  `outcomes.lookup_still_wins` stays true. `docs/results.md` keeps leading with "the lookup is at least as good as the
  models". The override goes in a new append-only `deploy/PROMOTIONS.md` and in the promotion PR.
  **Standards deviation, flagged:** the pre-registered decision rule is overridden. `prereg.yaml`
  `outcomes.lookup_still_wins` says verbatim "If the challenger does not beat the reason-code lookup on ROC-AUC under the
  rule above, the champion is not replaced". In preregistration practice, a deviation log is where such an override
  belongs. It is still not recorded in `prereg.yaml`, for two reasons:
  - **The owner's constraint:** "prereg.yaml (FINAL, never edit)".
  - **The file's schema:** its `deviations` entries require `field`/`old`/`new` for a changed value
    (`ops/prereg.py:412-430`), and no value changes here.

  Instead, `deploy/PROMOTIONS.md`, `docs/results.md` and the README each quote that clause verbatim next to the override,
  so no reader sees the promotion without the rule it overrides. **Second consequence, stated in the same places:** after
  the gate, the override and now monitoring, the 200k test window has been used three times. The "untouched validation
  population" that CLAUDE.md caveat (1) promised no longer exists, and a fresh-seed population would be needed for one
  (v3 follow-up).
- **Rejected alternative to D2, recorded:** shadow monitoring would have kept v1 as champion and named the 200k bundle in
  `deploy/monitoring.json`, with the score job passing `--bundle`. That meets FR19 without overriding the gate. It was
  offered to the owner at Step 0 (options "Shadow-monitor the 200k model" and "Shadow only, v1 stays champion") and
  declined in favour of the disclosed promotion. `check_champion_matches` encodes that decision.
- **D3 Ledger branch.** Job B commits `docs/run-ledger.md` and `docs/last-tick.json` to a dedicated, unprotected `ledger`
  branch (the PRD's fallback), linked from the README. Ruleset 24876876 (targets exactly `refs/heads/main` and
  `refs/heads/v2`; rule `pull_request`, 0 approvals; **no bypass actors**; read 2026-10-18 with `gh api`) is not changed:
  a "GitHub Actions" bypass would apply to every workflow, not to job B only.

## Why

The PRD's operations goals G9/G18/G20 (FR19-FR21) and metric M3: the scorer runs on a schedule without the owner, is
idempotent, monitors calibration once labels mature, and reports failures, all before tagging v2.0. Slices 1-6 built the
keyless scorer, data tests and telemetry, but nothing runs on a schedule yet, and nothing has run on `main`.

**Measured facts this design rests on (2026-10-18, read-only, local copy of `gs://synthea-readmission-risk-gold/v2/main_200k/`):**
- With the v1 champion on `full_run_2b`, a rolling 13-week (91-day) matured cohort holds at most 10 positives (median 2;
  41 test positives, 1,539 test rows), so every tick would report "insufficient data" (floor 20). Hence D2.
- `v2/main_200k`: 20.3 MB, 9 objects, `reference_date` 20261012, 185,294 rows, 2,825 positives. Bundle `569fdab6`'s test
  partition holds 31,659 rows and 616 positives, discharged 2023-01-02 → 2026-09-12. Rolling 91-day positives: min 24,
  median 40, max 68. The UNCHANGED offline scorer (`scripts/score_discharges.py --gold-dir <local copy> --run-date 20250630
  --bundle bundles/569fdab6…`) passes every guard and scores 675 discharges in 7 s.
- `label_horizon − index_stop` on the test partition: 30.0 days at the 99th percentile; 60 rows exceed 30 days (50 of them
  positives; max 83.1 days, a positive); negatives: 30.0-32.8 days.
- Gold rows dated ≥ 2023-01-01 that the split assigned to no partition fall on 2023-01-01…01-13, 01-15, 01-17, 01-18,
  01-20, 01-22, 01-24, 01-27, 01-30, 02-06, **02-21, 04-05**, and 2026-09-14, 09-16, 09-19, 09-20, 09-23, 09-25 (all past the
  observation horizon 2026-09-13 00:00Z; last valid run date 20260912). So, with a **7-day** window ending on a Monday, the completeness guard
  (`scoring/selection.py` `select_held_out_window`, step 3) refuses every Monday from 2023-01-02 to 2023-02-06, plus
  2023-02-27 (window from 02-21) and 2023-04-10 (window from 04-05); 2026-09-14 is refused by the horizon check. **Every
  Monday from 2023-04-17 to 2026-09-07 passes: 178 simulated weeks** (checked by enumerating every window). (Revision 0
  said "46 rows between 2023-01-04 and 2026-09-01"; those dates were the query's bounds, not the rows' dates.)

## Scope

**In:**
1. Promotion of `569fdab6` (D2): bundle upload, `deploy/champion.json`, `deploy/PROMOTIONS.md`, scoring gold defaults
   (`Makefile` `score`/`score-bq`, `score.yml`) switched to `v2/main_200k`, a route-neutral `DEMO_NOTICE`.
2. The open TODO "add a `gold_fingerprint_definition` column to `serving.predictions`/`scoring_runs` before the first
   `spark-rowhash-v1` bundle is scored" (D2 makes it a prerequisite), with data test PRED-11.
3. A new package `src/readmission_risk/monitoring/` (config, simulated clock, O/E rule, cohort, BigQuery store, tick
   orchestration, ledger rendering); BigQuery table `serving.monitoring` with ASSERT script `monitoring.sql`; CLIs
   `scripts/monitor_tick.py`, `scripts/render_ledger.py`, `scripts/m3_check.py`, plus two stdlib-only, self-contained
   scripts, `scripts/publish_ledger.py` and `scripts/tick_issues.py`.
4. `.github/workflows/monitor.yml`, with six jobs: clock, score, evaluate, ledger-render (job A), ledger-commit (job B),
   issues.
5. Terraform: SAs `monitor` and `ledger-reader`, their grants and WIF bindings, a second WIF binding for `scorer` keyed to
   `monitor.yml`, table `serving.monitoring`, the two new columns, and the new predictions description.
6. The live runbook to v2.0 (Verification > L0-L17).
7. Documentation for D1/D2/D3 (list below) and memory updates.

**Out (named follow-ups at the end):**
- the Streamlit dashboard and its heartbeat (v3.0);
- the BigQuery gold table;
- v1 on the 200k population as an external-population scoring mode (v3.0 candidate);
- auto-closing issues;
- regenerating the README figure and sample table from the 200k champion;
- the as-of scoring path;
- label v2;
- re-running the 6b gate after the promotion;
- the clock's end in about 2029.

## Surfaces touched

| Surface | Change |
|---|---|
| `deploy/champion.json` | → `{"bundle_uri": "gs://synthea-readmission-risk-bundles/569fdab6…", "sha256": <sha256 of that bundle's manifest.json bytes>}`, written with `bundles.champion.champion_json` |
| `deploy/PROMOTIONS.md` (new) | Append-only Markdown log, newest last. Entry fields: date, bundle id + MLflow run + experiment + gold, previous champion, gate decision with the Phase-B rule table, override (yes/no), reason, PR number. The number is filled in by a follow-up commit on the same PR once it exists; until then the field reads `pending`. Entry 1: 2026-10-11, v1 LR `75a1448729ef…` (initial, slice 2, no gate). Entry 2: `569fdab6…` (gate REFUSE, owner override, the D2 text). Also states how to re-run the 6b gate after this change (Failure modes) |
| `deploy/monitoring.json` (new) | monitoring config (Interfaces > config) |
| `src/readmission_risk/scoring/constants.py:14-23` | `DEMO_NOTICE` replaced by a route-neutral text (Interfaces > DEMO_NOTICE), and the comment at `:14-16` ("must be revisited before this scorer is pointed at the untouched v2 population") rewritten to say it was revisited in slice 7. `DEMO_NOTICE` is also the scorer CLIs' help, the stdout report (`scoring/report.py:9,45`, tested in `tests/scoring/test_report.py`), the local Parquet metadata (`scoring/store.py`) and the BigQuery `predictions` description (`tests/test_infra.py` keeps the last equal). CLAUDE.md's slice-4 follow-up "`DEMO_NOTICE` is dataset-specific …" is marked done |
| `src/readmission_risk/scoring/bundle_guard.py:84-95` | `ModelBundle` gains `gold_fingerprint_definition: str \| None = None` as its last field; `validate_bundle` sets it from `manifest["gold_fingerprint_definition"]` (already read at `:386`). New public `compute_gold_fingerprint(definition: str, gold: pd.DataFrame) -> str` = `_FINGERPRINT_DEFINITIONS[definition](gold)`, `ValueError` for an unknown definition (`:74-75` holds the map; guard 6 is not changed) |
| `src/readmission_risk/warehouse/schemas/predictions.json`, `scoring_runs.json` | append `gold_fingerprint_definition` STRING NULLABLE as the last column, description "Fingerprint definition of the gold table (pandas-csv-sha256-v1 or spark-rowhash-v1); null on rows written before v2 slice 7." The BigQuery `prediction_schema_version` label stays "2": the local partition schema (`scoring/store.py` `SCHEMA_VERSION`) is unchanged, and the extra column exists in BigQuery only, like `scoring_run_id` |
| `src/readmission_risk/warehouse/scoring_runs.py` | `RunRecord` gains `gold_fingerprint_definition: str \| None` as the last field (the order check at `:62` stays) |
| `src/readmission_risk/warehouse/run.py` | the record initialiser sets the new field `None`; `on_validated` sets `record.gold_fingerprint_definition = bundle.gold_fingerprint_definition`; the staging writer is built with `fingerprint_definition=lambda: record.gold_fingerprint_definition` |
| `src/readmission_risk/warehouse/staging.py` | `make_staging_writer(client, scoring_run_id, fingerprint_definition: Callable[[], str \| None])`: after `with_run_id`, the writer appends a NON-nullable string column `gold_fingerprint_definition` (same `pa.field(..., nullable=False)` pattern) with the callable's value; `RuntimeError` if the value is None (cannot happen: the writer runs after `on_validated`) |
| `src/readmission_risk/warehouse/sql/predictions.sql` | PRED-11 (Interfaces) |
| `src/readmission_risk/warehouse/seeded_check.py` | PRED-11 case in `PREDICTION_MUTATIONS`; a new `seeded_monitoring_check(client)` (Interfaces > M5) |
| `src/readmission_risk/warehouse/schemas/monitoring.json`, `sql/monitoring.sql` (new) | table + MON-01..MON-12 |
| `src/readmission_risk/warehouse/bigquery.py:22` | `_SCHEMA_NAMES` += `"monitoring"` |
| `src/readmission_risk/warehouse/data_tests.py:16,18-21` | `SCRIPT_NAMES` += `"monitoring"`; `_DESCRIPTION_RE` and `_ASSERT_MESSAGE_RE` alternation becomes `(?:PRED\|RUNS\|MON)`; `run_data_tests(client, "monitoring", table=…)` takes no parameters (the `scoring_runs` branch becomes `name in ("scoring_runs", "monitoring")`) |
| `src/readmission_risk/ops/prereg.py:69-115` | `Prereg.monitoring` property returning `self.raw["monitoring"]`; no schema change |
| `src/readmission_risk/monitoring/` (new) | `__init__.py` (docstring only), `config.py`, `clock.py`, `rule.py`, `cohort.py`, `store.py`, `tick.py`, `ledger.py`, `preflight.py` |
| `scripts/score_to_bigquery.py` | when env `GITHUB_OUTPUT` is set, appends `scoring_run_id=<id>\nstatus=<status>\n` right after the job summary (`:105-108`), i.e. before the exit-code logic, so refused/failed runs export them too |
| `scripts/seeded_data_test_check.py` | new flag `--monitoring` runs `seeded_monitoring_check` instead of `seeded_check`. `--run-date` becomes optional: required without `--monitoring`, refused with it. `_lines` and `SeededCheckError.results` accept the flat `dict[str, DataTestResult]` (rendered under one heading "monitoring") as well as the nested one |
| `scripts/monitor_tick.py` (new, Typer: `clock`, `evaluate`, `catch-up`, `preflight`, `simulate`), `render_ledger.py`, `m3_check.py` (new, Typer); `publish_ledger.py`, `tick_issues.py` (new, stdlib-only, self-contained) | CLIs (Interfaces) |
| `Makefile` | `GOLD_DIR`/`REFERENCE_DATE`/`EXPERIMENT` stay (they feed `train`, `export`, `export-portable`, the v1 tooling). New `SCORE_GOLD_DIR ?= data/gold/main_200k`, used by `score`. `GOLD_URI ?= gs://synthea-readmission-risk-gold/v2/main_200k`, used by `score-bq`. `RUN_DATE ?= 20250630` stays (its 30-day window passes on `v2/main_200k`: the offline scorer accepted it 2026-10-18). New `M5_RUN_DATE ?= 20230417`, used by `m5-check` instead of `RUN_DATE`: PRED-11 makes `m5-check` require a date published by slice-7 code, and 20230417 is L4's 7-day publish. New `MONITOR_SA ?= monitor@synthea-readmission-risk.iam.gserviceaccount.com`, `LEDGER_SA ?= ledger-reader@synthea-readmission-risk.iam.gserviceaccount.com`, `WEEKS ?= 1`, `CHAMPION_BUNDLE_DIR ?= bundles/569fdab6c17d433542b2cd6b531ed8efc5677fb37b00a792d84f79803c6a0f72`, `LEDGER_OUT ?= data/monitoring/ledger`. New targets and recipes: `gold-200k` = `mkdir -p $(SCORE_GOLD_DIR) && gcloud storage cp "$(GOLD_URI)/*" $(SCORE_GOLD_DIR)/`; `monitor-preflight` = `$(BIN)/python scripts/monitor_tick.py preflight --gold-dir $(SCORE_GOLD_DIR) --bundle $(CHAMPION_BUNDLE_DIR)`; `monitor-catch-up` = `… catch-up --weeks $(WEEKS) --scorer-sa $(SCORER_SA) --monitor-sa $(MONITOR_SA)`; `ledger` = `$(BIN)/python scripts/render_ledger.py --out-dir $(LEDGER_OUT) --impersonate-service-account $(LEDGER_SA)`; `m3-check` = `$(BIN)/python scripts/m3_check.py --impersonate-service-account $(LEDGER_SA)`; `m5-monitoring` = `$(BIN)/python scripts/seeded_data_test_check.py --monitoring --impersonate-service-account $(MONITOR_SA)`; `monitor-simulate` = `$(BIN)/python scripts/monitor_tick.py simulate --gold-dir $(SCORE_GOLD_DIR) --bundle $(CHAMPION_BUNDLE_DIR) --out data/monitoring/simulation.csv`; `m5-check`'s recipe uses `--run-date $(M5_RUN_DATE)`. `.PHONY` is updated; `help` is generated from the `##` comments. `tests/test_tooling.py`'s variable whitelist (`:35`), its `EXPECTED_TARGETS` (~`:25`, the seven new targets) and its dry-run expectations (`:122,132,142`) are updated |
| `.github/workflows/ci.yml` | `pipeline-dataproc-runtime`'s pytest step runs `pytest -rs tests/pipeline tests/scripts`; `Makefile` `test-runtime` gets the same two paths |
| `scripts/export_portable_bundle.py:50`, `tests/portable/test_cli.py:51` | the printed "deploy/champion.json stays on the schema-1 bundle" becomes "deploy/champion.json is not changed" (still true, and no longer names a v1 bundle); the test follows |
| `.gitignore` | adds `/monitor_out/` and `/ledger_out/` (CLI scratch directories), so a laptop run leaves the tree clean |
| `.github/workflows/score.yml:62` | `--gold-dir gs://synthea-readmission-risk-gold/v2/main_200k` (`tests/test_infra.py` keeps it equal to `deploy/monitoring.json`'s `gold_uri`); `concurrency.group` stays `score`, which `monitor.yml` shares |
| `.github/workflows/monitor.yml` (new) | six jobs (Interfaces > monitor.yml) |
| `infra/iam.tf`, `infra/bigquery.tf` | new SAs/grants/bindings, `serving.monitoring`, predictions description; `infra/wif.tf` unchanged |
| `tests/` | see Verification |
| Documentation | the PRD (amendment block under the Status line, dated 2026-10-18: D1/D2/D3; the slice list marks slice 8 "moved to v3.0"; FR14's direct-write clause, FR21's dashboard half, FR22-FR24 and any metric naming the dashboard are marked "v3.0"); `CLAUDE.md` (v2 architecture: a slice-7 bullet, the champion change, including the slice-2 bullet's "pins the logistic-regression v1 bundle" text, which becomes historical with a pointer to slice 7, the scope change, the merge; Build & test commands: new targets, the two gold defaults, the V-post/L-post TODO points to slice 7's runbook; the `gold_fingerprint_definition` TODO marked done); `README.md` (v2.0 status line, champion line, a "Monitoring (simulated clock)" section with the two badges, the honesty sentence and the ledger link, and a caption on the existing figure/sample table "v1 (10k) reference models"); `docs/setup.md` (new section "Monitoring tick (v2 slice 7)": runbook, `make gold-200k`, catch-up, ledger branch, `gh workflow enable monitor.yml`, the 60-day rule); `docs/scoring.md` (champion and gold defaults; the 10k route `--bundle bundles/75a1448729ef… --gold-dir data/gold/full_run_2b`); `docs/results.md` (a "Champion" paragraph: override, Phase-B table, lookup still wins); memory `project_wif_proof_deferred_branch_ruleset.md`, `project_slice6c_portable_done.md`, and a new `project_slice7_monitoring.md` |

## Interfaces

### `deploy/monitoring.json`

```json
{
  "model_bundle_id": "569fdab6c17d433542b2cd6b531ed8efc5677fb37b00a792d84f79803c6a0f72",
  "gold_uri": "gs://synthea-readmission-risk-gold/v2/main_200k",
  "first_sim_date": "20230417",
  "last_sim_date": "20260907",
  "step_days": 7,
  "window_days": 7,
  "top_n": 10,
  "crons": {"burn_in": "7,37 13-18 * * *", "weekly": "17 6 * * 1"},
  "epoch": "20261018"
}
```

`monitoring/config.py`:
- `@dataclass(frozen=True) MonitoringConfig`: `model_bundle_id: str`, `gold_uri: str`, `first_sim_date: date`,
  `last_sim_date: date`, `step_days: int`, `window_days: int`, `top_n: int`, `crons: dict[str, str]` (exactly
  `burn_in`, `weekly`), `epoch: date`.
- `read_monitoring_config(path: Path = Path("deploy/monitoring.json")) -> MonitoringConfig`. A missing file raises
  `FileNotFoundError` (passed through). Everything else raises `ValueError`:
  - non-UTF-8 or non-JSON content, or a top-level value that is not an object;
  - a key set other than exactly the above;
  - `model_bundle_id` not 64 lowercase hex;
  - `gold_uri` not matching `gs://<bucket>/<path>`;
  - any date not `YYYYMMDD`;
  - `first > last`, or `(last − first).days % 7 != 0`;
  - `step_days != 7` or `window_days != 7` (fixed, so that consecutive ticks' windows tile exactly; any other value is a
    design change);
  - `top_n` not an int in [1, 2^31−1];
  - `crons.burn_in` not one of `"7,37 13-18 * * *"` (default) or `"7 13-22 * * *"` (the hourly fallback, UX flow), or
    `crons.weekly != "17 6 * * 1"`.
- `sim_grid(config) -> list[date]`: `first + 7k` for `k = 0 … (last − first)/7`, which is 178 dates.
- `cadence_for(event_name: str, schedule: str | None, config) -> str`:
  - `"burn_in"` if `event_name == "schedule"` and `schedule == crons["burn_in"]`;
  - `"weekly"` for the weekly cron;
  - `"dispatch"` for `workflow_dispatch`;
  - `"catch_up"` for `event_name == "laptop"`, which is every laptop run, `clock` or `catch-up`;
  - `ValueError` for any other event or an unknown cron string.
  - An empty `schedule` string is treated as None.
- `check_champion_matches(config, champion: Champion) -> None`: `ValueError` unless
  `champion.bundle_uri.rstrip("/").rsplit("/", 1)[-1] == config.model_bundle_id`. A champion change therefore forces a
  deliberate monitoring-config change, which also re-derives the clock range.
- `period_of(cron: str) -> timedelta`, keyed on the cron string itself, not on the cadence: `"7,37 13-18 * * *"` → 30 minutes; `"7 13-22 * * *"` → 60 minutes; `"17 6 * * 1"` → 7 days; `ValueError` otherwise. Used by the badge.

### `monitoring/clock.py`

```python
class ClockError(ValueError): ...

def next_sim_date(succeeded: Iterable[date], config: MonitoringConfig, *, requested: date | None = None) -> date
```
- `succeeded`: the sim dates of monitoring rows with `status = 'succeeded'` and `model_bundle_id = config.model_bundle_id`.
  Rows of other bundles are ignored, so a re-promotion restarts the clock.
- `ClockError` if any succeeded date is off the grid. That would be corrupted state; MON-06 asserts it too.
- `head` = the grid date after `max(succeeded)`, or `first_sim_date` if there is none. When `max(succeeded) ==
  last_sim_date`: a `requested` date on the grid is still allowed (a replay); `requested is None` raises
  `ClockError("simulated clock exhausted at <last>")`.
- `requested is None` → `head`. A given `requested` (a dispatch replay) must be on the grid and `<= head`; otherwise
  `ClockError`. Skipping ahead is not allowed. Replaying an already-succeeded date is allowed, and is how idempotency is
  proven.
- **Catch-up semantics:** the clock moves only on success. A failed or dropped tick leaves `head` unchanged, so the next
  tick processes the same sim date. Re-processing a date replaces its prediction partition (slice 4's per-date DML) and
  appends an evaluation with identical metrics. A scheduled tick advances at most one week; the laptop catch-up advances
  N.

### `monitoring/rule.py`

```python
@dataclass(frozen=True)
class AlertRule:
    matured_cohort_weeks: int      # 13
    min_positives_floor: int       # 20
    band: tuple[float, float]      # (0.80, 1.25)

def alert_rule_from_prereg(prereg: Prereg) -> AlertRule
def poisson_ci(observed: int) -> tuple[float, float]
def evaluate_rule(observed: int, expected: float, rule: AlertRule) -> RuleResult   # RuleResult(ratio, ci_low, ci_high, verdict)
```
- `alert_rule_from_prereg`: `ValueError` unless `metric == "observed_expected_ratio"`, `ci ==
  "exact_poisson_95_on_observed"`, `condition == "ci_excludes_1_and_point_outside_band"` and `auc == "report_only"`
  (`parse_prereg` already enforces these literals; the check repeats them at the point of use).
- `poisson_ci` is the exact (Garwood) 95% interval: `lo = chi2.ppf(0.025, 2k)/2` (0 if k == 0), `hi = chi2.ppf(0.975,
  2k+2)/2`. It uses `scipy.stats.chi2`; scipy is already a runtime pin.
- `ratio_and_ci(observed: int, expected: float) -> tuple[float, float, float] | None`: None when `expected <= 0`; otherwise `(observed / expected, lo / expected, hi / expected)` with `(lo, hi) = poisson_ci(observed)`. `observed / expected` is `models.evaluate.observed_expected_ratio`'s definition (`sum(y)/sum(p)`), restated on the sums because the sums are what the record stores. E is treated as fixed, the standard SIR convention. This is the **only** place a ratio or CI is computed: `evaluate_rule` calls it, and `evaluate_cohort` calls it directly on the incomplete branch. The record stores its values, and MON-07/MON-08 check them.
- `verdict`:
  - `"insufficient_data"` if `observed < min_positives_floor` (**E1:** "positives" means observed positives in the matured
    cohort, the quantity the exact Poisson CI is built on). Ratio and CI are still computed and reported.
  - Otherwise `"alert"` iff (`ci_high < 1` or `ci_low > 1`) **and** (`ratio < band[0]` or `ratio > band[1]`).
  - Otherwise `"ok"`.
- `ValueError` if `observed < 0`, or if `expected` is not finite or `<= 0`.

### `monitoring/cohort.py` (pure)

All instants are UTC. `sim_end(S) = (S + 1 day) at 00:00Z` (exclusive). `L` = the gold metadata's
`readmission_window_days` (30); `C = 7 · matured_cohort_weeks = 91` days.

- **Matured cohort (E2):** every scored discharge with `discharge_time ∈ [sim_end − L − C, sim_end − L)`, i.e. whose 30-day
  outcome window closed during the 13 simulated weeks before the sim date, evaluated with its final gold label.
  **Membership depends only on the discharge date, never on the outcome.** Revision 0 excluded rows with `label_horizon >=
  sim_end`, which would have dropped mostly positives (measured: 50 of the 60 rows whose `label_horizon` exceeds 30 days
  are positives) and biased O/E downward.
- The price, disclosed: for those rows, the final label (whether the readmitting stay counts as planned) depends on
  records dated after the simulated date. `n_label_after_sim` counts the matured rows with `label_horizon >= sim_end`. It
  is reported, never used in the verdict, and is at most 60 of 31,659 rows overall. This is monitoring only; no model is
  fitted, so it is no training leak.
- **Immature cohort:** scored discharges with `discharge_time ∈ [sim_end − L, sim_end)`. They give `n_immature` and
  `expected_immature = Σ risk_score` (label-free).
- `CohortSpec(label_window_days: int, cohort_days: int)`, built by `cohort_spec(rule: AlertRule, metadata: GoldMetadata) ->
  CohortSpec` with `cohort_days = 7 · rule.matured_cohort_weeks`. It raises `ValueError` unless `label_window_days == 30`
  and `cohort_days == 91`: the design (18 ticks, warm-up, preflight) is sized for these values, and other values are a
  design change.
- `required_ticks(S: date, spec: CohortSpec, config) -> RequiredTicks`, with `RequiredTicks(matured: tuple[date, ...],
  immature: tuple[date, ...])`. It computes, for every grid-spaced date `S_j = S − 7k` (`k ≥ 0`), whether its window
  `[S_j − 6 d, S_j + 1 d)` overlaps each range. For (30, 91):
  - matured `k = 4 … 17`, 14 dates;
  - immature `k = 0 … 4`, 5 dates; `k = 4` (window `[S − 34 d, S − 27 d)`) straddles both ranges.

  Dates before `first_sim_date` are included in the tuples; they simply can never be found.
- `read_window(S, spec) -> tuple[date, date]` = `(S − 7·max_k, S)`, where `max_k` is the largest `k` in `required_ticks`.
  For (30, 91) that is `(S − 119 d, S)`.
- `evaluate_cohort(predictions, labels, *, sim_date, rule: AlertRule, spec: CohortSpec, found_ticks: set[date], required:
  RequiredTicks) -> Evaluation`:
  - `predictions` has columns `encounter_id, discharge_time (tz-aware UTC), risk_score`, one row per encounter
    (`ValueError` on a duplicate). `labels` has columns `encounter_id, is_readmitted, label_horizon` from gold.
  - `ValueError` if a matured prediction has no label (the message carries the count).
  - `matured_complete = set(matured_ticks) <= found_ticks`; `immature_complete = set(immature_ticks) <= found_ticks`.
  - In both branches: `n_matured`, `observed = Σ is_readmitted` and `expected = math.fsum(risk_score)` over the matured rows that exist; `expected_immature` also uses `math.fsum`. Rows are first sorted by `encounter_id`. `math.fsum` is exactly rounded, so the sums do not depend on BigQuery's row order, and the kill-trigger comparison is exact. These are 0, 0 and 0.0 for an empty set. `ratio`/`ci_low`/`ci_high` = `ratio_and_ci(observed, expected)`, null when it returns None.
  - **Matured OR immature incomplete** (i.e. `coverage_found < coverage_required`) → `verdict = "incomplete_cohort"`;
    `evaluate_rule` is never called. A verdict other than `incomplete_cohort` therefore always has full coverage, which
    is exactly what MON-08 asserts for `alert`. This covers catch-up ticks
    1-4, whose matured set is empty.
  - All 18 ticks found and `n_matured == 0` → `"insufficient_data"` with ratio and CI null. Otherwise
    `evaluate_rule(observed, expected, rule).verdict`.
  - `n_immature`/`expected_immature` are null unless `immature_complete`.
  - `auc`: `sklearn.metrics.roc_auc_score` on the matured rows that exist, in both branches, if both classes are present, else None. Report only. `n_label_after_sim` is also counted in both branches.
  - Module homes: `RuleResult` and `AlertRule` live in `rule.py`; `CohortSpec`, `RequiredTicks` and `Evaluation` in
    `cohort.py`; `MonitoringRecord` (a mutable `@dataclass`, fields in the table order below) in `store.py`; `ClockRequest`,
    `ClockResult`, `EvaluateRequest`, `EvaluateResult` and `TickCheckError` in `tick.py`; `PreflightLine` in
    `preflight.py`.
  - Returns `Evaluation(sim_date, cohort_start, cohort_end, n_matured, observed, expected, ratio, ci_low, ci_high, auc,
    n_label_after_sim, n_immature, expected_immature, coverage_required, coverage_found, verdict)`.
    `coverage_required = len(matured ∪ immature ticks) = 18`; `coverage_found` = how many of those are in `found_ticks`.
  - Inputs are not mutated, and two calls on the same inputs return equal results.

### BigQuery `serving.monitoring` (`warehouse/schemas/monitoring.json`)

One row per tick attempt that reached the clock or evaluate stage with a BigQuery client. The JSON gives every field a
`description`, equal to the "meaning" column below. Table description: "Monitoring ticks on a simulated clock (synthetic
Synthea data, not for clinical use): one row per clock-failure or evaluate attempt." Field order = this table's order =
`MonitoringRecord`'s field order.

| column | type | mode | meaning |
|---|---|---|---|
| monitoring_run_id | STRING | REQUIRED | uuid4 hex of the attempt |
| status | STRING | REQUIRED | `succeeded`, `refused` or `failed` |
| failed_stage | STRING | NULLABLE | `clock`, `score` or `evaluate` when not succeeded |
| started_at | TIMESTAMP | REQUIRED | wall clock (UTC) when the attempt started |
| finished_at | TIMESTAMP | REQUIRED | `started_at` plus the monotonic elapsed time |
| sim_date | DATE | NULLABLE | the simulated date; null when the clock failed or the date did not parse |
| cadence | STRING | NULLABLE | `burn_in`, `weekly`, `dispatch` or `catch_up`; null only when the clock failed before resolving it |
| trigger | STRING | REQUIRED | `laptop`, or the GitHub Actions event name |
| github_run_url | STRING | NULLABLE | the Actions run, when run in Actions |
| git_commit | STRING | NULLABLE | commit of the monitoring code |
| git_dirty | BOOLEAN | NULLABLE | uncommitted changes on a laptop run; null in Actions |
| github_run_attempt | INTEGER | NULLABLE | `GITHUB_RUN_ATTEMPT` (1 for a first attempt); null on the laptop |
| history_tests_passed | BOOLEAN | NULLABLE | whether `monitoring.sql` passed before this row (FR18); null when it was not run (clock-stage rows) |
| scoring_run_id | STRING | NULLABLE | the score stage's scoring_runs attempt |
| model_bundle_id | STRING | NULLABLE | the monitored bundle |
| gold_fingerprint | STRING | NULLABLE | fingerprint of the gold the labels came from |
| gold_fingerprint_definition | STRING | NULLABLE | its definition |
| verdict | STRING | NULLABLE | `ok`, `alert`, `insufficient_data` or `incomplete_cohort`; non-null iff succeeded |
| cohort_start | DATE | NULLABLE | first discharge day of the matured cohort |
| cohort_end | DATE | NULLABLE | day after its last discharge day (exclusive) |
| n_matured | INTEGER | NULLABLE | matured cohort rows |
| observed | INTEGER | NULLABLE | observed readmissions in the matured cohort |
| expected | FLOAT | NULLABLE | sum of predicted probabilities in the matured cohort |
| oe_ratio | FLOAT | NULLABLE | observed / expected |
| oe_ci_low | FLOAT | NULLABLE | exact Poisson 95% lower bound of the ratio |
| oe_ci_high | FLOAT | NULLABLE | exact Poisson 95% upper bound of the ratio |
| auc | FLOAT | NULLABLE | ROC-AUC of the matured cohort (report only) |
| n_label_after_sim | INTEGER | NULLABLE | matured rows whose label depends on records after the simulated date |
| n_immature | INTEGER | NULLABLE | scored discharges whose labels are not yet mature |
| expected_immature | FLOAT | NULLABLE | label-free expected positives of the immature cohort |
| coverage_required | INTEGER | NULLABLE | ticks the cohorts need (18) |
| coverage_found | INTEGER | NULLABLE | of those, ticks with complete published predictions |
| min_positives_floor | INTEGER | NULLABLE | the pre-registered floor applied |
| band_low | FLOAT | NULLABLE | the pre-registered band's lower edge |
| band_high | FLOAT | NULLABLE | the pre-registered band's upper edge |
| error_type | STRING | NULLABLE | exception class of a failed or refused attempt |
| error_message | STRING | NULLABLE | its message, at most 4,000 characters |

Terraform: partitioned DAY on `started_at`, `require_partition_filter = true`, clustering `["sim_date"]`,
`deletion_protection = true`. Every Python read filters `started_at >= TIMESTAMP(config.epoch)`.

**`monitoring.sql`.** It checks the table's state before each insert, with the same shape rule as `scoring_runs.sql`: one
reading `DECLARE`, and every `ASSERT` reads only `c`. It takes no parameters; `{table}` is rendered as in
`data_tests.render_script`. Its `WHERE` is the literal `` `started_at` >= TIMESTAMP '2026-10-18' `` (the epoch; a test keeps
it equal to `deploy/monitoring.json`'s `epoch`). An empty table passes. The assertions:
- **MON-01:** `monitoring_run_id` is unique.
- **MON-02:** status is in the three values.
- **MON-03:** `finished_at >= started_at`.
- **MON-04:** `(status = 'succeeded') = (verdict IS NOT NULL)`.
- **MON-05:** `status != 'succeeded' OR (<all of the following IS NOT NULL> AND history_tests_passed)`: succeeded rows have a passed history test and non-null `sim_date`, `cadence`, `scoring_run_id`, `model_bundle_id`, `n_matured`,
  `coverage_required` and `coverage_found`.
- **MON-06:** `sim_date IS NULL OR MOD(DATE_DIFF(sim_date, DATE '2023-04-17', DAY), 7) = 0`. The anchor is a literal; a
  test keeps it equal to `first_sim_date`.
- **MON-07:** `oe_ratio IS NULL OR (oe_ci_low <= oe_ratio AND oe_ratio <= oe_ci_high)`.
- **MON-08:** `verdict IS NULL OR verdict != 'alert' OR (observed >= min_positives_floor AND (oe_ci_high < 1 OR oe_ci_low > 1) AND (oe_ratio <
  band_low OR oe_ratio > band_high) AND coverage_found = coverage_required)`.
- **MON-09:** `verdict IS NULL OR verdict IN ('ok', 'alert', 'insufficient_data', 'incomplete_cohort')`.
- **MON-10:** `sim_date IS NULL OR sim_date BETWEEN DATE '2023-04-17' AND DATE '2026-09-07'` (literals kept equal to the
  config by a test).
- **MON-11:** `cadence IS NULL OR cadence IN ('burn_in', 'weekly', 'dispatch', 'catch_up')`.
- **MON-12:** `(status = 'succeeded') = (failed_stage IS NULL) AND (failed_stage IS NULL OR failed_stage IN ('clock', 'score', 'evaluate'))`.

Each row check is written `COUNTIF((<condition>) IS NOT TRUE)`, so a NULL counts as a failure. **NULL audit:** every
condition above is written so that it is never NULL on a valid failed, refused, clock-stage or incomplete row. Each
nullable operand is guarded with `IS NULL OR …` or by `status != 'succeeded' OR …`. The M5 clean copy proves it (below). Labels follow the pattern
`AS 'MON-0n: <message>'`.

**PRED-11.** Appended to `predictions.sql`. Two new members of `c`: `COUNT(DISTINCT gold_fingerprint_definition) AS
n_distinct_fp_definition` and `COUNTIF((gold_fingerprint_definition IN ('pandas-csv-sha256-v1', 'spark-rowhash-v1')) IS NOT
TRUE) AS bad_fp_definition`. The assertion is `ASSERT c.n_distinct_fp_definition = 1 AND c.bad_fp_definition = 0 AS
'PRED-11: gold_fingerprint_definition is missing, unknown or not constant';`. No RUNS assertion is added for the new column,
because slice-4 history rows are NULL.

**M5 for the new assertions.**
- `PREDICTION_MUTATIONS["PRED-11"]` sets `gold_fingerprint_definition = NULL` on one row of the fresh copy (the existing
  `make m5-check`, run as the scorer).
- `seeded_monitoring_check(client) -> dict[str, DataTestResult]` runs as the **monitor** SA through `make m5-monitoring`
  (`scripts/seeded_data_test_check.py --monitoring`). It follows `seeded_check`'s pattern (`copy_name`, a `live` set
  dropped in `finally`, `SeededCheckError` when any expectation fails). For the id `clean` and each MON id:
  1. `name = copy_name("m", id)` gives `scratch.m5m_<id>_<hex8>`: unique per run, so neither reruns nor a crash can
     collide; leftovers expire with the dataset's 1-day default.
  2. Create the copy: ``CREATE TABLE <name> PARTITION BY DATE(`started_at`) AS SELECT * FROM serving.monitoring WHERE
     `started_at` >= TIMESTAMP '2026-10-18' AND FALSE``. That is an empty CTAS with the same columns. A CTAS does not
     inherit `require_partition_filter`, so later DML needs no partition predicate. It needs `tables.getData` on the source
     (table-level `scorerTableWriter`) and create in `scratch` (`dataEditor`).
  3. `INSERT INTO <name> SELECT * FROM UNNEST([<MONITORING_CLEAN_ROWS>])`: five `STRUCT` literals in schema order, all
     valid, so the clean copy proves the NULL audit. Four of them are the companion rows:
     - `m5failed`: `failed`/`score`, verdict and metrics NULL, `history_tests_passed TRUE`;
     - `m5refused`: `refused`/`evaluate`, NULLs as `m5failed`;
     - `m5clock`: `refused`/`clock`, `sim_date` and `cadence` NULL, `history_tests_passed` NULL;
     - `m5incomplete`: `succeeded`, verdict `incomplete_cohort`, `n_matured 0`, `observed 0`, `expected 0.0`,
       ratio/CI/auc NULL, `coverage_found 4`, `coverage_required 18`, `history_tests_passed TRUE`, `scoring_run_id
       'm5run2'`, `sim_date DATE '2023-04-17'`; every other field as in the base row. The same holds for `m5failed`, `m5refused` and `m5clock`: fields not listed come
       from `MONITORING_BASE_ROW`, and each row has its own `monitoring_run_id`.

     The fifth is `MONITORING_BASE_ROW`: a valid succeeded row with `monitoring_run_id = 'm5base'`, `status 'succeeded'`,
     `failed_stage NULL`, `started_at = CURRENT_TIMESTAMP()`, `finished_at = CURRENT_TIMESTAMP()`, `sim_date DATE
     '2023-08-14'`, `cadence 'catch_up'`, `trigger 'laptop'`, `scoring_run_id 'm5run'`, `model_bundle_id` = the config's,
     `verdict 'ok'`, `n_matured 2000`, `observed 40`, `expected 40.0`, `oe_ratio 1.0`, `oe_ci_low 0.7144`, `oe_ci_high
     1.3617`, `coverage_required 18`, `coverage_found 18`, `history_tests_passed TRUE`, `min_positives_floor 20`, `band_low 0.8`, `band_high 1.25`,
     and every other column `CAST(NULL AS <BigQuery type>)`. The literal is generated in Python from `monitoring.json` (STRING→STRING, INTEGER→INT64, FLOAT→FLOAT64, BOOLEAN→BOOL, TIMESTAMP, DATE), because an untyped NULL in a STRUCT literal would be INT64. A test pins the generated literal's column order and types.
  4. Apply the id's statement from `MONITORING_MUTATIONS`, against the copy, with no parameters, each touching only the base row (so a companion row can never fire another
     assertion first):
     - MON-01: `INSERT INTO {t} SELECT * FROM {t} WHERE monitoring_run_id = 'm5base'`
     - MON-02: `UPDATE {t} SET status = 'done' WHERE monitoring_run_id = 'm5base'`
     - MON-03: `UPDATE {t} SET finished_at = TIMESTAMP_SUB(started_at, INTERVAL 1 SECOND) WHERE monitoring_run_id = 'm5base'`
     - MON-04: `UPDATE {t} SET verdict = NULL WHERE monitoring_run_id = 'm5base'`
     - MON-05: `UPDATE {t} SET scoring_run_id = NULL WHERE monitoring_run_id = 'm5base'`
     - MON-06: `UPDATE {t} SET sim_date = DATE '2023-08-15' WHERE monitoring_run_id = 'm5base'`
     - MON-07: `UPDATE {t} SET oe_ci_low = oe_ratio + 1 WHERE monitoring_run_id = 'm5base'`
     - MON-08: `UPDATE {t} SET verdict = 'alert' WHERE monitoring_run_id = 'm5base'` (the base O/E of 1.0 is inside its CI, so the alert is
       inconsistent)
     - MON-09: `UPDATE {t} SET verdict = 'great' WHERE monitoring_run_id = 'm5base'`
     - MON-10: `UPDATE {t} SET sim_date = DATE '2026-09-14' WHERE monitoring_run_id = 'm5base'`
     - MON-11: `UPDATE {t} SET cadence = 'hourly' WHERE monitoring_run_id = 'm5base'`
     - MON-12: `UPDATE {t} SET failed_stage = 'score' WHERE monitoring_run_id = 'm5base'`

     Every companion row passes all twelve checks unchanged; a test on the generated SQL pins that each mutation's
     `WHERE` names `m5base`. MON-06's date stays inside MON-10's bounds and MON-10's date is on the grid, so each mutation fires exactly its own
     assertion. The script stops at the first failing ASSERT, and the order MON-01…12 is relied on.
  5. `run_data_tests(client, "monitoring", table=name)`; expect `passed` for `clean` and exactly that label for each id.
     Drop the copy.

  The check needs no real tick, so it runs at L4. The base row's CI values are `poisson_ci(40)/40`, to 4 dp
  (implementation verifies them with scipy).

### `monitoring/store.py` (BigQuery)

`google` is imported inside the functions. Every query goes through `warehouse.bigquery.query_config`. Frames are built
by hand from `job.result()` rows (`[dict(row.items()) for row in rows]` → `pd.DataFrame(rows, columns=[...])`), never with
`to_dataframe()`, which would need the unpinned `db-dtypes`. `FakeJob.rows` holds dicts (`dict.items()` works the same).
- `monitoring_table_id() -> str` = `table_id("serving", "monitoring")`.
- `read_ticks(client, config) -> pd.DataFrame`: every monitoring column, rows since epoch, ordered by `started_at,
  monitoring_run_id`. An empty result gives an empty frame with those columns.
- `succeeded_sim_dates(ticks, config) -> set[date]`.
- `published_runs(client, config, lo: date, hi: date) -> pd.DataFrame`, one query:
  ```sql
  SELECT p.as_of_date, p.scoring_run_id, COUNT(*) AS n_rows
  FROM `<table_id("serving", "predictions")>` p   -- built with table_id() + quoted(), as every query in the package
  WHERE p.as_of_date BETWEEN @lo AND @hi AND p.model_bundle_id = @bundle
  GROUP BY 1, 2
  ```
  It is then joined in Python to `scoring_run_facts`.
- `scoring_run_facts(client, scoring_run_ids, config) -> pd.DataFrame`: `scoring_run_id, run_date, status, published,
  n_predictions, model_bundle_id, window_days, top_n, gold_fingerprint, gold_fingerprint_definition` from
  `serving.scoring_runs`, with `started_at >= TIMESTAMP(@epoch) AND scoring_run_id IN UNNEST(@ids)`.
- `read_scoring_runs(client, config, limit: int = 500) -> pd.DataFrame` (for the ledger): `scoring_run_id, started_at,
  run_date, status, trigger, github_run_url` with `started_at >= TIMESTAMP(@epoch)`, `ORDER BY started_at DESC,
  scoring_run_id LIMIT @limit`.
- `found_ticks(published, facts, config, candidates: Iterable[date]) -> set[date]` (pure). A candidate date counts as found
  iff all of these hold:
  - it has exactly one `scoring_run_id` with rows;
  - that run's facts are `status = 'succeeded'`, `published`, `run_date ==` the candidate date, `model_bundle_id ==
    config.model_bundle_id`, `window_days == 7`, `top_n == config.top_n`;
  - `n_rows == n_predictions`.

  This does not depend on monitoring rows: a replay, or a `score.yml` overwrite with another window, is judged by what is
  actually published for that date.
- `read_cohort_predictions(client, config, lo: date, hi: date, scoring_run_ids) -> pd.DataFrame`: `encounter_id,
  discharge_time, risk_score, as_of_date, scoring_run_id, gold_fingerprint, gold_fingerprint_definition` with
  `as_of_date BETWEEN @lo AND @hi AND scoring_run_id IN UNNEST(@ids)`. The ids are the found ticks' runs, and `(lo, hi) =
  cohort.read_window(S, spec)`.
- `append_monitoring(client, record: MonitoringRecord) -> None`: a parameterised DML INSERT with job id
  `monitor_<monitoring_run_id>`, `job_retry=None`, and the post-condition `num_dml_affected_rows == 1` (`RuntimeError`
  otherwise). The parameter types are `STRING`, `TIMESTAMP`, `DATE`, `BOOLEAN`→`BOOL`, `INTEGER`→`INT64`, `FLOAT`→`FLOAT64`,
  from a local map in `store.py` (slice 4's `scoring_runs._PARAMETER_TYPES` is not modified). A field-order check against
  the schema mirrors `scoring_runs.record_parameters`.
- `record_monitoring(client, record) -> tuple[bool, BaseException | None]`: the "not recorded" resolution. On a
  `RuntimeError` it reports not recorded. On any other exception it calls `warehouse.staging.resolve_job(client,
  "monitor_<id>")`; `True` means recorded, anything else not recorded, with a warning. This mirrors `warehouse/run.py`
  `_record`.

### `monitoring/tick.py`

```python
@dataclass(frozen=True)
class ClockRequest:
    event_name: str               # GITHUB_EVENT_NAME, or "laptop" when GITHUB_ACTIONS != "true"
    schedule: str | None          # github.event.schedule ("" -> None)
    requested: str | None         # dispatch input / --sim-date ("" -> None)
    config_path: Path = Path("deploy/monitoring.json")
    champion_path: Path = DEFAULT_CHAMPION_PATH

def prepare_tick(request: ClockRequest, *, client_factory=None) -> ClockResult
```
`ClockResult(sim_date: date | None, cadence: str | None, record: MonitoringRecord | None, error: BaseException | None,
recorded: bool)`. The steps:
1. **Build the client first**, as `evaluate_tick` does, so that every later refusal can be recorded.
2. Read the config and run `check_champion_matches(config, read_champion(champion_path))`.
3. Resolve the cadence with `cadence_for` (the laptop gets `catch_up`).
4. Parse `requested`, run `read_ticks`, then `next_sim_date`.

On success nothing is written: the evaluate stage writes the tick's row. On any exception, if a client exists, it writes
a row with `failed_stage = "clock"` and a status from `warehouse.run._classify(exc, publish_attempted=False)`, the same
classifier evaluate uses (`ClockError`, config and champion `ValueError`s → `refused`; infrastructure errors, including a
refusal-shaped `ValueError` raised from a 503, → `failed`), and returns the error. If no client could be built, `recorded = False` (exit
4). It never raises an `Exception`.

```python
@dataclass(frozen=True)
class EvaluateRequest:
    sim_date: str                 # YYYYMMDD from the clock stage
    cadence: str
    scoring_run_id: str | None    # the score stage's output ("" -> None)
    score_result: str             # needs.score.result: success | failure | cancelled | skipped; "success" on the laptop
    config_path: Path = Path("deploy/monitoring.json")
    prereg_path: Path = Path("prereg.yaml")

def evaluate_tick(request: EvaluateRequest, *, client_factory=None) -> EvaluateResult
```
`EvaluateResult(record, evaluation: Evaluation | None, error, recorded)`. It never raises an `Exception`. Order:
1. Build the client.
2. Parse and validate `sim_date` (on the grid and within `[first_sim_date, last_sim_date]`) and `cadence` (one of the
   four values). **Only values that pass are copied into the record**; an invalid one stays NULL in the row, and its
   text goes into `error_message`. A refusal can therefore never write an off-grid date or an unknown cadence, which
   would trip MON-06/10/11 on every later tick.
3. Run `monitoring.sql` on the real table and set `history_tests_passed`. A data-test failure raises
   `TickCheckError("evaluate", <MON label>)`: `status = failed`, `failed_stage = evaluate`, `error_type =
   "TickCheckError"`, `error_message` = the MON label.
4. `score_result != "success"` or no `scoring_run_id` → `status = failed`, `failed_stage = score`.
5. Read the prereg rule (`alert_rule_from_prereg(load_prereg(prereg_path))`). The config itself is read at the start of
   step 2, which needs it for the grid check; a config-read failure there is `refused`/`evaluate` via `_classify`.
6. Run `read_ticks` and `next_sim_date(succeeded, config, requested=sim_date)`. That function's own checks (on the grid,
   `<= head`, replays allowed after exhaustion) are the re-check; its `ClockError` → `refused`/`evaluate`. This is the concurrency re-check
   (Failure modes).
7. `scoring_run_facts` for this tick's run → `failed`/`score` unless all of these hold: succeeded; published; `run_date
   == sim_date`; the config bundle; `window_days == 7`; `top_n == config.top_n`.
8. `published_runs` over `cohort.read_window(S, spec)`, then `scoring_run_facts` for those runs, then `found_ticks`. →
   `failed`/`score` unless `S ∈ found_ticks` **and** the run found for `S` is this tick's `scoring_run_id`: the tick's own
   publish must be the one currently in place.
9. `read_cohort_predictions` for the found ticks' runs.
10a. Load the gold via `warehouse.gold_source.materialized_gold_dir(config.gold_uri)` and
    `models.data.load_gold_table_with_metadata`, then build `spec = cohort_spec(rule, metadata)`.
10b. (after 9) Compute `fp = scoring.bundle_guard.compute_gold_fingerprint(definition, gold)`, where `definition` is the
    single distinct `gold_fingerprint_definition` of the prediction rows. `TickCheckError("evaluate", …)` if any row's
    definition is NULL (data published before slice 7; the bundle filter makes it unlikely), if there is more than one
    definition, or
    if `fp` differs from any prediction row's `gold_fingerprint`. This works for both gold versions, so test fixtures may
    use version-1 gold. For version-2 gold the loader has already recomputed `spark-rowhash-v1`, so this recomputes it a
    second time (a few seconds). This is deliberate: it checks the predictions' recorded fingerprint against the gold
    actually loaded, through the scorer's own function, with no special case per version.
11. `evaluate_cohort`.
12. `record_monitoring`.

Execution order: 1-7, 10a, 8, 9, 10b, 11, 12. A step-2 validation failure is a `ValueError` → `refused`/`evaluate`. `prepare_tick` follows the same rule for `requested`: a refusal row never carries an invalid date.

**Non-exception failures** are raised internally as `TickCheckError(stage, message)`: steps 4, 7 and 8 with stage `score`,
and step 10b (more than one definition, or a fingerprint mismatch) with stage `evaluate`. `TickCheckError` is a `RuntimeError` subclass, and caught by the same handler. `record.status = "failed"`, `failed_stage = stage`,
`error_type = "TickCheckError"`, `error_message = message` (e.g. "score job result: failure", "scoring run <id> has
window_days 30, expected 7"), and `EvaluateResult.error` is that exception.

**Record population on rows that did not succeed:** every field known at the moment of the failure is filled; nothing
is back-filled.
- `model_bundle_id` = `config.model_bundle_id` once the config is read (`m5failed`/`m5refused` carry it).
- `min_positives_floor`/`band_*` once the rule is read.
- `scoring_run_id` whenever the score stage passed one.
- `gold_fingerprint`/`gold_fingerprint_definition` once step 10b has computed them.
- All metric fields stay NULL unless `evaluate_cohort` returned.

**Other exceptions** are classified by `warehouse.run._classify(exc, publish_attempted=False)`, imported, not copied:
evaluate never publishes predictions, so a refusal-shaped `ValueError` is `refused` and everything else `failed`, with
`failed_stage = "evaluate"`. `trigger`, `github_run_url`, `git_commit` and `git_dirty` come from
`warehouse.run._trigger_and_url` / `_git_state`, also imported.

### `monitoring/ledger.py` (pure)

- `render_ledger(monitoring: pd.DataFrame, scoring_runs: pd.DataFrame, *, config) -> str` writes Markdown with no
  wall-clock "rendered at" field, so the same inputs give byte-identical output.
- The header is verbatim: "**Simulated clock.** Each tick advances a simulated date by one week through the held-out
  2023–2026 discharges of a synthetic 200,000-patient population; the wall-clock time is when the tick ran. Ticks labelled
  catch-up (laptop) were run by the owner to fill the first weeks; ticks labelled burn-in (accelerated cadence) are
  scheduled every 30 or 60 minutes on a burn-in afternoon; weekly ticks are scheduled on Mondays. This ledger shows
  repeatability and idempotency, not weeks of unattended operation. The monitored discharges come from a gold table
  built with hindsight (deaths and later stays shape which discharges are included), so this is a demonstration of the
  monitoring mechanics, not an estimate of live calibration. The exact Poisson interval treats readmissions as independent
  events; patients with several stays in a cohort make it somewhat too narrow. Synthetic data, not for clinical use. Source of truth:
  BigQuery `serving.scoring_runs` and `serving.monitoring`."
- Then "## Ticks", newest first, with columns: wall time (`started_at`, UTC, to the minute; the badge uses `started_at` too), cadence label (`burn-in (accelerated cadence)`,
  `weekly`, `dispatch`, `catch-up (laptop)`, `-`), sim date, status (+ failed stage), verdict, O/E [95% CI] (3 dp),
  observed / expected (1 dp), n matured, AUC (report only), immature expected, run link (or `laptop`).
- Then "## Other scoring runs": scoring_runs rows since epoch whose `scoring_run_id` is in no monitoring row, the last 50,
  newest first: wall time, run date, status, trigger, link.
- Sorting is by `started_at`, then id. Floats use fixed format; NaN/None renders as `-`.
- `last_tick_badge(monitoring: pd.DataFrame, *, cron_state: str | None) -> dict`:
  - `cron_state` is the cron string of the committed `monitor.yml`'s `schedule` (one of the three allowed), or None when
    there is none. It is parsed by `render_ledger.py`.
  - It reads the latest row by `started_at`, any cadence, any status.
  - Returns `{"schemaVersion": 1, "label": "last tick (simulated clock)", "message": "<wall date YYYY-MM-DD HH:MM UTC> ·
    sim <YYYY-MM-DD or ->"<suffix>, "color": …}`.
  - `suffix` is `" · stale after <YYYY-MM-DD HH:MM UTC of (latest row with trigger 'schedule').started_at + 2 ×
    period_of(cron_state), truncated to the minute>"` when `cron_state` is set and a scheduled row exists (the time is in
    the same format as the message's wall date); `" · schedule not started"` when `cron_state` is set and no scheduled
    row exists; `" · dispatch only"` when `cron_state` is None.
  - In the burn-in cron state the badge reads "stale after" a time about an hour after the afternoon's last slot. The (c)
    PR is merged the same day, which ends that state. This is documented in `docs/setup.md`.
  - `color`: `brightgreen` if the latest row succeeded, `red` otherwise, `lightgrey` if there are no rows.
  - **E4:** FR21's "stale since <date>" needs a comparison at view time, which a static README cannot make. The badge
    states the time after which the reader should consider the schedule stale. The v3 dashboard will compute "stale
    since".

### CLIs and exit codes

- **`scripts/monitor_tick.py clock [--sim-date YYYYMMDD] [--impersonate-service-account SA]`.**
  - Reads env `GITHUB_EVENT_NAME` (`laptop` when `GITHUB_ACTIONS != "true"`, so the laptop cadence is `catch_up`),
    `SCHEDULE` and `REQUESTED_SIM_DATE` (the workflow passes the dispatch input via env). `--sim-date`, if given, wins
    over the env. On the laptop `clock` only resolves and prints; it is a read-only probe, except that a refusal is
    recorded.
  - Calls `prepare_tick`; prints `sim_date`/`cadence`. When `GITHUB_OUTPUT` is set it appends `sim_date=YYYYMMDD` and
    `cadence=<c>`; it also writes a job summary.
  - Exit 0 resolved; 1 refused; 2 failed; 4 refused or failed and not recorded.
- **`scripts/monitor_tick.py evaluate --sim-date … --cadence … --scoring-run-id … --score-result … [--out-dir monitor_out]
  [--impersonate-service-account SA]`.**
  - Calls `evaluate_tick`; writes `<out-dir>/evaluation.json` (`{"status", "failed_stage", "verdict", "sim_date",
    "cadence", "oe_ratio", "oe_ci_low", "oe_ci_high", "observed", "expected", "n_matured", "monitoring_run_id",
    "scoring_run_id"}`, nulls allowed) and a job summary: a `| field | value |` table of sim date, cadence, status (+
    stage), verdict, O/E [CI], observed / expected, n matured, coverage found/required, scoring run id, error. `clock`'s
    summary is the same table with sim date, cadence, head and status.
  - On the laptop (`GITHUB_ACTIONS != "true"`) `evaluate` refuses, like `catch-up`, unless the tree is clean and HEAD
    equals `origin/main`. The check runs before any client is built: an unrecorded refusal, exit 1. When `GITHUB_OUTPUT` is set it appends `status=<status>` and
    `verdict=<verdict or empty>`.
  - Exit 0 succeeded, whatever the verdict (an alert is a finding, not a failure); 1 refused; 2 failed; 4 not recorded.
- **`scripts/monitor_tick.py catch-up --weeks N --scorer-sa SA --monitor-sa SA`** (laptop only).
  - Refuses if `git status --porcelain` is non-empty, or if `git rev-parse HEAD` ≠ `git rev-parse origin/main` after `git
    fetch origin main`. Catch-up reads `deploy/*.json` and `prereg.yaml` from the working tree, which must be exactly
    `main`.
  - Refuses if `GITHUB_ACTIONS == "true"`, if N is not in [1, 20], or if `gh run list --workflow monitor.yml --json status
    --limit 20` shows a run whose `status != "completed"`. That covers every non-terminal state (queued, in_progress,
    waiting, pending, requested) and is the no-overlap rule. It also refuses if `gh` is missing or fails.
  - Loops N times:
    1. `gcp.configure(monitor)`, then `prepare_tick(ClockRequest("laptop", None, None))` (cadence `catch_up`).
    2. `gcp.configure(scorer)`, then `score_to_bigquery(BigQueryRunRequest(gold_uri=config.gold_uri,
       run_date=<sim>, top_n=config.top_n, window_days=7))` (champion default).
    3. `gcp.configure(monitor)`, then `evaluate_tick(..., score_result="success" if scoring.record.status == "succeeded"
       else "failure")`.
  - It stops at the first tick that did not succeed. The exit code is always in `monitor_tick`'s own scheme: 1 refused, 2
    failed, 4 not recorded.
    - A clock-stage problem maps from `ClockResult`.
    - A score-stage problem: evaluate still runs with `score_result="failure"`, so the tick is recorded as
      `failed`/`score`, and the exit is 2, or 4 if that row could not be recorded. `score_to_bigquery`'s exception is
      caught (it never raises; `BigQueryRunResult.error` is printed), not re-raised.
    - An evaluate problem maps from `EvaluateResult`.
    - It prints one line per tick: `sim_date status verdict`.
- **`scripts/monitor_tick.py preflight --gold-dir DIR --bundle DIR [--config deploy/monitoring.json]`** (offline,
  `monitoring/preflight.py`: `preflight(gold_dir: Path, bundle_dir: Path, config: MonitoringConfig) ->
  list[PreflightLine]`, with `PreflightLine(sim_date, n_rows: int | None, refusal: str | None)`). Tests pass a small
  config whose grid fits the synthetic gold.
  - Loads gold with `load_gold_table_with_metadata`, and validates the bundle with `validate_bundle` (guards and
    integrity; nothing is unpickled).
  - For every grid date it calls `select_held_out_window(gold, bundle.partition_of, run_date=S, window_days=7,
    observed_until=observation_horizon(...))`.
  - Prints one line per date (`S n_rows` or `S REFUSED <reason>`) and a total.
  - Exit 0 if every grid date is accepted, 1 otherwise.
- **`scripts/monitor_tick.py simulate --gold-dir DIR --bundle DIR --out CSV [--config …] [--prereg prereg.yaml]`**
  (offline; the sanity check and L10's oracle; `monitoring/preflight.py` `simulate(gold_dir, bundle_dir, config, rule)
  -> pd.DataFrame`).
  - For every grid date, calls the unchanged `scoring.pipeline.score_run_date(ScoringConfig(gold_dir, run_date=S.strftime("%Y%m%d"),
    bundle_uri=str(bundle_dir), top_n=config.top_n, window_days=7), writer=<in-memory writer that keeps the frame and
    returns "memory://S">)`. This is the scorer's own path: guards, completeness check, label-dropping and
    `build_predictions` arguments. `score_run_date` validates and loads the bundle itself on every date. It loads the gold itself, so each date reloads it (~7 s each, ~20 min for 178 dates, acceptable for
    an offline check); `score_run_date` is not changed.
  - Concatenates the per-date predictions. Then, for every grid date `S`, it calls `evaluate_cohort` with
    `found_ticks` = the grid dates ≤ `S` and ≥ `first_sim_date`, which is exactly what a clean catch-up-then-tick
    history gives. `evaluate_cohort` ignores every prediction row outside the matured and immature ranges of the `S` it
    evaluates, including rows of dates after `S`; this is stated in `evaluate_cohort`'s contract and tested.
  - Writes one CSV row per date with every `Evaluation` field.
  - No GCP, no BigQuery. Exit 0, or 2 on any error. Make target `monitor-simulate`.
- **`scripts/render_ledger.py --out-dir DIR [--workflow .github/workflows/monitor.yml] [--impersonate-service-account
  SA]`.**
  - Reads monitoring and scoring_runs since epoch.
  - Determines `cron_state` from the workflow file's `- cron: "…"` lines: the cron string if it equals
    `deploy/monitoring.json`'s `burn_in` or `weekly`; None if there is none; `ValueError` for an unknown cron or for more
    than one cron line.
  - Writes `DIR/docs/run-ledger.md`, `DIR/docs/last-tick.json` and `DIR/meta.json` (`{"sim_date": latest row's sim date or
    null, "run_url": env-derived or null}`). The job summary holds the first 30 tick rows.
  - Exit 0, or 2 on any failure.
- **`scripts/publish_ledger.py --in-dir DIR --branch ledger`** (stdlib only, self-contained, job B).
  - Message: `Update run ledger (sim <meta.sim_date or ->, run <GITHUB_RUN_ID>)`.
  - For `docs/run-ledger.md` then `docs/last-tick.json`:
    1. `gh api repos/$GITHUB_REPOSITORY/contents/<path>?ref=<branch>` → `sha` and base64 `content`; a 404 means a new
       file.
    2. Skip if the decoded content equals the new bytes.
    3. Otherwise write `{"message", "branch", "content": <base64>, "sha"?}` to a temporary JSON file and run `gh api -X PUT
       repos/$GITHUB_REPOSITORY/contents/<path> --input <file>`. The content never goes on the command line (the Linux
       per-argument limit is 128 KiB); a test publishes a 900 KiB ledger.
    4. GitHub's contents GET returns `"encoding": "none"` with an empty `content` for files over 1 MB. That case is
       treated as "changed" (the PUT still uses the returned `sha`), and a test covers it. Expected ledger size, a
       projection: ~200 bytes per tick row, so about 1 MB only after ~5,000 ticks.
  - Exit 1 if the branch does not exist (`gh api repos/$GITHUB_REPOSITORY/branches/<branch>` 404; L4b creates it, the
    script never does) or on any `gh` error.
  - **HTTP status handling.** Every read uses `gh api --include …`. The status code is parsed from the first output line
    (`HTTP/<v> <code> …`), and the body follows the first blank line. `gh` exits non-zero on any HTTP error, so the
    script decides by the parsed code: 200 → present, 404 → absent, anything else → exit 1 with the line printed.
  - GitHub's base64 `content` contains newlines, which are removed before decoding.
  - **Two PUTs means two commits per tick.** If the second PUT fails, the branch holds a new ledger and an old badge until
    the next tick re-renders both. Accepted: BigQuery is the source of truth, and the run is red.
  - `subprocess.run` with argv lists, `check=False`, inspected return codes; the token only via env `GH_TOKEN`; the
    repository only via env `GITHUB_REPOSITORY`.
- **`scripts/tick_issues.py --results-json '<json of {job: result}>' --evaluation monitor_out/evaluation.json --run-url
  URL`** (stdlib only, self-contained; its planning function `plan_issue_actions` lives in the script).
  - Failure action iff any of the five jobs (`clock`, `score`, `evaluate`, `ledger-render`, `ledger-commit`) is
    `failure` or `cancelled`. `skipped` is not counted, because a skip follows from an upstream failure that is counted.
  - Alert action iff the evaluation file exists and `verdict == "alert"`.
  - Every `gh` call passes `--repo "$GITHUB_REPOSITORY"` explicitly; nothing is inferred from the sparse checkout.
  - For each action: `gh label create <label> --force --color <hex>` (`d73a4a` for failure, `fbca04` for alert), then
    `gh issue list --label <label> --state open
    --json number --limit 1`, then `gh issue comment <n> --body-file <tmp>` or `gh issue create --title <title> --label
    <label> --body-file <tmp>`.
  - Titles: "Monitoring tick failing (simulated clock)" (label `monitoring-failure`) and "Calibration alert:
    observed/expected outside band (simulated clock)" (label `monitoring-alert`).
  - Bodies: run URL, sim date (if known), each job's result, O/E with CI for the alert, and "Not auto-closed: close it
    after a green tick."
  - Exit 0, or 1 on any `gh` error.
- **`scripts/m3_check.py [--impersonate-service-account SA]`** (laptop; also needs `gh`; exit 2 on a `gh` or BigQuery error).
  1. Lists the scheduled runs: `gh run list --workflow monitor.yml --event schedule --json
     databaseId,conclusion,createdAt,url --limit 200`, oldest first.
  2. Joins each run's URL to monitoring rows (`github_run_url`) for the cadence.
  3. M3 is met iff there are ≥ 5 consecutive scheduled runs with `conclusion == "success"`, each with a succeeded
     `burn_in` monitoring row, AND, after them, ≥ 1 scheduled run with conclusion success and a succeeded `weekly` row.
     - A streak is broken by any run with conclusion `failure`, `timed_out` or `startup_failure`, by a `cancelled` run
       that has a monitoring row (cancelled mid-tick), and by a `success` run without a succeeded row (not recorded).
     - Every other case also breaks the streak: a `cancelled` run whose `clock` job had started but that has no row; the
       conclusions `skipped`, `neutral`, `action_required`, `stale`; any run whose `attempt` (`gh run view <id> --json
       attempt`) is > 1, because a re-run is an owner intervention. Re-runs are allowed operationally and their rows carry
       `github_run_attempt`. When a URL has several rows, the row of the run's latest attempt with the highest
       `started_at` is used.
     - The weekly part: at least one weekly success after the streak; weekly runs that failed before it are listed in the
       evidence but do not invalidate it.
     - A `cancelled` run with **no** monitoring row and no started `clock` job (`gh run view <id> --json jobs`: the
       `clock` job absent, or its `startedAt` empty) counts as **dropped**. That is a pending run that GitHub's concurrency
       rule replaced. It is neither green nor red, and is listed as such in the evidence, like a schedule GitHub never
       fired.
  4. Prints the evidence (wall time, sim date, run URL).
  - Exit 0 met, 3 not met.

### `.github/workflows/monitor.yml`

```yaml
on:
  workflow_dispatch:
    inputs:
      sim_date:
        description: "Optional YYYYMMDD on the simulated grid, at or before the clock head (replay); empty = the head"
        required: false
        type: string
permissions: {}
concurrency:
  group: score
  cancel-in-progress: false
```
The cron states (D1) are three commits to `main`, each through a PR:
- (a) the v2 merge: no `schedule:`;
- (b) the burn-in PR adds `schedule:` / `- cron: "7,37 13-18 * * *"`;
- (c) after ≥ 5 consecutive green burn-in ticks, that line is replaced with `"17 6 * * 1"`.

`tests/test_infra.py` accepts exactly these three states.

Jobs (`runs-on: ubuntu-24.04`, `timeout-minutes: 20`, block-style `permissions:` per job). The four credentialed jobs
share the step pattern of `score.yml:31-56`: checkout with `persist-credentials: false`, setup-python 3.14, the
hash-checked install, then `google-github-actions/auth` with the job's SA.
1. **`clock`.** Permissions `contents: read`, `id-token: write`; SA `monitor`. Step `id: clock`: `python
   scripts/monitor_tick.py clock` with `env: SCHEDULE: ${{ github.event.schedule }}`, `REQUESTED_SIM_DATE: ${{
   inputs.sim_date }}`. Job `outputs: sim_date: ${{ steps.clock.outputs.sim_date }}` and `cadence: ${{
   steps.clock.outputs.cadence }}`.
2. **`score`.** `needs: clock`; permissions `contents: read`, `id-token: write`; SA `scorer`. Step `id: score`: `python
   scripts/score_to_bigquery.py --gold-dir gs://synthea-readmission-risk-gold/v2/main_200k --run-date "$SIM_DATE"
   --window-days 7 --top-n 10`, with `env: SIM_DATE: ${{ needs.clock.outputs.sim_date }}`. The literals are kept equal to
   `deploy/monitoring.json` by a test. Job `outputs: scoring_run_id`, `status` from `steps.score.outputs`.
3. **`evaluate`.** `needs: [clock, score]`; `if: always() && needs.clock.result == 'success'`; permissions `contents:
   read`, `id-token: write`; SA `monitor`. Runs `python scripts/monitor_tick.py evaluate --sim-date "$SIM_DATE" --cadence
   "$CADENCE" --scoring-run-id "$SCORING_RUN_ID" --score-result "$SCORE_RESULT"` with env from `needs.*`. Then
   `actions/upload-artifact` (`if: always()`, name `evaluation`, path `monitor_out/`, `if-no-files-found: ignore`, `overwrite: true`, so a "Re-run failed jobs" can re-upload). Job
   `outputs: status`.
4. **`ledger-render` (job A).** `needs: [clock, evaluate]`; `if: always()`; permissions `contents: read`, `id-token:
   write`; SA `ledger-reader`. Runs `python scripts/render_ledger.py --out-dir ledger_out`, then upload-artifact (name
   `ledger`, `overwrite: true`). Clock failures are recorded too, so it runs even after one.
5. **`ledger-commit` (job B).** `needs: ledger-render`; `if: always() && needs.ledger-render.result == 'success'`;
   permissions `contents: write` only, no id-token.
   - `actions/checkout` with `persist-credentials: false`, `sparse-checkout: scripts/publish_ledger.py`,
     `sparse-checkout-cone-mode: false`;
   - `actions/download-artifact` (name `ledger`, path `ledger_out`);
   - `python3 scripts/publish_ledger.py --in-dir ledger_out --branch ledger`, with `env: GH_TOKEN: ${{
     secrets.GITHUB_TOKEN }}`. The runner's system Python is used; nothing is installed.
6. **`issues`.** `needs: [clock, score, evaluate, ledger-render, ledger-commit]`; `if: always()`; permissions `contents:
   read`, `issues: write`, no id-token.
   - Sparse checkout of `scripts/tick_issues.py` (cone mode off, `persist-credentials: false`);
   - `actions/download-artifact` (name `evaluation`, `path: monitor_out`, `continue-on-error: true`). The upload's root
     is `monitor_out/`, so the file lands at `monitor_out/evaluation.json`;
   - `python3 scripts/tick_issues.py --results-json "$RESULTS" --evaluation monitor_out/evaluation.json --run-url
     "$RUN_URL"`, with `env: RESULTS: ${{ toJSON(needs) }}` (the script reads `.<job>.result`), `RUN_URL: ${{
     github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}`, `GH_TOKEN: ${{ secrets.GITHUB_TOKEN
     }}`.

Job B's and the issues job's scripts each append exactly one line to `$GITHUB_STEP_SUMMARY` when that env variable is
set: "Ledger: <n> of 2 file(s) committed to `ledger`" and "Issues: <comma-separated `created #n` / `commented #n`, or
`none`>". The scripts write it themselves, even on failure ("Ledger: failed: <reason>"). With score's existing summary, every job writes one (FR20). Both stdlib scripts run on the runner's
system `python3`: Ubuntu 24.04's Python 3.12, which `tests/test_infra.py` pins as `ubuntu-24.04`. CI therefore also
runs `tests/scripts/` under Python 3.12 in the `pipeline-dataproc-runtime` job (its pytest invocation becomes `pytest
tests/pipeline tests/scripts`; the two test files import only stdlib and pytest, which that job's lockfile has), so syntax
or stdlib APIs newer than 3.12 fail CI.

A run is red iff any of jobs 1-6 fails. **Concurrency, corrected from revision 1:** with `cancel-in-progress: false`,
GitHub keeps at most one running and one pending run per group, and a newer run *cancels* the pending one. Delayed runs
are therefore not all serialized: a pending tick can be dropped (M3 treats it as dropped, see `m3_check.py`). L10
measures one tick's wall time. If it exceeds 25 minutes, the burn-in uses the fallback cron `7 13-22 * * *` (hourly, 10
opportunities), changed in `deploy/monitoring.json` and the (b) PR together; `test_config.py` accepts only the two named
burn-in crons. `${{ … }}` appears only in `env:` values, `if:`, job `outputs:`, and `with:` of
the checkout, artifact and auth steps (fixed values). New pinned actions: `actions/upload-artifact` and
`actions/download-artifact`, each with a full SHA + `# vX.Y.Z`, resolved at implementation with `gh api
repos/actions/<name>/git/ref/tags/<latest v-tag>` (dereferenced if annotated); Dependabot covers them. The
`GITHUB_TOKEN`'s commits trigger no workflows, so there is no loop.

**WIF granularity (E6, disclosed):** the bindings are keyed to `workflow_ref` (`…/monitor.yml@refs/heads/main`). GitHub's
OIDC token has no per-job claim, so any job in `monitor.yml` could request any of the three SAs bound to that file. The
per-job SAs scope each job's credentials as written; they do not stop code in one job from requesting another job's SA.
The provider condition (numeric ids, `refs/heads/main`, `workflow_dispatch`/`schedule`) is unchanged.

### Terraform

- `local.monitor_workflow_ref = "${var.github_repository}/.github/workflows/monitor.yml@refs/heads/main"` and
  `local.monitor_principal_set` (pattern of `score_principal_set`).
- `google_service_account.monitor` (`monitor`, display name "Monitoring tick: reads predictions, scoring_runs and gold
  labels, writes monitoring"). Grants:
  - `scorerTableWriter` (the existing custom role, get/getData/updateData) on `serving.monitoring`;
  - `roles/bigquery.dataViewer` on the tables `serving.predictions` and `serving.scoring_runs`;
  - `roles/bigquery.dataEditor` on the dataset `scratch` (M5 copies only);
  - `roles/storage.objectViewer` on the gold bucket;
  - `roles/bigquery.jobUser` on the project;
  - `roles/iam.workloadIdentityUser` for `monitor_principal_set`;
  - operator `roles/iam.serviceAccountTokenCreator`.

  That is 8 members.
- `google_service_account.ledger_reader` (`ledger-reader`). Grants: `roles/bigquery.dataViewer` on `serving.scoring_runs`
  and `serving.monitoring`; project `roles/bigquery.jobUser`; WIF for `monitor_principal_set`; operator token creator. That
  is 5 members.
- `google_service_account_iam_member.scorer_wif_monitor`: scorer `roles/iam.workloadIdentityUser` for
  `monitor_principal_set`. That is 1 member.
- `google_bigquery_table.monitoring` as above. The schemas of `predictions` and `scoring_runs` gain the column, and
  `predictions.description` becomes the new `DEMO_NOTICE`.
- **Expected plan:** `Plan: 17 to add, 2 to change, 0 to destroy`. The 17 adds are 2 SAs + 14 IAM members + 1 table; the
  2 changes are `google_bigquery_table.predictions` (schema + description) and `google_bigquery_table.scoring_runs`
  (schema). Any other summary → stop.

### `DEMO_NOTICE` (route-neutral)

"DEMO, not clinical guidance. Scores come from a weak model trained on synthetic Synthea data, and the ranking mostly
reflects the admission reason (a reason-code-only lookup does as well). The rows are held-out discharges from the
model's own test partition; that window was already used to select models, so it is not a virgin holdout. Not
clinically validated."

This holds for both routes: the 10k window was used during design, and the 200k window by the promotion gate.

## UX flow

**A tick (monitor.yml):**
1. clock → `sim_date`, `cadence`.
2. score → write-audit-publish of the 7-day window ending on `sim_date`.
3. evaluate → one monitoring row with the cohort evaluation.
4. ledger-render (job A) → the ledger artifact.
5. ledger-commit (job B) → `ledger` branch.
6. issues → failure and/or alert issue opened or commented.

**Warm-up (E5).** The cohorts of tick `S` need ticks `S − 0 … S − 119 d` (18 grid dates). The first tick whose cohorts are
complete is the 18th, sim date 2023-08-14. Before the burn-in, the owner runs `make monitor-catch-up WEEKS=18` from the
laptop (cadence `catch_up`, labelled "catch-up (laptop)"):
- ticks 1-17 (2023-04-17 … 2023-08-07) report `incomplete_cohort`;
- tick 18 (2023-08-14) is the first with a full cohort.

Every burn-in tick then evaluates full cohorts. A tick before full coverage never alerts.

**Burn-in and weekly cadence.** Merge the cron-state (b) PR on the chosen day before 13:00 UTC. That gives 12
opportunities (13:07 … 18:37 UTC) for ≥ 5 consecutive green scheduled ticks. L10 and L12's closing dispatch have already advanced the clock to 2023-08-28, so 12 green slots take it to
about 2023-11-20. Then merge the (c) PR; the next Monday 06:17 UTC tick is the weekly one.

**E3:** the tick window is 7 days, not the scorer's 30-day default, so that consecutive ticks tile the timeline: every
discharge is scored by exactly one tick, and the weekly triage list is "this week's discharges".

## Data handling

- **Leakage and label timing.** The scorer is unchanged and label-free (it drops `LABEL_SIDE_COLUMNS` before scoring).
  Labels are joined only in evaluate, only for discharges whose 30-day window closed before `sim_end`. Cohort membership
  never depends on the outcome *given the gold table*. Two residuals are disclosed:
  - **`n_label_after_sim`** (Interfaces > cohort).
  - **The gold table's own selection uses post-discharge information:** an index stay is excluded when the patient died
    or the export ended before a readmission could be ruled out; the terminal and continuation rules look at later stays
    of the same patient; positives are kept regardless of censoring. The population that is scored and monitored is
    therefore already conditioned on events after each discharge. A real deployment would score every discharge at
    discharge time; this is the as-of scoring follow-up. This is the same caveat the slice-4 demo carries (CLAUDE.md,
    Slice 4), and the ledger header and the README monitoring section name it in one sentence: "The monitored discharges
    come from a gold table built with hindsight (deaths and later stays shape which discharges are included), so this is
    a demonstration of the monitoring mechanics, not an estimate of live calibration."

  The monitor SA cannot write predictions.
- **Split integrity.** Only the champion's test partition is ever scored (the completeness guard is unchanged). The 14
  guards and I1-I5 are unchanged. The clock range is the measured set of Mondays the guard accepts; preflight re-proves
  it.
- **Reproducibility.** There is no randomness. Re-evaluation over the same published predictions and gold gives
  identical metrics (tested). Scores are deterministic for a given bundle, platform and batch layout; slice 6c measured
  last-bit differences across platforms and batch sizes. The cohorts here are mixed-platform: catch-up ticks are scored
  on macOS, scheduled ticks on the Linux runner, so the oracle uses a tolerance (Verification). Sums use `math.fsum`
  over rows sorted by `encounter_id`, so they do not depend on BigQuery's row order. Gold integrity: the loader recomputes the `spark-rowhash-v1` fingerprint on every read.
- **prereg.** The rule's parameters are read from `prereg.yaml` at runtime, and tests assert the parsed values equal the
  file's.
- **Redaction.** Nothing committed carries a billing account, budget id or owner email. The ledger shows run URLs and ids
  only.
- **Cost.** Per tick: about 10 BigQuery queries (10 MB minimum each, ≤ 100 MB cap) and about 40 MB of GCS → internet
  egress (two gold downloads, ≈ $0.005). About 230 ticks in total (18 catch-up, ~12 burn-in, the weekly ones up to v2.0),
  ≈ CHF 1-2. Budgets are unchanged.

## Failure modes

| Case | Behaviour |
|---|---|
| Score refused/failed/data tests failed | scoring_runs row (slice 4); evaluate writes `failed`/`score`; the clock does not move; failure issue; run red |
| Evaluate exception (BigQuery, gold download, missing labels, fingerprint mismatch) | monitoring row `failed`/`refused`, stage `evaluate`; the clock does not move; failure issue |
| `monitoring.sql` history test fails | row `failed`, stage `evaluate`, message = the MON label; nothing evaluated. **It blocks every later tick until repaired** (the kill-trigger risk). Recovery (owner-only, documented in `docs/setup.md` > Monitoring tick > Repair): (1) read the label and find the offending row(s) with a `bq query` as the operator; (2) remove or correct them with one DML statement run as the **operator**, never as the monitor SA, after confirming it; (3) record the statement and reason in `data/monitoring/live-checks.md` and the slice note's Results. BigQuery time travel (7 days) can restore deleted rows (`FOR SYSTEM_TIME AS OF`). A fix that changes MON SQL instead goes through a PR. |
| Clock: exhausted, config ≠ champion, unknown cron, bad dispatch date | monitoring row `refused`, stage `clock` (if a client exists); exit 1; failure issue; the ledger shows it |
| Not recorded (insert failed and unresolved) | exit 4, run red, failure issue; m3_check breaks the streak (no row) |
| `score.yml` dispatched for a tick date (30-day window overwrites the partition) | `found_ticks` rejects it (`window_days != 7`), so every later tick whose 18 required ticks include that date reports `incomplete_cohort`, whether the date is matured or immature for it, until the date is replayed (`-f sim_date=`). L7 (L-post) does exactly this on 2025-06-30, a grid date the clock reaches only around grid week 115: the tick for 2025-06-30 replaces it before any cohort reads it, so it is harmless and is stated in L7 |
| A replay whose score succeeds but whose evaluate fails | the partition now holds the new run (found by `published_runs`); coverage is unaffected; the clock does not move for a replay anyway |
| Laptop catch-up concurrent with an Actions tick | prevented by the catch-up's `gh run list` check, and the owner must not dispatch or schedule during a catch-up. If it still happens (a run starts mid-catch-up), both can process the same head date: the later publish wins, and the other attempt's evaluate fails as `failed`/`score` (step 8: the run published for `S` is not its own). Catch-up then stops, or the Actions run turns red, which during the burn-in breaks the M3 streak. The state stays consistent, because the next tick reprocesses the head |
| GitHub delays/drops a scheduled run, or the concurrency rule cancels a pending one | nothing to catch up on the simulated clock (the next run processes the head); for M3 a dropped or pending-cancelled run is neither green nor red (`m3_check.py`); a run cancelled mid-tick breaks the streak and the issues job (if it runs) reports the cancelled jobs |
| Ledger commit fails (branch missing, API error) | job B red, failure issue; BigQuery remains the source of truth; the next tick re-renders everything |
| Issues job fails | that job red (visible in the run); no retry |
| 60-day schedule auto-disable (public repo without activity) | documented `gh workflow enable monitor.yml`; owner calendar reminder (L16) |
| Clock reaches 2026-09-07 (about 2029 at the weekly cadence) | the clock refuses every tick → failure issue; follow-up to disable the schedule or re-anchor |
| Re-running the 6b gate (`make gate`) after the promotion | `ops/gate_run.py:456-461` reads `deploy/champion.json` and assumes the frozen-v1 champion, so it would refuse. Phase B is closed; reproducing it needs a worktree at the Phase-B commit (documented in `deploy/PROMOTIONS.md`); not changed here |
| Empty cohort / one class only | `insufficient_data` / `auc = null` |

## Verification criteria

**Offline tests (CI, no GCP).** `tests/conftest.py`'s gcp guard stays; the fakes are `tests/warehouse/fake_bigquery.py`
(extended so `FakeJob.rows` returns dicts for the new SELECTs; `on_query` scripts them) and `tests/bundles/fake_gcs.py`.
- **`tests/monitoring/test_config.py`.**
  - The committed `deploy/monitoring.json` parses; its grid has 178 dates, first 2023-04-17, last 2026-09-07, all Mondays.
  - Each malformed variant is refused.
  - `cadence_for` maps every case, including an unknown cron.
  - A champion mismatch is refused.
  - **The committed config's bundle id equals the leaf of the committed `deploy/champion.json`.**
- **`tests/monitoring/test_clock.py`.**
  - Empty state → `first`; otherwise the head follows the max.
  - A failed tick does not advance (the same date is retried).
  - Off-grid state is refused; an exhausted clock is refused.
  - Replaying a succeeded date is allowed; skip-ahead and off-grid requests are refused.
  - Other bundles' rows are ignored.
- **`tests/monitoring/test_rule.py`.**
  - `poisson_ci(0) == (0.0, 3.6889)` and `poisson_ci(20) == (12.2165, 30.8884)`, both to 4 dp (computed 2026-10-18 with
    scipy).
  - The floor boundary at 19/20.
  - An alert needs BOTH conditions (four corner cases); the band edges 0.80 and 1.25 are not alerts.
  - The ValueErrors.
  - `alert_rule_from_prereg(load_prereg(Path("prereg.yaml")))` equals `(13, 20, (0.80, 1.25))`.
- **`tests/monitoring/test_cohort.py`.**
  - Range bounds on a hand-built frame, including a discharge exactly at each bound.
  - **A positive with `label_horizon >= sim_end` stays in the cohort and is counted in `n_label_after_sim` (the bias
    regression test).**
  - The immature sum, and null when the immature ticks are incomplete.
  - `required_ticks` is 14 + 5 dates (18 distinct); missing before `first`.
  - Incomplete coverage never alerts; an empty matured set with incomplete coverage (catch-up ticks 1-4) gives `incomplete_cohort` with observed 0, expected 0.0 and ratio/CI null, and never calls `evaluate_rule`.
  - `cohort_spec` refuses a window other than 30 or a cohort other than 13 weeks; `read_window` is `(S − 119 d, S)`.
  - A duplicate encounter or a missing label → ValueError.
  - Inputs are not mutated; two calls give equal results.
  - Prediction rows outside both ranges (including dates after `S`) do not change the result.
- **`tests/monitoring/test_store.py`.**
  - `found_ticks` accepts only one-run, succeeded, published, same-bundle, 7-day, `top_n`-matching dates with complete row
    counts. A 30-day overwrite and a missing-rows date are rejected.
  - Every query carries `maximum_bytes_billed` and the epoch/partition filter.
  - `append_monitoring`'s parameter order and types match the schema.
  - `record_monitoring` resolves via the job id.
- **`tests/monitoring/test_tick.py`** (fake BigQuery + fake GCS gold from the existing gold fixtures).
  - The happy path writes exactly one row carrying the evaluation.
  - A score failure → `failed`/`score`; a scoring run of another bundle or window → `failed`/`score`.
  - A fingerprint mismatch → `failed`/`evaluate` (`TickCheckError`); a history-test failure → `failed`/`evaluate`
    with `history_tests_passed = FALSE`; an off-grid `--sim-date` or an unknown `--cadence` → `refused` with both fields
    NULL in the written row.
  - `sim_date > head` is refused.
  - A replay of `last_sim_date` after the clock is exhausted is accepted by both the clock and evaluate.
  - Prediction rows with a NULL `gold_fingerprint_definition` → `failed`/`evaluate` (`TickCheckError`).
  - Laptop `evaluate` on a dirty tree, or with HEAD ≠ `origin/main`, refuses before any client exists.
  - Neither `prepare_tick` nor `evaluate_tick` ever raises.
  - `prepare_tick` builds the client first, writes a `clock` row on a config, champion, cron or clock refusal (exit-code mapping 1) and returns each cadence, `catch_up` for the laptop; no client → not recorded (4).
  - `evaluate_tick` refuses a scoring run whose `run_date` ≠ sim date, or that is not the run currently published for the sim date; `TickCheckError` rows carry `error_type`/`error_message`; `git_dirty` is recorded.
  - **Kill-trigger test:** two consecutive `evaluate_tick` calls for the same sim date over the same fake state produce
    identical metric fields.
- **`tests/monitoring/test_ledger.py`.**
  - The header is verbatim; the cadence labels; the ordering.
  - Byte-identical output on two calls.
  - The badge's colors, its `stale after` arithmetic for both cron states, "schedule not started", "dispatch only", and a
    red badge after a clock-failure row.
- **`tests/monitoring/test_preflight.py`:** a synthetic gold plus a bundle fixture; a refused date is reported with exit
  1.
- **`tests/monitoring/test_cli.py`.**
  - The exit codes of every subcommand.
  - `GITHUB_OUTPUT` lines.
  - `evaluation.json` keys.
  - Catch-up refuses under `GITHUB_ACTIONS`, with N out of range, or when a fake `gh` reports a run in progress.
  - Catch-up switches identities in the order monitor, scorer, monitor (recorded `gcp.configure` calls).
- **`tests/scripts/test_publish_ledger.py`, `test_tick_issues.py`.** These load each script by file path (`importlib.util`)
  from a temporary directory holding ONLY that script, which proves it is self-contained.
  - An AST check allows stdlib-only imports.
  - A fake `gh` (a recorded argv list) proves: create vs comment, label creation, no `shell=True`, the token only in env,
    unchanged files skipped, a missing branch → exit 1.
  - The result matrix: a skipped job is not a failure; a cancelled job is.
- **`tests/warehouse/`.**
  - Both schemas end with the new column.
  - `RunRecord`'s order check passes.
  - The staging writer appends a non-nullable `gold_fingerprint_definition`.
  - `run.py` records it.
  - PRED-11 is present and fired by its seeded mutation.
  - (Offline, `FakeBigQuery` executes no SQL, so these tests prove routing and label parsing only. The SQL itself,
    including the NULL audit and "each mutation fires only its own assertion", is proven live by `make m5-monitoring` at
    L4.) MON-01..12 are present and labelled, each fired by `seeded_monitoring_check`'s mutation on the fake, and the base row
    passes.
  - MON-06's anchor equals `first_sim_date`; the epoch literal equals `epoch`.
  - `score_to_bigquery.py` writes `scoring_run_id`/`status` to `GITHUB_OUTPUT` for succeeded and refused runs.
- **`tests/scoring/`:** `validate_bundle` sets `gold_fingerprint_definition` for both definitions; the CLI help shows the
  new `DEMO_NOTICE`.
- **`tests/test_infra.py`.**
  - Grants: the monitor and ledger-reader grants are exactly the listed ones; three WIF bindings are keyed to
    `monitor.yml@refs/heads/main`.
  - The monitoring table is partitioned, requires a partition filter, is deletion-protected and clustered on `sim_date`.
  - The predictions description equals `DEMO_NOTICE`.
  - `monitor.yml`:
    - `permissions: {}` at the top; block-style per-job permissions exactly as listed;
    - `id-token: write` only on clock/score/evaluate/ledger-render; job B's only permission is `contents: write`; the
      issues job has no id-token;
    - the trigger set ⊆ {`workflow_dispatch`, `schedule`}; the cron state is one of (a), (b), (c) with crons equal to
      `deploy/monitoring.json`;
    - `${{` appears only where allowed (above), and job `outputs:` is allowed;
    - the score step's gold URI, window and top-n equal the config;
    - jobs B and issues check out exactly one file with cone mode off.
  - `score.yml`: its gold URI equals the config, and it shares the concurrency group with `monitor.yml`.
  - **Job graph:** each job's `needs:` list and `if:` expression equal the design's exactly (clock: none/none; score:
    `clock`/none; evaluate: `[clock, score]`/`always() && needs.clock.result == 'success'`; ledger-render: `[clock,
    evaluate]`/`always()`; ledger-commit: `ledger-render`/`always() && needs.ledger-render.result == 'success'`; issues:
    the five jobs/`always()`), plus each job's `outputs:` keys.
  - The existing id-token test passes unchanged.
- **Existing tests this slice must change** (they would fail otherwise):
  - `tests/test_infra.py:80-83` `expected_gold_uri()` → `v2/main_200k`;
  - `test_scorer_grants_are_exactly`'s expected member list gains `scorer_wif_monitor`;
  - `test_score_workflow_is_dispatch_only_and_input_reaches_shell_only_via_env` and `test_score_workflow_matches_the_terraform_values`
    (the score step's new literal);
  - `test_predictions_table_description_is_demo_notice` (new text, same assertion);
  - `tests/test_tooling.py:35` (variable whitelist: `SCORE_GOLD_DIR`, `M5_RUN_DATE`, `MONITOR_SA`, `LEDGER_SA`, `WEEKS`,
    `CHAMPION_BUNDLE_DIR`, `LEDGER_OUT`) and `:122,132,142` (dry-run strings: `score` uses `SCORE_GOLD_DIR`, `GOLD_URI` is
    `v2/main_200k`, the `RUN_DATE` default stays 20250630, `m5-check` uses `M5_RUN_DATE` = 20230417);
  - `tests/warehouse/` assertions on the schema column lists, the `make_staging_writer` signature, `seeded_check`'s
    `PREDICTION_MUTATIONS` key set and `data_tests.SCRIPT_NAMES`;
  - any `tests/scoring/` assertion on the exact `DEMO_NOTICE` text.

  The implementation runs the full suite and lists each changed test in the final report. A test is changed only to
  follow the new behaviour, never weakened.
- **`tests/scripts/` under 3.12:** CI's `pipeline-dataproc-runtime` job runs them (above). `tests/scripts/__init__.py` is
  created, like every sibling package. `lint-and-test` (3.14) runs them too, as part of the full suite. `tests/scripts/`
  is a second deliberate exception to "tests mirror `src/`", after `tests/test_docs.py`: it tests two self-contained
  `scripts/` files that are not part of the package. CLAUDE.md's layout line names it.
- `tests/test_infra.py::test_iam_is_additive_only` expects the IAM member count to grow from 26 to 40 (+14).
- **`tests/test_docs.py`.**
  - The README links to `https://github.com/fma00/synthea-readmission-risk/blob/ledger/docs/run-ledger.md`.
  - The workflow badge URL is `https://github.com/fma00/synthea-readmission-risk/actions/workflows/monitor.yml/badge.svg?event=schedule&branch=main`.
  - The shields badge is `https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ffma00%2Fsynthea-readmission-risk%2Fledger%2Fdocs%2Flast-tick.json`.
  - The README contains "not weeks of unattended operation".
  - The existing doc tests still pass.
- **Gate:** `ruff check .`, the full `pytest` suite, `zizmor --offline .` (CI's online zizmor clean), and `make
  tf-validate`.

**Live runbook (each step owner-confirmed, with `CLOUDSDK_CORE_PROJECT=synthea-readmission-risk`).** Interim notes go in
the gitignored `data/monitoring/live-checks.md`; results are appended here under "Results".

| Step | Action | Pass |
|---|---|---|
| L0 | State check | no `serving.monitoring`; ruleset unchanged (`gh api …/rulesets/24876876`); clean tree |
| L1 | `make gold-200k` (`gcloud storage cp "gs://…/v2/main_200k/*" data/gold/main_200k/`), then `make monitor-preflight` | 178/178 accepted |
| L2 | Upload `569fdab6` as `bundle-writer` (slice-3 JSON-API recipe) | verified from GCS: the manifest sha256 equals the pin |
| L3 | `terraform plan`, then `apply` | plan = exactly the expected summary |
| L4 | `make score-bq RUN_DATE=20230417 SCORE_ARGS="--window-days 7"` (new champion); `make m5-check` (default `M5_RUN_DATE=20230417`); `make m5-monitoring` | exit 0, row has `spark-rowhash-v1`; every PRED, RUNS and MON assertion fires on its seeded copy; the base copy passes |
| L4b | **Before L5** (revised 2026-10-18 after the pre-commit review, round 3): create the orphan `ledger` branch through the GitHub API with a README, a placeholder `docs/run-ledger.md` and the no-tick `docs/last-tick.json`, never touching the working tree (recipe: `docs/setup.md` > Monitoring tick > The ledger branch) | `contents/docs/last-tick.json?ref=ledger` resolves; the README's badge renders "none yet" once on `main` |
| L5 | Merge the slice-7 PR into `v2`, then PR #4 `v2 → main` as a merge commit (owner) | `main` CI green |
| L6 | **V-post** (slice 3's recipe) | as specified there, except that the listing now also shows `569fdab6…/` (uploaded at L2): five bundle prefixes, the two schema-1 and two schema-2 v1 bundles plus the new champion |
| L7 | **L-post**: `gh workflow run score.yml --ref main -f run_date=20250630` | green; `scoring_runs` row with `trigger = workflow_dispatch` and the run URL |
| L8 | (moved to L4b, before the merge) | — |
| L9 | `make monitor-catch-up WEEKS=18` | 18 succeeded rows; 17 `incomplete_cohort`, the 18th (2023-08-14) not |
| L10 | `gh workflow run monitor.yml --ref main` | green; commit on `ledger`; every job's summary present; the badge JSON is served via shields; **oracle:** the row's `n_matured`, `observed` and verdict equal `make monitor-simulate`'s row for that sim date, and `expected` agrees within `abs ≤ n_matured × 1e-8` (a bound well above 2.6e-9 per row); the tick's wall time recorded (> 25 min → the hourly burn-in fallback) |
| L11 | Idempotency (kill trigger): before the replay, export the L10 date's partition `(encounter_id, rank, risk_score)` (`bq query` as the operator); then `gh workflow run monitor.yml --ref main -f sim_date=<L10's date>` | (a) the new partition holds exactly the same `(encounter_id, rank, risk_score)` set, bit-identical (same runner image and batch), under a new `scoring_run_id`; (b) the replay row's `n_matured`, `observed`, `expected`, `oe_ratio`, `oe_ci_low`, `oe_ci_high`, `auc`, `n_label_after_sim`, `n_immature`, `expected_immature`, `coverage_found` and `verdict` equal L10's row exactly; (c) the clock head is unchanged. (a) is the actual re-scoring idempotency check: the matured metrics read older partitions, which a replay does not touch |
| L12 | Failure path: `-f sim_date=20230418` (off-grid) twice | the clock refuses; run red; ONE failure issue created, then commented. Then a normal dispatch to make the badge green again; the owner closes the issue |
| L13 | Burn-in: merge the (b) PR on the chosen day before 13:00 UTC | ≥ 5 consecutive green scheduled ticks; else repeat on another afternoon |
| L14 | Merge the (c) PR | — |
| L15 | Wait for the next Monday 06:17 UTC tick, then `make m3-check` | the tick is green; exit 0 (M3 met) |
| L16 | Owner: calendar reminder for the last commit date + 50 days (check that `monitor.yml` is enabled); billing report for the slice's days | — |
| L17 | Tag `v2.0` on `main` + release notes (owner); CLAUDE.md/memory say "v2.0 released" | — |

An alert during the burn-in is a finding (an issue opened as designed), not a failure. Live coverage of the issue
paths, stated explicitly:
- L12 proves the clock-refusal → failure-issue path live;
- the score-failure, evaluate-failure, ledger-failure and alert paths are proven by tests only (`test_tick_issues.py`'s
  result matrix), unless one happens naturally;
- there is no production test knob.

`make monitor-simulate` is the oracle for L10: the same bundle and gold, scored through the same functions. It runs on
macOS, while the live cohorts are **mixed-platform**: the 18 catch-up ticks are scored on the macOS laptop, the
scheduled ticks on the Linux runner. Slice 6c measured ≤ 2.6e-9 per-row differences across platforms, on the v1 LR only.
So `expected` is compared within `n_matured × 1e-8`, never bit-exactly, and if that bound is ever exceeded the
per-row difference is measured on 569fdab6 before anything is concluded. The verdict could in principle flip on a
boundary case; the L10 Results then record both values.

**Sanity check before reporting done (offline, no decision depends on it).** Using the local gold and bundle, score
every grid date from 2023-04-17 with 7-day windows using the offline scorer, then evaluate the cohorts for every date
from 2023-08-14. Print the verdict distribution, the O/E range and `n_label_after_sim` per cohort, so we know before the
burn-in how often an alert will fire. The rule is pre-registered; this changes nothing.

## Out-of-scope follow-ups

1. **v3.0:** the Streamlit dashboard, with the heartbeat and a real "stale since" (FR22, FR21's dashboard half); the model
   card and README overhaul (FR23-FR24); the BigQuery gold direct-write + connector MERGE test (from FR14); regenerating
   the README figure and sample table from the 200k champion (experiment `readmission-risk-v2-main200k`).
2. **v3.0 candidate:** the v1 (10k) model on the 200k population as a separately guarded external-population scoring mode
   (a drift-monitoring story without a promotion).
3. Auto-closing the failure issue after a green tick.
4. Re-running the 6b gate after a promotion (`ops/gate_run.py` assumes champion = frozen v1).
5. The clock's end (2026-09-07, about 148 weekly ticks after v2.0): disable the schedule or re-anchor before then.
6. **Still open from earlier slices (unchanged):**
   - slice-6c follow-up 7 (the cross-platform portable reference policy, needed before the FR16 portable fallback or any
     v1 pin move);
   - a champion-compatibility check for library bumps (guard 8);
   - the pandas 3 check (out of v2 scope; measured 2026-10-18: the portable form is bit-exact under pandas 3.0.6 on
     macOS);
   - the 45 cross-machine QALY rows;
   - cosmetic: in the 6c note, follow-up 7 sits between items 1 and 2;
   - the as-of scoring path;
   - label v2.

## Design-check log

- **Round 1 (2026-10-18):** comprehension passed (minor: "kill trigger" undefined, E3 missing, an awkward job-B sentence;
  all fixed). Critic: 19 gaps; readiness: 16 questions. All addressed in revision 1:
  - M5 for the MON assertions: synthetic rows, run as monitor, scratch grant, runnable at L4.
  - Job B's `always()`.
  - Stdlib scripts self-contained, cone mode off.
  - No `db-dtypes`: frames built from rows.
  - The plan count corrected to 17 add / 2 change.
  - Makefile defaults split: v1 tooling keeps `GOLD_DIR`; scoring uses `SCORE_GOLD_DIR`/`GOLD_URI`.
  - Route-neutral `DEMO_NOTICE`.
  - The clock facts reconciled with the actual dates.
  - Immature coverage defined (18 ticks).
  - The outcome-dependent exclusion removed (bias).
  - Badge periods and cron state; clock failures recorded.
  - M3 uses run conclusions.
  - Issues include the ledger jobs.
  - The `monitoring.sql` epoch literal.
  - `MonitoringRecord` order and the FLOAT64 map; the not-recorded path.
  - `found_ticks` checks window/top-n and judges what is published.
  - The dispatch input specified.
  - `gold_uri` validation; ledger determinism; header tense.
  - README assets moved to a follow-up.
  - The shields URL fixed and tested.
  - Block-style permissions.
  - The clock-end follow-up.
  - O/E via `observed_expected_ratio`.
  - D2's wording and citation (`74accd7`).
  - Catch-up/Actions concurrency rule + re-check.
  - The score job's literal gold URI, with a test.
  - Publish's sim date from `meta.json`.
  - Catch-up's cadence override.
  - L9 → `WEEKS=18`.
- **Round 2 (2026-10-18):** critic 17 gaps, readiness 11 questions; all addressed in revision 2 except one, rebutted:
  - Addressed:
    - the empty incomplete cohort no longer calls `evaluate_rule`;
    - the concurrency semantics are corrected, and M3 treats pending-cancelled runs as dropped (hourly fallback cron if a
      tick takes > 25 min);
    - evaluate checks `run_date` and that its own run is the one published;
    - `CohortSpec`/`RequiredTicks`/`read_window` signatures, with 30/13 asserted;
    - the gold's hindsight-selection residual is disclosed;
    - D2 is flagged as a standards deviation, the prereg clause is quoted in three places, and the loss of an untouched
      validation population is stated;
    - M5-monitoring: CTAS + `copy_name`, the base row and the mutations spelled out;
    - PRED-11 vs the `RUN_DATE` default: the default is now 20230417;
    - MON-09/MON-10 added, and MON-08 also requires full coverage;
    - the laptop cadence is `catch_up`, and an empty schedule is None;
    - the stdlib scripts are tested under 3.12;
    - `gh` 404 parsing, base64 newlines, the two-commit trade-off, `--repo`, the non-terminal states;
    - the existing tests to change are listed;
    - the header covers catch-up and no longer depends on the afternoon;
    - summaries in job B and issues;
    - `git_dirty` column;
    - the L10 oracle;
    - which issue paths are live-tested is stated;
    - the client is built first in `prepare_tick`;
    - the evaluate job outputs;
    - `read_scoring_runs`;
    - fingerprint via `compute_gold_fingerprint` (works on v1 gold);
    - the Makefile recipes;
    - the catch-up exit codes;
    - `TickCheckError` for non-exception failures;
    - the copy names;
    - the badge time format;
    - the issues artifact path;
    - the job-summary line citation.
  - Rebutted, critic 6's "better: a `deviations` entry": the owner's constraint "prereg.yaml (FINAL, never edit)" (Step 0
    brief), and the file's deviation schema records value changes only. Instead the override is flagged as a standards
    deviation and the clause is quoted verbatim in `PROMOTIONS.md`, `docs/results.md` and the README.
- **Round 3 (2026-10-18):** critic 13 gaps, readiness 7 questions; all addressed in revision 3:
  - evaluate steps split into 10a/10b, with the order stated;
  - a separate `M5_RUN_DATE`, `RUN_DATE` kept;
  - the oracle tolerance is `n × 1e-8`, and mixed-platform cohorts are stated;
  - a `simulate` subcommand serves as the oracle;
  - `math.fsum` over sorted rows;
  - `period_of` keyed on the cron string, with the badge in burn-in state documented;
  - typed NULLs in the M5 base row;
  - MON-11/12 and cadence validation;
  - a `github_run_attempt` column, and M3 rules for re-runs and every conclusion;
  - a repair procedure for a failing history test;
  - `gh api --input`;
  - `tests/scripts/__init__.py`, with the layout exception named;
  - a single `ratio_and_ci`;
  - the scripts write their own summary line;
  - `preflight`'s signature and `--config`;
  - wall time = `started_at`;
  - the IAM count and `EXPECTED_TARGETS` tests.
- **Round 4 (2026-10-18; after the 3-revision cap, owner: "apply fixes and run one more round"):** critic 11 gaps,
  readiness 7 questions; all addressed in revision 4:
  - MON-08 made NULL-safe, every check NULL-audited;
  - evaluate and the clock never write invalid `sim_date`/cadence;
  - M5's clean copy carries failed, refused, clock-stage and incomplete companion rows, and mutations touch only the base
    row;
  - L11 compares the replayed partition itself and names the compared fields;
  - Makefile/test/L4 text made consistent (`M5_RUN_DATE`);
  - `simulate` added to the subcommand list;
  - V-post expects five bundle prefixes;
  - a `history_tests_passed` column (FR18);
  - the contents API `encoding: none` branch handled;
  - the clock projection corrected;
  - catch-up requires a clean `main` checkout;
  - the fingerprint-mismatch stage is `evaluate`;
  - the `PROMOTIONS.md` PR number filled in by a follow-up commit;
  - module homes for the record and result types;
  - `ci.yml`/`test-runtime` rows;
  - `auc`/`n_label_after_sim` in both branches;
  - `.gitignore` entries.
- **Round 5 (2026-10-18):** readiness closed (`implementation ready`; its five self-resolvable notes adopted: config read
  at step 2, record population, `m3_check` exit 2 on a `gh`/BigQuery error, `--run-date` optional with `--monitoring`, and
  the flat result dict in `_lines`). Critic: 1 blocking gap + 12 minor, all addressed in revision 5:
  - **blocking:** any verdict other than `incomplete_cohort` now requires all 18 ticks, so MON-08 and `evaluate_cohort`
    agree;
  - the config is read at step 2;
  - `_classify` used in `prepare_tick` too;
  - replays are allowed after the clock is exhausted;
  - `simulate` goes through `score_run_date`;
  - record population specified;
  - the badge text untangled;
  - a job-graph test;
  - the MON SQL is proven live (stated);
  - the stale "schema-1" wording and CLAUDE.md lines;
  - L-post onto a grid date acknowledged;
  - the deliberate fingerprint recomputation;
  - the shadow alternative recorded as offered and declined.
- **Round 6 (2026-10-18):** readiness closed again (`implementation ready`; its notes adopted). Critic: 1 blocking + 9
  minor, all addressed in revision 6:
  - **blocking:** evaluate's re-check calls `next_sim_date(..., requested=sim_date)`;
  - the concurrent catch-up row corrected;
  - NULL definitions → `TickCheckError`;
  - the history failure's `error_type`;
  - `published_runs` via `table_id`;
  - the `DEMO_NOTICE` consumers and the stale comment;
  - `overwrite: true` on artifacts;
  - the Poisson-independence clause in the ledger header (and the README section);
  - laptop `evaluate` requires a clean `main`;
  - `simulate`'s `run_date` string, no redundant pre-validation, out-of-range rows ignored;
  - the companion-row fields;
  - the `seeded_data_test_check` flags;
  - the job-summary contents.
- **Gate outcome (2026-10-18):** readiness closed in rounds 5 and 6, but the critic never closed on any revision.
  - Round 6's single blocking item (a one-line consistency fix) and its nine minor items were applied in revision 6.
  - The owner overrode the no-code gate after round 6 ("Implement now"); revision 6 was not re-checked.

## Results

- **L1 (2026-10-18, offline):** `make monitor-preflight` accepted all 178 grid dates. `make monitor-simulate`: 17 `incomplete_cohort` ticks (2023-04-17 to 2023-08-07); on the 161 full cohorts there were 160 `ok` and 1 `alert` (2026-08-24, O/E 1.309, CI 1.008-1.672) and no `insufficient_data` (observed 26-64). O/E ranged from 0.706 to 1.309, AUC from 0.80 to 0.95, and `n_label_after_sim` was at most 3. The expected burn-in range, about 2023-08-14 to 2023-11-27, is `ok` throughout.
- **L2 (2026-10-18):** bundle `569fdab6…` was uploaded to `gs://synthea-readmission-risk-bundles/` as `bundle-writer` through the JSON API with `ifGenerationMatch=0`, manifest last (all three HTTP 200). The checks:
  - each object's sha256 equals the local file's;
  - `manifest.json`'s sha256 (`c13a7027…922c`) equals the pin in `deploy/champion.json`;
  - each object has exactly one generation;
  - the scorer validated the bundle from GCS and scored 2025-06-30 on the local `main_200k` copy (integrity checks I1-I5 and guards 1-14 passed), writing the output to a scratch directory only.
- **L3 (2026-10-18):** the owner confirmed the apply after reviewing the plan.
  - **Plan:** `Plan: 17 to add, 2 to change, 0 to destroy`, nothing forcing a replacement. The two in-place changes were the nullable `gold_fingerprint_definition` column on `predictions` and `scoring_runs`, plus the new `predictions` description. The saved plan was applied: `Apply complete! Resources: 17 added, 2 changed, 0 destroyed.`
  - **Re-plan:** "No changes."
  - **Live state:** `serving.predictions` has 20 columns, `scoring_runs` 27 (each ending with `gold_fingerprint_definition`). `serving.monitoring` has 37 columns, a required partition filter on `started_at` and clustering on `sim_date`. Both service accounts `monitor` and `ledger-reader` exist.
- **L4 (2026-10-18):** all three checks passed.
  - **`make score-bq RUN_DATE=20230417 SCORE_ARGS="--window-days 7"`:** scoring run `0094151f021e40c588c4f523414a6875`, succeeded, published and recorded. The `scoring_runs` row shows run date 2023-04-17, window 7, top-n 10, 167 predictions, bundle `569fdab6…`, `gold_fingerprint_definition = spark-rowhash-v1`, and both test suites passed. In `serving.predictions`, as of 2023-04-17, there are 167 rows with one fingerprint definition (`spark-rowhash-v1`), discharged 2023-04-11T02:03 to 2023-04-17T23:53.
  - **`make m5-check` (M5 date 2023-04-17):** all 18 assertions (PRED-01..11, RUNS-01..07) fired on their seeded copy, and both clean copies passed.
  - **`make m5-monitoring`:** all 12 MON assertions fired, and the clean copy passed. It holds the base row plus the failed, refused, clock-stage and incomplete companions, so the NULL audit is proven live. No seeded copies are left in `scratch`.
- **L4b (2026-10-18):** the orphan `ledger` branch was created through the GitHub API with the recipe in `docs/setup.md`, before the merge.
  - **Commit:** `22f42b07…`, with no parents, holding `README.md`, `docs/run-ledger.md` and `docs/last-tick.json`.
  - **Badge:** `raw.githubusercontent.com/…/ledger/docs/last-tick.json` serves the no-tick JSON, and shields.io renders "none yet".
  - **Ruleset:** 24876876 still targets only `refs/heads/main` and `refs/heads/v2`.
- **L5 (2026-10-18):** PR #23 merged into `v2`; PR #4 merged `v2` into `main` as merge commit `0e4fe9d` (the prereg commit `c9427b2` is on `main`).
- **L6, V-post (2026-10-18):** passed, with run [39156510316](https://github.com/fma00/synthea-readmission-risk/actions/runs/39156510316) (green, five prefixes) and run [39156545023](https://github.com/fma00/synthea-readmission-risk/actions/runs/39156545023) (STS attribute-condition refusal). The refusal surfaced in `auth`, not in the listing step; see the slice-3 note's V-post section.
- **L7, L-post (2026-10-18):** [run 39156975790](https://github.com/fma00/synthea-readmission-risk/actions/runs/39156975790): `score.yml` from `main`, `run_date=20250630`: green; scoring run `1e38747afae74b9fabce486713c31097` succeeded, published and recorded with `trigger = workflow_dispatch`, `github_run_url` = this run, `git_commit` = `0e4fe9d` (the v2 merge), window 30, 675 predictions, bundle `569fdab6…`, `gold_fingerprint_definition = spark-rowhash-v1`; `serving.predictions` 2025-06-30 holds exactly these 675 rows (one scoring run, replacing the slice-4 v1 rows of that date).
