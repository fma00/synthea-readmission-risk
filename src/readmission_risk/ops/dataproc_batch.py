"""The Big Join on Dataproc Serverless runtime 3.0 (v2 PRD FR13 pilot, FR14 cap): preflight, submit (maxExecutors=7, standard
tier, ttl 2 h, the dedicated runtime SA and subnet), wait, and record wall-clock and DCU-hours. Every GCP call is a gcloud argv
run through ops.gcloud. See notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md, Interfaces section 11
("Big Join batch submitter"). Before submitting it waits for IAM propagation when the Admin Activity audit log shows a recent
IAM change (notes/eg-new-feature/ops-iam-propagation-and-interrupt-2026-10-12.md). Exit codes: 0 succeeded; 1 refused
(including IAM still changing after 3 propagation waits); 2 batch failed or cancelled (also when the usage wait was
interrupted after such a batch), or monitoring failed after submission (the batch may still be running, bounded by its ttl;
the message gives the describe and cancel commands); 130 interrupted (during the IAM propagation wait or its re-check: nothing submitted; while polling: the batch
keeps running, bounded by its ttl; during the usage wait after a SUCCEEDED batch: record written).
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .gcloud import (
    PROJECT,
    GcloudError,
    Runner,
    list_labelled,
    list_objects,
    parse_gcloud_time,
    run_json,
    run_ok,
)
from .generation_vm import (
    EXIT_FAILED,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_REFUSED,
    MAIN_RUN,
    REGION,
    SUBNET_NAME,
    Artifact,
    LaunchContext,
    Refused,
    active_batches,
    check_quota,
    expected_raw_layout,
    gold_prefix,
    raw_prefix,
    run_dashed,
    sha256_file,
    upload_artifact,
)
from .iam_propagation import (
    IAM_PROPAGATION_SECONDS,
    IamStillChanging,
    wait_for_iam_propagation,
)
from .prereg import (
    Prereg,
    PreregError,
    PreregRecord,
    check_prereg_against_code,
    check_prereg_committed,
    load_prereg,
)
from .spark_zip import build_spark_zip

RUNTIME_SA = f"dataproc-runtime@{PROJECT}.iam.gserviceaccount.com"
SUBNET = f"projects/{PROJECT}/regions/{REGION}/subnetworks/{SUBNET_NAME}"
RUNTIME_VERSION = "3.0"
MAX_EXECUTORS = 7
BATCH_TTL = "2h"
# FR14: driver + 7 executors; the standard tier's per-node disk default is to be confirmed on Google's Serverless properties
# page before the first batch (the 3200 GB figure is the PRD's)
BATCH_DISK_GB = 3200
BATCH_VCPUS = 32
SPARK_PROPERTIES = {
    "dataproc.tier": "standard",
    "spark.dynamicAllocation.maxExecutors": str(MAX_EXECUTORS),
    "spark.executor.cores": "4",
    "spark.driver.cores": "4",
    "spark.sql.shuffle.partitions": "128",
}
MAIN_SCRIPT = Path("scripts/run_big_join.py")
TERMINAL_STATES = ("SUCCEEDED", "FAILED", "CANCELLED")
USAGE_WAIT_SECONDS = 600
NEVER_RAN_STATES = ("FAILED", "CANCELLED")  # a SUCCEEDED batch certainly ran
PERMISSION_FAILURE_MARKER = "Permission validation failed"  # Dataproc's pre-check message prefix (5b, 2026-10-12)
PERMISSION_HINT = (
    "HINT: Dataproc's permission pre-check refused the batch before it ran (no cost). If an IAM role or binding for "
    f"{RUNTIME_SA} changed in the last ~8 minutes (e.g. the dataprocRuntimeWorker custom role), it may not have propagated "
    "yet: wait and resubmit. Otherwise grant the listed permissions in infra/iam.tf. See docs/setup.md, 'Lessons from the "
    "first live run'."
)


@dataclass(frozen=True)
class SubmitOptions:
    run: str
    allow_main_run: bool = False
    dry_run: bool = False
    poll_seconds: float = 30.0


@dataclass(frozen=True)
class BatchUsage:
    state: str
    runtime_version: str | None
    wall_seconds: float | None
    dcu_hours: float | None
    shuffle_gb_hours: float | None



def parse_batch_usage(describe: dict) -> BatchUsage:
    """`gcloud dataproc batches describe --format=json` -> BatchUsage. int64 fields may come as JSON strings; usage is None until
    Dataproc has computed it (runtimeInfo.approximateUsage appears after completion)."""
    usage = (describe.get("runtimeInfo") or {}).get("approximateUsage")
    created, ended = parse_gcloud_time(describe.get("createTime")), parse_gcloud_time(describe.get("stateTime"))
    milli_dcu = usage.get("milliDcuSeconds") if usage else None
    shuffle = usage.get("shuffleStorageGbSeconds") if usage else None
    return BatchUsage(
        state=describe.get("state", "UNKNOWN"),
        runtime_version=(describe.get("runtimeConfig") or {}).get("version"),
        wall_seconds=(ended - created).total_seconds() if created and ended else None,
        dcu_hours=int(milli_dcu) / 3.6e6 if milli_dcu is not None else None,
        shuffle_gb_hours=int(shuffle) / 3600 if shuffle is not None else None,
    )


def never_ran(describe: dict) -> bool:
    """True when the batch ended FAILED or CANCELLED and Dataproc never moved it to RUNNING: it failed or was cancelled in
    validation/PENDING, so no usage will ever be computed (5b: batch ...1318, stateHistory PENDING only, FAILED after 5 s).
    False when the history is missing or malformed: then the usage wait runs as before (the conservative default)."""
    if describe.get("state") not in NEVER_RAN_STATES:
        return False
    history = describe.get("stateHistory")
    if not isinstance(history, list) or not history:
        return False
    if not all(isinstance(entry, dict) and isinstance(entry.get("state"), str) for entry in history):
        return False
    return not any(entry["state"] == "RUNNING" for entry in history)


def permission_hint(state_message: object) -> str | None:
    """PERMISSION_HINT when Dataproc's stateMessage reports a failed permission pre-check, else None."""
    return PERMISSION_HINT if isinstance(state_message, str) and PERMISSION_FAILURE_MARKER in state_message else None




def submit_argv(
    batch_id: str, run: str, prereg: Prereg, record: PreregRecord, main: Artifact, zip_: Artifact
) -> list[str]:
    label = prereg.label
    provenance = {
        "head_commit": record.head_commit,
        "zip_sha256": zip_.sha256,
        "main_sha256": main.sha256,
        "prereg_commit": record.prereg_commit,
        "batch_id": batch_id,
    }
    return [
        "gcloud", "dataproc", "batches", "submit", "pyspark", main.uri,
        "--async",
        f"--batch={batch_id}",
        f"--region={REGION}",
        f"--version={RUNTIME_VERSION}",
        f"--py-files={zip_.uri}",
        f"--service-account={RUNTIME_SA}",
        f"--subnet={SUBNET}",
        f"--ttl={BATCH_TTL}",
        f"--labels=run={run_dashed(run)},prereg={record.prereg_commit[:12]}",
        "--properties=" + ",".join(f"{k}={v}" for k, v in SPARK_PROPERTIES.items()),
        "--",
        "--input-dir", raw_prefix(run),
        "--output-dir", gold_prefix(run),
        "--reference-date", prereg.generation["reference_date"],
        "--lookback-years", str(label["lookback_years"]),
        "--readmission-window-days", str(label["readmission_window_days"]),
        "--spark-master", "external",
        *[arg for key, value in provenance.items() for arg in ("--provenance", f"{key}={value}")],
        "--record-runtime",  # slice 6a (FR14): runtime.* facts in build_provenance
    ]  # fmt: skip


def generation_summary_problems(summary: dict, prereg: Prereg, run: str) -> list[str]:
    """The raw run's generation_summary.json against the values prereg.yaml fixes for `run` (P7 here, C3 in
    ops/population_checks.py): one message per mismatch, [] when it matches."""
    population, generation = prereg.populations[run], prereg.generation
    expected = {
        "seed": population.seed,
        "clinician_seed": population.clinician_seed,
        "population_size": population.population,
        "reference_date": generation["reference_date"],
        "end_date": generation["end_date"],
        "jvm_timezone": generation["jvm_timezone"],
        "state": generation["state"],
        "years_of_history": generation["years_of_history"],
        "included_files": generation["included_files"],
    }
    problems = [f"{k}: raw has {summary.get(k)!r}, prereg {v!r}" for k, v in expected.items() if summary.get(k) != v]
    provenance = summary.get("provenance") or {}
    if provenance.get("synthea_jar_sha256") != generation["synthea_jar_sha256"]:
        problems.append(f"provenance.synthea_jar_sha256: raw has {provenance.get('synthea_jar_sha256')!r}")
    alive = summary.get("n_alive_patients")
    if not isinstance(alive, int) or alive < population.population:
        problems.append(f"n_alive_patients: {alive!r} < {population.population}")
    return problems


def check_raw_matches_prereg(run_: Runner | None, run: str, prereg: Prereg, repo_root: Path, run_git: Callable) -> None:
    """P7: the raw run was generated with exactly the values in force for this run, from an ancestor of HEAD's prereg."""
    summary = json.loads(run_ok(run_, ["gcloud", "storage", "cat", f"{raw_prefix(run)}/generation_summary.json"]))
    problems = generation_summary_problems(summary, prereg, run)
    provenance = summary.get("provenance") or {}
    prereg_commit = provenance.get("prereg_commit")
    # read from GCS: passed to git only as a full lowercase hex sha, never as something git could parse as an option
    well_formed = isinstance(prereg_commit, str) and re.fullmatch(r"[0-9a-f]{40}", prereg_commit) is not None
    ancestor = well_formed and run_git(
        ["git", "-C", str(repo_root), "merge-base", "--is-ancestor", prereg_commit, "HEAD"], capture_output=True, check=False
    ).returncode == 0
    if not ancestor:
        problems.append(f"provenance.prereg_commit {prereg_commit!r} is not an ancestor of HEAD")
    if problems:
        raise Refused(f"{raw_prefix(run)} does not match prereg.yaml: " + "; ".join(problems))


def preflight(options: SubmitOptions, ctx: LaunchContext, *, remote: bool) -> tuple[Prereg, PreregRecord]:
    run_git = ctx.run_git or subprocess.run
    try:
        record = check_prereg_committed(ctx.repo_root, run_git=run_git)
        prereg = load_prereg(ctx.repo_root / "prereg.yaml")
        check_prereg_against_code(prereg)
    except PreregError as exc:
        raise Refused(str(exc)) from exc
    if options.run not in prereg.populations:
        raise Refused(f"run {options.run!r} is not in prereg.yaml (known: {sorted(prereg.populations)})")
    if options.run == MAIN_RUN and not options.allow_main_run:
        raise Refused(f"{MAIN_RUN} needs --allow-main-run (slice 6)")
    if not remote:
        return prereg, record
    if list_labelled(ctx.run, "instances") or list_labelled(ctx.run, "disks"):
        raise Refused("a labelled generation VM or disk exists: the VM and the batch share the 32-vCPU quota")
    if active_batches(ctx.run):
        raise Refused("another Dataproc batch is active")
    found = set(list_objects(ctx.run, raw_prefix(options.run)))
    if found != expected_raw_layout(options.run, prereg):
        raise Refused(f"{raw_prefix(options.run)}/ does not hold exactly a finished generation run (missing _DONE or tables?)")
    if list_objects(ctx.run, gold_prefix(options.run)):
        raise Refused(
            f"{gold_prefix(options.run)}/ is not empty; to retry, the owner deletes it: gcloud storage rm -r {gold_prefix(options.run)}/"
        )
    check_quota(ctx.run, regional=("CPUS",), vcpus=BATCH_VCPUS, disk_gb=BATCH_DISK_GB)
    check_raw_matches_prereg(ctx.run, options.run, prereg, ctx.repo_root, run_git)
    return prereg, record


def _describe(ctx: LaunchContext, argv: list[str]) -> dict:
    found = run_json(ctx.run, argv)  # GcloudError on a non-zero exit, ValueError (JSONDecodeError) on non-JSON stdout
    if not isinstance(found, dict):
        # ValueError, not TypeError: malformed API output is handled together with json.JSONDecodeError (a ValueError)
        raise ValueError(f"gcloud dataproc batches describe returned {type(found).__name__}, not a JSON object")  # noqa: TRY004
    return found


def _finish(
    ctx: LaunchContext, options: SubmitOptions, batch_id: str, argv: list[str], record: PreregRecord, main: Artifact,
    zip_: Artifact, describe: dict, skipped_wait: bool, *, interrupted: bool, usage_wait_error: str | None = None,
) -> int:
    """Write the batch record, print the summary and, for a non-SUCCEEDED batch, the failure, cleanup and hint lines.
    Returns EXIT_OK for SUCCEEDED, else EXIT_FAILED."""
    usage = parse_batch_usage(describe)
    out = ctx.repo_root / "data" / "pilot" / options.run
    out.mkdir(parents=True, exist_ok=True)
    record_json = {
        "batch_id": batch_id,
        "argv": argv,
        "usage": usage.__dict__,
        "never_ran": skipped_wait,
        "interrupted": interrupted,
        "usage_wait_error": usage_wait_error,  # set when a describe failed during the usage wait of the finished batch
        "describe": describe,
        "provenance": {"head_commit": record.head_commit, "zip_sha256": zip_.sha256, "main_sha256": main.sha256,
                       "prereg_commit": record.prereg_commit},
    }  # fmt: skip
    (out / f"batch_{batch_id}.json").write_text(json.dumps(record_json, indent=2, default=str) + "\n")
    if usage.dcu_hours is not None:
        dcu = f"{usage.dcu_hours:.3f}"
    elif skipped_wait:
        dcu = "none (stateHistory never reached RUNNING; if it did run, re-run gcloud dataproc batches describe later)"
    else:
        dcu = "not yet populated (re-run gcloud dataproc batches describe later)"
    ctx.echo(
        f"Batch {batch_id}: {usage.state} runtime={usage.runtime_version} wall={usage.wall_seconds}s DCU-hours={dcu} "
        f"shuffle GB-hours={usage.shuffle_gb_hours}\n  recorded in {out / f'batch_{batch_id}.json'}"
    )
    if usage.state != "SUCCEEDED":
        ctx.echo(f"FAILED: {describe.get('stateMessage')}")
        # always, also for a batch whose stateHistory never reached RUNNING: harmless on an empty prefix
        ctx.echo(
            f"  if {gold_prefix(options.run)}/ holds partial output, the owner deletes it: gcloud storage rm -r {gold_prefix(options.run)}/"
        )
        hint = permission_hint(describe.get("stateMessage"))
        if hint is not None:
            ctx.echo(hint)
        return EXIT_FAILED
    return EXIT_OK


def recheck_after_wait(options: SubmitOptions, ctx: LaunchContext, record: PreregRecord) -> None:
    """The IAM wait can last ~24 min: re-run the remote preflight (active batches, VM, quota, gold prefix, prereg/P1) and refuse
    if HEAD or the prereg commit moved, because the zip, the argv provenance and the batch record were built before the wait."""
    _, rechecked = preflight(options, ctx, remote=True)
    if (rechecked.head_commit, rechecked.prereg_commit) != (record.head_commit, record.prereg_commit):
        raise Refused(
            f"HEAD or the prereg commit changed during the IAM wait (was {record.head_commit[:12]}/"
            f"{record.prereg_commit[:12]}, now {rechecked.head_commit[:12]}/{rechecked.prereg_commit[:12]}); "
            "the zip and provenance were built from the old commit: rerun to build from the new one"
        )


def submit(options: SubmitOptions, ctx: LaunchContext) -> int:
    try:
        prereg, record = preflight(options, ctx, remote=not options.dry_run)
        zip_path, zip_sha = build_spark_zip(ctx.repo_root, ctx.repo_root / "dist")
        main = Artifact(ctx.repo_root / MAIN_SCRIPT, sha256_file(ctx.repo_root / MAIN_SCRIPT))
        zip_ = Artifact(zip_path, zip_sha)
        batch_id = f"bigjoin-{run_dashed(options.run)}-{ctx.now().strftime('%Y%m%d%H%M')}"
        argv = submit_argv(batch_id, options.run, prereg, record, main, zip_)
        if options.dry_run:
            ctx.echo("would run: " + " ".join(argv))
            ctx.echo(
                f"would check the Admin Activity audit log for IAM changes in the last {IAM_PROPAGATION_SECONDS // 60} "
                "minutes, then submit"
            )
            return EXIT_OK
        upload_artifact(ctx.run, zip_)
        upload_artifact(ctx.run, main)
        try:
            if wait_for_iam_propagation(ctx) > 0:
                recheck_after_wait(options, ctx, record)
        except KeyboardInterrupt:
            ctx.echo(
                "INTERRUPTED during the IAM propagation wait or its re-check: no batch was submitted (the uploaded artifacts "
                "are reused on the next run)"
            )
            return EXIT_INTERRUPTED
    except (Refused, GcloudError, IamStillChanging) as exc:
        ctx.echo(f"REFUSED: {exc}")
        return EXIT_REFUSED

    describe_argv = ["gcloud", "dataproc", "batches", "describe", batch_id, f"--region={REGION}"]
    describe: dict | None = None
    skipped_wait = False
    submitted = False
    try:
        run_ok(ctx.run, argv)
        submitted = True
        while True:
            describe = _describe(ctx, describe_argv)
            if describe.get("state") in TERMINAL_STATES:
                break
            ctx.sleep(options.poll_seconds)
        skipped_wait = never_ran(describe)
        if skipped_wait:
            ctx.echo(f"Batch {batch_id} ended {describe.get('state')}; stateHistory never reached RUNNING: skipping the usage wait")
        waited = 0.0
        while not skipped_wait and not (describe.get("runtimeInfo") or {}).get("approximateUsage") and waited < USAGE_WAIT_SECONDS:
            ctx.sleep(30)
            waited += 30
            describe = _describe(ctx, describe_argv)
    except KeyboardInterrupt:
        if describe is not None and describe.get("state") in TERMINAL_STATES:
            code = _finish(ctx, options, batch_id, argv, record, main, zip_, describe, skipped_wait, interrupted=True)
            ctx.echo(
                f"INTERRUPTED during the usage wait: batch {batch_id} had already ended {describe['state']}; its record was "
                "written with the usage known so far (re-run gcloud dataproc batches describe later for DCU-hours)"
            )
            return code if code == EXIT_FAILED else EXIT_INTERRUPTED
        ctx.echo(f"INTERRUPTED: the batch keeps running (ttl {BATCH_TTL}); cancel it: gcloud dataproc batches cancel {batch_id} --region={REGION}")
        return EXIT_INTERRUPTED
    except (GcloudError, ValueError) as exc:  # ValueError: a describe that is not a JSON object
        if describe is not None and describe.get("state") in TERMINAL_STATES:
            ctx.echo(
                f"WARNING: describe failed during the usage wait of finished batch {batch_id}; recording what is known: "
                f"{str(exc).splitlines()[-1]}"
            )
            return _finish(
                ctx, options, batch_id, argv, record, main, zip_, describe, skipped_wait, interrupted=False,
                usage_wait_error=str(exc).splitlines()[-1],
            )
        if submitted:  # polling failed: the batch may well still be running
            ctx.echo(
                f"FAILED to monitor batch {batch_id}: {str(exc).splitlines()[-1]}\n  it may still be running (ttl {BATCH_TTL}); "
                f"check it: gcloud dataproc batches describe {batch_id} --region={REGION}; cancel it: "
                f"gcloud dataproc batches cancel {batch_id} --region={REGION}"
            )
            return EXIT_FAILED
        ctx.echo(f"FAILED: {exc}")  # unchanged: the submit itself failed
        return EXIT_FAILED
    return _finish(ctx, options, batch_id, argv, record, main, zip_, describe, skipped_wait, interrupted=False)
