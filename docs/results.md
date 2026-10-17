# Results and caveats

> Terminology: "slice N" names the build stages in the [README Status table](../README.md#status) (1 Synthea generation, 2 PySpark feature join, 2b unplanned label, 3 models, 4 Top-N CLI); "v2 population" is the planned scale-up cohort (see the README Roadmap).

## v2: the pre-registered promotion gate on a fresh 200,000-patient population (slice 6b)

**The reason-code lookup is at least as good as the models.** On `main_200k`, a 200,000-patient population generated with a new seed specifically for validation, a logistic regression retrained at that scale beats the v1 champion but **does not beat the no-model reason-code lookup**, so under the pre-registered rule the champion is not replaced. This is a reported finding, not a failure (`prereg.yaml`, `outcomes.lookup_still_wins`).

Every analysis choice was fixed before any model saw this population: the rules and their thresholds in `prereg.yaml` Phase A (committed before the population was generated), and the challenger (logistic regression), the rows for the external validation and the code in the `analysis_lock` block (committed before training). The gate refuses to run if any of them changed. Training set: 126,168 rows / 1,926 positives (1.53%); test window (2023-01-01 onward, patient-disjoint): 31,659 rows / **616 positives** (1.95%), above the pre-registered floor of 400. Intervals are 95% patient-clustered paired percentile bootstrap intervals (2,000 resamples, seed 20261012). The results are recorded line by line in `prereg.yaml` Phase B and in the committed gate reports ([notes/eg-new-feature/v2-slice6b-reports/](../notes/eg-new-feature/v2-slice6b-reports/)); design and runbook: [notes/eg-new-feature/v2-slice6b-promotion-gate-frozen-v1-2026-10-16.md](../notes/eg-new-feature/v2-slice6b-promotion-gate-frozen-v1-2026-10-16.md).

| Test window (31,659 rows, 616 positives) | ROC-AUC | Brier score | Observed / expected |
|---|---|---|---|
| Challenger: logistic regression trained on `main_200k` | 0.872 | 0.0182 | 1.01 |
| Champion: v1 logistic regression (trained on the 10,000-patient v1 population) | 0.863 | 0.0181 | 1.11 |
| Reason-code lookup (no model, fitted on the same training rows) | 0.871 | 0.0184 | 0.96 |

| Pre-registered rule | Paired 95% interval of the difference | Threshold | Result |
|---|---|---|---|
| ROC-AUC: challenger − champion | [+0.0012, +0.0172] | lower bound > 0 | pass |
| ROC-AUC: challenger − lookup | [−0.0033, +0.0060] | lower bound > 0 | **fail** |
| Brier: challenger − champion | upper bound +0.0003 | ≤ 0.05 × champion Brier = 0.0009 | pass |

- **The AUC rule is one-sided.** "Beats" means the lower bound of the interval of the difference is above 0. Against the champion it is (+0.0012); against the lookup the interval includes 0, so the challenger is not shown to rank better than the lookup. This is what the v1 results below anticipated: more data narrowed the interval of the logistic regression's ROC-AUC difference to the lookup from 0.067 wide (v1, [−0.036, +0.031]) to 0.009 (v2) and let the model catch up to the lookup, but not pass it.
- **Calibration differs more than ranking.** On this window the champion under-predicts by about 11% (O/E 1.11, interval of challenger − champion O/E [−0.121, −0.089]), the retrained challenger is close to 1 (1.01) and slightly better calibrated in Brier than the lookup (challenger − lookup Brier [−0.0003, −0.0001]). Calibration is not one of the promotion rules, so this does not change the decision.
- **The calibrated XGBoost was trained and reported but, by pre-registration, never gated** (point estimates on the same window: ROC-AUC 0.881, Brier 0.0185, O/E 1.36). Its higher ROC-AUC is not evidence for promotion: choosing between the two models after seeing these numbers is exactly the selection the lock prevents. It is at most a hypothesis for a separately pre-registered study.
- **A deliberately bad challenger is refused.** Replacing the challenger with a constant (the training prevalence) fails all three rules (AUC lower bounds around −0.38), so the gate does refuse an uninformative model.

**Frozen-v1 external validation** (the v1 models, unchanged, on all 157,827 modeling rows of `main_200k`, 2,542 positives; the v1 models never saw any of these patients). Pre-registered acceptance: ROC-AUC interval lower bound ≥ 0.80, and an O/E interval overlapping [0.80, 1.25].

| v1 model | ROC-AUC | Brier score | Observed / expected | Accepted |
|---|---|---|---|---|
| Logistic regression | 0.905 [0.899, 0.911] | 0.0135 [0.0129, 0.0140] | 1.03 [0.99, 1.07] | yes |
| Calibrated XGBoost | 0.855 [0.849, 0.862] | 0.0155 [0.0150, 0.0161] | 1.09 [1.05, 1.14] | yes |

These rows span all years, not only the test window, so these figures are not comparable with the test-window table above.

- **Process note.** After the lock and before the gate ran, the training log's test-window metrics were printed into the working session by mistake. Nothing that could have been influenced by them was still open: the challenger, rules, rows and code were locked and merged before training, and the gate refuses any later change.
- Like v1, this is synthetic data: a demonstration of the pipeline and the evaluation method, not a clinically validated result (see the README's "About the data").

### Portable export parity (FR0.3, slice 6c)

The two frozen v1 models were exported once more with a portable (non-pickle) form: JSON parameters plus XGBoost's own UBJ boosters, read by a small numpy predictor that never touches scikit-learn objects or pickle. On the 1,539 v1 test rows and 325 probe rows (every fitted category, an unseen value and `MISSING`), the portable form reproduces the cloudpickle's `predict_proba` with a **maximum absolute difference of 0** for both models: logistic regression (bundle `fb44c38910b4…`, from `75a1448729ef…`) and calibrated XGBoost (bundle `bf66d1cb272b…`, from `30009d198070…`). Each bundle stores its parity inputs and the pickle's outputs, so the check can be repeated after a library upgrade, when the pickle itself can no longer be loaded. That later check is bit-exact and covers those reference rows only; see [scoring.md](scoring.md#model-bundles) and the [design note](../notes/eg-new-feature/v2-slice6c-portable-v1-export-2026-10-17.md).

## v1: slice 3 on the slice-2b gold table (a development set)

Measured on `data/gold/full_run_2b` (10,000-patient Synthea population; **unplanned**-readmission label from slice 2b — see [notes/eg-new-feature/readmission-label-planned-exclusion-2026-09-20.md](../notes/eg-new-feature/readmission-label-planned-exclusion-2026-09-20.md); `--test-start-date 20230101`): 6,391 training rows / 96 positives (1.50%), 1,539 test rows / **41 positives (2.66%)**; 95% patient-clustered bootstrap intervals in brackets. Every figure here (and the recipe to re-derive it) is in [notes/eg-new-feature/readmission-label-planned-exclusion-2026-09-20-measurements.md](../notes/eg-new-feature/readmission-label-planned-exclusion-2026-09-20-measurements.md).

| Model | ROC-AUC | Brier score | Expected calibration error |
|---|---|---|---|
| Logistic regression | 0.869 [0.820, 0.919] | 0.0255 [0.0184, 0.0340] | 0.013 [0.008, 0.023] |
| Calibrated XGBoost | 0.823 [0.751, 0.884] | 0.0257 [0.0181, 0.0347] | 0.018 [0.013, 0.029] |
| Reason-code-only lookup (reference, no model; `readmission_risk.models.baselines.reason_code_lookup`) | 0.873 | 0.0252 | 0.012 |

- **The result is weak, and small-sample.** The test window holds 41 positives, below slice 3's own 50-positive reliability threshold, so `train_models.py` prints its "metrics and intervals are unreliable" warning on this run (expected; the threshold was deliberately not lowered) and the intervals are wide (LR's AUC interval spans 0.10). Brier skill scores against the training prevalence are 0.022 (LR) and 0.014 (XGBoost): barely better than always predicting the base rate.
- **Neither model beats the reason-code lookup.** Paired differences against the lookup: logistic regression — ROC-AUC [−0.036, +0.031], Brier [−0.0004, +0.0011], ECE [−0.002, +0.006], all including 0; calibrated XGBoost — ROC-AUC [−0.094, −0.011] and ECE [+0.002, +0.013] (both *worse*), Brier [−0.0009, +0.0020] (inconclusive). Between the two models, XGBoost is worse than logistic regression on ROC-AUC (paired −0.047 [−0.092, −0.010]) and on ECE (+0.0048 [0.0018, 0.0091]); Brier is inconclusive (+0.0002 [−0.0009, +0.0013]).
- **These are not virgin-holdout numbers.** During design, throwaway prototypes scored these models on exactly this test window, and the cutoff date, the feature exclusions, the training-row choice and (now) the label rule were all decided after looking at this data. Read them as development-set-quality estimates. The planned untouched evaluation is the larger v2 population with a new seed; at a ~1.6% positive rate it must be sized for statistical power, not just freshness.
- **What to expect from the larger (100k–200k patients) v2 population — two different things.** *Measurement precision will improve for sure:* 10–20× the patients means roughly 1,000–2,000 training and 400–800 test positives at the current rate (arithmetic, not a measurement), which removes the 50-positive reliability warning and narrows the intervals about 3–4×, so the models can finally be told apart from the lookup. *Model performance is expected to improve only up to a ceiling set by the admission reason.* In a learning-curve check on the current data the lookup is already saturated (test ROC-AUC 0.863 → 0.871 → 0.873 at 25/50/100% of the training data), while logistic regression (0.764 → 0.837 → 0.869) and XGBoost (0.692 → 0.753 → 0.808) are still climbing because they must learn the reason table from very few positives; expect them to catch up to the lookup and XGBoost's deficit to shrink. Beyond that, within CABG-history and CHF no patient feature separates readmitted from non-readmitted stays (per-feature ROC-AUC 0.44–0.46 and 0.56–0.58), consistent with Synthea generating these events from scripted disease-module pathways plus random draws, so more patients should not add signal the simulator never encoded. Expect all three approaches to land around 0.87–0.90 with the models at or slightly above the lookup, not far above it (an expectation, not a measurement; the aortic-valve reasons look informative within reason but on only 68 and 27 rows and probably reflect care-pathway stage). Recipe: the measurements note.
- **The positive rate shifts between partitions (1.50% train vs 2.66% test)**, and both models under-predict on the test window (calibration gap −0.010 LR, −0.012 XGBoost: mean prediction ≈ 1.7% against 2.66% observed). That is what a prevalence shift looks like; it should not be read as a model defect on its own.
- **The label is much cleaner than before but still reason-concentrated.** Slice 2b removed the scripted lung-cancer chemotherapy cycles (in the pre-2b all-cause label they were 72.6% of the positives, with the same reason on the readmitting stay in 98.5% / 99.8% of cases arriving 28.8 ± 1.9 / 24.5 ± 2.1 days after discharge) and the overlapping-stay fragments of surgical episodes. What remains, 151 positives: CABG-history 45, CHF 30, aortic stenosis 20, aortic regurgitation 17, and so on; in the **test window CABG-history alone is 23 of 41 (56%)** — same-reason repeats a median 16.6 days after discharge. On inspection they are fixed 24-hour post-operative stays that carry a generic procedure bundle (discharge, post-op education, stair-climbing and walking tests) but no chemotherapy/radiation code, and only 1 of the 45 readmitting stays begins through the emergency department (versus 27 of 30 for CHF), so they look like a scripted pathway rather than deterioration; the rule has no planned-procedure marker with which to exclude them. That is why a reason-code lookup is still hard to beat. Whether to iterate the label further is an open decision: the design note's "Label v2" section measures four candidate stricter definitions, and at this cohort size none leaves enough test positives to evaluate (at most 18, mostly 9–12), so adoption is deferred to the larger population.
- **The reference row is a point estimate, reproducible by hand** (it is not a tracked MLflow run and has no interval of its own; the paired intervals above come from the recipe at the end of this snippet, run once per model pair, a manual real-data check rather than a CI test):
  ```python
  from datetime import date; from pathlib import Path
  from readmission_risk.models.baselines import reason_code_lookup
  from readmission_risk.models.data import load_gold_table
  from readmission_risk.models.evaluate import evaluate_probabilities
  from readmission_risk.models.split import chronological_group_split
  sp = chronological_group_split(load_gold_table(Path("data/gold/full_run_2b")), reference_date=date(2026, 9, 16), test_start_date=date(2023, 1, 1))
  y_tr, y_te = sp.train["is_readmitted"].to_numpy(), sp.test["is_readmitted"].to_numpy()
  p = reason_code_lookup(sp.train["admission_reason_code"], y_tr, sp.test["admission_reason_code"])
  print(evaluate_probabilities(y_te, p, train_prevalence=float(y_tr.mean())).metrics)  # roc_auc 0.8726, brier_score 0.0252

  # paired model-vs-lookup intervals: reload a logged model (run_id is printed by scripts/train_models.py); clustered_bootstrap
  # yields paired differences only for EXACTLY two models, so run it once per pair
  from readmission_risk.models.data import prepare_features
  from readmission_risk.models.evaluate import clustered_bootstrap
  from readmission_risk.models.tracking import load_logged_model
  p_lr = load_logged_model(Path("mlflow"), "<logistic_regression run_id>").predict_proba(prepare_features(sp.test))[:, 1]
  r = clustered_bootstrap(y_te, {"lookup": p, "logistic_regression": p_lr}, sp.test["patient_id"].to_numpy(), n_bootstrap=500, seed=42)
  print(r.paired_differences["logistic_regression_minus_lookup"])  # roc_auc (-0.036, 0.031), brier_score (-0.0004, 0.0011), ...
  ```
- **Training-window sensitivity (`--train-start-date 20170916`, run under its own experiment `readmission-risk-2b-sensitivity`; trained on Python 3.12.11 on the gold table before it gained `label_horizon`, whose rows, labels and features are identical, and not rerun) rests on only 24 training positives.** It drops 4,715 of the 6,391 default training rows (74%) that predate 2017-09-16, leaving 1,676 rows / 24 positives (grouped calibration folds hold 8, 3, 7, 2 and 4 positives), against the same 41 test positives. Their positive rates before and after the cutoff are similar (72 of 4,715 = 1.53% versus 24 of 1,676 = 1.43%). Logistic regression: ROC-AUC 0.860 [0.805, 0.907], Brier 0.0246; XGBoost: ROC-AUC 0.778 [0.700, 0.844], Brier 0.0255; XGBoost is worse on ROC-AUC (paired −0.082 [−0.144, −0.037]) and ECE (+0.0068 [0.0011, 0.0095]), Brier inconclusive. Given 24 positives, treat this as a direction, not an estimate.
- **The label/test leakage margin is enforced, not just measured (since 2026-10-10).** The label depends on the *content* of the readmitting stay (its planned-procedure records, which can be dated after the stay ends), on its end time and on earlier stays' end times, not only on when it started. The feature join therefore records, per row, `label_horizon`: the latest timestamp of any record the label depends on. The split drops every training row whose `label_horizon` is on or after the test start, reports the count (`n_dropped_label_horizon_purge`) and warns when it is non-zero. So every training row's *label* is determined by records dated before the test start. On this data the purge drops 0 rows at 2023-01-01 and every split count is unchanged; the closest classifying record is 2022-12-25T01:59:59Z, about 6.9 days before the test start. What is *not* claimed: that the training set's composition is independent of later data. The purge itself reads later dates, and whether a stay is an index row at all (cohort membership) is still only measured. Two paths, both 0 on this data: 0 of 470 candidates dropped by the terminal rule because of a stay ending on or after the test start, and 0 stays excluded as planned only because of a procedure dated on or after it. Making membership provably independent is a named follow-up. Design and measurements: [notes/eg-new-feature/label-horizon-purge-2026-10-09.md](../notes/eg-new-feature/label-horizon-purge-2026-10-09.md).
- **Provenance is enforced.** The gold table ships a `_gold_metadata.json`; training refuses a table without it (e.g. one built before slice 2b) or one whose `reference_date` / `readmission_window_days` differ from the training config. Treat model performance as a demonstration of the pipeline and methodology, not a clinically validated result.

## Reliability figure

The README's reliability diagram plots both models' calibration curves on the held-out test window (10 quantile bins; 1,539 rows, 41 positives) with 95% Wilson intervals. The curves are the `calibration_curve.json` artifacts already logged by `train_models.py`; nothing is retrained. The intervals treat rows as independent, whereas patients contribute several stays, so they understate the uncertainty (the bootstrap intervals above are patient-clustered). Eight of the ten logistic-regression bins and nine of the ten XGBoost bins have mean predicted probability at or below 1.5%, so on linear axes they crowd the lower-left corner. The figure is a development-set result, like the rest of this page.

```sh
python scripts/make_readme_assets.py --experiment-name readmission-risk-v1 --predictions data/predictions/run_date=20250630/predictions.parquet
```

Regenerating the `predictions.parquet` it reads requires scoring (see [scoring.md](scoring.md)); the `readmission-risk-v1` runs were trained on Python 3.14.7, so both steps run in the default environment.

The command writes `docs/img/reliability_light.svg`, `docs/img/reliability_dark.svg` and `docs/img/sample_topn.md`, plus low-resolution PNG previews in the gitignored `docs/img/preview/`. It refuses curves that do not sum to the run's test rows and positives, runs that do not share the same test encounters, and a predictions file from a different run. Design: [notes/eg-new-feature/readme-reliability-plot-and-sample-table-2026-10-09.md](../notes/eg-new-feature/readme-reliability-plot-and-sample-table-2026-10-09.md).
