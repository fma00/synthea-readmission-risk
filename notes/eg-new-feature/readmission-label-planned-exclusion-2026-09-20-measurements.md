# Slice 2b measurements — recipes and expected values

Companion to [readmission-label-planned-exclusion-2026-09-20.md](readmission-label-planned-exclusion-2026-09-20.md). Every figure the README, CLAUDE.md and the design doc cite for slice 2b is produced by the single script below (pandas only; reads encounters/patients/procedures, never observations or claims), so each number is re-derivable from the repo plus the data. The project's reproducibility rule applies to measurements too.

**Run** (repo root, `.venv` active, JDK not needed; needs `data/raw/full_run/` = 10,000 patients, seed 42, `years_of_history=10`, `reference_date=20260916`, and the *pre-2b* all-cause gold table at `data/gold/full_run` for the feature columns of the prototype metrics):

```sh
MLFLOW_DISABLE_AGENT_HINT=1 python <the script below saved as measure_label.py>
```

The final `[gold]` line only prints once `data/gold/full_run_2b` exists (the Big Join has been run); it checks that the Spark-built gold table has exactly the same encounter ids and labels as this pandas implementation of the rules.

**Conventions.** "Readmitting stay" for a positive index = the earliest in-window stay by `START` (all-cause for pre-2b figures, counting stays for post-2b figures); gap = time from the index `STOP` to that stay's `START`; reasons are grouped by `admission_reason_description` (a null reason is "no recorded reason"); distinct-event counts use that same earliest stay; gap bins are half-open.

## Expected output (verified by running the script on 2026-09-20)

| Tag | Figure | Value |
|---|---|---|
| `[diag]` | inpatient stays / planned / continuation / non-terminal | 13,565 / 3,320 / 682 / 682 (equal by coincidence, not identity) |
| `[pre-2b]` | rows / positives (all-cause, slice 2) | 13,222 / 1,607 |
| `[pre-2b]` | lung-reason inpatient stays / carrying a planned code | 3,321 / 3,315 |
| `[pre-2b]` | positives on the two lung reasons / chemo-marked positives | 1,166 of 1,607 (72.6%) / 1,187 |
| `[pre-2b]` | `424132000`: n, same-reason share, gap | 662, 98.5%, 28.8 ± 1.9 d |
| `[pre-2b]` | `67811000119102`: n, same-reason share, gap | 504, 99.8%, 24.5 ± 2.1 d |
| `[pre-2b]` | abnormal cardiac imaging `274531002` | 158 positives, all readmit as "History of CABG", median gap 1.4 d |
| `[pre-2b]` | aortic stenosis `60573004` | 69 positives: 62 → "History of aortic valve replacement", 5 same-reason, 2 lung; 1.1 d |
| `[pre-2b]` | aortic regurgitation `60234000` | 26 positives: 24 → "History of aortic valve replacement", 2 same-reason; 1.2 d |
| `[funnel]` | ignore planned readmits | 13,222 rows / 423 positives |
| `[funnel]` | + ignore continuation readmits (death/censoring re-applied) | 13,214 / 218 |
| `[funnel]` | + drop planned index stays | 9,921 / 207 |
| `[funnel]` | + terminal-only index rows (**final cohort**) | **9,284 / 151** |
| `[variant]` | transplant codes added (pre-terminal cohort) | 9,651 / 202 |
| `[prototype]` | planned index kept (pre-terminal): train / test; lookup; LR | 9,624 (131 pos) / 2,076 (67 pos); AUC 0.862, Brier 0.0298; AUC 0.868, Brier 0.0305 |
| `[prototype]` | planned index dropped (pre-terminal) | 6,786 (121) / 1,743 (66); lookup AUC 0.849, Brier 0.0350; LR AUC 0.846, Brier 0.0359 |
| `[prototype]` | final cohort | 6,391 (96) / 1,539 (41); lookup AUC 0.873, Brier 0.0252; LR AUC 0.869, Brier 0.0255 |
| `[split]` | final cohort, `test_start` 2023-01-01 | n_input 9,284; censor-buffer drop 1; purge 34; patient-overlap 1,319; train 6,391 (96 pos, 1.50%); test 1,539 (41 pos, 2.66%); 4,167 / 1,295 patients |
| `[horizon]` | train rows with an in-window stay / violating / max horizon | 118 / 0 / 2022-12-25T01:59:59Z |
| `[concentration]` | all 151 | CABG-history 45, CHF 30, aortic stenosis 20, aortic regurgitation 17, NSTEMI 7, no recorded reason 7, dependent drug abuse 7, abnormal imaging 5 |
| `[concentration]` | train 96 | CHF 20, aortic stenosis 18, CABG-history 17, aortic regurgitation 15, then four reasons at 4 |
| `[concentration]` | test 41 | CABG-history 23, CHF 10, three reasons at 2 (dependent drug abuse, NSTEMI, no recorded reason), two singletons |
| `[gaps]` | distinct first-readmit stays among the 151 positives | 151 (no duplicated event on this data) |
| `[gaps]` | first counting readmit ≤ 48 h / < 24 h | 39 (one at exactly 48.0 h) / 13 |
| `[gaps]` | half-open bins in hours: [1,6) [6,24) [24,48) [48,168) [168,360) [360,720] | 11, 2, 25, 31, 33, 49 |
| `[gaps]` | CABG-history positives / same-reason repeats / median gap | 45 / 43 / 16.57 d (43 same-reason: 16.74 d) |
| `[sensitivity]` | `--train-start-date 20170916` | 5,403 dropped before the start; 1,676 train rows, 24 positives |
| `[cv]` | grouped calibration-fold positives, default run / sensitivity run | 15, 25, 13, 21, 22 / 8, 3, 7, 2, 4 |

## The script

```python
"""Slice 2b measurement recipes (pandas). Run from the repo root with the repo .venv:  python measure_label.py
Reads only encounters/patients/procedures (never observations/claims). Every figure printed here is quoted in the design doc,
README or CLAUDE.md; the expected values are listed in the measurements note."""

import warnings
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from readmission_risk.models.baselines import reason_code_lookup
from readmission_risk.models.data import load_gold_table, prepare_features
from readmission_risk.models.evaluate import evaluate_probabilities
from readmission_risk.models.modeling import build_logistic_regression, make_grouped_cv_splits
from readmission_risk.models.split import chronological_group_split

warnings.filterwarnings("ignore")
RAW, OLD_GOLD, NEW_GOLD = Path("data/raw/full_run/csv"), Path("data/gold/full_run"), Path("data/gold/full_run_2b")
W = 30
REF, TEST_START = date(2026, 9, 16), date(2023, 1, 1)
REF_END = pd.Timestamp("2026-09-17", tz="UTC")  # reference_date + 1 day, exclusive
CHEMO = {"703423002", "367336001", "33195004"}
TRANSPLANT = {"70536003", "88039007", "58776007", "234336002", "58390007"}
LUNG = ["424132000", "67811000119102"]


def load():
    enc = pd.read_csv(RAW / "encounters.csv", dtype={"REASONCODE": "string", "CODE": "string"},
                      usecols=["Id", "START", "STOP", "PATIENT", "ENCOUNTERCLASS", "REASONCODE", "REASONDESCRIPTION"])
    for col in ("START", "STOP"):
        enc[col] = pd.to_datetime(enc[col], utc=True)
    pat = pd.read_csv(RAW / "patients.csv", usecols=["Id", "DEATHDATE"])
    pat["DEATHDATE"] = pd.to_datetime(pat["DEATHDATE"], utc=True)
    proc = pd.read_csv(RAW / "procedures.csv", usecols=["ENCOUNTER", "CODE"], dtype=str)
    return enc, pat, proc


def stay_flags(enc):
    """Inpatient stays, the continuation ids (START <= running max of EARLIER stays' STOP, order (START, Id)) and the
    non-terminal ids (another same-patient stay has START <= this STOP < that stay's STOP)."""
    inp = enc[enc.ENCOUNTERCLASS == "inpatient"].sort_values(["PATIENT", "START", "Id"]).copy()
    inp["prev_max"] = inp.groupby("PATIENT").STOP.transform(lambda s: s.cummax().shift())
    continuation = set(inp.loc[inp.START <= inp.prev_max, "Id"])
    cols = ["Id", "PATIENT", "START", "STOP"]
    x = inp[cols].add_prefix("c_").merge(inp[cols].add_prefix("s_"), left_on="c_PATIENT", right_on="s_PATIENT")
    nonterminal = set(x[(x.c_Id != x.s_Id) & (x.s_START <= x.c_STOP) & (x.s_STOP > x.c_STOP)].c_Id)
    return inp, continuation, nonterminal


def in_window_pairs(inp):
    """Every (index candidate c, later inpatient stay a) with a.START in (c.STOP, c.STOP + W days] (all-cause, pre-2b)."""
    c = inp[inp.STOP.notna()].add_prefix("c_")
    a = inp.add_prefix("a_")
    m = c.merge(a, left_on="c_PATIENT", right_on="a_PATIENT")
    return c, m[(m.a_Id != m.c_Id) & (m.a_START > m.c_STOP) & (m.a_START <= m.c_STOP + pd.Timedelta(days=W))]


def cohort(inp, pat, planned, continuation, nonterminal, *, ignore_planned_readmit=True, ignore_continuation_readmit=True,
           drop_planned_index=True, drop_nonterminal=True):
    """(encounter_id, y) after the label and the death/censoring keep-rule. All four switches on = the slice-2b cohort."""
    c, m = in_window_pairs(inp)
    if drop_planned_index:
        c = c[~c.c_Id.isin(planned)]
    if drop_nonterminal:
        c = c[~c.c_Id.isin(nonterminal)]
    m = m[m.c_Id.isin(set(c.c_Id))]
    if ignore_planned_readmit:
        m = m[~m.a_Id.isin(planned)]
    if ignore_continuation_readmit:
        m = m[~m.a_Id.isin(continuation)]
    c = c.merge(pat, left_on="c_PATIENT", right_on="Id", how="left")
    deadline = c.c_STOP + pd.Timedelta(days=W)
    c["y"] = c.c_Id.isin(set(m.c_Id)).astype(int)
    observable = (c.DEATHDATE.isna() | (c.DEATHDATE > deadline)) & (deadline < REF_END)
    return c[(c.y == 1) | observable][["c_Id", "y"]].rename(columns={"c_Id": "encounter_id"}), m


def normalized_old_gold():
    """The pre-2b gold table (no metadata) with load_gold_table's dtype contract, for prototype metrics on variant cohorts."""
    df = pd.read_parquet(OLD_GOLD)
    for col in ("index_start", "index_stop"):
        df[col] = pd.to_datetime(df[col], utc=True)
    counts = [c for c in df.columns if c.endswith("_count") or c.endswith("_window")]
    df[counts] = df[counts].astype("int64")
    return df


def metrics(gold, cohort_df):
    g = gold.merge(cohort_df, on="encounter_id")
    g["is_readmitted"] = g.y.astype("int8")
    sp = chronological_group_split(g.drop(columns=["y"]), reference_date=REF, test_start_date=TEST_START)
    ytr, yte = sp.train.is_readmitted.to_numpy(), sp.test.is_readmitted.to_numpy()
    prev = float(ytr.mean())
    lookup = evaluate_probabilities(yte, reason_code_lookup(sp.train.admission_reason_code, ytr, sp.test.admission_reason_code), train_prevalence=prev).metrics
    lr = build_logistic_regression(42).fit(prepare_features(sp.train), ytr)
    lrm = evaluate_probabilities(yte, lr.predict_proba(prepare_features(sp.test))[:, 1], train_prevalence=prev).metrics
    return sp, lookup, lrm


def main():
    enc, pat, proc = load()
    inp, continuation, nonterminal = stay_flags(enc)
    planned = set(proc.loc[proc.CODE.isin(CHEMO), "ENCOUNTER"]) & set(inp.Id)
    print(f"[diag] inpatient {len(inp)} planned {len(planned)} continuation {len(continuation)} non-terminal {len(nonterminal)}")

    # (a) pre-2b facts -----------------------------------------------------------------------------------------
    old, _ = cohort(inp, pat, planned, continuation, nonterminal, ignore_planned_readmit=False, ignore_continuation_readmit=False,
                    drop_planned_index=False, drop_nonterminal=False)
    print(f"[pre-2b] rows {len(old)} positives {int(old.y.sum())}")
    lung = inp[inp.REASONCODE.isin(LUNG)]
    print(f"[pre-2b] lung stays {len(lung)}, with a planned code {int(lung.Id.isin(planned).sum())}")
    c, m = in_window_pairs(inp)
    m = m[m.c_Id.isin(set(old[old.y == 1].encounter_id))]
    first = m.sort_values("a_START").groupby("c_Id").head(1).copy()  # the readmitting stay = the earliest in-window stay
    first["gap_d"] = (first.a_START - first.c_STOP).dt.total_seconds() / 86400
    print(f"[pre-2b] positives on lung reasons {int(first.c_REASONCODE.isin(LUNG).sum())} of {len(first)}; chemo-marked positives "
          f"{int(m.groupby('c_Id').a_Id.apply(lambda s: s.isin(planned).any()).sum())}")
    for code in LUNG:
        x = first[first.c_REASONCODE == code]
        print(f"[pre-2b] {code}: n {len(x)} same-reason {100 * (x.a_REASONCODE == code).mean():.1f}% gap {x.gap_d.mean():.1f} +/- {x.gap_d.std():.1f} d")
    for code in ("274531002", "60573004", "60234000"):
        x = first[first.c_REASONCODE == code]
        print(f"[pre-2b] {code}: n {len(x)} readmit reasons {dict(x.a_REASONDESCRIPTION.str[:32].value_counts().head(3))} median gap {x.gap_d.median():.1f} d")

    # (b) funnel and variant cohorts -----------------------------------------------------------------------------
    steps = [("ignore planned readmits", dict(ignore_continuation_readmit=False, drop_planned_index=False, drop_nonterminal=False)),
             ("+ ignore continuation readmits", dict(drop_planned_index=False, drop_nonterminal=False)),
             ("+ drop planned index", dict(drop_nonterminal=False)),
             ("+ terminal only (FINAL)", dict())]
    for name, kw in steps:
        co, _ = cohort(inp, pat, planned, continuation, nonterminal, **kw)
        print(f"[funnel] {name}: rows {len(co)} positives {int(co.y.sum())}")
    tr_planned = planned | (set(proc.loc[proc.CODE.isin(TRANSPLANT), "ENCOUNTER"]) & set(inp.Id))
    co, _ = cohort(inp, pat, tr_planned, continuation, nonterminal, drop_nonterminal=False)
    print(f"[variant] transplant codes added (pre-terminal cohort): rows {len(co)} positives {int(co.y.sum())}")
    g0 = normalized_old_gold()
    for name, kw in [("planned index KEPT (pre-terminal)", dict(drop_planned_index=False, drop_nonterminal=False)),
                     ("planned index dropped (pre-terminal)", dict(drop_nonterminal=False)), ("FINAL", dict())]:
        co, _ = cohort(inp, pat, planned, continuation, nonterminal, **kw)
        sp, lk, lr = metrics(g0, co)
        print(f"[prototype] {name}: train {sp.summary['n_train']}/{sp.summary['n_train_positive']} test {sp.summary['n_test']}/{sp.summary['n_test_positive']} "
              f"lookup AUC {lk['roc_auc']:.3f} Brier {lk['brier_score']:.4f} | LR AUC {lr['roc_auc']:.3f} Brier {lr['brier_score']:.4f}")

    # (c)(d)(e) final cohort, split, horizon, concentration, gaps, sensitivity ---------------------------------
    final, m = cohort(inp, pat, planned, continuation, nonterminal)
    g = g0.merge(final, on="encounter_id")
    g["is_readmitted"] = g.y.astype("int8")
    g = g.drop(columns=["y"])
    sp = chronological_group_split(g, reference_date=REF, test_start_date=TEST_START)
    print("[split]", {k: v for k, v in sp.summary.items() if k.startswith("n_") or k.endswith("rate")})
    inp["horizon"] = inp[["STOP", "prev_max"]].max(axis=1)
    pairs = sp.train[["encounter_id", "patient_id", "index_stop"]].merge(inp[["PATIENT", "START", "horizon"]], left_on="patient_id", right_on="PATIENT")
    pairs = pairs[(pairs.START > pairs.index_stop) & (pairs.START <= pairs.index_stop + pd.Timedelta(days=W))]
    test_start = pd.Timestamp(TEST_START, tz="UTC")
    print(f"[horizon] train rows with an in-window stay {pairs.encounter_id.nunique()}, violating {pairs[pairs.horizon >= test_start].encounter_id.nunique()}, max {pairs.horizon.max()}")
    for name, part in (("all", g), ("train", sp.train), ("test", sp.test)):
        p = part[part.is_readmitted == 1]
        print(f"[concentration] {name} {len(p)}:", dict(p.admission_reason_description.fillna("no recorded reason").value_counts().head(8)))
    fm = m[m.c_Id.isin(set(g[g.is_readmitted == 1].encounter_id))].copy()
    fm["gap_h"] = (fm.a_START - fm.c_STOP).dt.total_seconds() / 3600
    fm = fm.sort_values("a_START").groupby("c_Id").head(1)
    cabg = fm[fm.c_REASONCODE == "399261000"]
    print(f"[gaps] positives {len(fm)} distinct first-readmit stays {fm.a_Id.nunique()}; <=48h {int((fm.gap_h <= 48).sum())} (exactly 48.0h {int((fm.gap_h == 48).sum())}), <24h {int((fm.gap_h < 24).sum())}")
    print("[gaps] half-open bins (h):", dict(pd.cut(fm.gap_h, [0, 1, 6, 24, 48, 168, 360, 720.0001], right=False).value_counts().sort_index()))
    print(f"[gaps] CABG-history positives {len(cabg)}, same-reason {int((cabg.a_REASONCODE == cabg.c_REASONCODE).sum())}, median gap {cabg.gap_h.median() / 24:.2f} d")
    sens = chronological_group_split(g, reference_date=REF, test_start_date=TEST_START, train_start_date=date(2017, 9, 16))
    print(f"[sensitivity] train_start 2017-09-16: dropped {sens.summary['n_dropped_before_train_start']}, train {sens.summary['n_train']}/{sens.summary['n_train_positive']}")
    for name, part in (("default", sp.train), ("sensitivity", sens.train)):
        y, grp = part.is_readmitted.to_numpy(), part.patient_id.to_numpy()
        print(f"[cv] {name} calibration-fold positives:", [int(y[cal].sum()) for _, cal in make_grouped_cv_splits(y, grp, 5)])

    # (g) the real gold table agrees with the pandas cohort (only after the Big Join has been run) -----------------
    if NEW_GOLD.exists():
        real = load_gold_table(NEW_GOLD)
        same = set(real.encounter_id) == set(final.encounter_id) and real.set_index("encounter_id").is_readmitted.sort_index().equals(
            final.set_index("encounter_id").y.sort_index().astype("int8").rename("is_readmitted"))
        print(f"[gold] {NEW_GOLD}: rows {len(real)} positives {int(real.is_readmitted.sum())}; identical ids+labels to the pandas cohort: {same}")


if __name__ == "__main__":
    main()
```

## Additional recipe: cohort-membership horizon property (README "thin leakage margin")

```python
# non-terminal, non-planned candidates whose label window closes before test_start, and how many are dropped as non-terminal
# ONLY because an overlapping stay ends on/after test_start (expected: 470 candidates, 0 affected)
import pandas as pd
RAW = "data/raw/full_run/csv/"
enc = pd.read_csv(RAW + "encounters.csv", usecols=["Id", "START", "STOP", "PATIENT", "ENCOUNTERCLASS"])
proc = pd.read_csv(RAW + "procedures.csv", usecols=["ENCOUNTER", "CODE"], dtype=str)
for c in ("START", "STOP"):
    enc[c] = pd.to_datetime(enc[c], utc=True)
planned = set(proc.loc[proc.CODE.isin({"703423002", "367336001", "33195004"}), "ENCOUNTER"])
inp = enc[enc.ENCOUNTERCLASS == "inpatient"]
T = pd.Timestamp("2023-01-01", tz="UTC")
x = inp[["Id", "PATIENT", "START", "STOP"]].add_prefix("c_").merge(inp[["Id", "PATIENT", "START", "STOP"]].add_prefix("s_"), left_on="c_PATIENT", right_on="s_PATIENT")
nt = x[(x.c_Id != x.s_Id) & (x.s_START <= x.c_STOP) & (x.s_STOP > x.c_STOP)]
cand = nt[~nt.c_Id.isin(planned) & (nt.c_STOP + pd.Timedelta(days=30) < T)]
print(cand.c_Id.nunique(), cand[cand.s_STOP >= T].c_Id.nunique())
```

## Real runs (2026-09-20)

Big Join: `python scripts/run_big_join.py --input-dir data/raw/full_run --output-dir data/gold/full_run_2b --reference-date 20260916` (~20 s) → **Rows 9,284; Positives 151 (1.63%); Inpatient stays 13,565 (planned 3,320, continuation 682, non-terminal 682)**; `[gold]` line of the script: identical encounter ids and labels to the pandas implementation of the rules. `gold_fingerprint` = `c05486183e2b8a4742301cb59d8d16ebd44d8bdfe0cf152d8717115929fdf7ac`.

Training: `python scripts/train_models.py --gold-dir data/gold/full_run_2b --reference-date 20260916 --test-start-date 20230101 --experiment-name readmission-risk-2b` (~8 s; MLflow experiment `readmission-risk-2b`; the first pair of runs was tagged `git_dirty=dirty` because the working tree was uncommitted, so both experiments were deleted and re-run on commit `d7d3908`: all four runs are tagged `git_dirty=clean`, and every metric, interval and the gold fingerprint reproduced exactly). Split summary as pinned above; the "only 41 positive rows in the test window (< 50)" warning fired, as expected.

| Model | ROC-AUC | PR-AUC | Brier | Brier skill | ECE | calibration gap |
|---|---|---|---|---|---|---|
| Logistic regression | 0.8692 [0.8198, 0.9191] | 0.1234 | 0.0255 [0.0184, 0.0340] | 0.0224 | 0.0131 [0.0076, 0.0228] | −0.0100 [−0.0188, −0.0020] |
| Calibrated XGBoost | 0.8225 [0.7505, 0.8842] | 0.1134 | 0.0257 [0.0181, 0.0347] | 0.0143 | 0.0179 [0.0126, 0.0287] | −0.0122 [−0.0214, −0.0041] |
| Reason-code lookup (point estimate) | 0.8726 | 0.1351 | 0.0252 | — | 0.0116 | −0.0087 |

Paired differences (n_bootstrap 500, seed 42; `clustered_bootstrap` gives paired differences only for exactly two models, so one call per pair): XGBoost − LR: ROC-AUC −0.0467 [−0.0916, −0.0102]; Brier +0.0002 [−0.0009, +0.0013]; ECE +0.0048 [+0.0018, +0.0091]; |calibration gap| +0.0022 [+0.0007, +0.0040]. LR − lookup: ROC-AUC [−0.0361, +0.0307]; Brier [−0.0004, +0.0011]; ECE [−0.0020, +0.0060]. XGBoost − lookup: ROC-AUC [−0.0941, −0.0109]; Brier [−0.0009, +0.0020]; ECE [+0.0020, +0.0130].

Sensitivity run (`--train-start-date 20170916 --experiment-name readmission-risk-2b-sensitivity`): 5,403 dropped before the start; 1,676 train rows / 24 positives; LR ROC-AUC 0.8596 [0.8049, 0.9070], Brier 0.0246; XGBoost ROC-AUC 0.7778 [0.6998, 0.8442], Brier 0.0255; paired XGBoost − LR ROC-AUC −0.0818 [−0.1438, −0.0367], Brier +0.0008 [−0.0003, +0.0020], ECE +0.0068 [+0.0011, +0.0095]. Train rows before 2017-09-16 in the default run: 6,391 − 1,676 = 4,715 (74%), of which 96 − 24 = 72 positive (1.53%) versus 24 of 1,676 (1.43%) after.

Refusal checks (exit code 1, no MLflow directory created): `--gold-dir data/gold/full_run` (pre-2b) → `ERROR: Gold metadata not found: …/_gold_metadata.json -- this gold table predates slice 2b or is incomplete; rebuild it with scripts/run_big_join.py`; `--reference-date 20261231` against `full_run_2b` → `ERROR: reference_date 20261231 does not match the gold table's own reference_date 20260916 (see …/_gold_metadata.json)`.

Mutation spot-check (design requirement, manual, not committed): 17 named mutants of `big_join.py` (continuation `<=`→`<`; running max → immediate predecessor; no per-patient partition; terminal self-join without patient equality / with `<` / over all encounter classes; no `.distinct()` on planned ids; candidates without the terminal filter / also dropping continuations / keeping planned stays; readmission pool without the continuation filter / including planned stays / restricted to non-null `STOP`; lower bound `>`→`>=`; upper bound `<=`→`<`; readmission join without patient equality; metadata continuation/non-terminal swap) were each applied to the production code and **every one made the test suite fail (0 survivors)**.

## Scaling expectations: learning curve and signal beyond the admission reason (2026-09-20)

Backs the README/CLAUDE.md statement about what to expect from the 100k-200k population. Run after the Big Join has built `data/gold/full_run_2b`. The learning curve is seeded (`default_rng(0)`, 8 patient-grouped draws per fraction) and reproduces exactly. The XGBoost here uses 3 calibration folds (production uses 5), so its 100% figure (0.808) sits below the production run's 0.823.

Expected output:

| Share of train | Positives | Lookup | Logistic regression | XGBoost |
|---|---|---|---|---|
| 25% | 24 | 0.863 ± 0.008 | 0.764 ± 0.027 | 0.692 ± 0.056 |
| 50% | 48 | 0.871 ± 0.006 | 0.837 ± 0.021 | 0.753 ± 0.032 |
| 100% | 96 | 0.873 | 0.869 | 0.808 |

Within-reason per-feature AUC (0.50 = no signal; pooled over all 9,284 rows, descriptive only, ~12 features × 4 reasons so multiple-comparison caveats apply): CABG-history (n 401, 45 positive) strongest 0.44–0.46; CHF (n 341, 30) 0.56–0.58; aortic stenosis (n 68, 20) 0.10–0.16; aortic regurgitation (n 27, 17) 0.04–0.08 for the counts and 0.82 for age. The aortic-valve rows are too small to rely on and plausibly reflect the stage of a scripted care pathway (earlier work-up visit versus the surgical admission) rather than clinical risk.

Projection arithmetic (not a measurement): at the current rates, 10–20× the patients gives about 1,500–3,000 positives in total, roughly 1,000–2,000 in training and 400–800 in the test window; bootstrap intervals shrink roughly with the square root of the number of positives, i.e. about 3–4×.

```python
"""Scaling-expectation checks (slice 2b): (1) learning curve, (2) signal beyond the admission reason.
Run from the repo root with the repo .venv, after the Big Join has built data/gold/full_run_2b."""
import warnings
from datetime import date
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score

from readmission_risk.models.baselines import reason_code_lookup
from readmission_risk.models.data import FEATURE_COLUMNS, load_gold_table, prepare_features
from readmission_risk.models.modeling import build_calibrated_xgboost, build_logistic_regression, make_grouped_cv_splits
from readmission_risk.models.split import chronological_group_split

warnings.filterwarnings("ignore")
gold = load_gold_table(Path("data/gold/full_run_2b"))
sp = chronological_group_split(gold, reference_date=date(2026, 9, 16), test_start_date=date(2023, 1, 1))
tr, te = sp.train, sp.test
Xte, yte = prepare_features(te), te.is_readmitted.to_numpy()

print("(1) LEARNING CURVE: test ROC-AUC vs share of the training data (patient-grouped subsamples, 8 draws, mean +/- sd)")
rng = np.random.default_rng(0)
patients = tr.patient_id.unique()
for frac in (0.25, 0.5, 1.0):
    res = {"lookup": [], "LR": [], "XGB": []}
    for _ in range(8 if frac < 1 else 1):
        keep = set(rng.choice(patients, int(len(patients) * frac), replace=False)) if frac < 1 else set(patients)
        sub = tr[tr.patient_id.isin(keep)]
        y = sub.is_readmitted.to_numpy()
        if y.sum() < 10:
            continue
        res["lookup"].append(roc_auc_score(yte, reason_code_lookup(sub.admission_reason_code, y, te.admission_reason_code)))
        res["LR"].append(roc_auc_score(yte, build_logistic_regression(42).fit(prepare_features(sub), y).predict_proba(Xte)[:, 1]))
        cv = make_grouped_cv_splits(y, sub.patient_id.to_numpy(), 3)  # 3 calibration folds here (production uses 5)
        res["XGB"].append(roc_auc_score(yte, build_calibrated_xgboost(42, cv, "sigmoid").fit(prepare_features(sub), y).predict_proba(Xte)[:, 1]))
    print(f"  {int(frac * 100):3d}% ({int(tr.is_readmitted.sum() * frac):3d} positives): "
          + "  ".join(f"{k} {np.mean(v):.3f}+/-{np.std(v):.3f}" for k, v in res.items()))

print("(2) SIGNAL BEYOND THE REASON: per-feature ROC-AUC WITHIN one admission reason (0.50 = none); all 9,284 rows pooled, descriptive only")
X, y = prepare_features(gold), gold.is_readmitted.to_numpy()
numeric = [c for c in FEATURE_COLUMNS if X[c].dtype != object]
for code, name in [("399261000", "CABG-history"), ("88805009", "CHF"), ("60573004", "aortic stenosis"), ("60234000", "aortic regurgitation")]:
    m = (gold.admission_reason_code == code).to_numpy()
    aucs = {c: roc_auc_score(y[m], X.loc[m, c]) for c in numeric if X.loc[m, c].nunique() > 1}
    top = sorted(aucs.items(), key=lambda kv: -abs(kv[1] - 0.5))[:3]
    print(f"  {name:22s} n={int(m.sum()):4d} pos={int(y[m].sum()):2d}  strongest 3: " + ", ".join(f"{k} {v:.2f}" for k, v in top))
```

## Label residuals and label-v2 candidates (2026-09-20)

Backs the design note's "Label v2" section. Requires the first script above saved as `measure_label.py` in the same directory (it imports its loaders and `in_window_pairs`) and `data/gold/full_run_2b`; run from the repo root with that directory on `PYTHONPATH`: `PYTHONPATH=<dir> python label_residuals.py`. Each `[variant]` line relabels under the candidate definition, re-applies the death/censoring keep-rule, and then runs the same chronological split (test start 2023-01-01) as training.

Expected output (verified 2026-09-20):

| Tag | Figure | Value |
|---|---|---|
| `[ed]` | share of all inpatient stays beginning via the ED: same instant / within 1 h / within 24 h | 0.047 / 0.081 / 0.083 |
| `[ed]` | positives with an ED-initiated counting readmission, by index reason | CHF 27 of 30; CABG-history 1 of 45; aortic stenosis 0 of 20; aortic regurgitation 0 of 17; NSTEMI 0 of 7; drug abuse 0 of 7; no recorded reason 0 of 7; abnormal imaging 0 of 5; kidney transplant 1 of 3; total **30 of 151** |
| `[cabg]` | CABG-history index rows / lasting exactly 24 h / with procedures (positives among them) / the four bundle procedures on each | 401 / 401 / 391 (45) / 391 stays each |
| `[cabg]` | the 45 positives: readmitting stay same reason / exactly 24 h / zero procedures / ED-initiated / gap min-median-max | 43 / 44 / 1 / 1 / 0.7–16.6–28.7 d |
| `[cabg]` | CABG-history stays (patients) / with a later CABG-history stay / gap quantiles 10-25-50-75-90 (d) / share within 30 d | 769 (364) / 47 / 2.5, 9.3, 17.7, 24.9, 28.4 / 0.91 |
| `[chf]` | index rows / positives / median index LOS; readmitting stays same reason / ED-initiated / median gap / median LOS | 341 / 30 / 121 h; 27 / 27 / 12.0 d / 61 h |
| `[aortic]` | aortic-valve positives / readmitting stay is "History of aortic valve replacement" / gap min-median-max / within 72 h / ED-initiated | 37 / 30 / 3–30–648 h / 25 / 0 |
| `[aortic]` | positives in the [24,48) h gap bin / of which aortic-valve index | 25 / 13 |
| `[variant]` | A current | 9,284 rows, 151 positives; train 6,391 / 96, test 1,539 / 41 |
| `[variant]` | B (option 1) ED-initiated readmission | 9,282 rows, 30 (0.32%); train 6,390 / 21, test 1,539 / 9 |
| `[variant]` | C1 (option 2a) status-post reasons dropped as index | 8,791 rows, 106 (1.21%); train 6,188 / 80, test 1,381 / 18 |
| `[variant]` | C2 (option 2b) … and as readmitting stays | 8,791 rows, 65 (0.74%); train 6,188 / 43, test 1,381 / 16 |
| `[variant]` | D (option 3a) HF + AMI index cohorts | 610 rows, 37 (6.07%); train 506 / 25, test 97 / 12 |
| `[variant]` | D′ (option 3b) HF only | 341 rows, 30 (8.80%); train 290 / 20, test 44 / 10 |
| `[variant]` | E1 (option 4a) index must begin via the ED | 457 rows, 31 (6.78%); train 362 / 21, test 88 / 10 |
| `[variant]` | E2 (option 4b) index and readmission both via the ED | 457 rows, 27 (5.91%); train 362 / 18, test 88 / 9 |

```python
"""Slice 2b follow-up analysis: what the 151 remaining positives are, and four candidate label-v2 definitions, each MEASURED
(relabel -> death/censoring keep-rule -> chronological split). Needs measure_label.py (the recipe above) saved in the same
directory, plus data/gold/full_run_2b. Run from the repo root:  python label_residuals.py"""
import warnings
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from measure_label import CHEMO, LUNG, REF, REF_END, TEST_START, W, in_window_pairs, load, stay_flags
from readmission_risk.models.data import load_gold_table
from readmission_risk.models.split import chronological_group_split

warnings.filterwarnings("ignore")
CABG, CHF, AVR_HISTORY, NSTEMI = "399261000", "88805009", "1231000119100", "401314000"

enc, pat, proc = load()
inp, continuation, nonterminal = stay_flags(enc)
planned = set(proc.loc[proc.CODE.isin(CHEMO), "ENCOUNTER"]) & set(inp.Id)
inp["los_h"] = (inp.STOP - inp.START).dt.total_seconds() / 3600
inp["n_proc"] = inp.Id.map(proc.ENCOUNTER.value_counts()).fillna(0).astype(int)
gold = load_gold_table(Path("data/gold/full_run_2b"))

# an inpatient stay "begins via the ED" if the same patient has an emergency encounter starting at the SAME instant
ed = enc[enc.ENCOUNTERCLASS == "emergency"][["PATIENT", "START"]].rename(columns={"START": "ed_start"})
x = inp[["Id", "PATIENT", "START"]].merge(ed, on="PATIENT")
x["d_h"] = (x.START - x.ed_start).dt.total_seconds() / 3600
via_ed = set(x[x.d_h == 0].Id)
print(f"[ed] share of ALL inpatient stays beginning via the ED: same instant {len(via_ed) / len(inp):.3f}, "
      f"within 1h {x[(x.d_h >= 0) & (x.d_h <= 1)].Id.nunique() / len(inp):.3f}, within 24h {x[(x.d_h >= 0) & (x.d_h <= 24)].Id.nunique() / len(inp):.3f}")

pos_ids = set(gold[gold.is_readmitted == 1].encounter_id)
c, m = in_window_pairs(inp)
m = m[m.c_Id.isin(pos_ids) & ~m.a_Id.isin(planned) & ~m.a_Id.isin(continuation)].copy()
m["a_ed"] = m.a_Id.isin(via_ed)
first = m.sort_values("a_START").groupby("c_Id").head(1).copy()
first["gap_d"] = (first.a_START - first.c_STOP).dt.total_seconds() / 86400
first["index_reason"] = first.c_REASONDESCRIPTION.fillna("no recorded reason").str[:36]
any_ed = m.groupby("c_Id").a_ed.any()
first["any_ed"] = first.c_Id.map(any_ed)
tab = first.groupby("index_reason").agg(positives=("c_Id", "size"), readmitted_via_ED=("any_ed", "sum")).sort_values("positives", ascending=False)
print("[ed] positives whose readmitting stay begins via the ED, by index reason (top 9):"); print(tab.head(9).to_string())
print(f"[ed] ALL positives {len(first)}; with an ED-initiated counting readmission {int(first.any_ed.sum())}")

cab = gold[gold.admission_reason_code == CABG].merge(inp[["Id", "los_h", "n_proc"]], left_on="encounter_id", right_on="Id")
print(f"[cabg] index rows {len(cab)}; LOS exactly 24h: {int((cab.los_h.round(2) == 24).sum())}; with procedures {int((cab.n_proc > 0).sum())} (positives {int(cab[cab.n_proc > 0].is_readmitted.sum())}); "
      f"index stays containing the 4-procedure bundle: {proc[proc.ENCOUNTER.isin(set(cab.encounter_id))].groupby('CODE').ENCOUNTER.nunique().sort_values(ascending=False).head(4).tolist()}")
fc = first[first.c_REASONCODE == CABG]
print(f"[cabg] positives {len(fc)}: readmitting stay same reason {int((fc.a_REASONCODE == CABG).sum())}, exactly 24h {int((fc.a_los_h.round(2) == 24).sum())}, "
      f"zero procedures {int((fc.a_n_proc == 0).sum())}, ED-initiated {int(fc.any_ed.sum())}; gap min/median/max {fc.gap_d.min():.1f}/{fc.gap_d.median():.1f}/{fc.gap_d.max():.1f} d")
allc = inp[inp.REASONCODE == CABG].copy()
allc["next_reason"], allc["next_start"] = allc.groupby("PATIENT").REASONCODE.shift(-1), allc.groupby("PATIENT").START.shift(-1)
nx = allc[(allc.next_reason == CABG) & ((allc.next_start - allc.STOP).dt.total_seconds() > 0)].copy()
nx["gap_d"] = (nx.next_start - nx.STOP).dt.total_seconds() / 86400
print(f"[cabg] CABG-history stays {len(allc)} (patients {allc.PATIENT.nunique()}); with a LATER CABG-history stay {len(nx)}; "
      f"gap quantiles 10/25/50/75/90 {nx.gap_d.quantile([.1, .25, .5, .75, .9]).round(1).tolist()}; share within 30 d {(nx.gap_d <= 30).mean():.2f}")
chf = gold[gold.admission_reason_code == CHF].merge(inp[["Id", "los_h"]], left_on="encounter_id", right_on="Id")
fh = first[first.c_REASONCODE == CHF]
print(f"[chf] index rows {len(chf)}, positives {int(chf.is_readmitted.sum())}, median index LOS {chf.los_h.median():.0f} h; readmitting stays: same reason {int((fh.a_REASONCODE == CHF).sum())}, "
      f"ED-initiated {int(fh.any_ed.sum())}, median gap {fh.gap_d.median():.1f} d, median LOS {fh.a_los_h.median():.0f} h")

av = first[first.c_REASONCODE.isin(["60573004", "60234000"])]
print(f"[aortic] positives {len(av)}: readmitting stay is 'History of aortic valve replacement' {int(av.a_REASONDESCRIPTION.fillna('').str.startswith('History of aortic valve').sum())}, "
      f"gap min/median/max {av.gap_d.min() * 24:.0f}/{av.gap_d.median() * 24:.0f}/{av.gap_d.max() * 24:.0f} h, within 72 h {int((av.gap_d * 24 < 72).sum())}, ED-initiated {int(av.any_ed.sum())}")
b = first[(first.gap_d * 24 >= 24) & (first.gap_d * 24 < 48)]
print(f"[aortic] positives in the [24,48) h gap bin: {len(b)}, of which aortic-valve index {int(b.c_REASONCODE.isin(['60573004', '60234000']).sum())}")


def variant(name, *, pool_exclude=frozenset(), index_exclude=frozenset(), index_only=None, require_ed=False, index_require_ed=False):
    """Relabel under a candidate definition, re-apply the death/censoring keep-rule, then split and count."""
    cand, pairs = in_window_pairs(inp)
    cand = cand[~cand.c_Id.isin(planned) & ~cand.c_Id.isin(nonterminal) & ~cand.c_REASONCODE.isin(index_exclude)]
    if index_only is not None:
        cand = cand[cand.c_REASONCODE.isin(index_only)]
    if index_require_ed:
        cand = cand[cand.c_Id.isin(via_ed)]
    pairs = pairs[pairs.c_Id.isin(set(cand.c_Id)) & ~pairs.a_Id.isin(planned) & ~pairs.a_Id.isin(continuation) & ~pairs.a_REASONCODE.isin(pool_exclude)]
    if require_ed:
        pairs = pairs[pairs.a_Id.isin(via_ed)]
    cand = cand.merge(pat, left_on="c_PATIENT", right_on="Id", how="left")
    deadline = cand.c_STOP + pd.Timedelta(days=W)
    cand["y"] = cand.c_Id.isin(set(pairs.c_Id)).astype(int)
    keep = cand[(cand.y == 1) | ((cand.DEATHDATE.isna() | (cand.DEATHDATE > deadline)) & (deadline < REF_END))]
    g = gold.drop(columns=["is_readmitted"]).merge(keep[["c_Id", "y"]].rename(columns={"c_Id": "encounter_id"}), on="encounter_id")
    g["is_readmitted"] = g.y.astype("int8")
    try:
        s = chronological_group_split(g.drop(columns=["y"]), reference_date=REF, test_start_date=TEST_START).summary
        print(f"[variant] {name:62s} rows {len(g):5d} positives {int(g.is_readmitted.sum()):3d} ({100 * g.is_readmitted.mean():.2f}%) | train {s['n_train']}/{s['n_train_positive']} test {s['n_test']}/{s['n_test_positive']}")
    except ValueError as e:
        print(f"[variant] {name:62s} rows {len(g):5d} positives {int(g.is_readmitted.sum()):3d} | split impossible: {str(e)[:60]}")


variant("A  current label (slice 2b)")
variant("B  a readmission must begin via the ED", require_ed=True)
variant("C1 drop 'history of ...' status reasons as INDEX", index_exclude={CABG, AVR_HISTORY})
variant("C2 drop them as index AND as readmission stays", index_exclude={CABG, AVR_HISTORY}, pool_exclude={CABG, AVR_HISTORY})
variant("D  index restricted to HF + AMI cohorts (CHF, NSTEMI)", index_only={CHF, NSTEMI})
variant("D' HF cohort only (CHF)", index_only={CHF})
variant("E1 index must itself begin via the ED (acute-admission cohort)", index_require_ed=True)
variant("E2 index AND readmission both begin via the ED", index_require_ed=True, require_ed=True)
```
