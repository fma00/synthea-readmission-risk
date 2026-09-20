from __future__ import annotations

from readmission_risk.scoring.constants import DEMO_NOTICE
from readmission_risk.scoring.ranking import ObservedSummary
from readmission_risk.scoring.report import format_report
from tests.scoring.helpers import make_result

_IDS = [f"enc-000{i}" for i in range(1, 6)]


def _table_lines(report: str) -> list[str]:
    lines = report.splitlines()
    heading = lines.index("Top 3 of 5 scored discharges:")
    wrote = next(i for i, line in enumerate(lines) if line.startswith("Wrote "))
    return [line for line in lines[heading + 1 : wrote] if "enc-" in line]  # header/blank/ruled lines are ignored


def test_report_contents_and_order():
    report = format_report(make_result())
    lines = report.splitlines()
    assert lines[0] == DEMO_NOTICE
    assert "model: logistic_regression run_id=run-abc (trained at commit 0123456, working tree clean)" in lines
    assert (
        "window: 2026-03-08T00:00:00+00:00 to 2026-03-11T00:00:00+00:00 (end exclusive), 5 held-out discharges scored" in lines
    )
    assert "Top 3 of 5 scored discharges:" in lines
    assert any(line == "Wrote out/run_date=20260310/predictions.parquet" for line in lines)

    table = _table_lines(report)
    assert len(table) == 3
    assert [next(i for i in _IDS if i in line) for line in table] == ["enc-0003", "enc-0001", "enc-0005"]
    assert "enc-0002" not in report and "enc-0004" not in report  # not in the Top-N, so nowhere in the report
    for line, risk in zip(table, ["0.5000", "0.4000", "0.3000"], strict=True):
        assert risk in line
        assert "2026-03-0" in line  # a YYYY-MM-DD discharge date (make_batch discharges on 2026-03-01 ... -05)

    rank1, rank2, rank3 = table
    assert "d" * 48 in rank1
    assert not any(tail in rank1 for tail in ("d" * 48 + "T", "d" * 48 + "...", "d" * 48 + "…"))  # exact truncation at 48
    assert "(none)" in rank2  # a null description
    assert "e" * 48 in rank3  # exactly 48 characters is printed whole (kills truncation at 47)
    assert "Observed outcomes" not in report


def test_report_observed_block_and_missing_git_metadata():
    observed = ObservedSummary(
        n_batch=5, n_positive_batch=0, batch_positive_rate=0.0, n_top=3, n_positive_top=0, precision_at_n=0.0, lift=None
    )
    report = format_report(make_result(git_commit=None, git_dirty=None, observed=observed))
    assert "trained at commit unknown, working tree unknown" in report  # no crash on None[:7]
    assert "Observed outcomes (opt-in development-set sanity check; NOT written to the output file;" in report
    assert "lift n/a" in report
    assert "Counts this small are not evidence of model quality." in report

    with_lift = ObservedSummary(
        n_batch=5, n_positive_batch=1, batch_positive_rate=0.2, n_top=3, n_positive_top=1, precision_at_n=1 / 3, lift=(1 / 3) / 0.2
    )
    assert "lift 1.7" in format_report(make_result(observed=with_lift))
