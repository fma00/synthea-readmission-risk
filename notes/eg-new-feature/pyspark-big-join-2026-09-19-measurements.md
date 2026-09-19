# Empirical measurements backing the Big Join design (slice 2)

Durable record of the real measurements that grounded `notes/eg-new-feature/pyspark-big-join-2026-09-19.md`'s `years_of_history=10` and `lookback_years=1` decisions, per this repo's standing practice of keeping quantitative design justifications auditable (mirrors slice 1's `generation_summary.json`). All Synthea runs below used seed 42, reference date `20260916`, state Massachusetts, matching `data/raw/dry_run`/`data/raw/full_run`.

## 1. Size vs. `years_of_history`, at population=1,000 (10 selected tables: patients, encounters, conditions, medications, procedures, careplans, observations, payers, providers, organizations)

| `years_of_history` | Total size @ 1,000 patients | Extrapolated @ 10,000 |
|---|---|---|
| 5 | 110.4 MB | ~1.1–1.2 GB |
| 10 | 193.6 MB | ~1.9–2.1 GB |
| unlimited (sentinel `0`) | 521.2 MB | 5.53 GB (real measurement, not extrapolated) |

Per-table breakdown confirmed `observations.csv` drives most of the size sensitivity to history depth (19.9% of its unlimited size at 5 years, 37.6% at 10 years), while `patients`/`payers`/`providers`/`organizations` are flat (one row per entity, independent of history depth).

## 2. Per-calendar-year encounter/observation sparsity (existing full-history 10k-patient export)

- **0.0% of alive patients had zero recorded observations in any full calendar year from 1916 through 2025.**
- Encounters showed a 7–11% zero-rate even in the densest recent decade (2011–2025) — read as genuine signal (not every patient is hospitalized every year), not sparsity requiring a longer window.
- This supports `lookback_years=1`, matching the LACE index's 12-month convention.

## 3. `years_of_history` truncation semantics — does it undercount long-standing active conditions? (added during design-check revision, 2026-09-19)

The design's initial "~10% truncation dead zone" estimate (`lookback_years / years_of_history`) was derived assuming `years_of_history` applies a single global date cutoff to every row, including currently-active (ongoing) condition/medication/careplan records. A design-check critic pass raised a valid concern: if that assumption were correct, `active_condition_count`/`active_medication_count`/`active_careplan_count` — computed from `(STOP IS NULL OR STOP >= window_start)`, with **no lower bound on `START`** — could be severely undercounted for any patient whose long-standing chronic condition started before the truncation boundary, since ~59.5% of currently-active conditions in the existing full-history export have a `START` more than 10 years before the reference date.

**This was checked directly against real regenerated data** (`years_of_history=5` and `years_of_history=10` test runs, population=1,000, same seed/reference-date/state as above) rather than assumed either way:

| Table | Metric | yoh=5 | yoh=10 | unlimited |
|---|---|---|---|---|
| `conditions.csv` | active (STOP empty) | 10,615 | 10,615 | 10,612 |
| `conditions.csv` | resolved (STOP present) | 15,557 | 28,250 | 90,709 |
| `medications.csv` | active | 2,961 | 2,961 | 2,960 |
| `medications.csv` | resolved | 25,371 | 43,390 | 89,381 |
| `careplans.csv` | active | 1,887 | 1,887 | 1,887 |
| `careplans.csv` | resolved | 938 | 1,952 | 8,059 |

**Finding: active (ongoing) condition/medication/careplan counts are essentially invariant to `years_of_history`** — Synthea's exporter preserves a patient's full current "problem list" regardless of how far back it started, and only prunes *resolved/historical* (closed, `STOP`-present) records outside the retained window. The original ~10% dead-zone concern is real but applies only to the strictly-windowed count features (`prior_encounter_count`, `prior_inpatient_count`, `prior_emergency_count`, `procedure_count_window`, `observation_count_window`) — **not** to the `active_*` comorbidity features, which turn out to be effectively immune to this truncation setting. This is a stronger result than the original design doc claimed, not a weaker one.

**Scale check (added during round-2 design-check revision, 2026-09-19):** the above was originally checked only at population=1,000. A round-2 critic pass correctly flagged that this slice's actual target scale is population=10,000 (`data/raw/full_run`), a 10x difference, and the claim hadn't been verified there. Re-checked directly: a fresh `years_of_history=10` run at population=10,000 (same seed/reference-date/state) was compared against the existing `years_of_history=0`/unlimited `data/raw/full_run` (also population=10,000):

| Table | Metric | yoh=10 @ 10,000 | unlimited @ 10,000 |
|---|---|---|---|
| `conditions.csv` | active | 108,804 | 108,798 |
| `conditions.csv` | resolved | 302,126 | 957,838 |
| `medications.csv` | active | 30,427 | 30,411 |
| `medications.csv` | resolved | 525,823 | 998,956 |
| `careplans.csv` | active | 18,842 | 18,839 |
| `careplans.csv` | resolved | 19,485 | 83,329 |

The invariance holds at the real target scale: active counts differ by <0.1% (noise from a non-identical run, not systematic undercounting) while resolved counts scale sharply with `years_of_history`, exactly as at population=1,000. The finding above is no longer a 10x extrapolation — it's directly confirmed at 10,000 patients.
