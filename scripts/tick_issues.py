#!/usr/bin/env python3
"""Opens or updates ONE GitHub issue per kind after a monitoring tick (the issues job of monitor.yml; v2 slice 7).

A failure issue when any tick job failed or was cancelled; an alert issue when the evaluation's verdict is alert. An open
issue with the kind's label is commented on; otherwise one is created. Issues are never closed here. Standard library
only and self-contained (the runner's system Python, nothing installed); `gh` with GH_TOKEN and GITHUB_REPOSITORY from
the environment. Exit 0, or 1 on any gh error. Design:
notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md (CLIs > tick_issues.py).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

JOBS = ("clock", "score", "evaluate", "ledger-render", "ledger-commit")
FAILURE = ("Monitoring tick failing (simulated clock)", "monitoring-failure", "d73a4a")
ALERT = ("Calibration alert: observed/expected outside band (simulated clock)", "monitoring-alert", "fbca04")


@dataclass(frozen=True)
class IssueAction:
    kind: str
    title: str
    label: str
    color: str
    body: str


def plan_issue_actions(job_results: dict, evaluation: dict | None, run_url: str) -> list[IssueAction]:
    results = {job: (job_results.get(job) or {}).get("result", "missing") for job in JOBS}
    sim = (evaluation or {}).get("sim_date") or "unknown"
    actions = []
    if any(result in ("failure", "cancelled") for result in results.values()):
        lines = [f"Run: {run_url}", f"Simulated date: {sim}", "", "| job | result |", "|---|---|"]
        lines += [f"| {job} | {result} |" for job, result in results.items()]
        lines += ["", "Not auto-closed: close it after a green tick."]
        actions.append(IssueAction("failure", *FAILURE, "\n".join(lines)))
    if evaluation and evaluation.get("verdict") == "alert":
        lines = [
            f"Run: {run_url}", f"Simulated date: {sim}",
            f"Observed / expected: {evaluation.get('observed')} / {evaluation.get('expected')}",
            f"O/E: {evaluation.get('oe_ratio')} (95% CI {evaluation.get('oe_ci_low')} to {evaluation.get('oe_ci_high')}), "
            f"outside the pre-registered band [{evaluation.get('band_low')}, {evaluation.get('band_high')}] with the CI "
            "excluding 1 (prereg.yaml monitoring.alert_rule).",
            "", "Synthetic data on a simulated clock; not for clinical use. Not auto-closed.",
        ]  # fmt: skip
        actions.append(IssueAction("alert", *ALERT, "\n".join(lines)))
    return actions


def _gh(args: list[str], repository: str, run) -> str:
    done = run(["gh", *args, "--repo", repository], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {done.stderr.strip()}")
    return done.stdout


def apply(actions: list[IssueAction], repository: str, *, run=subprocess.run) -> list[str]:
    taken = []
    for action in actions:
        _gh(["label", "create", action.label, "--force", "--color", action.color], repository, run)
        existing = json.loads(_gh(["issue", "list", "--label", action.label, "--state", "open", "--json", "number",
                                   "--limit", "1"], repository, run) or "[]")  # fmt: skip
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
            handle.write(action.body + "\n")
        try:
            if existing:
                number = existing[0]["number"]
                _gh(["issue", "comment", str(number), "--body-file", handle.name], repository, run)
                taken.append(f"commented #{number}")
            else:
                url = _gh(["issue", "create", "--title", action.title, "--label", action.label, "--body-file", handle.name],
                          repository, run).strip()  # fmt: skip
                taken.append(f"created #{url.rsplit('/', 1)[-1]}")
        finally:
            os.unlink(handle.name)
    return taken


def _repository() -> str:
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository or not os.environ.get("GH_TOKEN"):
        raise RuntimeError("GITHUB_REPOSITORY and GH_TOKEN must be set")
    return repository


def main(argv: list[str] | None = None, *, run=subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--results-json", required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args(argv)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    try:
        repository = _repository()
        evaluation = json.loads(args.evaluation.read_text(encoding="utf-8")) if args.evaluation.is_file() else None
        taken = apply(plan_issue_actions(json.loads(args.results_json), evaluation, args.run_url), repository, run=run)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        line = f"Issues: failed: {exc}"
        code = 1
    else:
        line = f"Issues: {', '.join(taken) or 'none'}"
        code = 0
    print(line)
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
