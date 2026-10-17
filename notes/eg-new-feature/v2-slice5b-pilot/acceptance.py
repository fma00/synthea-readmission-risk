"""Slice 5b: L6 (cross-environment fingerprint) and pilot acceptance A1-A6 on the Dataproc-built pilot gold.

Run from the repo root: .venv/bin/python notes/eg-new-feature/v2-slice5b-pilot/acceptance.py (needs ADC + CLOUDSDK_CORE_PROJECT).
Read-only: downloads the gold through the scorer's own materialized_gold_dir and the raw generation_summary.json.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import date

from readmission_risk.models.data import load_gold_table_with_metadata
from readmission_risk.models.split import chronological_group_split
from readmission_risk.pipeline.fingerprint import rowhash_fingerprint
from readmission_risk.warehouse.gold_source import materialized_gold_dir

GOLD = "gs://synthea-readmission-risk-gold/v2/pilot_20k"
RAW_SUMMARY = "gs://synthea-readmission-risk-raw/v2/pilot_20k/generation_summary.json"
V1_LOCAL = "data/gold/full_run_2b"
ok = True


def check(name: str, passed: bool, detail: str) -> None:
    global ok
    ok &= passed
    print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")


print(f"python {sys.version.split()[0]}")
import pandas as pd  # noqa: E402

print(f"pandas {pd.__version__}")

# L6 -- the loader recomputes and checks the v2 fingerprint itself; assert it explicitly too (no vacuous pass on v1 metadata)
with materialized_gold_dir(GOLD) as gold_dir:
    frame, meta = load_gold_table_with_metadata(gold_dir)
check("L6 metadata_version", meta.metadata_version == 2, str(meta.metadata_version))
check("L6 definition", meta.gold_fingerprint_definition == "spark-rowhash-v1", str(meta.gold_fingerprint_definition))
local_fp = rowhash_fingerprint(frame)
check("L6 fingerprint", local_fp == meta.gold_fingerprint, f"python {local_fp} vs dataproc {meta.gold_fingerprint} (spark {meta.spark_version})")

# A1-A3
check("A1 n_rows > 0", meta.n_rows > 0 and len(frame) == meta.n_rows, f"metadata {meta.n_rows}, frame {len(frame)}")
check("A2 n_planned_stays > 0", meta.n_planned_stays > 0, str(meta.n_planned_stays))
rate = meta.n_positive / meta.n_rows
check("A3 positive rate in [0.5%, 5%]", 0.005 <= rate <= 0.05, f"{meta.n_positive}/{meta.n_rows} = {rate:.4%} (frame sum {int(frame['is_readmitted'].sum())})")

# A4 -- the 24 gold columns (frame as loaded; the label is is_readmitted)
print(f"columns ({len(frame.columns)}): {list(frame.columns)}")
all_null = [c for c in frame.columns if frame[c].isna().all()]
check("A4 no all-null column", not all_null and len(frame.columns) == 24, f"all-null {all_null}; {len(frame.columns)} columns")
null_frac = frame["payer_ownership"].isna().mean()
check("A4 payer_ownership null fraction <= 10%", null_frac <= 0.10, f"{null_frac:.4%}")
v1, _ = load_gold_table_with_metadata(__import__("pathlib").Path(V1_LOCAL))
print(f"  comparison: v1 {V1_LOCAL} payer_ownership null fraction {v1['payer_ownership'].isna().mean():.4%}; "
      f"v1 positive rate {v1['is_readmitted'].mean():.4%} ({int(v1['is_readmitted'].sum())}/{len(v1)})")
print("  per-column null fraction (pilot):", {c: round(float(frame[c].isna().mean()), 4) for c in frame.columns if frame[c].isna().any()})

# A5 -- generation_summary equality and the prereg in force at generation
raw_summary = json.loads(subprocess.run(["gcloud", "storage", "cat", RAW_SUMMARY], check=True, capture_output=True, text=True).stdout)
check("A5 generation_summary equal", raw_summary == meta.generation_summary, "gold metadata vs raw generation_summary.json")
prov = meta.generation_summary["provenance"]
committed = subprocess.run(["git", "show", f"{prov['prereg_commit']}:prereg.yaml"], check=True, capture_output=True).stdout
check("A5 prereg_sha256", hashlib.sha256(committed).hexdigest() == prov["prereg_sha256"],
      f"sha256(git show {prov['prereg_commit'][:12]}:prereg.yaml) vs {prov['prereg_sha256']}")

# A6 -- pilot_test_window_positives per power.population_adjustment, with this prereg's split settings
split = chronological_group_split(
    frame, reference_date=date(2026, 10, 12), test_start_date=date(2023, 1, 1), readmission_window_days=30, train_start_date=None
)
s = split.summary
print("A6 split summary:", json.dumps(s, sort_keys=True))
n_test_pos = int(s["n_test_positive"])
projected = n_test_pos * 200000 / 20000
print(f"A6 pilot_test_window_positives = {n_test_pos}; projected main_200k test positives = {projected:.0f} "
      f"(trigger_below 500, min_test_positives 400)")
if projected >= 500:
    print("A6 population rule outcome: no change (projection >= 500)")
else:
    k = 50000
    pop = 200000
    while n_test_pos * pop / 20000 < 500 and pop < 400000:
        pop += k
    print(f"A6 population rule outcome: TRIGGERED -> raise main_200k.population to {pop} via a logged deviation (cap 400000)")
check("A6 recorded", True, "see above")

print("ALL PASS" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
