"""Slice 5b L7c: compare two Synthea CSV exports table by table, ignoring row order.

Per table: header equality, line count, and an order-independent digest (sum of per-line sha256 mod 2**256), streamed.
payers.csv drops AMOUNT_COVERED, AMOUNT_UNCOVERED, REVENUE, QOLS_AVG (thread-order floating-point aggregates, measured
non-deterministic in slice 5's design check). Usage: python -I notes/eg-new-feature/v2-slice5b-pilot/compare_raw.py <dir_a/csv> <dir_b/csv>
"""

from __future__ import annotations

import csv
import hashlib
import io
import sys
from pathlib import Path

TABLES = ["patients.csv", "encounters.csv", "conditions.csv", "medications.csv", "procedures.csv", "careplans.csv",
          "observations.csv", "payers.csv", "providers.csv", "organizations.csv"]  # fmt: skip
PAYERS_DROP = {"AMOUNT_COVERED", "AMOUNT_UNCOVERED", "REVENUE", "QOLS_AVG"}
MOD = 2**256


def digest(path: Path) -> tuple[str, int, str]:
    total = 0
    n = 0
    with path.open("r", encoding="utf-8", newline="") as fh:
        if path.name == "payers.csv":
            reader = csv.reader(fh)
            header = next(reader)
            keep = [i for i, c in enumerate(header) if c not in PAYERS_DROP]
            head = ",".join(header[i] for i in keep)
            for row in reader:
                buf = io.StringIO()
                csv.writer(buf).writerow([row[i] for i in keep])
                total = (total + int.from_bytes(hashlib.sha256(buf.getvalue().encode()).digest(), "big")) % MOD
                n += 1
        else:
            head = fh.readline().rstrip("\r\n")
            for line in fh:
                total = (total + int.from_bytes(hashlib.sha256(line.rstrip("\r\n").encode()).digest(), "big")) % MOD
                n += 1
    return head, n, f"{total:064x}"


def main(a: Path, b: Path) -> int:
    all_equal = True
    for table in TABLES:
        ha, na, da = digest(a / table)
        hb, nb, db = digest(b / table)
        equal = (ha, na, da) == (hb, nb, db)
        all_equal &= equal
        note = " (4 aggregate columns dropped)" if table == "payers.csv" else ""
        print(f"{'EQUAL' if equal else 'DIFFERENT'} {table}{note}: rows {na} vs {nb}; header {'same' if ha == hb else 'differs'}; "
              f"digest {da[:16]}... vs {db[:16]}...")
    print("ALL TABLES EQUAL" if all_equal else "SOME TABLES DIFFER")
    return 0 if all_equal else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
