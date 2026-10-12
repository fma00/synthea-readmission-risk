"""The Big Join on Dataproc Serverless runtime 3.0 (v2 PRD FR13 pilot, FR14 cap): preflight, submit (maxExecutors=7, standard
tier, ttl 2 h, the dedicated runtime SA and subnet), wait, and record wall-clock and DCU-hours. Every GCP call is a gcloud argv
run through ops.gcloud. See notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md, Interfaces section 11
("Big Join batch submitter"). Exit codes: 0 succeeded; 1 refused; 2 batch failed or cancelled; 130 interrupted (the batch keeps
running, bounded by its ttl).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .gcloud import (
    PROJECT,
    GcloudError,
    Runner,
    list_labelled,
    list_objects,
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
    STAGING_BUCKET,
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


def _parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def parse_batch_usage(describe: dict) -> BatchUsage:
    """`gcloud dataproc batches describe --format=json` -> BatchUsage. int64 fields may come as JSON strings; usage is None until
    Dataproc has computed it (runtimeInfo.approximateUsage appears after completion)."""
    usage = (describe.get("runtimeInfo") or {}).get("approximateUsage")
    created, ended = _parse_time(describe.get("createTime")), _parse_time(describe.get("stateTime"))
    milli_dcu = usage.get("milliDcuSeconds") if usage else None
    shuffle = usage.get("shuffleStorageGbSeconds") if usage else None
    return BatchUsage(
        state=describe.get("state", "UNKNOWN"),
        runtime_version=(describe.get("runtimeConfig") or {}).get("version"),
        wall_seconds=(ended - created).total_seconds() if created and ended else None,
        dcu_hours=int(milli_dcu) / 3.6e6 if milli_dcu is not None else None,
        shuffle_gb_hours=int(shuffle) / 3600 if shuffle is not None else None,
    )


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
        f"--staging-bucket={STAGING_BUCKET}",
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
    ]  # fmt: skip


def check_raw_matches_prereg(run_: Runner | None, run: str, prereg: Prereg, repo_root: Path, run_git: Callable) -> None:
    """P7: the raw run was generated with exactly the values in force for this run, from an ancestor of HEAD's prereg."""
    summary = json.loads(run_ok(run_, ["gcloud", "storage", "cat", f"{raw_prefix(run)}/generation_summary.json"]))
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
    prereg_commit = provenance.get("prereg_commit")
    ancestor = prereg_commit and run_git(
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
            return EXIT_OK
        upload_artifact(ctx.run, zip_)
        upload_artifact(ctx.run, main)
    except (Refused, GcloudError) as exc:
        ctx.echo(f"REFUSED: {exc}")
        return EXIT_REFUSED

    describe_argv = ["gcloud", "dataproc", "batches", "describe", batch_id, f"--region={REGION}"]
    try:
        run_ok(ctx.run, argv)
        while True:
            describe = run_json(ctx.run, describe_argv)
            if describe.get("state") in TERMINAL_STATES:
                break
            ctx.sleep(options.poll_seconds)
        waited = 0.0
        while not (describe.get("runtimeInfo") or {}).get("approximateUsage") and waited < USAGE_WAIT_SECONDS:
            ctx.sleep(30)
            waited += 30
            describe = run_json(ctx.run, describe_argv)
    except KeyboardInterrupt:
        ctx.echo(f"INTERRUPTED: the batch keeps running (ttl {BATCH_TTL}); cancel it: gcloud dataproc batches cancel {batch_id} --region={REGION}")
        return EXIT_INTERRUPTED
    except GcloudError as exc:
        ctx.echo(f"FAILED: {exc}")
        return EXIT_FAILED

    usage = parse_batch_usage(describe)
    out = ctx.repo_root / "data" / "pilot" / options.run
    out.mkdir(parents=True, exist_ok=True)
    record_json = {
        "batch_id": batch_id,
        "argv": argv,
        "usage": usage.__dict__,
        "describe": describe,
        "provenance": {"head_commit": record.head_commit, "zip_sha256": zip_.sha256, "main_sha256": main.sha256,
                       "prereg_commit": record.prereg_commit},
    }  # fmt: skip
    (out / f"batch_{batch_id}.json").write_text(json.dumps(record_json, indent=2, default=str) + "\n")
    dcu = "not yet populated (re-run gcloud dataproc batches describe later)" if usage.dcu_hours is None else f"{usage.dcu_hours:.3f}"
    ctx.echo(
        f"Batch {batch_id}: {usage.state} runtime={usage.runtime_version} wall={usage.wall_seconds}s DCU-hours={dcu} "
        f"shuffle GB-hours={usage.shuffle_gb_hours}\n  recorded in {out / f'batch_{batch_id}.json'}"
    )
    if usage.state != "SUCCEEDED":
        ctx.echo(
            f"FAILED: {describe.get('stateMessage')}\n  if {gold_prefix(options.run)}/ holds partial output, the owner deletes it: "
            f"gcloud storage rm -r {gold_prefix(options.run)}/"
        )
        return EXIT_FAILED
    return EXIT_OK
