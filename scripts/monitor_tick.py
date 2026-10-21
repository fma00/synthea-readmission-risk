#!/usr/bin/env python
"""The monitoring tick's command line (v2 slice 7): clock, evaluate, the laptop catch-up, and two offline checks.

The simulated clock advances one week per tick through the champion's held-out 2023-2026 discharges; each tick scores
the week (scripts/score_to_bigquery.py) and evaluates the 13-week cohort whose labels have matured. Exit codes: 0
resolved / succeeded (an alert is a finding, not a failure); 1 refused; 2 failed; 4 not recorded. Design:
notes/eg-new-feature/v2-slice7-monitoring-tick-ledger-2026-10-18.md.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from readmission_risk import gcp
from readmission_risk.monitoring.config import (
    DEFAULT_CONFIG_PATH,
    read_monitoring_config,
)
from readmission_risk.monitoring.tick import (
    ClockRequest,
    ClockResult,
    EvaluateRequest,
    EvaluateResult,
    evaluate_tick,
    prepare_tick,
)

app = typer.Typer(add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False, help=__doc__.split("\n\n")[1])

EXIT_REFUSED, EXIT_FAILED, EXIT_NOT_RECORDED = 1, 2, 4
MAX_CATCH_UP_WEEKS = 20
REPO = "fma00/synthea-readmission-risk"
GROUP_WORKFLOWS = ("monitor.yml", "score.yml")  # they share the concurrency group "score"


def _in_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def _append(env: str, text: str) -> None:
    path = os.environ.get(env)
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def _exit_code(status: str | None, recorded: bool, error: BaseException | None) -> int:
    if error is None and recorded:
        return 0
    if not recorded:
        return EXIT_NOT_RECORDED
    return EXIT_REFUSED if status == "refused" else EXIT_FAILED


def _table(rows: list[tuple[str, object]]) -> str:
    body = "\n".join(f"| {k} | {str('-' if v is None else v).replace('|', chr(92) + '|')} |" for k, v in rows)
    return f"| field | value |\n|---|---|\n{body}\n"


def require_clean_main(run=None) -> None:
    """Laptop runs that write ticks read deploy/*.json and prereg.yaml from the working tree, which must be exactly main."""
    run = run or subprocess.run

    def git(*args: str) -> str:
        done = run(["git", *args], capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise ValueError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
        return done.stdout.strip()

    if git("status", "--porcelain"):
        raise ValueError("the working tree has uncommitted changes; laptop ticks run only from a clean checkout of main")
    git("fetch", "origin", "main")
    if git("rev-parse", "HEAD") != git("rev-parse", "origin/main"):
        raise ValueError("HEAD is not origin/main; laptop ticks run only from a clean checkout of main")


def require_no_running_workflow(run=None) -> None:
    run = run or subprocess.run
    for workflow in GROUP_WORKFLOWS:
        done = run(["gh", "run", "list", "-R", REPO, "--workflow", workflow, "--json", "status", "--limit", "20"],
                   capture_output=True, text=True, check=False)  # fmt: skip
        if done.returncode != 0:
            raise ValueError(f"gh run list failed (needed to rule out an overlapping Actions tick): {done.stderr.strip()}")
        busy = [r["status"] for r in json.loads(done.stdout or "[]") if r.get("status") != "completed"]
        if busy:
            raise ValueError(f"a {workflow} run is {busy[0]}; never catch up while an Actions tick may run")


def _report_clock(result: ClockResult) -> int:
    if result.error is None and result.skip_reason:
        print(f"skip: {result.skip_reason}")
        _append("GITHUB_OUTPUT", "skip=true\n")
        _append("GITHUB_STEP_SUMMARY", "## Monitoring clock (simulated)\n\n" + _table(
            [("event", "schedule"), ("status", "skipped"), ("reason", result.skip_reason)]))  # fmt: skip
        return 0
    if result.error is None:
        print(f"sim_date {result.sim_date:%Y%m%d} cadence {result.cadence}")
        _append("GITHUB_OUTPUT", f"sim_date={result.sim_date:%Y%m%d}\ncadence={result.cadence}\nskip=false\n")
        _append("GITHUB_STEP_SUMMARY", "## Monitoring clock (simulated)\n\n" + _table(
            [("sim date", result.sim_date.isoformat()), ("cadence", result.cadence), ("status", "resolved")]))  # fmt: skip
        return 0
    record = result.record
    print(f"ERROR: {result.error}", file=sys.stderr)
    _append("GITHUB_STEP_SUMMARY", "## Monitoring clock (simulated)\n\n" + _table(
        [("status", record.status if record else "not recorded"), ("error", str(result.error))]))  # fmt: skip
    return _exit_code(record.status if record else None, result.recorded, result.error)


def _evaluation_json(result: EvaluateResult) -> dict:
    r = result.record
    keys = ("status", "failed_stage", "verdict", "cadence", "oe_ratio", "oe_ci_low", "oe_ci_high", "observed", "expected",
            "n_matured", "monitoring_run_id", "scoring_run_id", "band_low", "band_high", "min_positives_floor")  # fmt: skip
    out = {k: getattr(r, k) for k in keys}
    out["sim_date"] = r.sim_date.isoformat() if r.sim_date else None
    return out


def _report_evaluate(result: EvaluateResult, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "evaluation.json").write_text(json.dumps(_evaluation_json(result), indent=2, sort_keys=True) + "\n")
    r = result.record
    ratio = "-" if r.oe_ratio is None else f"{r.oe_ratio:.3f} [{r.oe_ci_low:.3f}, {r.oe_ci_high:.3f}]"
    oe = "-" if r.observed is None else f"{r.observed} / {r.expected:.1f}"
    status = r.status + (f" ({r.failed_stage})" if r.failed_stage else "")
    _append("GITHUB_STEP_SUMMARY", "## Monitoring tick (simulated clock)\n\n" + _table([
        ("sim date", r.sim_date), ("cadence", r.cadence), ("status", status), ("verdict", r.verdict),
        ("O/E [95% CI]", ratio), ("observed / expected", oe), ("n matured", r.n_matured),
        ("coverage", f"{r.coverage_found}/{r.coverage_required}" if r.coverage_required else None),
        ("scoring run", r.scoring_run_id), ("recorded", "yes" if result.recorded else "no"),
        ("error", f"{r.error_type}: {r.error_message}" if r.error_type else None),
    ]))  # fmt: skip
    _append("GITHUB_OUTPUT", f"status={r.status}\nverdict={r.verdict or ''}\n")
    print(f"Tick {r.monitoring_run_id}: {status}, verdict {r.verdict or '-'} (recorded: {'yes' if result.recorded else 'no'})")
    if result.error is not None:
        print(f"ERROR: {result.error}", file=sys.stderr)
    return _exit_code(r.status, result.recorded, result.error)


def _event_name() -> str:
    return (os.environ.get("GITHUB_EVENT_NAME") or "github_actions") if _in_actions() else "laptop"


@app.command()
def clock(
    sim_date: Annotated[str | None, typer.Option(help="YYYYMMDD on the grid at or before the head (a replay).")] = None,
    config: Annotated[Path, typer.Option(help="The monitoring config.")] = DEFAULT_CONFIG_PATH,
    impersonate_service_account: Annotated[str | None, typer.Option(help="Laptop: the monitor SA.")] = None,
) -> None:
    """Resolve the tick's simulated date and cadence (writes a monitoring row only on a refusal)."""
    gcp.configure(impersonate_service_account=impersonate_service_account)
    requested = sim_date or os.environ.get("REQUESTED_SIM_DATE") or None
    request = ClockRequest(_event_name(), os.environ.get("SCHEDULE") or None, requested, config_path=config,
                           requested_cadence=os.environ.get("REQUESTED_CADENCE") or None)  # fmt: skip
    raise typer.Exit(code=_report_clock(prepare_tick(request)))


@app.command()
def evaluate(
    sim_date: Annotated[str, typer.Option(help="YYYYMMDD from the clock stage.")],
    cadence: Annotated[str, typer.Option(help="burn_in, weekly, dispatch or catch_up.")],
    scoring_run_id: Annotated[str, typer.Option(help="The score stage's scoring run ('' if none).")] = "",
    score_result: Annotated[str, typer.Option(help="The score job's result (success, failure, cancelled, skipped).")] = "success",
    out_dir: Annotated[Path, typer.Option(help="Where evaluation.json is written.")] = Path("monitor_out"),
    config: Annotated[Path, typer.Option(help="The monitoring config.")] = DEFAULT_CONFIG_PATH,
    impersonate_service_account: Annotated[str | None, typer.Option(help="Laptop: the monitor SA.")] = None,
) -> None:
    """Evaluate the tick's cohorts and record one serving.monitoring row."""
    if not _in_actions():
        try:
            require_clean_main()
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            raise typer.Exit(code=EXIT_REFUSED) from None
    gcp.configure(impersonate_service_account=impersonate_service_account)
    request = EvaluateRequest(sim_date=sim_date, cadence=cadence, scoring_run_id=scoring_run_id or None,
                              score_result=score_result, config_path=config)  # fmt: skip
    raise typer.Exit(code=_report_evaluate(evaluate_tick(request), out_dir))


def _check_catch_up_args(weeks: int) -> None:
    if _in_actions():
        raise ValueError("catch-up runs on the laptop only")
    if type(weeks) is not int or not 1 <= weeks <= MAX_CATCH_UP_WEEKS:
        raise ValueError(f"--weeks must be in [1, {MAX_CATCH_UP_WEEKS}]; got {weeks}")
    _check_catch_up_window()


def _check_catch_up_window() -> None:
    """The weekly retry slots fire Monday-Wednesday; six Thursday hours cover a delayed Wednesday 23:17 slot."""
    now = datetime.now(UTC)
    if now.weekday() in (0, 1, 2) or (now.weekday() == 3 and now.hour < 6):
        raise ValueError("catch-up does not run from Monday 00:00 to Thursday 06:00 (UTC): the weekly retry slots (and "
                         "their delayed runs) may be active")  # fmt: skip


@app.command("catch-up")
def catch_up(
    weeks: Annotated[int, typer.Option(help=f"Ticks to run, 1..{MAX_CATCH_UP_WEEKS}.")],
    scorer_sa: Annotated[str, typer.Option(help="The scorer service account.")],
    monitor_sa: Annotated[str, typer.Option(help="The monitor service account.")],
    config: Annotated[Path, typer.Option(help="The monitoring config.")] = DEFAULT_CONFIG_PATH,
) -> None:
    """Laptop only: run N ticks (clock as monitor, score as scorer, evaluate as monitor), cadence catch_up. Refused from Monday 00:00 to Thursday 06:00 UTC (the weekly retry slots)."""
    from readmission_risk.warehouse.run import BigQueryRunRequest, score_to_bigquery

    try:
        _check_catch_up_args(weeks)
        require_clean_main()
        require_no_running_workflow()
        settings = read_monitoring_config(config)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(code=EXIT_REFUSED) from None
    for _ in range(weeks):
        try:  # again before every week: a long catch-up must not run into a retry slot or an Actions tick
            _check_catch_up_window()
            require_no_running_workflow()
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            raise typer.Exit(code=EXIT_REFUSED) from None
        gcp.configure(impersonate_service_account=monitor_sa)
        clock_result = prepare_tick(ClockRequest("laptop", None, None, config_path=config))
        if clock_result.error is not None:
            raise typer.Exit(code=_report_clock(clock_result))
        sim = clock_result.sim_date
        gcp.configure(impersonate_service_account=scorer_sa)
        scored = score_to_bigquery(BigQueryRunRequest(
            gold_uri=settings.gold_uri, run_date=f"{sim:%Y%m%d}", top_n=settings.top_n, window_days=settings.window_days))  # fmt: skip
        if scored.error is not None:
            print(f"score: {type(scored.error).__name__}: {scored.error}", file=sys.stderr)
        gcp.configure(impersonate_service_account=monitor_sa)
        result = evaluate_tick(EvaluateRequest(
            sim_date=f"{sim:%Y%m%d}", cadence="catch_up", scoring_run_id=scored.record.scoring_run_id,
            score_result="success" if scored.record.status == "succeeded" else "failure", config_path=config))  # fmt: skip
        r = result.record
        print(f"{sim.isoformat()} {r.status} {r.verdict or '-'}")
        code = _exit_code(r.status, result.recorded, result.error)
        if code:
            if result.error is not None:
                print(f"ERROR: {result.error}", file=sys.stderr)
            raise typer.Exit(code=code)


@app.command("preflight")
def preflight_command(
    gold_dir: Annotated[Path, typer.Option(help="A local copy of the monitored gold (make gold-200k).")],
    bundle: Annotated[Path, typer.Option(help="The monitored bundle, local.")],
    config: Annotated[Path, typer.Option(help="The monitoring config.")] = DEFAULT_CONFIG_PATH,
) -> None:
    """Offline: does the completeness guard accept every grid date's 7-day window? (no model is loaded)"""
    from readmission_risk.monitoring.preflight import preflight

    lines = preflight(gold_dir, bundle, read_monitoring_config(config))
    for line in lines:
        print(f"{line.sim_date:%Y%m%d} {line.n_rows}" if line.refusal is None else f"{line.sim_date:%Y%m%d} REFUSED {line.refusal}")
    refused = [line for line in lines if line.refusal is not None]
    print(f"{len(lines) - len(refused)} of {len(lines)} grid dates accepted")
    raise typer.Exit(code=1 if refused else 0)


@app.command("simulate")
def simulate_command(
    gold_dir: Annotated[Path, typer.Option(help="A local copy of the monitored gold.")],
    bundle: Annotated[Path, typer.Option(help="The monitored bundle, local.")],
    out: Annotated[Path, typer.Option(help="CSV with one row per grid date.")],
    config: Annotated[Path, typer.Option(help="The monitoring config.")] = DEFAULT_CONFIG_PATH,
    prereg: Annotated[Path, typer.Option(help="prereg.yaml (the alert rule).")] = Path("prereg.yaml"),
) -> None:
    """Offline: score every grid date through the scorer and evaluate each cohort (the sanity check and L10's oracle)."""
    from readmission_risk.monitoring.preflight import simulate
    from readmission_risk.monitoring.rule import alert_rule_from_prereg
    from readmission_risk.ops.prereg import load_prereg

    try:
        frame = simulate(gold_dir, bundle, read_monitoring_config(config), alert_rule_from_prereg(load_prereg(prereg)))
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise typer.Exit(code=2) from None
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)
    full = frame[frame["verdict"] != "incomplete_cohort"]
    counts = full["verdict"].value_counts().to_dict()
    print(f"wrote {len(frame)} rows to {out}; verdicts over full cohorts: {counts}")
    if not full.empty:
        print(f"O/E range {full['ratio'].min():.3f}-{full['ratio'].max():.3f}; observed {full['observed'].min()}-{full['observed'].max()}; "
              f"n_label_after_sim max {full['n_label_after_sim'].max()}")  # fmt: skip


if __name__ == "__main__":
    app()

