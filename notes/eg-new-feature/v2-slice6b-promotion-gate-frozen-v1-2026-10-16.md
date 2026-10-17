# v2 slice 6b: challenger at 200k, promotion gate (FR15), frozen-v1 external validation (FR16)

**Date:** 2026-10-16. **Branch:** a feature branch off `v2`, PR into `v2`. **PRD:** `notes/prds/v2-mlops-keyless-scheduled-scale-up-2026-10-09.md`
(FR15, FR16, M6/M7; Implementation hints item 6). **Predecessor:** `notes/eg-new-feature/v2-slice6a-main-200k-and-checks-2026-10-14.md`
(Results: `main_200k` exists and passed C1-C9; 616 test positives). **Owner decisions (2026-10-16):**
- **D1 The FR15 challenger is the logistic regression** trained on `main_200k`'s training partition. The calibrated XGBoost is
  still trained (the pipeline fits both) and reported, never gated. Neither `prereg.yaml` nor the PRD named the challenger;
  choosing between the two after seeing their test metrics would be a choice made from the validation data.
- **D2 Analysis lock:** before any model is trained on `main_200k`, a committed `analysis_lock` block in `prereg.yaml` fixes the
  challenger, the experiment name, the FR16 rows, the negative control and the code commit; Phase B is appended after the gate.
- **D3 Split:** 6b = this code (designed and tested offline) + the live runbook below; the FR0.3 portable export moves to
  **slice 6c**; `observed_expected_ratio` stays here.
- **D4 Rehearsal:** the live sequence runs on `pilot_20k` first, as pipeline sanity only; no pilot number is used for any choice.
- **D5 FR16 rows: all modeling rows** (train ∪ test of the pre-registered split; ≈ 157,827 rows) — the PRD's "on the 200k
  population"; the frozen v1 models never saw any `main_200k` patient (6a C5). The gate stays on the test window (the challenger
  was trained on the train partition).
- **D6 Negative control: a constant predictor** (the training prevalence) replaces the challenger's predictions and goes through
  the full decision, report and Phase B path. This is a **weak control**: it does not exercise the bundle-loading path and only
  the ROC-AUC rules can refuse it; the offline tests show each rule refusing on its own.

## Why

FR15: "Retraining at 200k produces a challenger bundle. The gate refuses promotion unless the challenger beats **both** the
current champion and the reason-code lookup on ROC-AUC (patient-level paired bootstrap, CI of the difference excluding 0), and
its Brier score is **non-inferior** … One deliberately bad challenger is submitted and visibly refused (G2)." FR16: frozen-v1
external validation "in an evaluation mode separate from the scorer … applies guard 14's major.minor rule itself, fails on
`InconsistentVersionWarning`, and reports the pre-registered metrics on the 200k population." `prereg.yaml` (Phase A) fixes
every number: `bootstrap` {patient_clustered_paired_percentile, 2000, seed 20261012, 0.95}, `promotion` {comparators
[champion, reason_code_lookup], roc_auc_rule paired_ci_lower_gt_zero, Brier margin 0.05 of the champion's Brier, lookup
smoothing 10.0}, `frozen_v1` {models [logistic_regression, xgboost_calibrated], report [roc_auc, brier_score,
observed_expected_ratio], acceptance {roc_auc_ci_lower_min 0.80, observed_expected_ci_must_overlap [0.80, 1.25]}},
`metrics.definitions.observed_expected_ratio` = sum(y_true) / sum(y_prob), and `outcomes.lookup_still_wins`.

**The AUC rule is one-sided**: "beats" = the lower bound of the 95% paired CI of the difference is > 0 (prereg
`paired_ci_lower_gt_zero`), which implies the PRD's "CI excludes 0" in the favourable direction. Reports and `docs/results.md`
must describe it that way, not as a two-sided test.

## Scope

**In:**
- **S1** `observed_expected_ratio` in `models/evaluate.py` (point metric and bootstrap metric).
- **S2** `models/gate.py`: pure decision functions for FR15 and FR16.
- **S3** `models/gate_run.py` + `scripts/run_promotion_gate.py`: verification, predictions, report; one CLI computes the gate
  and frozen-v1 (the champion IS the frozen-v1 logistic regression). `--negative-control` (D6).
- **S4** `ops/prereg.py` accepts two optional appended blocks, `analysis_lock` and `phase_b`, and verifies that Phase A is
  unchanged; `scripts/prereg_append.py` writes them (append-only).
- **S5** `parse_python_major_minor`, its regex, `installed_versions` and the tag→distribution map move from
  `scoring/bundle_guard.py` to `models/versions.py` (stdlib only: `re`, `sys`, `importlib.metadata`); `bundle_guard` imports
  them under the same names; no guard logic changes.
- **S6** Make targets `gate`, `prereg-lock`, `prereg-phase-b`; `docs/setup.md` subsection; the live runbook L0-L7.

**Out:** FR0.3 portable export (6c); promotion itself if the gate passes (bundle upload, the `gold_fingerprint_definition`
column in `serving.*`, the `deploy/champion.json` PR) — a follow-up with its own design; any change to the label, split,
leakage controls, hyperparameters, the scorer's guards; tuning; gating XGBoost; monitoring (slice 7).

## Surfaces touched

| Surface | Change |
|---|---|
| `src/readmission_risk/models/evaluate.py:15-21` (`BOOTSTRAP_METRICS`), `:84-116` (`evaluate_probabilities`), `:144-153` (`_resample_metrics`) | add `observed_expected_ratio` (last in `BOOTSTRAP_METRICS`) |
| `scripts/train_models.py:96` | iterates `BOOTSTRAP_METRICS` for printing; prints the new metric too (no code change expected; verified by its test) |
| `src/readmission_risk/models/versions.py` | gains `_PYTHON_VERSION_RE`, `parse_python_major_minor`, `LIBRARY_VERSION_DISTRIBUTIONS` (the map formerly `_DISTRIBUTIONS`), `installed_versions` — moved verbatim from `scoring/bundle_guard.py:63, 65-71, 97-115` |
| `src/readmission_risk/scoring/bundle_guard.py:63-115` | those definitions removed; `from readmission_risk.models.versions import LIBRARY_VERSION_DISTRIBUTIONS as _DISTRIBUTIONS, installed_versions, parse_python_major_minor` |
| `src/readmission_risk/ops/prereg.py:58-79` | properties `training`, `bootstrap`, `promotion`, `frozen_v1` (→ `self.raw[...]`), `analysis_lock`, `phase_b` (dict or `None`) |
| `src/readmission_risk/ops/prereg.py:169-264, 359-367` | schema: optional `analysis_lock`, `phase_b` after `deviations` |
| `src/readmission_risk/ops/prereg.py:403-405` | the hand-added `\| {"observed_expected_ratio"}` removed (now part of `evaluate_probabilities`) |
| `src/readmission_risk/models/gate.py` (new), `src/readmission_risk/models/gate_run.py` (new) | S2, S3 |
| `scripts/run_promotion_gate.py` (new), `scripts/prereg_append.py` (new) | CLIs |
| `Makefile`, `tests/test_tooling.py` | targets, each with a `## ` help line and in `.PHONY`: `gate` (`@test -n "$(RUN)"` guard like `bigjoin-batch`, then `$(BIN)/python scripts/run_promotion_gate.py --run $(RUN) $(GATE_ARGS)`), `prereg-lock` (`@test -n "$(LOCK_ARGS)"` guard, then `$(BIN)/python scripts/prereg_append.py lock --challenger-model logistic_regression $(LOCK_ARGS)`), `prereg-phase-b` (`$(BIN)/python scripts/prereg_append.py phase-b $(PHASE_B_ARGS)`); variables `GATE_ARGS`, `LOCK_ARGS`, `PHASE_B_ARGS`; `gate` added to `test_live_targets_refuse_without_required_variables` (`tests/test_tooling.py:307`); the three targets added to `EXPECTED_TARGETS` and the three variables to `MAKEFILE_VARIABLES` (`tests/test_tooling.py:26-36`) |
| `tests/models/test_evaluate.py:121` (`len(BOOTSTRAP_METRICS) == 5` → 6), `tests/models/test_training.py` (metric-set assertions), `tests/models/test_gate.py`, `tests/models/test_gate_run.py`, `tests/ops/test_prereg.py`, `tests/ops/test_prereg_append.py`, `tests/scripts/` or `tests/models/test_run_promotion_gate_cli.py` (new), `tests/scoring/test_bundle_guard.py` (unchanged imports still resolve) | tests |
| `docs/setup.md` (new section `## Promotion gate and frozen-v1 validation (v2 slice 6b)` after the 6a section: the three targets, exit codes, report fields, a pointer to this runbook), `CLAUDE.md`, this note; `notes/eg-new-feature/v2-slice6b-reports/` (committed reports, at L6) | docs |

Not touched: `models/training.py`, `models/split.py`, `models/data.py`, `models/baselines.py`, `scoring/*` other than the import,
`bundles/*`, `pipeline/*`, Phase A of `prereg.yaml`.

## Interfaces

### S1 `observed_expected_ratio`

`evaluate_probabilities`: `metrics["observed_expected_ratio"] = float(y_true.sum() / y_prob.sum())` if `y_prob.sum() > 0`, else
`float("nan")`. `BOOTSTRAP_METRICS` gains it (last); `_resample_metrics` computes the same per resample. Training logs
`test_observed_expected_ratio` and its interval automatically.

### S2 `models/gate.py` (pure; numpy + `models.evaluate`)

```python
ARMS = ("challenger", "champion", "reason_code_lookup")
SUPPORTED_BOOTSTRAP_KIND = "patient_clustered_paired_percentile"
FROZEN_REPORT_METRICS = ("roc_auc", "brier_score", "observed_expected_ratio")
RULE_NAMES = ("roc_auc_vs_champion", "roc_auc_vs_reason_code_lookup", "brier_noninferior_vs_champion")

@dataclass(frozen=True)
class RuleOutcome:
    name: str        # one of RULE_NAMES
    statistic: str   # exactly, with P = f"{ci_level:.0%}":
                     # f"lower bound of the {P} paired CI of roc_auc(challenger) - roc_auc(champion)"
                     # f"lower bound of the {P} paired CI of roc_auc(challenger) - roc_auc(reason_code_lookup)"
                     # f"upper bound of the {P} paired CI of brier(challenger) - brier(champion)"
    value: float
    threshold: float # 0.0 for the AUC rules; brier_margin_fraction * point["champion"]["brier_score"] for Brier
    passed: bool     # AUC: value > threshold (strict); Brier: value <= threshold (inclusive)

def decide_gate(differences: Mapping[str, Mapping[str, tuple[float, float]]], point: Mapping[str, Mapping[str, float]], *,
                brier_margin_fraction: float, ci_level: float) -> tuple[tuple[RuleOutcome, ...], str]:
    """Pure over intervals: rules in RULE_NAMES order; decision 'PROMOTE' iff all passed else 'REFUSE'. A NaN value fails."""

@dataclass(frozen=True)
class GateResult:
    point: dict[str, dict[str, float]]                     # arm -> evaluate_probabilities(...).metrics
    differences: dict[str, dict[str, tuple[float, float]]] # "challenger_minus_champion", "challenger_minus_reason_code_lookup"
    rules: tuple[RuleOutcome, ...]
    decision: str                                           # "PROMOTE" | "REFUSE"
    lookup_still_wins: bool                                 # rule roc_auc_vs_reason_code_lookup failed
    n_resamples_used: int
    n_resamples_skipped: int

def evaluate_gate(y_true, groups, probs: Mapping[str, np.ndarray], *, train_prevalence: float, n_resamples: int,
                  seed: int, ci_level: float, brier_margin_fraction: float) -> GateResult: ...

@dataclass(frozen=True)
class FrozenModelResult:
    point: dict[str, float]                    # FROZEN_REPORT_METRICS
    intervals: dict[str, tuple[float, float]]  # FROZEN_REPORT_METRICS
    roc_auc_ci_lower_ok: bool
    observed_expected_overlap_ok: bool
    accepted: bool

def frozen_acceptance(point_intervals: Mapping[str, tuple[float, float]], *, roc_auc_ci_lower_min: float,
                      oe_band: tuple[float, float]) -> tuple[bool, bool]:
    """Pure: (intervals["roc_auc"][0] >= roc_auc_ci_lower_min, lo <= band_hi and hi >= band_lo for O/E); NaN -> False."""

def evaluate_frozen_v1(y_true, groups, probs: Mapping[str, np.ndarray], *, n_resamples: int, seed: int, ci_level: float,
                       roc_auc_ci_lower_min: float, oe_band: tuple[float, float]) -> dict[str, FrozenModelResult]: ...
```

- `probs` keys must equal `ARMS` (gate) / the frozen models given (frozen); each array has `len(y_true)` entries, finite, in
  [0, 1]; `y_true` binary with both classes; else `ValueError`.
- Gate bootstrap: two `clustered_bootstrap` calls with the same `seed`, `groups`, `y_true`: `{"champion": …, "challenger": …}`
  and `{"reason_code_lookup": …, "challenger": …}`, `alpha = 1 - ci_level`. Both draw identical resamples (draws depend only on
  the number of groups; skips only on `y`); `RuntimeError` if their `n_resamples_used` differ. Keys are as
  `clustered_bootstrap` names them (`f"{b}_minus_{a}"`).
- `decide_gate`: `roc_auc_vs_champion.value = differences["challenger_minus_champion"]["roc_auc"][0]`;
  `roc_auc_vs_reason_code_lookup` likewise; `brier_noninferior_vs_champion.value =
  differences["challenger_minus_champion"]["brier_score"][1]`.
- `point[arm] = evaluate_probabilities(y_true, probs[arm], train_prevalence=train_prevalence).metrics`.
- Frozen: one `clustered_bootstrap` call over the frozen models (same `seed`); `point` from `evaluate_probabilities` (train
  prevalence irrelevant to the three reported metrics; pass the modeling rows' own prevalence).

### S3 `models/gate_run.py`, `scripts/run_promotion_gate.py`

```python
CODE_PATHS = ("src", "scripts", "Makefile", "requirements.txt", "requirements-linux-extras.txt")
LOCK_MARKER = "analysis_lock:"

@dataclass(frozen=True)
class GateRunConfig:
    run: str                        # "main_200k" | "pilot_20k"
    challenger_experiment: str
    challenger_bundle: Path         # local bundles/<bundle_id> exported from the challenger LR run
    repo_root: Path                 # every relative path below resolves against it; git runs as `git -C repo_root`
    champion_path: Path = Path("deploy/champion.json")
    bundles_root: Path = Path("bundles")
    frozen_experiment: str = "readmission-risk-v1"
    tracking_dir: Path = Path("mlflow")
    v1_gold_dir: Path = Path("data/gold/full_run_2b")
    rehearsal: bool = False
    negative_control: bool = False

class GateRunError(RuntimeError): ...

def run_gate(config: GateRunConfig, *, prereg: Prereg, run_git: Callable,
             gold_opener: Callable[[str], AbstractContextManager[Path]] = materialized_gold_dir) -> dict: ...
```

`run_git(argv, capture_output=True, check=False)` as in `ops/prereg.py`; `argv = ["git", "-C", str(repo_root), …]`.
`run_gate` returns the report body (every key below except `generated_at_utc`, `error`, `head_commit`, `prereg_sha256`, which the
CLI adds). Steps, each failure a `GateRunError` naming the step:

1. **Mode and lock.** `rehearsal` requires `run == "pilot_20k"`; a non-rehearsal run requires `run == "main_200k"`.
   `prereg.bootstrap["kind"] == SUPPORTED_BOOTSTRAP_KIND`; `prereg.promotion["comparators"] == ["champion",
   "reason_code_lookup"]`; `prereg.frozen_v1["report"] == list(FROZEN_REPORT_METRICS)`. Non-rehearsal: `prereg.analysis_lock`
   present with `challenger_model == "logistic_regression"`, `experiment_name == challenger_experiment`, `frozen_v1_rows ==
   "modeling_rows"`, `negative_control == "constant_train_prevalence"`; **the code at HEAD equals the locked code**: `git diff
   --quiet <code_commit> HEAD -- CODE_PATHS` exits 0; `git log -1 --format="%H %ct" -S"analysis_lock:" -- prereg.yaml` gives the
   commit that added the block and its committer time (`lock_time`); that lock commit must be contained in a remote branch
   (`git branch -r --contains <lock commit>` non-empty after `git fetch origin`, the pattern of `check_prereg_committed`,
   `ops/prereg.py:458`), because a local committer time is settable by hand. **Prereg agrees with the code:**
   `check_prereg_against_code(prereg)` (`ops/prereg.py:378`, P1b; it reads Phase A keys only) must not raise, in every mode.
2. **Gold.** `gold_opener(gold_prefix(run))` + `load_gold_table_with_metadata`; metadata `metadata_version == 2`,
   `gold_fingerprint_definition == "spark-rowhash-v1"`, `label_definition`, `tuple(planned_procedure_codes)` (metadata holds a
   tuple, prereg a list: compared as tuples), `readmission_window_days`, `lookback_years` equal prereg's `label`, `reference_date == prereg.generation["reference_date"]`. **Phase A intact:**
   `phase_a_commit = metadata.generation_summary["provenance"]["prereg_commit"]` (must match `[0-9a-f]{40}`); `old =
   parse_prereg(git show <phase_a_commit>:prereg.yaml)`; **every Phase A key, `deviations` included, must be equal** in
   `old.raw` and `prereg.raw` (parsed values, so formatting is irrelevant). Reason (owner rule, 2026-10-14 Step 0): "any
   change to an analysis value is a committed `deviations` entry BEFORE main_200k exists" — `main_200k` exists, so no Phase A
   value and no deviation may change any more; a needed change goes through `/eg-prd` and a new design, not through this gate. Then `split =
   chronological_group_split(...)` with prereg's settings (the call `ops/population_checks.py` builds). **Power (M7)**
   (non-rehearsal): `split.summary["n_test_positive"] >= prereg.power["min_test_positives"]`, else `GateRunError` (6a's C9 already passed with
   616; the gate re-checks the pre-registered precondition); reported as `power`.
3. **Challenger runs.** For `name` in (`logistic_regression`, `xgboost_calibrated`): `resolve_run_id(tracking_dir,
   challenger_experiment, name)` (`models/tracking.py:49`). Tags: `model_name == name`, `ROWHASH_TAG ==
   metadata.gold_fingerprint`, `git_dirty == "clean"`, `mlflow.source.git.commit` present. Params (MLflow strings): `seed ==
   str(prereg.training["seed"])`, `reference_date == prereg.generation["reference_date"]`, `test_start_date ==
   prereg.split["test_start_date_resolved"]`, `train_start_date == "none"` (prereg `null`), `readmission_window_days ==
   str(label window)`, `gold_lookback_years == str(label lookback)`; for XGBoost also `calibration_method ==
   prereg.training["calibration"]["method"]`, `calibration_cv_folds == str(…["cv_folds"])`. Non-rehearsal: `git diff --quiet
   <run commit> <code_commit> -- CODE_PATHS` exits 0, and the run's `start_time` (ms) > `lock_time * 1000`.
   The **challenger bundle's verified** `split_assignment.csv` (step 7; it is the file the scorer will later rely on): per
   partition, the set of encounter ids equals `split.train` / `split.test`'s, no duplicates, no other partition values (order
   ignored). This check runs after step 7.
   **No other model saw this gold:** non-rehearsal, `runs_on_gold(tracking_dir, fingerprint)` lists **every** run in the store
   whose tag `ROWHASH_TAG` equals `metadata.gold_fingerprint`: experiments via `search_experiments(view_type=ViewType.ALL)`,
   runs via `search_runs(..., run_view_type=ViewType.ALL, max_results=1000)` following `page_token` until exhausted (deleted
   runs and deleted experiments included). Every such run must belong to `challenger_experiment`; a **deleted** one is allowed
   only there and only with `start_time > lock_time` (L3's partial-failure remedy). The per-experiment counts (active/deleted)
   are reported as `runs_on_this_gold`. Limit, stated: a store on another machine, or a run whose `mlflow.db` rows were removed
   by hand, cannot be seen.
4. **Frozen-v1 runs.** `resolve_run_id(tracking_dir, frozen_experiment, name)` for `prereg.frozen_v1["models"]`. **Live
   reproduction (FR16's "exactly the v1 pins"):** `load_gold_table_with_metadata(v1_gold_dir)` (`GateRunConfig.v1_gold_dir`,
   default `data/gold/full_run_2b`; missing → `GateRunError`), split with the frozen LR run's own params (`reference_date`,
   `test_start_date`, `train_start_date`, `readmission_window_days`), and both frozen models' `roc_auc` and `brier_score` on that
   test window must equal their logged `test_roc_auc` / `test_brier_score` within 1e-9 — this catches any library drift that
   changes predictions, including libraries the runs do not tag (scipy).
5. **Python and libraries (guard 14 and guard 8's rules, applied here)** for all four runs: tag `python_version` parses with
   `parse_python_major_minor` and equals the running interpreter's major.minor; each tag in `LIBRARY_VERSION_DISTRIBUTIONS`
   equals `installed_versions()[tag]`.
6. **Champion bundle.** `champion = read_champion(champion_path)` (`bundles/champion.py:23`; fields `bundle_uri`, `sha256`);
   `d = bundles_root / bundle_uri_leaf(champion.bundle_uri)`. The sha256 of the raw bytes of `d/manifest.json` must equal
   `champion.sha256` (the pin is over the file's bytes; the manifest has no `sha256` key); then `verify_bundle_dir(d)` (below);
   `manifest["mlflow"]["run_id"] ==` the frozen-v1 LR run id.
7. **Challenger bundle.** `verify_bundle_dir(challenger_bundle)`; `manifest["mlflow"]["run_id"] ==` the challenger LR run id;
   `manifest["git"]["commit"] ==` the run's `mlflow.source.git.commit` tag (the report's and Phase B's `git_commit` is that
   tag).
   `verify_bundle_dir(d) -> (manifest, model_bytes)`: `manifest = json.loads(d/manifest.json)`; `compute_bundle_id(manifest) ==
   manifest["bundle_id"] == d.name`; the directory holds exactly `manifest.json` plus the keys of `manifest["files"]` (entries
   `{sha256, size, role}`); each file's sha256 and size equal its entry (the scorer's I1-I4 for a local bundle); returns the
   verified `model.pkl` bytes.
8. **Load.** Challenger LR and champion: `cloudpickle.loads(model_bytes)` of the verified bundle bytes, so **the bundles are what
   is evaluated** and Phase B's bundle ids name exactly the scored models. Challenger XGBoost (reported only) and both frozen-v1
   models for FR16: `load_logged_model(tracking_dir, run_id)`. The frozen LR's MLflow model and the champion bundle are the same
   run; their predictions on the modeling rows must be equal (`np.array_equal`), else `GateRunError`. Every load sits inside
   `warnings.catch_warnings(); simplefilter("error", InconsistentVersionWarning)`.
   Run data is read through one helper, `_read_run(tracking_dir, run_id) -> RunRecord(tags, params, metrics, start_time,
   lifecycle_stage, experiment_id)`, so tests replace it for immutable-param cases (seed, calibration) instead of cloning runs.
9. **Predict** (`predict_proba(prepare_features(frame))[:, 1]`, shape and range checked, else `GateRunError`):
   - test window (`split.test`): challenger LR, challenger XGBoost (reported only), frozen LR (= champion), lookup =
     `reason_code_lookup(split.train["admission_reason_code"], split.train[LABEL_COLUMN], split.test["admission_reason_code"],
     smoothing=prereg.promotion["reason_code_lookup_smoothing"])`; negative control: challenger :=
     `np.full(len(test), train_prevalence)`;
   - modeling rows (`pd.concat([split.train, split.test])`): both frozen models (D5).
   **Reproduction check:** the challenger LR's (from its bundle) and XGBoost's test `roc_auc` and `brier_score` from
   `evaluate_probabilities` equal the runs' logged `test_roc_auc` / `test_brier_score` within 1e-9, else `GateRunError`
   (proves the bundle bytes are the trained model, and the feature path).
10. **Decide.** `evaluate_gate` (test window; `train_prevalence` = mean of `split.train` labels) and `evaluate_frozen_v1`
    (modeling rows) with prereg's `bootstrap` (`n_resamples`, `seed`, `ci_level`) and `frozen_v1.acceptance` values.

**Report** (`out_dir/<run>/gate_report.json`, or `negative_control_report.json` with `--negative-control`; `out_dir` =
`data/gate`), keys: `run`, `mode` (`"main"` | `"negative_control"`), `rehearsal` (bool), `head_commit`, `prereg_sha256`,
`generated_at_utc` (ISO `Z`), `error` (null on success), `analysis_lock` (the block or null), `lock_time_utc` (ISO `Z`; null in rehearsal), `phase_a_commit` (always), `power` {`n_test`, `n_test_positive`, `min_test_positives`},
`gold_fingerprint`, `split_summary` (only the keys of `split.summary` whose value is an `int` and not a `bool`, in
`SPLIT_SUMMARY_KEYS` order, `models/split.py:26-46`), `challenger` {`run_id`, `bundle_id`, `git_commit`, `replaced_by`:
null or `"constant_train_prevalence"`}, `champion` {`bundle_id`, `run_id`}, `gate` {`point`, `differences`, `rules`,
`decision`, `lookup_still_wins`, `n_resamples_used`, `n_resamples_skipped`}, `frozen_v1` {`rows`: `"modeling_rows"`, `n_rows`,
`n_positive`, per model {`run_id`, `point`, `intervals`, `roc_auc_ci_lower_ok`, `observed_expected_overlap_ok`, `accepted`}},
`xgboost_reported` {`run_id`, `point`}. Tuples become lists; every NaN becomes `null` (sanitized before `json.dumps(…,
allow_nan=False, sort_keys=True, indent=2)` + newline). **No overwrite:** before any computation the CLI refuses (exit 2,
nothing written) when the target report exists, is non-rehearsal and has `error == null`; an earlier error report or a
rehearsal report is overwritten. The table file follows its report: written with a successful report; **deleted** (if
present) when an error report is written, so no stale table sits next to an error. On exit 2 stdout prints nothing; stderr
prints one `ERROR: …` line. In rehearsal the report's `analysis_lock` and `lock_time_utc` are `null` even when the file has a
lock (rehearsal ignores it).

**`gate_table.md`** (same directory; `negative_control_table.md` in that mode). Every line ends in `\n`, blank lines as shown;
in rehearsal the file starts with `REHEARSAL: pipeline sanity only, not a result` and a blank line:
```
# Promotion gate: <run> (<mode>)

Decision: **<PROMOTE|REFUSE>**

| Rule | Statistic | Value | Threshold | Result |
|---|---|---|---|---|
| roc_auc_vs_champion | <statistic> | <value> | > <threshold> | <PASS|FAIL> |
| roc_auc_vs_reason_code_lookup | <statistic> | <value> | > <threshold> | <PASS|FAIL> |
| brier_noninferior_vs_champion | <statistic> | <value> | <= <threshold> | <PASS|FAIL> |

## Frozen v1 (all modeling rows, n=<n_rows>, positives=<n_positive>)

| Model | ROC-AUC [<P> CI] | Brier [<P> CI] | O/E [<P> CI] | Accepted |
|---|---|---|---|---|
| <model> | <point> [<lo>, <hi>] | <point> [<lo>, <hi>] | <point> [<lo>, <hi>] | <yes|no> |
```
One model row per `prereg.frozen_v1.models`, in that order; numbers `f"{x:.4f}"`, `n/a` for null; `<P>` = `f"{ci_level:.0%}"`.

**CLI** `scripts/run_promotion_gate.py --run {main_200k,pilot_20k} --challenger-experiment NAME --challenger-bundle PATH
[--rehearsal] [--negative-control] [--tracking-dir mlflow] [--out-dir data/gate]`, `main(argv=None, *, run_gate=run_gate,
run_git=subprocess.run, repo_root=REPO_ROOT, environ=os.environ, gold_opener=materialized_gold_dir) -> int`.
Preconditions (exit 2, `ERROR:` on stderr, no report): `CLOUDSDK_CORE_PROJECT == synthea-readmission-risk`; HEAD resolvable;
`HEAD:prereg.yaml` equals the working tree and parses; non-rehearsal: `git status --porcelain` empty (no untracked files
either). Exit **0** = PROMOTE, **3** = REFUSE, **2** = could not run (`GateRunError` or any other exception after the
preconditions: a report with only `run`, `mode`, `rehearsal`, `head_commit`, `prereg_sha256`, `generated_at_utc`, `error` =
`"<Type>: <message>"`). Python's own exit 1 (an uncaught error before `main`'s handler) therefore never means REFUSE. Stdout:
the table, and for rehearsal a first line `REHEARSAL: pipeline sanity only, not a result`. Frozen-v1 acceptance never changes
the exit code (FR16 is reported, not gated). The runbook and `phase-b` check the report's `decision` and `mode`, not only
exit codes.

### S4 `analysis_lock` and `phase_b`

```yaml
analysis_lock:
  locked_on: "2026-10-1X"                    # ISO date (UTC today)
  challenger_model: logistic_regression      # D1
  experiment_name: readmission-risk-v2-main200k
  frozen_v1_rows: modeling_rows              # D5
  negative_control: constant_train_prevalence  # D6
  code_commit: "<40 hex>"                    # HEAD when written; CODE_PATHS at HEAD must equal it when gating
phase_b:
  recorded_on: "2026-10-1X"
  reports:                                   # committed copies (D: below), sha256 of their bytes
    gate: {path: notes/eg-new-feature/v2-slice6b-reports/gate_report.json, sha256: "<64 hex>"}
    negative_control: {path: notes/eg-new-feature/v2-slice6b-reports/negative_control_report.json, sha256: "<64 hex>"}
  decision: PROMOTE | REFUSE
  challenger: {run_id: "<32 hex>", bundle_id: "<64 hex>", git_commit: "<40 hex>"}
  champion: {run_id: "<32 hex>", bundle_id: "<64 hex>"}
  rules:                                     # exactly RULE_NAMES, in order
    - {name: roc_auc_vs_champion, value: <float>, threshold: <float>, passed: <bool>}
  lookup_still_wins: <bool>
  negative_control_decision: REFUSE          # the literal REFUSE; anything else is invalid
  frozen_v1:                                 # keys exactly prereg.frozen_v1.models
    logistic_regression: {roc_auc: [point, lo, hi], brier_score: [...], observed_expected_ratio: [...], accepted: <bool>}
  power: {n_test: <int>, n_test_positive: <int>, min_test_positives: <int>}
```

`parse_prereg`: the document's top-level keys must be Phase A's keys in their current order, optionally followed by
`analysis_lock`, then optionally `phase_b`; `phase_b` without `analysis_lock` is invalid. Schema exact (no extra keys);
hex lengths enforced; numbers may be `null` (from NaN) in `value`/`threshold`/triples; booleans strict; `decision` one of the two
words. Leaf rules (`ops/prereg.py` stays stdlib + pyyaml at module level, so it holds its own `PREREG_MODEL_NAMES` and
`PREREG_RULE_NAMES` tuples; `tests/ops/test_prereg.py` asserts they equal `models.constants.MODEL_NAMES` and
`models.gate.RULE_NAMES`): `locked_on`/`recorded_on` with the existing `_iso_date`; `challenger_model` in `PREREG_MODEL_NAMES`; `experiment_name` a
non-empty string; `frozen_v1_rows` the literal `modeling_rows`; `negative_control` the literal `constant_train_prevalence`;
`reports.gate.path` / `reports.negative_control.path` the two literal paths above; run ids 32 hex, bundle ids and sha256 64 hex,
commits 40 hex; `decision` PROMOTE/REFUSE; `negative_control_decision` the literal REFUSE. `parse_prereg` validates structure
only (no file I/O); the committed reports' sha256 values are checked by `prereg_append.py phase-b` when it writes and by a
repository test (`tests/ops/test_prereg.py`: if the repo's `prereg.yaml` has `phase_b`, both report files exist and hash to the
recorded values). `Prereg.analysis_lock` / `Prereg.phase_b` return the dict or `None`. `check_prereg_against_code` and the launchers read
Phase A keys only.

`scripts/prereg_append.py` (argparse; `main(argv=None, *, repo_root=REPO_ROOT, run_git=subprocess.run, today=None)`):
- `lock --experiment-name NAME [--challenger-model logistic_regression] [--tracking-dir mlflow] [--gold-fingerprint HEX]`:
  refuses unless `git status --porcelain` is empty, the file has no `analysis_lock`, `check_prereg_against_code(prereg)` passes,
  the experiment does not exist in `tracking_dir/mlflow.db` (any lifecycle stage), and **no run in the store, deleted or
  active** (`runs_on_gold`) carries `ROWHASH_TAG ==
  --gold-fingerprint` (default: `main_200k`'s `80edd13dcc967d69b6fa4cfa17b4d7fa40188f8e7e17b2031b833332a59c63f3`, from the 6a
  Results) (read only, via `MlflowClient`; a missing store counts as empty). Appends `"\n# Slice 6b analysis lock (scripts/prereg_append.py);
  Phase A above is unchanged.\n" + yaml.safe_dump({"analysis_lock": block}, sort_keys=False, default_flow_style=False)`.
- `phase-b [--report data/gate/main_200k/gate_report.json] [--negative-control-report data/gate/main_200k/negative_control_report.json]`:
  refuses unless the tree is clean, `analysis_lock` exists, `phase_b` does not, the main report has `run == main_200k`, `mode
  == "main"`, `rehearsal == false`, `error == null`, `analysis_lock` equal to the file's block; the negative-control report has
  `mode == "negative_control"`, `decision == "REFUSE"`, `run == "main_200k"`, `rehearsal == false`, `error == null`, and the
  same `analysis_lock`, `head_commit`, `gold_fingerprint` and challenger `run_id`/`bundle_id`/`git_commit` as the main report. Copies both reports byte-for-byte to
  `notes/eg-new-feature/v2-slice6b-reports/`, then appends `phase_b` (comment header like the lock).
- Both re-parse the would-be file with `parse_prereg` and check that the old bytes are an exact prefix of the new before an atomic
  replace; neither commits.

## UX flow (live runbook; each step owner-confirmed immediately before it)

**Make's exit status is not the script's:** `make` prints `make: *** [gate] Error <n>` and exits 2 for any non-zero recipe, so
every Pass below is judged from the report's `mode`, `decision` and `error` fields (and the printed `Error <n>`), never from
`make`'s own status.

Preconditions for every step: `export CLOUDSDK_CORE_PROJECT=synthea-readmission-risk`; the operator's own user ADC (6a L5 proved
it reads `gold/v2/`); the local `mlflow/` store holding `readmission-risk-v1` and the local champion copy
`bundles/<champion leaf>` (recovery if missing: `gcloud storage cp -r <champion bundle_uri> bundles/` as the operator); the
local v1 gold `data/gold/full_run_2b` (step 4's live frozen-v1 reproduction; recovery: `gcloud storage cp -r
gs://synthea-readmission-risk-gold/full_run_2b data/gold/`); **no
untracked or modified files** (training records `git_dirty`, which counts untracked files, `models/training.py:127-145`).

| Step | Action | Pass |
|---|---|---|
| L0 | PR #16 (6a results) and this 6b code PR merged into `v2`; `git pull`; tree clean; CI green | |
| L1 Rehearsal | `make train TRAIN_EXPERIMENT=rehearsal-6b-pilot20k GOLD_DIR=gs://synthea-readmission-risk-gold/v2/pilot_20k REFERENCE_DATE=20261012`; `make export EXPERIMENT=rehearsal-6b-pilot20k EXPORT_MODEL=logistic_regression`; `make gate RUN=pilot_20k GATE_ARGS="--rehearsal --challenger-experiment rehearsal-6b-pilot20k --challenger-bundle bundles/<id>"`; same plus `--negative-control` | main report: `error` null and a decision; negative-control report: `mode == negative_control`, `decision == REFUSE`, `error` null. No pilot number is used for anything. |
| L2 Lock | `make prereg-lock LOCK_ARGS="--experiment-name readmission-risk-v2-main200k"`; commit `prereg.yaml` alone; PR into `v2`; merge; `git pull` | parses; CI green |
| L3 Train | `make train TRAIN_EXPERIMENT=readmission-risk-v2-main200k GOLD_DIR=gs://synthea-readmission-risk-gold/v2/main_200k REFERENCE_DATE=20261012` | two FINISHED runs, `git_dirty` clean, rowhash tag `80edd13d…63f3`. **If training fails partway** (e.g. a FINISHED LR run but no XGBoost run): owner-confirmed `mlflow runs delete --run-id <id>` for every run of the experiment (`resolve_run_id` searches active runs only), then re-run L3; the lock is untouched and the new runs still start after the lock time. |
| L4 Export | `make export EXPERIMENT=readmission-risk-v2-main200k EXPORT_MODEL=logistic_regression` (the target passes `--experiment-name $(EXPERIMENT) --model $(EXPORT_MODEL)`, `Makefile:79-80`) | `bundles/<id>`, local only |
| L5 Gate | `make gate RUN=main_200k GATE_ARGS="--challenger-experiment readmission-risk-v2-main200k --challenger-bundle bundles/<id>"`, then with `--negative-control` | main report: `error` null and a decision (either is a result); negative-control report: `decision == REFUSE`, `error` null |
| L6 Phase B | `make prereg-phase-b`; commit `prereg.yaml` + the two copied reports; PR into `v2` | parses; numbers equal the reports |
| L7 | Results into this note, `docs/results.md` (the outcome; `outcomes.lookup_still_wins` wording if it applies; AUC rule described as one-sided), CLAUDE.md; PR | |

If L5 says PROMOTE, promotion is a separate, owner-confirmed follow-up.

## Data handling

- **No peeking.** The challenger (D1), FR16 rows (D5), negative control (D6) and the code are locked before any model sees
  `main_200k`; the gate refuses code at HEAD that differs from the lock, a challenger run trained before the lock or from other
  code, other training settings (step 3 params), and a modified Phase A (step 2 prefix check). Training scores the test window as
  it always has (`train_and_evaluate`); no number from it feeds a choice. XGBoost is reported, never selected.
- **Leakage.** Lookup and challenger fitted on `split.train` only; gate predictions on `split.test` only; the champion and
  frozen-v1 models were trained on v1 (patients disjoint from `main_200k`, 6a C5), so all modeling rows are external for them.
- **Reproducibility.** Training seed and settings checked per run (step 3); bootstrap seed 20261012, 2000 resamples, 95%
  percentile, identical resamples across the gate's two calls; a re-run produces a byte-identical `gate` and `frozen_v1` section.
  The committed reports and Phase B record HEAD, prereg sha256, lock, run ids, bundle ids, gold fingerprint.
- **Unseen categories.** v1 pipelines one-hot encode with `handle_unknown="infrequent_if_exist"` (`models/features.py:24`).
- **Reports** hold metrics, counts, ids of runs/bundles/commits only — no patient or encounter ids — so committing them is safe.

## Failure modes

- Any verification in steps 1-7 fails → exit 2 with the reason, nothing decided.
- Python or library version drift, or `InconsistentVersionWarning` → exit 2 (remedy: the matching environment, `docs/scoring.md`).
- The reproduction check fails (loading or feature path differs from training) → exit 2.
- All bootstrap resamples single-class → `ValueError` → exit 2 (impractical at 616 positives).
- Constant challenger → AUC CI lower bound ≤ 0 → REFUSE (exit 3).
- O/E `nan` (a model predicting 0 everywhere) → `null` in reports, acceptance false.
- Test positives below `power.min_test_positives` → exit 2 (rehearsal on the pilot skips this check: 62 positives; reported).
- An existing non-rehearsal report → exit 2 (delete it deliberately to re-run; Phase B points at committed copies anyway).
- `prereg_append.py lock` after training (experiment exists) → refused; `phase-b` with a crashed, rehearsal or mismatched
  report → refused.

## Verification criteria

Offline (CI; no GCP):
1. `tests/models/test_evaluate.py`: O/E on y=[1,0,0,1], p=[.5,.5,.5,.5] → 1.0; p all 0 → nan; present in `clustered_bootstrap`
   intervals; `len(BOOTSTRAP_METRICS) == 6`. `tests/models/test_training.py` metric-set assertions include
   `test_observed_expected_ratio`.
2. `tests/models/test_gate.py`: `decide_gate` on hand-built intervals: AUC lower bound 0.0 → FAIL (strict), 1e-9 → PASS; Brier
   upper bound exactly the threshold → PASS, above → FAIL; NaN → FAIL; only the Brier rule failing → REFUSE; only the lookup rule
   failing → REFUSE with `lookup_still_wins`. `frozen_acceptance`: lower 0.7999 vs 0.80; O/E [0.70, 0.7999] (no overlap), [0.70,
   0.80] and [1.25, 1.40] (inclusive overlap), NaN → False. `evaluate_gate` on a seeded synthetic window: clearly better
   challenger → PROMOTE; challenger == champion → REFUSE; constant challenger → REFUSE; the two bootstrap calls' first resamples
   equal (`cluster_resample_indices` with the same seed); wrong keys / out-of-range / single-class `y` → `ValueError`;
   `evaluate_frozen_v1` returns the three metrics and flags.
3. `tests/models/test_gate_run.py` (module fixture, one local MLflow store with **two experiments**: `frozen-test` trained by
   `train_and_evaluate` on gold A = `make_gold_frame(seed=1)` and `challenger-test` trained on gold B =
   `make_gold_frame(2400, seed=2)` with patient ids prefixed `b-` (disjoint from A; the fixture asserts its pre-registered split
   yields ≥ 400 test positives, so the real Phase A floor applies unmodified), both written with version-2 metadata, `reference_date="20261012"` and a
   `generation_summary` whose `provenance.prereg_commit` is a fake sha; challenger tags `git_dirty` and
   `mlflow.source.git.commit` overwritten with `MlflowClient.set_tag`; the LR runs of both exported with `export_bundle`; a
   champion file pointing at the `frozen-test` LR bundle, `frozen_experiment="frozen-test"`; the gate runs on gold B; a fake `run_git` answering `rev-parse`, `diff`, `log`, `show`, `fetch`, `branch -r --contains`;
   `prereg` = `parse_prereg(real Phase A text + an appended lock)`; `gold_opener = lambda uri: nullcontext(dir)`): happy path report
   keys and values; each step 1-7 failing with its message (code diff from the lock, run before the lock time, a changed Phase A value in the `git show` result, metadata reference_date, seed param, calibration param, tampered `split_assignment.csv`, dirty tag, other gold
   fingerprint, Python tag 3.12, a library tag mismatch, champion sha mismatch, champion run id, challenger bundle run id);
   `InconsistentVersionWarning` during load → `GateRunError`; reproduction mismatch (a monkeypatched logged metric) →
   `GateRunError`; negative control → REFUSE with `replaced_by`; rehearsal on `main_200k` refused; determinism (two runs, equal
   `gate`/`frozen_v1`); `check_prereg_against_code` raising (monkeypatched) → `GateRunError`; a run in another experiment
   carrying the gold's rowhash tag → `GateRunError`; the lock commit not on a remote branch (fake `run_git`) → `GateRunError`;
   `n_test_positive` below the floor (a third, small gold C = `make_gold_frame(300, seed=3)` with its own challenger runs) →
   `GateRunError`; frozen live reproduction mismatch (a monkeypatched logged
   metric) → `GateRunError`; a changed `deviations` list → `GateRunError`; a tampered `model.pkl` in either bundle →
   `GateRunError`; champion-bundle and frozen-LR predictions
   equal. Frozen-v1 reproduction: the champion bundle's predictions on gold A's test rows (same split settings) reproduce the
   `frozen-test` LR run's logged `test_roc_auc` within 1e-9.
4. `tests/models/test_run_promotion_gate_cli.py`: exit 0/3/2 with an injected `run_gate`; preconditions without a report
   (project, dirty tree, prereg mismatch, unparsable prereg); the error report shape; NaN → `null`; no overwrite of a main report;
   `gate_table.md` lines for a fixed result.
5. `tests/ops/test_prereg.py`, `tests/ops/test_prereg_append.py`: Phase A alone parses; + lock; + lock + phase_b; phase_b
   without lock, wrong order, extra key, bad hex, wrong rule names, a non-literal `frozen_v1_rows`/`negative_control` → refused; `lock` refuses a
   dirty tree, an existing lock, an existing experiment, a run on the locked gold, a P1b failure; `phase-b` refuses rehearsal, error, mismatched lock, non-REFUSE negative
   control; appended files keep the old bytes as a prefix; copied reports byte-identical.
6. `tests/scoring/test_bundle_guard.py` passes unchanged (imports still resolve); the scorer's no-mlflow subprocess test passes.
7. `tests/test_tooling.py`: the new targets, variables, and `gate` refusing without `RUN`.
8. `ruff check .`, full `pytest`.

Live: L1-L7. **6b is accepted when L5's main run has `error` null and a decision (exit 0 or 3), the negative control has
`decision == REFUSE`, and Phase B is committed.** Either main decision is a valid outcome (`outcomes.lookup_still_wins`).

## Out-of-scope follow-ups

- 6c: FR0.3 portable v1 export.
- Promotion if PROMOTE: bundle upload, `gold_fingerprint_definition` in `serving.*`, champion PR with `gate_table.md`.
- A stronger negative control (a shuffled-label model through the bundle path) if wanted later.
- Tuning, gating XGBoost, label v2; slice 7 monitoring reuses S1.

## Revision log

- r0 (2026-10-16): initial draft.
- r1 (2026-10-16): round 1 (comprehension passed; critic 15 gaps; readiness 11 questions). Owner decisions D5 (FR16 on all
  modeling rows) and D6 (constant negative control, documented as weak). Gate code locked too (HEAD diff vs `code_commit`), clean
  tree; training after the lock (`start_time` > lock commit time); run params vs prereg; Phase A prefix check; exit 3 for
  REFUSE; negative-control fields; library-version check (guard 8's rule) via moved `installed_versions`; reports committed
  under `notes/…/v2-slice6b-reports/`, no overwrite; pure `decide_gate` / `frozen_acceptance`; one-sided rule stated; S5 moves
  the regex too; `Champion.sha256`; redundant O/E exemption removed; `RuleOutcome.statistic` from `ci_level`; NaN → null;
  reproduction check of logged metrics; determinism test; error-report test; runbook preconditions (local mlflow store,
  champion copy, ADC identity, no untracked files); report/run_gate key split; `repo_root`; gold metadata checks; XGBoost run
  checks; fixture recipe; `phase_b` schema strictness; YAML emission; Make recipes; `gate_table.md` format; split check as
  per-partition sets.
- r2 (2026-10-17): round 2 (critic 5 gaps; readiness 9 questions). Runbook judged from report fields (make exits 2 on any
  failure); partial-training remedy; Phase A check compares parsed Phase A keys with `deviations` append-only (the byte-prefix
  check contradicted the deviation rule); bundles verified (I1-I4 for local directories) and the challenger LR and champion
  scored from the verified bundle bytes; the champion pin is the sha256 of the manifest bytes; fixture with separate frozen and
  challenger experiments on disjoint golds; exact statistic strings and `gate_table.md` lines; phase-b checks on the
  negative-control report; no-overwrite only for successful non-rehearsal reports, checked first; `lock_time_utc` format;
  `negative_control_decision` literal; Make recipes; docs section heading.
- r3 (2026-10-17): round 3 (critic 6 gaps; readiness 6 questions). No Phase A value or deviation may change after generation
  (owner's Step-0 rule), so `deviations_since_phase_a` is removed; P1b (`check_prereg_against_code`) in the gate and the lock;
  no other experiment may hold runs on `main_200k`'s gold (gate and lock); the lock commit must be on a remote branch; power
  floor checked and reported; live frozen-v1 reproduction on v1 gold (catches untagged library drift such as scipy); exact
  `split_summary` keys; `split_assignment.csv` from the verified challenger bundle; `Prereg.training`; tuple comparison of
  planned codes; leaf rules of the two new blocks; `parse_prereg` without file I/O plus a repository test for committed report
  hashes; table deleted on error, stderr only; rehearsal ignores an existing lock; `git_commit` = run tag, cross-checked with the
  manifest; citation fix.
- r4 (2026-10-17): round 4 (critic 4 gaps; readiness 8 questions); 3-revision cap reached, **owner override** ("override and
  proceed"); applied before coding: deleted runs/experiments and pagination in the gold-usage scan (`ViewType.ALL`), deleted runs
  allowed only in the locked experiment after the lock; fixture gold B sized for ≥ 400 test positives and a small gold C for the
  power failure; v1 gold precondition and recovery; `power.n_test` in the report; lock commit hash and fake-git commands;
  parsed-Phase-A test wording; `_read_run` seam for param tests; prereg's own constant tuples tied by a test; `prereg_bytes`
  dropped; citations; `EXPECTED_TARGETS`. The critic did not formally close on this revision.

## Implementation notes (2026-10-17, before the live run)

Design check: comprehension passed (round 1); critic gaps 15 → 5 → 6 → 4, readiness questions 11 → 9 → 6 → 8; the 3-revision cap
was reached and the **owner overrode** ("override and proceed"); r4 applied every round-4 item before coding. The critic never
closed in the same round as readiness. Deviations from the text above:
- **`gate_run.py` lives in `src/readmission_risk/ops/`, not `models/`**: it needs `ops.prereg` (P1b, Phase A comparison) and
  `ops.generation_vm.gold_prefix`, and `models/` never imports `ops/` (checked; `ops/population_checks.py` follows the same
  pattern). Its tests are `tests/ops/test_gate_run.py`; the two CLIs' tests are `tests/ops/test_promotion_gate_clis.py`.
- The appended blocks must come after every Phase A key and the lock before `phase_b`; Phase A's own key order is not
  additionally enforced (it equals `SPEC`'s order today; enforcing it could break existing tests that rebuild documents).
- The report's `frozen_v1` block nests the per-model results under `models` (`{rows, n_rows, n_positive, models: {...}}`).
- `verify_bundle_dir` also returns the verified `split_assignment.csv` bytes (the split check reads them).
- The gate-run tests run the two bootstrap evaluations with 50 resamples by wrapping them (the schema requires >= 1000 in the
  file); a third small gold C (300 patients, its own experiment) provides the power-floor failure.
- Sanity run on real artifacts (no `main_200k` access): both `readmission-risk-v1` runs pass guard 14 and guard 8's rules; the
  champion bundle verifies and is the v1 LR run; both frozen models reproduce their logged v1 test metrics on
  `data/gold/full_run_2b` (LR 0.8692, XGBoost 0.8225); champion-bundle and frozen-LR predictions are identical.

## Results (live run 2026-10-17)

**6b is accepted:** the main gate run produced a decision with `error` null, the negative control was refused, and Phase B is
committed (PR #19). **The pre-registered decision is REFUSE: the lookup still wins**; the champion is not replaced and no
promotion follows. Each step was confirmed by the owner immediately before it; interim log `data/gate/live-checks.md` (gitignored).

### L0-L2

- **L0 PASS** (v2 `1b3b44b`): prereg parses, P1b ok, no lock, no Phase B, `deviations: []`; `readmission-risk-v1` LR `fe9633ca…`
  and XGBoost `abe9c605…` (Python 3.14.7, library tags = installed); champion copy verifies and is the v1 LR run; v1 gold present;
  no rehearsal or main experiment; **no run in the store had used `main_200k`'s gold**; ADC ok; CI green.
- **L1 PASS (rehearsal, pipeline sanity only):** `rehearsal-6b-pilot20k` trained in 14 s (LR `d5afdeb4…`, XGBoost `01f59490…`),
  bundle `af67e25f…`; gate `--rehearsal` in 44 s with every verification step passing, `error` null, 2,000/2,000 resamples; the
  negative control refused. No pilot number was used for any choice.
- **L2 PASS:** `make prereg-lock` appended `analysis_lock` (`code_commit` `1b3b44b`), committed as `84b6f42` (PR #18, merged as
  `55dce06`). CI on #18 first failed because three new test modules assumed a Phase-A-only repository `prereg.yaml`; fixed in
  `83b34ee` with a `phase_a_text()` test helper (tests only; the locked code paths unchanged). Re-checked with real git after the
  merge: lock commit on `origin/v2`, block unchanged since it, no `prereg.yaml` change after it, HEAD code = `code_commit`, HEAD
  contains the lock commit, no run on `main_200k`'s gold.

### L3-L4

- **L3 PASS:** `make train TRAIN_EXPERIMENT=readmission-risk-v2-main200k` (gold from `gs://…/v2/main_200k`) in 47 s, exit 0. LR
  `a9f32f4b7c584f4c8fe992748410789f`, XGBoost `17b5c6042a8a41cf9121633acbeaec0f`: active, `git_dirty` clean, commit `55dce06`
  (contains the lock commit), rowhash `80edd13d…`, seed 42, test start 20230101, versions = installed; exactly these two runs on
  `main_200k`'s gold.
- **Process slip, disclosed:** the post-training error check (`grep Error`) also matched `expected_calibration_error` and printed
  the training log's test-window metric lines into the working session before the gate ran. No choice could depend on them:
  challenger, rules, FR16 rows, negative control and gate code were locked and merged before training, and the gate refuses any
  later change. Recorded here and in `docs/results.md`.
- **L4 PASS:** challenger bundle `569fdab6c17d433542b2cd6b531ed8efc5677fb37b00a792d84f79803c6a0f72` (local).

### L5: the gate on main_200k

Main run 12:58:34Z-13:05:30Z (6 min 56 s), negative control 13:05:44Z-13:12:08Z; both `error` null, every verification step
passing, 2,000/2,000 resamples, power 616 test positives (floor 400).

| Rule | Value | Threshold | Result |
|---|---|---|---|
| roc_auc_vs_champion (lower bound) | +0.0012 | > 0 | PASS |
| roc_auc_vs_reason_code_lookup (lower bound) | −0.0033 | > 0 | FAIL |
| brier_noninferior_vs_champion (upper bound) | +0.00027 | ≤ 0.00091 | PASS |

Decision **REFUSE**, `lookup_still_wins` true. Test-window points: challenger ROC-AUC 0.8719 / Brier 0.01824 / O/E 1.006; champion
0.8625 / 0.01814 / 1.111; lookup 0.8706 / 0.01839 / 0.961; challenger − champion ROC-AUC [0.0012, 0.0172], challenger − lookup
[−0.0033, 0.0060]. XGBoost (reported only): 0.8805 / 0.0185 / O/E 1.36. Frozen v1 on all 157,827 modeling rows (2,542 positives):
LR 0.905 [0.899, 0.911], O/E 1.03 [0.99, 1.07], accepted; XGBoost 0.855 [0.849, 0.862], O/E 1.09 [1.05, 1.14], accepted. Negative
control: REFUSE, all three rules failed (AUC lower bounds −0.376 / −0.385; Brier upper 0.0013 > 0.0009).

### L6-L7

- **L6 PASS:** `make prereg-phase-b` appended `phase_b` and copied both reports to `notes/eg-new-feature/v2-slice6b-reports/`
  (sha256 `70e046b2…` and `d045bf20…`, verified); commit `185a884`, PR #19, merged.
- **L7:** this section, the v2 section leading `docs/results.md`, CLAUDE.md.

### Findings and follow-ups

- The pre-registered expectation held: at 200k patients the retrained model catches up with the reason-code lookup but does not
  pass it; the champion's main weakness on the new population is calibration (O/E 1.11), not ranking.
- Not pursued (would need its own pre-registration): XGBoost as a challenger; promotion on calibration grounds.
- Still open: 6c (FR0.3 portable export); slice 7 (monitoring); V-post and L-post after v2 merges into `main`.
