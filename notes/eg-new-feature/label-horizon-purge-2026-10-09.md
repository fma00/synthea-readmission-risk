# Label-horizon purge: enforce the ~6-day label/test leakage margin — design, 2026-10-09

Feature request (via `/eg-new-feature`, v1 closeout item 5 in
`notes/eg-brainstorms/v2-scope-and-v1-closeout-2026-10-09.md`; PRD `notes/prds/v2-mlops-keyless-scheduled-scale-up-2026-10-09.md`
FR0.1): "Harden the ~6-day leakage margin into an assertion that fails the run, because a new seed and `reference_date`
no longer guarantee the margin, and v2 is a one-shot holdout that can't be re-run cleanly afterwards."

Owner decisions taken in Step 0 (2026-10-09):
1. **Purge + invariant, not fail-only.** The split drops every training row whose label horizon (defined below) is on or
   after `test_start_date`, counts the drops in the split summary, and then checks a post-condition that raises
   `RuntimeError` if any training row still violates the rule. That check fires only on a bug, never on data. The owner
   chose this over a pure hard failure because measurement (below) projects a ~20–40% chance that a hard failure would
   fire at v2's pre-registered cutoff, which would force a deviation from the pre-registration.
2. **The horizon is a new gold column, and the v1 artifacts are rebuilt.** The Big Join computes a per-row `label_horizon`;
   training refuses a gold table without it. The local v1 gold table is rebuilt, both models are retrained (expected
   identical, verified), and the README assets / scoring-doc run id are refreshed.

## Why

- **Where it came from.** Slice 2b (`notes/eg-new-feature/readmission-label-planned-exclusion-2026-09-20.md`, Data
  handling, "New label-side property") made the label depend on records that slice 3's label-window purge does not cover:
  - the readmitting stay's *content* (`is_planned` = any procedure with a code in `PLANNED_PROCEDURE_CODES` attached to it);
  - its *end* time;
  - the `STOP`s of the patient's earlier stays (the continuation rule).

  The purge (`src/readmission_risk/models/split.py:136`, `index_stop + W < T`) guarantees only that a readmitting stay
  **starts** before the test period.
- **What was measured instead of enforced.** On the 10k data, 0 of 118 at-risk training rows violate, with the latest
  classifying record at 2022-12-25T01:59:59Z (≈ 6.9 days before `test_start` 2023-01-01). The split docstring
  (`split.py:72-78`), `docs/results.md:41`, `README.md:96` and CLAUDE.md caveat (4) all say this is "measured, not
  guaranteed".
- **Why v2 needs a guarantee.** v2 uses a new seed and `reference_date` and a one-shot, pre-registered holdout
  (brainstorm "Pre-register before generating v2"; PRD FR0.1 and risk row "Leakage via the ~6-day label/test margin at
  200k"). The property must hold by construction, not by luck, because a contaminated v2 result cannot be re-run cleanly.

### Measurements taken in this design session (read-only, 10k data: `data/raw/full_run`, `data/gold/full_run_2b`)

- **Reproduces the known figure.** With the horizon defined below, at `test_start` 2023-01-01: 118 train rows have an
  in-window inpatient stay, 0 violate, and the latest classifying record is 2022-12-25T01:59:59Z (margin 6.92 days).
- **Cutoff scan.** 40 quarterly cutoffs (Jan/Apr/Jul/Oct 1st, 2016–2025), each run through the current split:
  **1 of 40 cutoffs has a violating train row** (2025-07-01: one row, its readmitting stay ends 5.5 days after the
  cutoff). The result is the same for three horizon variants (stay `STOP` only; plus `prev_max_stop` and planned-procedure
  dates; plus any-procedure dates).
- **Projection for v2, not a measurement.** At 100k–200k patients there are ≈10–20× more stays straddling any cutoff, so
  P(≥1 violation at a given cutoff) ≈ 1 − exp(−0.025·k) ≈ 22% (k=10) to 39% (k=20). That is why a purge is needed and a
  hard failure is not enough.
- **Procedures can be dated after their stay ends.** 29 inpatient procedure rows start after their encounter's `STOP`, by
  up to 8,283 hours (≈ 345 days). All 29 are code `410006001` (not a planned code), so the label is unaffected on this
  data. It still means a stay's `STOP` alone is **not** a safe horizon for its content: the planned-procedure dates must be
  part of the horizon.
- **Data shape.** 13,565 inpatient stays, 0 null `STOP`, longest stay 80.16 days. 0 null procedure `STOP`s on inpatient
  stays. **175 of the 9,284 gold rows** (whole table, any partition) have at least one in-window inpatient stay; 118 of
  them are train rows at `test_start` 2023-01-01.

The scan recipe is written out in full under "Independent pandas recipe" (Verification criteria), which R3–R5 use.

## Scope

**In:**
1. A new gold column `label_horizon` (UTC timestamp, non-null) computed by the Big Join: the latest timestamp of any
   record the row's label depends on (exact definition in Interfaces).
2. In `chronological_group_split`:
   - input validation of `label_horizon`;
   - a new purge step **4b**, which drops pre-period rows with `label_horizon >= T` that survived step 4 and counts them;
   - a new post-condition (`RuntimeError`);
   - a `UserWarning` whenever step 4b drops ≥ 1 row;
   - a new summary key.
3. The gold schema contract in `models/data.py`:
   - the column is classified as excluded (never a feature), required non-null and normalized to UTC;
   - an old gold table without the column is refused with a specific rebuild message;
   - `gold_fingerprint` covers the column automatically.
4. Scorer hygiene. `label_horizon` is label-side information, so the scorer drops it alongside the label before scoring,
   and `ranking._require_label_free` refuses it structurally.
5. Test-helper updates so every synthetic gold frame carries the column without changing any existing seeded draw or
   split count.
6. **Phase B (see Sequencing):** the v1 artifact rebuild, a manual procedure in Verification criteria R1–R9:
   - rebuild the gold table, verified identical apart from the new column;
   - retrain under Python 3.14.7 into a new experiment, with identical split assignment as the gate and metric
     differences attributed and reported;
   - re-score, regenerate the README assets, and refresh the docs.
7. **Phase B:** doc updates per the inventory below.

**Out (explicitly):**
- **The cohort-membership property** (slice-2b doc, "A second, related property"). Whether a stay is an index row at all
  depends, through the terminal rule, on the `STOP`s of overlapping stays, which can fall after `T`. A per-row gold
  column cannot cover rows that are *absent* from the gold table, and it is not a "label-classifying record of a training
  row" (PRD FR0.1 wording). Measured 0 affected of 470 candidates on the 10k data. **A second membership path** (found in
  the round-5 design check): an index candidate must not be planned (`big_join.py:181-182`), and `is_planned(c)` reads
  every planned-code procedure attached to `c` whatever its date, so a stay that ended before `T` but whose only planned
  record is dated on or after `T` is silently absent from train. Measured 2026-10-10 on the 10k data: 0 of 3,320 planned
  stays have a planned procedure dated after the stay's `STOP`, and 0 stays at either 2023-01-01 or 2025-07-01 owe their
  planned flag only to records dated `>= T`. Both paths together stay a named follow-up.
- Changing the label definition (`LABEL_DEFINITION` is NOT bumped: no rule that decides `is_readmitted` or index
  membership changes; only a derived column is added).
- Changing `_gold_metadata.json` (no new field; `GOLD_METADATA_VERSION` stays 1). The missing-column refusal is enough
  (see Failure modes).
- Changes to `models/training.py`, `models/tracking.py` or `scoring/runs.py` (none needed; see Sequencing for the
  parallel session). The new summary key reaches MLflow automatically through the existing
  `params.update({f"split_{k}": v ...})` (`training.py:257`) and `split_summary.json` (`training.py:292`).

## Surfaces touched

| Layer | File | Change |
|---|---|---|
| Data (Spark) | `src/readmission_risk/pipeline/big_join.py` | New public pure function `compute_label_horizons`; `build_index_encounters` (lines 171–236) joins its output and adds `label_horizon` to its closing `.select`; `GOLD_TABLE_COLUMNS` (lines 68–92) gains `"label_horizon"` right after `"is_readmitted"`; `join_patient_demographics`' closing `.select` (≈ line 363, after `F.col("is_readmitted")`) gains `F.col("label_horizon")`. `flag_inpatient_stays` is unchanged. |
| Gold contract | `src/readmission_risk/models/data.py` | New constants `LABEL_HORIZON_COLUMN = "label_horizon"` and `LABEL_SIDE_COLUMNS = (LABEL_COLUMN, LABEL_HORIZON_COLUMN)`; `EXPECTED_GOLD_COLUMNS` gains `"label_horizon"` after `"is_readmitted"` (24 columns; the "(23 columns)" comment and `gold_fingerprint` docstring updated to 24); `EXCLUDED_COLUMN_REASONS` (line 77) gains an entry; `_REQUIRED_NONNULL_COLUMNS` (line 96) gains it; the timestamp normalisation loop (line 186) covers it; the schema check (line 149) gets a dedicated message when `label_horizon` is the only missing column. |
| Split | `src/readmission_risk/models/split.py` | Validation, step 4b, post-condition, warning, summary key, docstring step 4 paragraph rewritten. |
| Scoring | `src/readmission_risk/scoring/ranking.py` (`_require_label_free`, lines 57–61, and the `score_batch` docstring), `src/readmission_risk/scoring/pipeline.py` (line 100) | Refuse / drop `LABEL_SIDE_COLUMNS` instead of only `LABEL_COLUMN`. |
| Tests | `tests/pipeline/test_big_join.py`, `tests/models/helpers.py`, `tests/models/test_split.py`, `tests/models/test_data.py`, `tests/models/test_cli.py`, `tests/models/test_training.py` (one line), `tests/scoring/test_ranking.py`, `tests/scoring/test_pipeline.py` | See Verification criteria. |
| Docs and help strings (phase B only) | `README.md`, `docs/results.md`, `docs/scoring.md`, `docs/setup.md`, CLAUDE.md, `scripts/score_discharges.py:46`, `scripts/make_readme_assets.py:28`, `src/readmission_risk/scoring/pipeline.py:34` (comment), slice-2b design doc follow-up line, this note | See Doc-update inventory. |
| Artifacts (gitignored, phase B) | `data/gold/full_run_2b` (rebuilt), MLflow experiment `readmission-risk-v1` (new), `data/predictions/run_date=20250630/` (rewritten); tracked: `docs/img/*` regenerated (only the SVG `dc:description` is expected to change) | Manual R1–R9. |

## Interfaces

### `label_horizon`: definition

Notation for one gold row `c`: `W` = `readmission_window_days`; `deadline(c) = c.STOP + W days` (the existing
`readmit_deadline`); `R_end` = `reference_date + 1 day` at 00:00 UTC (the existing exclusive observation end).

An **in-window stay** of `c` is any inpatient stay `a` (from `flag_inpatient_stays`' output — ALL inpatient stays,
whatever their `is_planned` / `is_continuation` / `is_terminal` flags, because those flags are exactly what is being
classified) of the same patient, with `a.Id != c.Id` and `c.STOP < a.START <= deadline(c)` (the label window's own
bounds: strict lower, inclusive upper, as in `build_index_encounters`).

For each inpatient stay `a`:

```
stay_horizon(a) = greatest(
    a.START,
    coalesce(a.STOP, R_end),          # an open (null-STOP) stay is treated as lasting to the end of observation
    a.prev_max_stop,                   # flag_inpatient_stays' column; null if no earlier stay -> ignored by greatest
    planned_record_max(a),             # null if a has no planned-code procedure -> ignored by greatest
)
planned_record_max(a) = max over procedures p with p.ENCOUNTER == a.Id and p.CODE in PLANNED_PROCEDURE_CODES
                        of coalesce(greatest(p.START, p.STOP), R_end)
                        # greatest ignores a null STOP; a record with BOTH dates null (or unparseable -> null under the
                        # CSV reader's PERMISSIVE mode) still makes `a` planned, so it is treated like an open stay: R_end
```

Then `label_horizon(c) = greatest(deadline(c), max over in-window stays a of stay_horizon(a))`. With no in-window stay,
the max is null and `label_horizon(c) = deadline(c)` exactly.

Spark `greatest` skips nulls and returns null only if all arguments are null. `deadline(c)` is non-null because
`build_index_encounters` filters candidates to `STOP IS NOT NULL` before computing `readmit_deadline`, so
`label_horizon` is never null. This does not rely on schema nullability, which Spark's CSV reader does not enforce.
A null `a.START` would only make `a` fail the in-window join condition.

**Why the conservative max, not a tighter rule (a decision, recorded because v2's pre-registration will quote the
purge count).** The label is an OR over qualifying in-window stays. A positive whose first qualifying readmit
`a1` has `stay_horizon(a1) < T` is fully determined before `T`, even if some *other* in-window stay (for example a
planned one) straddles `T`. A tighter, still-sound rule exists:
- for positives, `max(deadline, min over qualifying stays a of stay_horizon(a))`;
- for negatives, the max over all in-window stays.

It would purge fewer positives near the boundary.

It is **not** adopted because it couples the horizon to the label rules: "qualifying" means re-implementing the
planned/continuation decisions inside the horizon, so any label change could silently break the horizon. The simple
max needs no knowledge of the flags; test 11 pins that independence. On v1 the two rules give the same result
(0 purged at 2023-01-01). The tighter rule is a named follow-up, to adopt if v2's purge count turns out material.

**Why each term (and why nothing else):**
- `deadline(c)`: the label window's close. This subsumes step 4's rule, so `label_horizon < T` implies `index_stop + W < T`.
- `a.START`: always `<= deadline(c)`, so it never raises the max. It is kept so the formula reads as "every record of
  `a`" and so a future change to the window bounds cannot silently drop it.
- `a.STOP` (null → `R_end`): the stay's content (its procedures) accrues until it ends. A stay still open at export
  straddles every possible `T < reference_date`, so it must always purge.
- `planned_record_max(a)`: `is_planned(a)` reads every planned-code procedure attached to `a`, regardless of date, and
  procedures can be dated after the stay's `STOP` (measured: up to ≈ 345 days, non-planned codes only on this data).
  Non-planned procedure codes do not affect any label rule, so they are excluded; a test pins this. A planned record
  with no usable date (both `START` and `STOP` null, e.g. an unparseable timestamp, since Spark's CSV reader does not
  enforce the schema's non-null `START` and PERMISSIVE mode nulls a bad value) still makes the stay planned, so its
  date is unknowable and it contributes `R_end`: the row is always purged rather than silently trusted.
- `a.prev_max_stop`: `is_continuation(a)` compares `a.START` with the maximum `STOP` over the patient's earlier stays. If
  the records are truncated at `T`, an earlier stay ending at or after `T` looks open (null), contributes nothing under
  `flag_inpatient_stays`' null semantics, and could flip `a` from continuation to counting.
  *For rows that pass the current terminal rule this term is provably redundant:*
  - an earlier stay `e` with `e.STOP > c.STOP` either starts `<= c.STOP`, which makes `c` non-terminal (not a gold
    row), or starts `> c.STOP`;
  - in the second case `e` is itself an in-window stay, because `e.START <= a.START <= deadline(c)`, so its `STOP`
    is already in the max.

  It is kept anyway, at zero cost, so the horizon stays correct if the terminal rule ever changes. It is unit-tested by
  calling `compute_label_horizons` directly with a candidate that the terminal rule would have excluded.
- **Not included**, each with a reason:
  - `c`'s own procedures: `c` is not planned, so it has no planned-code procedure at any date.
  - `c`'s own `STOP`: it is `< deadline(c)`.
  - The death rule: a train negative is kept iff `DEATHDATE` is null or `> deadline(c)`, which is knowable at
    `deadline(c) < T`; truncating at `T` gives the same keep decision.
  - The censoring rule: it compares `deadline(c)` with the fixed `R_end`.
  - The terminal rule: it governs membership, not the label (see Scope > Out).

### `compute_label_horizons` (new, `src/readmission_risk/pipeline/big_join.py`, placed after `flag_inpatient_stays`)

```python
def compute_label_horizons(
    candidates: DataFrame,   # needs columns Id, PATIENT, STOP (non-null), readmit_deadline (timestamp)
    flagged: DataFrame,      # flag_inpatient_stays' output (needs Id, PATIENT, START, STOP, prev_max_stop)
    procedures: DataFrame,   # needs ENCOUNTER, CODE, START, STOP
    reference_date: date,
) -> DataFrame:
    """Pure. One row per candidate: (Id, label_horizon). <docstring = the definition above, condensed>"""
```

- `R_end` literal: `F.to_timestamp(F.lit((reference_date + timedelta(days=1)).isoformat()))`. The session timezone is UTC
  (`spark_session.py:26`), so this is 00:00Z.
- **Computation, in this order:**
  1. `planned_record_max` per encounter (filter on `PLANNED_PROCEDURE_CODES`, `groupBy("ENCOUNTER")`,
     `max(coalesce(greatest(START, STOP), R_end))`).
  2. `stay_horizon` on `flagged`, via a left join on `Id == ENCOUNTER`.
  3. A left join of `candidates` (alias `c`) to the stays (alias `a`) on
     `c.PATIENT == a.PATIENT & c.Id != a.Id & a.START > c.STOP & a.START <= c.readmit_deadline`, followed by
     `groupBy(c.Id)`, `max(a.stay_horizon)`.

     *The left join (not a semi join) keeps every candidate, so the output has exactly one row per candidate.* The
     `groupBy` key is `c.Id`, which is unique among candidates because they are a filter of `flagged` (one row per
     stay).
  4. `label_horizon = greatest(c.readmit_deadline, max_stay_horizon)`.
- **Join shapes, specified so self-join ambiguity cannot bite.** `candidates` and `flagged` share lineage, so the
  aliasing follows `build_index_encounters`' existing `c`/`a` pattern. The aggregation is
  `.groupBy(F.col("c.Id").alias("_hz_id")).agg(F.max("a.stay_horizon").alias("_hz_max_stay"), F.first("c.readmit_deadline").alias("_hz_deadline"))`,
  and the function's **final projection is exactly**
  `.select(F.col("_hz_id").alias("Id"), F.greatest("_hz_deadline", "_hz_max_stay").alias("label_horizon"))`. That
  gives two freshly named columns with no `c`/`a`-qualified attribute in the output. `F.first` is safe because
  `readmit_deadline` is constant per `c.Id`.
- **Join-back in `build_index_encounters`.** It renames `Id → _horizon_id`, left-joins on
  `F.col("c.Id") == F.col("_horizon_id")`, and drops `_horizon_id`. The existing later references `F.col("c.PATIENT")`,
  `F.col("c.Id")` and `F.col("c.readmit_deadline")` (≈ lines 211–235) therefore still resolve to `labeled`'s `c`.
  Tests 13 and 13b exercise that path end to end.

### `build_index_encounters` (changed)

- After `candidates` is built (line ≈ 183), it computes `horizons = compute_label_horizons(candidates, flagged,
  procedures, reference_date)`.
- `labeled` gains `label_horizon` through a left join of `horizons` on `c.Id`, renamed to avoid ambiguity
  (`_horizon_id`, dropped after the join). This happens **before** the death/censoring filter, so the filter is
  unaffected.
- The closing `.select` gains `F.col("label_horizon")` after `F.col("is_readmitted")`.
- The docstring gains one sentence pointing at `compute_label_horizons`.

**Downstream.** `compute_lookback_features` and `join_dimension_attributes` carry extra index columns through
unchanged; the implementer verifies this, and the end-to-end test will catch it. `join_patient_demographics`' closing
select is in `GOLD_TABLE_COLUMNS` order and must list the new column.

**Procedures input.** `procedures` passed to `build_index_encounters` must now have `START`/`STOP`. The production
`PROCEDURES_SCHEMA` already has both (`schemas.py:105-108`: `START` timestamp non-null, `STOP` timestamp nullable). The
test-only `PROCEDURES_TEST_SCHEMA` (`tests/pipeline/test_big_join.py`, ≈ line 82) gains
`StructField("START", TimestampType(), True)` and `StructField("STOP", TimestampType(), True)` after `CODE`. Its `_procs`
helper pads 2-tuples `(encounter, code)` to `(encounter, code, None, None)` and passes 4-tuples through unchanged, so
every existing test is untouched.

### Gold schema (`GOLD_TABLE_COLUMNS` / `EXPECTED_GOLD_COLUMNS`)

The new column goes in position 6 (0-based 5), right after `"is_readmitted"`. Spark type `TimestampType`; post-load
pandas dtype `datetime64[*, UTC]` (normalized with `pd.to_datetime(..., utc=True)` exactly like `index_start` /
`index_stop`). `test_expected_gold_columns_match_big_join` keeps both tuples in sync.

`EXCLUDED_COLUMN_REASONS["label_horizon"] = "label-side, post-index information (latest record the label depends on);
used only by the split's label-horizon purge -- never a feature"`.

### `chronological_group_split` (changed)

The signature is unchanged. The new behaviour, in execution order:

- **Validation** (after the existing `_require_tz_aware` calls, before step 1), all `ValueError`:
  1. `label_horizon` column absent →
     `"chronological_group_split input has no 'label_horizon' column (gold tables built before 2026-10-09 lack it; rebuild with scripts/run_big_join.py)"`.
  2. `_require_tz_aware(df, "label_horizon")`.
  3. Any null → `f"label_horizon has {n} null value(s)"`.
  4. Any row with `label_horizon < index_stop + W` →
     `f"{n} row(s) have label_horizon before index_stop + {W} days: the gold table was built with a different readmission window, or is malformed"`.

     This makes "4b subsumes step 4" an invariant rather than an assumption.
- **Step 4b**, right after step 4 and before step 5:
  ```python
  horizon_known = pre["label_horizon"] < test_start
  n_dropped_label_horizon_purge = int((~horizon_known).sum())
  pre = pre.loc[horizon_known]
  ```
  Strictly `<`, matching step 4: a record dated exactly at `T` is a test-period record.
- **Warning**, emitted after step 4b if `n_dropped_label_horizon_purge > 0`, as
  `warnings.warn(..., UserWarning, stacklevel=2)`. Message:
  `f"The label-horizon purge dropped {n} training row(s) whose label depends on a record dated on or after test_start_date ({test_start_date.isoformat()}); report this count with the results"`.
- **Post-condition**, added after the existing four (lines 152–159):
  ```python
  if not (train["label_horizon"] < test_start).all():
      raise RuntimeError("split invariant violated: a train row's label horizon reaches into the test period")
  ```
- **Summary**: new key `"n_dropped_label_horizon_purge": int(...)`, inserted immediately after
  `"n_dropped_label_window_purge"`. The accounting identity becomes
  `n_input == n_dropped_censor_buffer + n_test + n_dropped_before_train_start + n_dropped_label_window_purge + n_dropped_label_horizon_purge + n_dropped_patient_overlap + n_train`.
  The four-step docstring is updated accordingly: step 4's "Since slice 2b two more things depend on records…" paragraph
  is replaced by a step 4b description stating what is now guaranteed and what is not (cohort membership).
- **Test rows**: `label_horizon` is not filtered on the test partition. Test labels may legitimately depend on any later
  record; the censoring buffer already handles observability.
- **MLflow**: the new key appears as param `split_n_dropped_label_horizon_purge` and inside `split_summary.json`, with no
  training-code change.

### Scorer

- `ranking._require_label_free(frame, caller)` raises `ValueError` if **any** of `LABEL_SIDE_COLUMNS` is in
  `frame.columns`. The message names the offending column(s):
  `f"{caller} must not be handed label-side column(s) {present}: the scorer never sees the observed outcome"`, where
  `present = [c for c in LABEL_SIDE_COLUMNS if c in frame.columns]` is rendered as a Python list repr. The literal
  `is_readmitted` therefore appears in the message whenever the label is present, so the existing
  `pytest.raises(ValueError, match=LABEL_COLUMN)` assertions (`tests/scoring/test_ranking.py:87,89`) keep passing
  unchanged.
- `scoring/pipeline.py:100` becomes `label_free = batch.drop(columns=list(LABEL_SIDE_COLUMNS))`; the comment on line 99
  is updated. `PREDICTION_COLUMNS` is unchanged, so the predictions file schema is unchanged.
- `summarize_observed` (line 120) still reads only `LABEL_COLUMN` from `gold`.

### Test helpers (`tests/models/helpers.py`)

- `make_gold_frame` gains a keyword-only `readmission_window_days: int = 30`. After the existing `DataFrame`
  construction and **without any rng draw**, it sets
  `df["label_horizon"] = df["index_stop"] + pd.Timedelta(days=readmission_window_days)`. With horizon == deadline for
  the window the frame will be split with, step 4b drops a row only if step 4 already did, so every existing seeded
  draw, row and split count is unchanged and no new warning fires. Docstring "23-column" becomes "24-column".
- `tests/models/test_cli.py::_write_gold(directory, **metadata_overrides)` changes its frame construction to
  `make_gold_frame(300, seed=1, readmission_window_days=metadata_overrides.get("readmission_window_days", 30))`.
  `test_cli_maps_flags_to_config_and_prints_report`, which runs with a 14-day window, then keeps its exact behaviour.
  The 14-day case in `tests/models/test_training.py` (≈ line 454, a config-vs-metadata mismatch) is rejected before
  the split runs and needs no change.
- `tests/models/test_training.py::test_warnings_from_split_are_not_swallowed` (≈ lines 432–442) moves 5 rows'
  `index_start`/`index_stop` to 2026-08-10/2026-08-18 after `make_gold_frame`. Their `label_horizon` would stay at the
  old `index_stop + 30 d`, which fails validation 4. One line is added after the two moves:
  `df.loc[idx, "label_horizon"] = df.loc[idx, "index_stop"] + pd.Timedelta(days=30)`.
  A grep of `tests/` found no other test that changes `index_stop` on a frame that later reaches the split
  (`test_data.py:130,168` are loader/`prepare_features`-only).
- `row(...)` gains a keyword `label_horizon: str | None = None` and includes the key; the docstring changes
  "ALL 23 columns" to 24.
- `frame(rows)`: after converting `index_start`/`index_stop`, fills `label_horizon` with `index_stop + 30 days` where it
  is `None`, then converts it to UTC. The 30 matches `readmission_window_days`' default.

## UX flow (pipeline)

```
Synthea CSVs --(Big Join: build_index_encounters + compute_label_horizons)--> gold Parquet (24 cols, incl. label_horizon)
  --(load_gold_table: schema/null/UTC checks)--> chronological_group_split
       1 censor buffer -> 2 test -> 3 train_start -> 4 label-window purge -> 4b label-horizon purge (NEW) -> 5 patient overlap
       -> post-conditions (NEW: label_horizon < T on train) -> summary (NEW key) --> MLflow params / split_summary.json
scorer: load gold -> guards -> select window -> drop LABEL_SIDE_COLUMNS (CHANGED) -> score -> predictions (schema unchanged)
```

Nobody runs anything new. Training prints/logs one extra summary number and may warn.

## Data handling

- **Leakage.** This feature exists to remove a leakage path:
  - **Label side.** After it, every training row's label is a function of records dated strictly before `T`, under the
    definition above. The split's post-condition enforces this on every run, and the Big Join unit tests pin the
    definition.
  - **Feature side.** `label_horizon` is itself post-index information. It is classified as excluded, and
    `test_every_gold_column_is_classified_exactly_once` fails if it is ever left unclassified. The scorer strips it
    structurally, and `prepare_features` selects only `FEATURE_COLUMNS`, so it cannot reach a model.
  - **What is NOT claimed.** The *composition* of the training set is itself a function of post-`T` data, in two ways:
    - step 4b reads stay `STOP`s and procedure dates after `T` to decide which rows to purge (the normal trade-off of
      label-end-time purging, and outcome-dependent, see the selection-bias note);
    - the two membership paths in Scope > Out.

    Docs and the v2 pre-registration must say "every training row's *label* is determined by records dated before
    `T`", never "the training set depends only on records before `T`".
- **Selection bias of the purge, flagged.** Unlike step 4, which drops every row in the last `W` days before `T`
  regardless of outcome, step 4b drops only rows that have an in-window stay extending past `T`. Those rows are mostly
  positives or planned/continuation readmissions, so a non-zero purge slightly lowers the training prevalence near the
  boundary. This is the accepted trade-off of label-end-time purging (López de Prado's purged CV has the same property).
  On the 10k data at 2023-01-01 it drops 0 rows. The warning and the summary count make every non-zero case reportable,
  and v2's pre-registration should name the count as a reported quantity.
- **Train/test split integrity.**
  - Test rows are unchanged by construction.
  - Train can only shrink: 4b runs after 4 and before 5, and rows dropped by 4b no longer reach step 5's overlap count.
    This only matters for the accounting of rows that would have been dropped twice, which are counted once, under 4b.
  - On v1 data at 2023-01-01, train, test and every count must be identical to the current run (R5).
- **Reproducibility.** Deterministic: no randomness is added. Seeds are unchanged (`seed=42`). `gold_fingerprint`
  changes because it hashes all columns, so every existing MLflow run is (correctly) refused by the scorer's
  fingerprint guard until the rebuild/retrain (R1–R9).

## Failure modes

| Situation | Behaviour |
|---|---|
| Old gold table (pre-2026-10-09) without `label_horizon` | `load_gold_table_with_metadata` raises `ValueError`: `f"Gold table in {gold_dir} has no 'label_horizon' column: it was built before the label-horizon purge (2026-10-09); rebuild it with scripts/run_big_join.py into a fresh --output-dir"`. This message is used exactly when `missing == ["label_horizon"] and not unexpected`; every other combination uses the existing generic schema-mismatch message, unchanged. Both training and the scorer load through this function, so both refuse. |
| Null `label_horizon` in Parquet | Loader: the existing `_REQUIRED_NONNULL_COLUMNS` message. The split rejects it too (for frames that bypass the loader). |
| tz-naive `label_horizon` (hand-built frame) | Split: `ValueError` from `_require_tz_aware`. |
| `label_horizon < index_stop + W` (wrong window or corrupt) | Split: `ValueError` (validation 4). |
| A train row would violate | Purged (4b) + `UserWarning` + summary count. Never a failure. |
| The purge empties train or leaves one class | Existing `_require_two_classes` → `ValueError` naming `test_start_date` (unchanged path). |
| Bug leaves a violating train row | Post-condition `RuntimeError`. |
| Open (null-`STOP`) in-window stay | Horizon = `R_end` → always purged for any `T < reference_date`. |
| Planned procedure with null `STOP` | `greatest` uses its `START`. |
| Planned procedure with null/unparseable `START` **and** `STOP` | Contributes `R_end`: any row that has the stay in its window is purged for every `T < reference_date` (test 5b). |
| Procedures with null `ENCOUNTER` | They never match a stay `Id` (an equality join on null is false); ignored. |
| Empty procedures table | Cannot reach the Big Join (`TABLES_MUST_BE_NONEMPTY` raises first); in unit tests it gives `planned_record_max` null everywhere, so the horizon reduces to the stay terms. |
| Zero candidates | `compute_label_horizons` returns zero rows; the existing zero-row gold path is unchanged (`test_build_big_join_no_inpatient_stays`). |
| Scorer handed a frame containing `label_horizon` | `ValueError` from `_require_label_free`. |
| Class imbalance | Not affected beyond the selection-bias note above. |

## Verification criteria

### Unit tests (all must pass; names are the ones to create)

**`tests/pipeline/test_big_join.py`.** New helper
`_horizons(spark, stays, procedures=(), deaths=None, reference=date(2026, 9, 16), window=30) -> dict[str, str]`. It
mirrors `_labels`' full signature and patients-frame construction, including `deaths` (`{patient: date}`, default
none). It calls `build_index_encounters`, then selects
`F.date_format("label_horizon", "yyyy-MM-dd'T'HH:mm:ss").alias("h")` **in Spark** before `collect()`, and returns
`{encounter_id: h}`.

The formatting must happen in Spark. The session time zone is UTC (`spark_session.py:26`), so the strings are UTC
wall-clock. `collect()` instead returns naive *local-time* datetimes, which differ between a CEST laptop and UTC CI.
Expectations are written as plain strings like `"2020-03-31T00:00:00"`.

A sibling helper `_direct_horizons(spark, candidates, stays, procedures=(), reference=date(2026, 9, 16), window=30)`.
Its `candidates` frame is created from `(Id, PATIENT, STOP)` tuples with the explicit schema
`StructType([StructField("Id", StringType()), StructField("PATIENT", StringType()), StructField("STOP", TimestampType())])`.

This frame does **not** share lineage with `flagged`. The direct tests therefore check the definition only; the
self-join aliasing that production relies on is covered only by the `_horizons` tests (1–3, 5–8, 10–11, 13, 13b).

The helper builds:
- `flagged = flag_inpatient_stays(encounters, procs)`;
- a `candidates` frame from `(Id, PATIENT, STOP)` tuples, plus `readmit_deadline = STOP + INTERVAL 30 DAYS` computed
  in Spark as `F.expr(f"STOP + INTERVAL {window} DAYS")`, exactly as in `build_index_encounters`.

It calls `compute_label_horizons` and returns the same `{Id: h}` string dict. Procedures are 4-tuples
`(encounter, code, start, stop)`, using `_t()`/`ts()` datetimes or `None`.

1. `test_horizon_without_in_window_stay_is_the_deadline`: a single stay; horizon == `STOP + 30 d` exactly.
2. `test_horizon_extends_to_an_in_window_stay_that_ends_after_the_deadline`: the readmit starts inside the window and
   ends after the deadline; horizon == readmit `STOP`.
3. `test_horizon_ignores_an_in_window_stay_that_ends_before_the_deadline`: horizon == deadline.
4. `test_horizon_window_bounds_match_the_label` (direct `compute_label_horizons` call, so the terminal rule cannot
   interfere). The candidate `c` has `STOP = 2020-03-01T00:00Z`, so its deadline is 2020-03-31T00:00Z.
   - A stay starting exactly at `c.STOP` and ending 2020-06-01 is NOT in-window, so the horizon stays at the deadline.
   - In a second call, a stay starting exactly at the deadline and ending 2020-06-01 IS in-window, so the horizon is
     2020-06-01.
5. `test_horizon_includes_a_late_planned_procedure`: an in-window planned readmit (code `703423002`) with a procedure
   dated after the stay's `STOP` and after the deadline; horizon == `greatest(proc START, proc STOP)`. Also the
   null-`STOP` procedure variant: horizon == proc `START`.
5b. `test_horizon_undated_planned_procedure_reaches_the_observation_end`: as test 5, but the planned procedure has
   `start=None, stop=None`. Horizon == `"2026-09-17T00:00:00"` for `reference=date(2026, 9, 16)`. A second assertion
   pins that the same undated record still makes the readmit planned (via `_labels`: the index stays 0).
6. `test_horizon_ignores_a_late_non_planned_procedure`: same as 5 with code `410006001`; horizon == max(deadline,
   stay `STOP`).
7. `test_horizon_ignores_procedures_of_out_of_window_stays`: a planned procedure dated far in the future on a stay
   outside the window; horizon == deadline.
8. `test_horizon_open_in_window_stay_reaches_the_observation_end`: in-window readmit with null `STOP`; horizon ==
   `2026-09-17T00:00Z` for `reference=date(2026, 9, 16)`.
9. `test_horizon_includes_prev_max_stop` (via `_direct_horizons`). The candidate frame is built by hand with:
   - a candidate `c`;
   - an earlier stay `e` with `e.START < c.STOP < e.STOP` (the terminal rule would exclude `c`; the direct call
     bypasses it);
   - an in-window stay `a`, with `a.STOP` before the deadline.

   `e.STOP` is later than both `a.STOP` and `c`'s deadline. Concretely: `c` = 03-01→03-02, `e` = 02-20→05-01,
   `a` = 03-10→03-12. Horizon == `e.STOP` (2020-05-01T00:00:00), reached only through `a.prev_max_stop`.
10. `test_horizon_patient_isolation`: another patient's long stay in the same dates has no effect.
11. `test_horizon_is_independent_of_flags`, two cases via `_horizons`, each with a stay that extends the horizon on its own:
    - planned: `c` 01-01→01-02, planned `a` 01-10→03-15 (procedure dated inside it) → horizon `"2020-03-15T00:00:00"`;
    - continuation: `c` 01-01→01-02, `b` 01-10→01-20, continuation `a` 01-15→03-15 (it starts inside `b`, and
      `a.STOP > b.STOP`) → horizon `"2020-03-15T00:00:00"`.
    The label ignores the planned `a` and the continuation `a`; the horizon does not.
12. `test_compute_label_horizons_one_row_per_candidate`: three candidates (one with two in-window stays, one with none,
    one with an open stay). It calls `compute_label_horizons` directly, not `_direct_horizons`, whose dict would hide
    duplicate rows, and asserts `result.count() == 3` and `result.select("Id").distinct().count() == 3`.
13. `test_build_big_join_end_to_end` (existing, changed). Its `expected` row tuple (≈ lines 944–953, built with
    `schema=gold.schema`) gains a 24th value `ts("2020-02-01T00:00:00")` in position 6, right after the label `0`.
    That value is `index_stop` 2020-01-02 + 30 days, and the fixture has no other stay. `assertDataFrameEqual` then
    pins the column's position, type and value.
13b. `test_horizon_survives_the_death_and_censoring_filter` (via `_horizons` with `deaths=`):
    - patient p1 has index `c1` (01-01→01-02) and readmit `a1` (01-10→01-15), and dies 01-20, so the row is kept as a
      positive. Expected horizon `"2020-02-01T00:00:00"` (the deadline, later than `a1.STOP`);
    - patient p2 has index `c2` (01-01→01-02) and a long readmit `a2` (01-20→03-10), with no death. Expected horizon
      `"2020-03-10T00:00:00"`.

    The assertions are keyed: `result["c1"]` and `result["c2"]` have the stated values, not exact dict equality,
    because `a2` is itself a kept negative index row (horizon 2020-04-09), and `a1` is dropped by the death rule. Each
    value is checked after the death join, the keep filter and the closing select.
13c. `test_build_big_join_end_to_end_label_wiring` (existing, extended). This is the only end-to-end check of a horizon
    **above** the deadline, through CSV parsing of the procedure dates, the feature joins and Parquet.
    - `_proc_csv` (≈ line 988) gains optional `start="01-01T00:00:00"` and `stop="01-01T01:00:00"` arguments (2020
      month-day-time strings, rendered as `2020-{start}Z`). Its defaults reproduce today's CSV text exactly.
    - The fixture's `_proc_csv("e2", PLANNED)` becomes
      `_proc_csv("e2", PLANNED, start="04-01T00:00:00", stop="04-02T00:00:00")`: e2 is still planned (planned-ness
      ignores dates), so every existing label and metadata assertion is unchanged.
    - New assertion, with `frame = pd.read_parquet(output_dir)` and the horizon normalized via
      `pd.to_datetime(frame["label_horizon"], utc=True)`, as a `{encounter_id: horizon}` dict equal to:
      `e1 → 2020-04-02T00:00Z` (the late planned procedure on in-window e2; deadline 02-01), `e4 → 2020-04-10T00:00Z`
      (deadline; in-window e5 ends 03-21), `e5 → 2020-04-20T00:00Z` (deadline), `e7 → 2020-06-02T00:00Z` (deadline;
      in-window e8/e9 end by 05-20, and e9's `prev_max_stop` 05-20 is also below it), `e12 → 2020-08-04T00:00Z` and
      `e13 → 2020-08-04T00:00Z` (deadlines; e13 starts before e12's `STOP`, so it is not in e12's window).

    A mutant that replaces the horizon with the deadline after `build_index_encounters`, or reads the wrong procedure
    date columns from the real schema, fails on e1.
14. `test_join_dimension_attributes_and_demographics_no_gender_collision` / `test_join_patient_demographics_age_calculation`:
    `_add_stub_feature_columns` (≈ line 755) gains a `label_horizon` stub, `F.lit(index_start_value)`, so their inputs
    still carry every gold column.

**`tests/models/test_split.py`.** Uses `row(..., label_horizon=...)`.

15. `test_label_horizon_purge_boundary`: rows with `index_stop + 30 d < T` and horizon `T − 1 s` (kept), `T` (purged)
    and `T + 1 s` (purged); `n_dropped_label_horizon_purge == 2`; `pytest.warns(UserWarning, match="label-horizon purge")`.
16. `test_label_horizon_purge_does_not_touch_test_rows`: a test row with a horizon years after `T` stays in test.
17. `test_no_label_horizon_warning_when_nothing_is_purged`: pad-only frame; `warnings.simplefilter("error")` passes.
18. `test_split_rejects_bad_label_horizon` (parametrized): missing column / tz-naive / null / below `index_stop + W` →
    `ValueError` with the messages above.
19. `test_summary_accounting_identity` (existing, extended):
    - `_every_drop_reason_frame` gains a row
      `row("hz", "2020-03-01T00:00:00Z", "2020-03-05T00:00:00Z", 0, label_horizon="2023-01-02T00:00:00Z")`;
    - the identity includes the new key, and every drop reason, including the new one, is ≥ 1;
    - both users of that frame, this test and `test_train_start_date_only_filters_train`, gain
      `@pytest.mark.filterwarnings("ignore:The label-horizon purge:UserWarning")` next to their existing
      censoring-buffer filter. The latter gets no new assertion.
20. `test_readmission_window_days_reaches_both_the_purge_and_the_buffer` (existing, adjusted): the "pre" row gets
    `label_horizon = index_stop + window days` per parameter, so it satisfies validation 4 for both windows and is kept
    at W=14.
21. `test_split_postconditions_hold_property` (existing, extended). For each seed, push the horizon past `T` for a
    deterministic subset of the `make_gold_frame` rows:
    ```python
    T = pd.Timestamp("2023-01-01", tz="UTC")
    pushed = df.index % 7 == 0
    floor = T + pd.Timedelta(days=1)
    df["label_horizon"] = df["label_horizon"].where(~pushed | (df["label_horizon"] >= floor), floor)
    ```
    `Series.where` (not `np.maximum`, whose behaviour on tz-aware columns varies by version) only ever raises a value,
    so validation 4 stays satisfied. **Each seed's split** runs inside its own
    `pytest.warns(UserWarning, match="label-horizon purge")` block, and for that seed the test asserts:
    - `result.summary["n_dropped_label_horizon_purge"] >= 1`;
    - `(train.label_horizon < T).all()`;
    - no row with `pushed & (index_start < T)` is in train.

    With 400 patients and dates spread over 2018–2026, every seed has ≈ 50+ pushed pre-period rows, so the
    ≥ 1 is not fragile. The implementer confirms it for seeds 0–4.

**`tests/models/test_data.py`.**

22. `test_every_gold_column_is_classified_exactly_once`: its `assert len(EXPECTED_GOLD_COLUMNS) == 23`
    (`tests/models/test_data.py:60`) becomes `== 24`; nothing else in it changes. `test_expected_gold_columns_match_big_join`
    passes unmodified.
22b. `tests/models/test_cli.py`: only the `_write_gold` change in Interfaces > Test helpers; the existing tests pass.
23. `test_load_gold_table_refuses_a_table_without_label_horizon`: write a frame with the column dropped; the specific
    rebuild message.
24. `test_load_gold_table_rejects_bad_data` (existing parametrization) gains a `null_label_horizon` case.
25. `test_load_gold_table_reads_spark_style_directory` (existing, extended). In `_write_parts`' `spark_like` branch
    (`tests/models/test_data.py` ≈ lines 36–40), the naive-timestamp loop
    `for col in ("index_start", "index_stop")` becomes `("index_start", "index_stop", "label_horizon")`, so the written
    file really is naive. The test then asserts that the loaded `label_horizon` dtype is tz-aware UTC and its values
    equal the original tz-aware ones.
25b. `test_label_side_columns_are_excluded_from_the_features`: `set(LABEL_SIDE_COLUMNS) - {LABEL_COLUMN}` ⊆
    `set(EXCLUDED_COLUMN_REASONS)`, and `LABEL_SIDE_COLUMNS[0] == LABEL_COLUMN`. This ties the scorer's strip list to
    the feature classification.
26. `test_gold_fingerprint` (existing, extended): changing one `label_horizon` value changes the fingerprint.

**Scoring.**

27. `tests/scoring/test_ranking.py`: a new `test_score_batch_and_build_predictions_refuse_label_horizon`. The existing
    `.drop(columns=[LABEL_COLUMN])` call sites (lines 118, 156) become `.drop(columns=list(LABEL_SIDE_COLUMNS))`.
28. `tests/scoring/test_pipeline.py:89`: the same drop change. The existing output-schema tests must pass unchanged,
    proving the predictions schema did not change.

**Gate.** `ruff check .` clean; the full `pytest` passes (expected ≈ 527 + ~20 tests; the exact count is reported).

### Independent pandas recipe (the oracle for R3–R5)

This recipe re-derives `label_horizon` from the raw CSVs with pandas only, independently of the Spark code. Its rules
restate the definition:
- in-window means `c.STOP < a.START <= c.STOP + W` (strict lower, inclusive upper) and `a.Id != c.Id`;
- all inpatient stays count, whatever their flags;
- `prev_max_stop` is the running maximum of `STOP` over the patient's strictly earlier stays in `(START, Id)` order,
  with nulls ignored (a null-only prefix gives NaT);
- only planned codes contribute procedure dates, as `max(START, STOP)` per procedure;
- a null stay `STOP` maps to `R_end`.

It is run with the project venv from the repo root (`.venv/bin/python - <<'EOF' … EOF`):

```python
import pandas as pd
from pathlib import Path
from readmission_risk.models.data import load_gold_table
from readmission_risk.pipeline.gold_metadata import PLANNED_PROCEDURE_CODES

RAW, W = "data/raw/full_run/csv/", pd.Timedelta(days=30)
R_END = pd.Timestamp("2026-09-17", tz="UTC")                       # reference_date 20260916 + 1 day
gold = load_gold_table(Path("data/gold/full_run_2b"))
enc = pd.read_csv(RAW + "encounters.csv", usecols=["Id", "START", "STOP", "PATIENT", "ENCOUNTERCLASS"])
enc = enc[enc.ENCOUNTERCLASS == "inpatient"].drop(columns="ENCOUNTERCLASS")
for c in ("START", "STOP"):
    enc[c] = pd.to_datetime(enc[c], utc=True)
# Recipe assumption, checked BEFORE use: no open stays. With a null STOP, cummax() skips the NaN, but shift(1) would
# copy the NaN into the NEXT stay's prev_max_stop and hide the earlier maximum. On data with open stays (possible on v2)
# replace this line with a null-aware running max (e.g. s.ffill().cummax().shift(1)) and re-derive.
assert enc["STOP"].notna().all()
# (PATIENT, START, Id) string order equals Spark's binary order for ASCII Ids (Synthea UUIDs are ASCII).
enc = enc.sort_values(["PATIENT", "START", "Id"], kind="stable")
enc["prev_max_stop"] = enc.groupby("PATIENT")["STOP"].transform(lambda s: s.cummax().shift(1))
p = pd.read_csv(RAW + "procedures.csv", usecols=["START", "STOP", "ENCOUNTER", "CODE"], dtype=str)
for c in ("START", "STOP"):
    p[c] = pd.to_datetime(p[c], utc=True)
planned = p[p.CODE.isin(PLANNED_PROCEDURE_CODES)]
# an undated planned record (both dates NaT) contributes R_END, matching coalesce(greatest(START, STOP), R_end) in Spark
planned_max = (planned[["START", "STOP"]].max(axis=1).fillna(R_END)
               .groupby(planned["ENCOUNTER"]).max().rename("planned_max"))
enc = enc.join(planned_max, on="Id")
enc["stay_horizon"] = pd.concat(
    [enc["START"], enc["STOP"].fillna(R_END), enc["prev_max_stop"], enc["planned_max"]], axis=1
).max(axis=1)
m = gold[["encounter_id", "patient_id", "index_stop"]].merge(
    enc.rename(columns={"PATIENT": "patient_id", "Id": "aid"}), on="patient_id")
m = m[(m.aid != m.encounter_id) & (m.START > m.index_stop) & (m.START <= m.index_stop + W)]
max_stay = m.groupby("encounter_id")["stay_horizon"].max()
oracle = pd.concat([gold.set_index("encounter_id")["index_stop"] + W, max_stay.reindex(gold.encounter_id)],
                   axis=1).max(axis=1)                              # Series: encounter_id -> label_horizon
```

The design-session measurements came from this same logic, run from a scratchpad script that is not a repo artifact.

### Real-data verification (manual, phase B, see Sequencing; commands are from the repo root, interpreter = the project's `.venv`, Python 3.14.7)

- **R1.** `mv data/gold/full_run_2b data/gold/full_run_2b_pre_horizon`. This is non-destructive; the owner deletes the
  old directory after review.
- **R2.** `python scripts/run_big_join.py --input-dir data/raw/full_run --output-dir data/gold/full_run_2b --reference-date 20260916`.
- **R3.** Equality check (pandas, inline):
  - `_gold_metadata.json` is byte-identical between the old and new directories.
  - Old = `pd.read_parquet(old_dir)`, because the new loader refuses it. New = `pd.read_parquet(new_dir)`.
  - Both have 9,284 rows and 151 positives.
  - After `sort_values("encounter_id").reset_index(drop=True)`, `pd.testing.assert_frame_equal(old, new.drop(columns="label_horizon"))`
    passes (both read raw by `pd.read_parquet`, so the dtypes match without normalization).
  - Every `label_horizon >= index_stop + 30 d`.
  - The number of rows with `label_horizon > index_stop + 30 d` is reported. It must be **≤ 175** (only rows with an
    in-window stay can exceed the deadline). More than 175 is a stop-and-report failure.
- **R4.** Spark vs oracle: `oracle` from the recipe equals the Spark column on **all 9,284 rows**, by exact timestamp
  equality after aligning on `encounter_id` (using `load_gold_table` for the new table). Then, for each of the 40
  quarterly cutoffs `T` (1 Jan/Apr/Jul/Oct, 2016–2025), with `reference_date=date(2026, 9, 16)`:
  - `new = chronological_group_split(gold, …)`, the real column;
  - `old = chronological_group_split(gold.assign(label_horizon=gold.index_stop + W), …)`, which forces the horizon to
    the deadline so step 4b is a no-op and reproduces the pre-change rule;
  - check that `set(new.train.encounter_id) == set(old.train.encounter_id) - set(oracle[oracle >= T].index)` and
    `new.test` equals `old.test`.

  Expected: no difference at 39 cutoffs; at 2025-07-01, ≥ 1 row fewer in train and the warning fires. Splits that
  raise (`_require_two_classes`) are reported, not hidden; none did at design time.
- **R5.** At `test_start` 2023-01-01:
  - `n_dropped_label_horizon_purge == 0` and no label-horizon warning;
  - every other summary count equals the current run: n_train 6,391 (96 positive), n_test 1,539 (41 positive),
    censor 1, purge 34, overlap 1,319;
  - the latest in-window `stay_horizon` over train rows (from the oracle's `max_stay`) is 2022-12-25T01:59:59Z. This
    is the margin the docs quote.
- **R6.** Retrain under Python 3.14.7:
  `python scripts/train_models.py --gold-dir data/gold/full_run_2b --reference-date 20260916 --test-start-date 20230101 --experiment-name readmission-risk-v1`.
  - A new experiment name is needed because `resolve_run_id` refuses an experiment holding two FINISHED runs per model.
    The old `readmission-risk-2b` experiment is left untouched.
  - Python 3.14 is the project interpreter, and the scorer's guard 14 (parallel session) refuses the 3.12-trained
    `readmission-risk-2b` runs on 3.14 anyway, so the v1 reference runs **must** be re-made on 3.14 whatever this
    feature does.
- **R7.** Results comparison, which attributes any difference rather than stopping on it:
  - **(a) Gate.** Each new run's `split_assignment.csv` is byte-identical to the corresponding `readmission-risk-2b`
    run's. Together with R3/R5, this proves the training and test rows, labels and features are identical, so the
    purge changed nothing on v1. If (a) fails, **stop and report**: this is the only failure that would implicate this
    feature.
  - **(b) Report.** The new runs' logged metrics are compared with the old runs' metrics, and the old models'
    test-row `predict_proba` with the new models'. The old cloudpickles are loaded through
    `readmission_risk.models.tracking.load_logged_model`, **not** `validate_run`, because guard 14 refuses them. Max
    |Δ| is reported per model.
  - Because (a) holds, any non-zero Δ is attributable to the Python 3.12 → 3.14 interpreter change, not to this
    feature. It is recorded in the Measurements section and, if non-zero, the docs' metric figures are updated from
    the new runs; the README results table and `docs/results.md` stay equal, as `tests/test_docs.py` enforces.
  - Expected: Δ == 0 (the bootstrap and models are seeded, and 3.12→3.14 scoring was identical at the 2026-10-09
    check), but this is not required.
- **R8.** Copy the old predictions file to the scratchpad, then score:
  `python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --experiment-name readmission-risk-v1 --run-date 20250630`.
  - Compare `rank`/`encounter_id`/`risk_score` with the old file: equal if R7's Δ is 0, otherwise the differences are
    reported.
  - `model_run_id` and `gold_fingerprint` always differ.
- **R9.** `python scripts/make_readme_assets.py --experiment-name readmission-risk-v1 --predictions data/predictions/run_date=20250630/predictions.parquet`.
  - **Expected diff:** in `docs/img/reliability_light.svg` and `reliability_dark.svg`, only the `<dc:description>`
    line changes (new experiment name and run ids, written by `reporting/assets.py:171`). `docs/img/sample_topn.md`
    is unchanged.
  - Any other change is allowed only if R7(b) found a non-zero Δ, and then it is explained in the commit message.

## Sequencing (parallel session)

The Python-version guard, developed in a parallel session in this same working tree, was **committed before
implementation began** (`91cb37c`, followed by `76a991b`; owner confirmation 2026-10-09). At that point the tree was
clean except for untracked notes. There is therefore no interleaving, and phase A may edit `tests/models/test_training.py`
(the one-line fix in Interfaces > Test helpers). Phase A still makes no change to `models/training.py`,
`models/tracking.py` or `scoring/runs.py`; none is needed.

- **Phase A: code and tests.** Files:
  - `pipeline/big_join.py`, `models/data.py`, `models/split.py`, `scoring/ranking.py`, `scoring/pipeline.py` (line 100
    only; its line-34 comment is phase B);
  - `tests/pipeline/test_big_join.py`, `tests/models/helpers.py`, `tests/models/test_split.py`,
    `tests/models/test_data.py`, `tests/models/test_cli.py`, `tests/models/test_training.py`,
    `tests/scoring/test_ranking.py`, `tests/scoring/test_pipeline.py`;
  - this note.

  `/eg-precommit-review` and the test gate (`ruff check .`, full `pytest`) run on this diff. The commit stages these
  paths explicitly (`git add <paths>`, never `-A`), so the unrelated untracked brainstorm/PRD notes stay out.
- **Phase B: R1–R9 plus the doc-update inventory.** Runs right after phase A is committed, as a separate commit:
  - the retrained runs carry the Python-version tag (guard 14);
  - the shared docs are edited on top of the parallel session's committed text.

  Between the two commits, the local `full_run_2b` is refused by training and the scorer for lacking `label_horizon`.
  That is expected and is stated in phase A's commit message.

## Doc-update inventory (phase B)

| Where | Statement | Action |
|---|---|---|
| `README.md` (≈ line 96) | "A thin leakage margin of about 6 days between the label window and the test start" | Rewrite: the split purges any training row whose label depends on a record dated on or after the test start (0 rows on this data; the closest record is ≈ 6 days before it); keep the details link |
| `docs/results.md` (≈ line 41) | "A thin leakage margin, checked and documented … tightening the purge is a named follow-up" | Rewrite: the label-side property is **enforced** by the label-horizon purge since 2026-10-09 (0 rows dropped at 2023-01-01; margin 6.9 days); cohort membership remains measured-only, naming **both** paths with their own figures (terminal rule: 0 of 470; planned-index exclusion: 0 stays at 2023-01-01), and a follow-up; wording follows Data handling > Leakage > "What is NOT claimed" |
| `docs/results.md` (≈ line 40) | `readmission-risk-2b-sensitivity` figures (`--train-start-date 20170916`) | **Kept, not rerun**: add "trained on the pre-horizon gold table, whose rows, labels and features are identical (R3); Python 3.12.11" so it is labelled as history |
| `docs/results.md` (≈ line 49), `docs/setup.md` (≈ line 65), `docs/scoring.md` (commands), CLAUDE.md (build/test commands), `scripts/score_discharges.py:46` and `scripts/make_readme_assets.py:28` (help-text examples), `src/readmission_risk/scoring/pipeline.py:34` (comment) | `readmission-risk-2b` as the reference experiment | → `readmission-risk-v1` (grep `readmission-risk-2b` repo-wide, excluding `notes/` history and the `-sensitivity` experiment, and change every remaining hit) |
| `docs/scoring.md` (example command) | `--run-id 2bb281f6…` | The new LR run id from R6 |
| Parallel session's Python-version paragraphs: `docs/scoring.md` (≈ line 16), `docs/results.md` (≈ line 52), CLAUDE.md (≈ lines 107–108), the `docs/setup.md` comment | "the reference runs in `readmission-risk-2b` were trained on 3.12.11 and are refused on 3.14 / need a 3.12 environment" | Rewrite to: the reference runs are `readmission-risk-v1`, trained on Python 3.14.7; the older `readmission-risk-2b` runs (3.12.11, pre-horizon gold) are refused by both the fingerprint and the Python-version guard and are kept only as history. This is the parallel note's follow-up F1, executed here |
| CLAUDE.md | Caveat (4) "(tightening slice 3's purge is a follow-up)"; the "Resolved 2026-09-19" split paragraph; test count | Caveat (4): the label-side margin is now enforced by step 4b (this note); membership stays a follow-up. One added sentence on step 4b. Updated test count |
| `notes/eg-new-feature/readmission-label-planned-exclusion-2026-09-20.md:335` | "Tightening slice 3's purge…" follow-up | Append "— executed 2026-10-09 for the label side, see `label-horizon-purge-2026-10-09.md`; the membership property remains open" (history not rewritten) |
| `split.py` docstring | Step 4's slice-2b paragraph | Phase A (it is code): replaced as in Interfaces |
| `docs/scoring.md` (≈ line 39, the `scoring_window_days` column description) | "not the 30-day label horizon" | Phase B: "not the 30-day label window", same reason as the `ranking.py:107` row below (found by the phase-A pre-commit review) |
| `src/readmission_risk/scoring/ranking.py:107` (`build_predictions` docstring) | "confusable with the 30-day label horizon" | Phase A (same file is edited): "the 30-day label window", so "label horizon" means only the new per-row column |
| `notes/prds/v2-mlops-keyless-scheduled-scale-up-2026-10-09.md` FR0.1 (≈ line 161) and the risk row "Leakage via the ~6-day label/test margin at 200k" (≈ line 340); `notes/eg-brainstorms/v2-scope-and-v1-closeout-2026-10-09.md` closeout item 5 (≈ line 143) | "an assertion … a test fails if any training row's label-classifying record is dated on or after test_start_date"; "FR0.1 assertion runs on the 200k split too" | Phase B, dated amendment (history not rewritten): "implemented 2026-10-10 as a label-horizon **purge + post-condition** (owner decision, see `label-horizon-purge-2026-10-09.md`); it never fails on data; `n_dropped_label_horizon_purge` is reported and should be pre-registered as a reported quantity". The risk-row mitigation changes to "the label-horizon purge runs on the 200k split; its count is reported" |

## Out-of-scope follow-ups

- **Cohort-membership horizon.** Making index membership provably independent of post-`T` records. This needs either
  open-stay semantics in the terminal rule at a cutoff, or a gold-level record of stays excluded because of post-`T`
  `STOP`s. It covers both membership paths: the terminal rule (overlapping stays' `STOP`s) and the planned-index
  exclusion (planned-code procedures attached to `c` dated `>= T`; see Scope > Out).
- **Point-in-time label reconstruction test.** A stronger, more expensive check: recompute the label from CSVs truncated
  at `T` and compare it with the full-data label for every train row. That would verify the horizon definition itself
  against the label code rather than against a hand-derived definition.
- **Pre-registering the purge count.** v2's `prereg.yaml` should name `n_dropped_label_horizon_purge` as a reported
  quantity and fix in advance what to do if it is large; for example, a cap after which the cutoff rule is re-examined
  (that was the rejected Step-0 option 3).
- **Content fingerprint in metadata.** Unchanged from the existing follow-up list.
- **Horizon-definition provenance.** `LABEL_DEFINITION` and `GOLD_METADATA_VERSION` are deliberately unchanged, so a
  gold table records no marker of which `label_horizon` definition built it. If the definition changes later (the
  tighter positive-row rule below, or a membership extension), an older table with a non-null but differently defined
  column would be accepted silently; the fingerprint detects a different table, not a different definition. Accepted
  gap for now; fix with a `label_horizon_definition` metadata field when the definition first changes.
- **Tighter horizon for positives** (see Interfaces, "Why the conservative max"). Adopt only if v2's purge count is
  material.
- **Downstream consumers must strip `label_horizon`.** The planned BigQuery serving table and the Streamlit dashboard
  read "the gold table". `label_horizon` is post-index, label-side information, so any writer or reader for those
  consumers must drop it, as the scorer does (`LABEL_SIDE_COLUMNS`). The as-of scoring path (unlabelled recent
  discharges) has no label and so no horizon: it cannot reuse a loader that requires a non-null `label_horizon`, which
  goes on that follow-up's design list next to the `flag_inpatient_stays` open-stay caveat.

## Measurements

The pre-design measurements are listed under "Why". R3–R5 results are appended here after the rebuild.

### Phase-A pre-commit check on real data (2026-10-10)

The Big Join was rebuilt from `data/raw/full_run` with the phase-A code into a **scratchpad** directory, so
`data/gold/full_run_2b` was not touched (16 s). R3–R5 were then run against it, with the old table as reference:

- **R3.**
  - `_gold_metadata.json` is byte-identical.
  - The 23 pre-existing columns are identical on all 9,284 rows (151 positives).
  - Every `label_horizon >= index_stop + 30 d`.
  - **13** rows have a horizon above their deadline (the ceiling was ≤ 175).
- **R4.**
  - The Spark `label_horizon` equals the independent pandas oracle on **all 9,284 rows**. 175 rows have an in-window stay.
  - The set identity (new train = forced-deadline train − oracle-flagged rows; test unchanged) holds at all 40
    quarterly cutoffs.
  - Train shrinks only at **2025-07-01** (1 row; `n_dropped_label_horizon_purge` = 1), as measured at design time.
- **R5** (`test_start` 2023-01-01).
  - n_train 6,391 (96 positive); n_test 1,539 (41 positive).
  - Drops: censor 1, label-window purge 34, **label-horizon purge 0**, patient overlap 1,319. These are identical to
    the current run.
  - The latest in-window stay record over train rows is 2022-12-25T01:59:59Z (margin ≈ 6.9 days).

R1/R2 (in place) and R6–R9 remain phase B.
