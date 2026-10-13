"""The Synthea generation launcher (v2 PRD FR13): one spot VM created from the Terraform instance template, bounded by the
template's max_run_duration and termination action DELETE, deleted by this launcher when the run ends, and then PROVEN gone.
Every GCP call is a gcloud argv run through ops.gcloud (a fake Runner in tests). See
notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md, Interfaces section 11 ("Generation launcher").
Before creating the VM it waits for IAM propagation when the Admin Activity audit log shows a recent IAM change
(ops/iam_propagation.py). Exit codes: 0 done; 1 refused (preflight, or IAM still changing after 3 propagation waits); 2
generation failed; 3 teardown proof failed; 130 interrupted (during the IAM propagation wait or its re-check: no VM was created;
after creation: the VM is torn down and proven gone). Precedence when several apply: 3 > 2 > 130 > 0.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .gcloud import (
    PROJECT,
    GcloudError,
    Runner,
    list_labelled,
    list_objects,
    object_exists,
    run_json,
    run_ok,
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

REGION = "us-central1"
DEFAULT_ZONE = "us-central1-a"
RAW_BUCKET = "synthea-readmission-risk-raw"
GOLD_BUCKET = "synthea-readmission-risk-gold"
ARTIFACTS_BUCKET = "synthea-readmission-risk-artifacts"
STAGING_BUCKET = "synthea-readmission-risk-dataproc-staging"
TEMPLATE_PREFIX = "synthea-generator-"
TEMPLATE_CLASS_FOR_RUN = {"pilot_20k": "pilot", "main_200k": "main"}
GENERATOR_SA = f"synthea-generator@{PROJECT}.iam.gserviceaccount.com"
SUBNET_NAME = "spark-us-central1"
MACHINE_TYPE = "n2-standard-32"
MACHINE_VCPUS = 32
WAIT_MARGIN_SECONDS = 300
MAIN_RUN = "main_200k"
STARTUP_SCRIPT = Path("infra/generation/startup.sh")
GEN_SCRIPT = Path("scripts/generate_synthea_data.py")
LABEL_VALUE_RE = re.compile(r"[a-z0-9_-]{0,63}")
INSTANCE_NAME_RE = re.compile(r"[a-z]([-a-z0-9]{0,61}[a-z0-9])?")
SCRIPT_RUNNER_LOG = f"projects/{PROJECT}/logs/google_metadata_script_runner"
STARTUP_LINE_PREFIX = 'Metadata key("startup-script"), command("/bin/bash"): '
# Both forms name the denied permission (measured 2026-10-13 for gcloud storage ls as a service account without access;
# assumed for gcloud storage cp on the VM: notes/eg-new-feature/ops-launcher-permission-hint-2026-10-13.md)
_DENIED_PERMISSION = re.compile(r"Permission '([A-Za-z0-9_.]+)' denied")
_LACKS_PERMISSION = re.compile(r"does not have ([a-z][A-Za-z0-9_]*\.[A-Za-z0-9_.]+) access")
DIAGNOSIS_TAIL_LINES = 15
# The log query starts this long before the laptop's creation time: laptop and Cloud Logging clocks can differ, and the
# instance name (unique per launch minute; P3 refuses leftover VMs) already confines the query to this VM.
VM_LOG_MARGIN = timedelta(minutes=10)

EXIT_OK, EXIT_REFUSED, EXIT_FAILED, EXIT_PROOF_FAILED, EXIT_INTERRUPTED = 0, 1, 2, 3, 130


class Refused(Exception):
    """A preflight refusal (exit 1)."""


def raw_prefix(run: str) -> str:
    return f"gs://{RAW_BUCKET}/v2/{run}"


def gold_prefix(run: str) -> str:
    return f"gs://{GOLD_BUCKET}/v2/{run}"


def run_dashed(run: str) -> str:
    return run.replace("_", "-")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def md5_base64_file(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 -- compared with GCS's md5_hash, an integrity check, not a security control
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return base64.b64encode(digest.digest()).decode("ascii")


@dataclass(frozen=True)
class Artifact:
    path: Path
    sha256: str

    @property
    def uri(self) -> str:
        return f"gs://{ARTIFACTS_BUCKET}/sha256/{self.sha256}/{self.path.name}"


def upload_artifact(run: Runner | None, artifact: Artifact) -> str:
    """Content-addressed, create-only. An existing object is reused only if its md5_hash equals the local file's."""
    uri = artifact.uri
    if object_exists(run, uri):
        described = run_json(run, ["gcloud", "storage", "objects", "describe", uri])
        if not isinstance(described, dict) or described.get("md5_hash") != md5_base64_file(artifact.path):
            raise Refused(f"artifact name collision: {uri} exists with different content")
        return uri
    run_ok(run, ["gcloud", "storage", "cp", str(artifact.path), uri, "--if-generation-match=0"])
    return uri


def check_quota(run: Runner | None, *, regional: tuple[str, ...], vcpus: int, disk_gb: int = 0) -> None:
    region = run_json(run, ["gcloud", "compute", "regions", "describe", REGION])
    project = run_json(run, ["gcloud", "compute", "project-info", "describe"])
    available = {}
    for scope in (region, project):
        for quota in (scope or {}).get("quotas", []):
            available[quota["metric"]] = float(quota["limit"]) - float(quota["usage"])
    needs = {"CPUS_ALL_REGIONS": vcpus, **{metric: vcpus for metric in regional}}
    if disk_gb:
        needs["DISKS_TOTAL_GB"] = disk_gb
    short = [f"{m} (need {n}, available {available.get(m)})" for m, n in needs.items() if available.get(m, 0) < n]
    if short:
        raise Refused(f"quota too small: {', '.join(short)}")


def active_batches(run: Runner | None) -> list[dict]:
    batches = run_json(
        run, ["gcloud", "dataproc", "batches", "list", f"--region={REGION}", "--filter=state=RUNNING OR state=PENDING"]
    )
    return list(batches or [])


def resolve_template(run: Runner | None, run_name: str) -> tuple[str, int]:
    """(template name, max run seconds) for the run's class: exactly one template named synthea-generator-<class>-<N>s."""
    klass = TEMPLATE_CLASS_FOR_RUN[run_name]
    pattern = rf"^{TEMPLATE_PREFIX}{klass}-[0-9]+s$"
    found = run_json(run, ["gcloud", "compute", "instance-templates", "list", f"--filter=name ~ {pattern}"]) or []
    if len(found) != 1:
        raise Refused(f"need exactly one instance template matching {pattern}; found {[t.get('name') for t in found]}")
    name = found[0]["name"]
    described = run_json(run, ["gcloud", "compute", "instance-templates", "describe", name])
    seconds = int(described["properties"]["scheduling"]["maxRunDuration"]["seconds"])
    return name, seconds


@dataclass
class LaunchOptions:
    run: str
    jdk_tarball: Path
    jar_path: Path = Path("tools/synthea/synthea-with-dependencies.jar")
    zone: str = DEFAULT_ZONE
    java_max_heap: str = "96g"
    allow_main_run: bool = False
    dry_run: bool = False
    poll_seconds: float = 30.0


@dataclass
class LaunchContext:
    """Injection points for tests."""

    repo_root: Path
    run: Runner | None = None
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    run_git: Callable | None = None
    echo: Callable[[str], None] = print


def preflight_local(options: LaunchOptions, ctx: LaunchContext) -> tuple[Prereg, PreregRecord]:
    """P1, P1b, P2, P5: no gcloud."""
    try:
        record = check_prereg_committed(ctx.repo_root, **({"run_git": ctx.run_git} if ctx.run_git else {}))
        prereg = load_prereg(ctx.repo_root / "prereg.yaml")
        check_prereg_against_code(prereg)
    except PreregError as exc:
        raise Refused(str(exc)) from exc
    if options.run not in prereg.populations:
        raise Refused(f"run {options.run!r} is not in prereg.yaml (known: {sorted(prereg.populations)})")
    if options.run == MAIN_RUN and not options.allow_main_run:
        raise Refused(f"{MAIN_RUN} needs --allow-main-run (slice 6)")
    if not options.zone.startswith(REGION + "-"):
        raise Refused(f"--zone {options.zone} is not in {REGION} (the subnet is regional)")
    generation = prereg.generation
    if sha256_file(ctx.repo_root / options.jar_path) != generation["synthea_jar_sha256"]:
        raise Refused(f"{options.jar_path} does not have prereg.yaml's synthea_jar_sha256")
    if sha256_file(options.jdk_tarball) != generation["jdk"]["sha256"]:
        raise Refused(f"{options.jdk_tarball} does not have prereg.yaml's jdk.sha256")
    return prereg, record


def preflight_remote(options: LaunchOptions, ctx: LaunchContext) -> None:
    """P3, P4, P6."""
    leftovers = list_labelled(ctx.run, "instances") + list_labelled(ctx.run, "disks")
    if leftovers:
        names = [item.get("name") for item in leftovers]
        raise Refused(f"labelled generation instance(s)/disk(s) still exist: {names}; delete them first")
    batches = active_batches(ctx.run)
    if batches:
        raise Refused(f"a Dataproc batch is active ({[b.get('name') for b in batches]}): the VM and a batch share the 32-vCPU quota")
    if list_objects(ctx.run, raw_prefix(options.run)):
        raise Refused(
            f"{raw_prefix(options.run)}/ is not empty; to retry, the owner deletes it: gcloud storage rm -r {raw_prefix(options.run)}/"
        )
    check_quota(ctx.run, regional=("CPUS", "N2_CPUS"), vcpus=MACHINE_VCPUS)


def recheck_after_wait(options: LaunchOptions, ctx: LaunchContext, record: PreregRecord) -> None:
    """The IAM wait can last ~24 min: re-run both preflights (P1/P1b/P2/P5 locally; P3/P4/P6 remotely) and refuse if HEAD or
    the prereg commit moved, because the uploaded artifacts and the VM metadata were built before the wait."""
    _, rechecked = preflight_local(options, ctx)
    preflight_remote(options, ctx)
    if (rechecked.head_commit, rechecked.prereg_commit) != (record.head_commit, record.prereg_commit):
        raise Refused(
            f"HEAD or the prereg commit changed during the IAM wait (was {record.head_commit[:12]}/"
            f"{record.prereg_commit[:12]}, now {rechecked.head_commit[:12]}/{rechecked.prereg_commit[:12]}); "
            "the artifacts and VM metadata were built from the old commit: rerun to build from the new one"
        )


def instance_name(run: str, now: datetime) -> str:
    name = f"synthea-gen-{run_dashed(run)}-{now.strftime('%Y%m%d%H%M')}"
    if not INSTANCE_NAME_RE.fullmatch(name):
        raise ValueError(f"invalid instance name {name!r}")
    return name


def run_metadata(
    options: LaunchOptions, prereg: Prereg, record: PreregRecord, artifacts: dict[str, Artifact]
) -> dict[str, str]:
    population = prereg.populations[options.run]
    generation = prereg.generation
    return {
        "run": options.run,
        "seed": str(population.seed),
        "clinician_seed": str(population.clinician_seed),
        "population": str(population.population),
        "reference_date": generation["reference_date"],
        "end_date": generation["end_date"],
        "jvm_timezone": generation["jvm_timezone"],
        "state": generation["state"],
        "years_of_history": str(generation["years_of_history"]),
        "included_files": ";".join(generation["included_files"]),
        "java_max_heap": options.java_max_heap,
        "raw_prefix": raw_prefix(options.run),
        "head_commit": record.head_commit,
        "prereg_commit": record.prereg_commit,
        "prereg_sha256": record.sha256,
        **{f"{key}_uri": a.uri for key, a in artifacts.items()},
        **{f"{key}_sha256": a.sha256 for key, a in artifacts.items()},
    }


def create_argv(
    name: str, options: LaunchOptions, template: str, labels: dict[str, str], metadata: dict[str, str], repo_root: Path
) -> list[str]:
    for value in labels.values():
        if not LABEL_VALUE_RE.fullmatch(value):
            raise ValueError(f"invalid label value {value!r}")
    return [
        "gcloud", "compute", "instances", "create", name,
        f"--zone={options.zone}",
        f"--source-instance-template=projects/{PROJECT}/global/instanceTemplates/{template}",
        "--labels=" + ",".join(f"{k}={v}" for k, v in labels.items()),
        f"--metadata-from-file=startup-script={repo_root / STARTUP_SCRIPT}",  # absolute: the CLI may run from anywhere
        "--metadata=" + ",".join(f"{k}={v}" for k, v in {"enable-oslogin": "TRUE", "block-project-ssh-keys": "true", **metadata}.items()),
    ]  # fmt: skip


def positive_control(ctx: LaunchContext, name: str, zone: str, max_run_seconds: int) -> list[str]:
    """Problems with the created VM; [] when it has every bound the run depends on."""
    problems = []
    inst = run_json(ctx.run, ["gcloud", "compute", "instances", "describe", name, f"--zone={zone}"]) or {}
    if inst.get("labels", {}).get("purpose") != "synthea-generation":
        problems.append("instance lacks label purpose=synthea-generation")
    items = {i.get("key"): i.get("value") for i in inst.get("metadata", {}).get("items", [])}
    if items.get("startup-script") != (ctx.repo_root / STARTUP_SCRIPT).read_text(encoding="utf-8"):
        problems.append("startup-script differs from infra/generation/startup.sh")
    scheduling = inst.get("scheduling", {})
    if scheduling.get("provisioningModel") != "SPOT":
        problems.append(f"provisioningModel is {scheduling.get('provisioningModel')!r}, not SPOT")
    if scheduling.get("instanceTerminationAction") != "DELETE":
        problems.append(f"instanceTerminationAction is {scheduling.get('instanceTerminationAction')!r}, not DELETE")
    if str(scheduling.get("maxRunDuration", {}).get("seconds")) != str(max_run_seconds):
        problems.append(f"maxRunDuration is {scheduling.get('maxRunDuration')!r}, not {max_run_seconds}s")
    if any(nic.get("accessConfigs") for nic in inst.get("networkInterfaces", [])):
        problems.append("the instance has an external IP (accessConfigs)")
    if [sa.get("email") for sa in inst.get("serviceAccounts", [])] != [GENERATOR_SA]:
        problems.append(f"service accounts are {[sa.get('email') for sa in inst.get('serviceAccounts', [])]}, not [{GENERATOR_SA}]")
    disk = run_json(ctx.run, ["gcloud", "compute", "disks", "describe", name, f"--zone={zone}"]) or {}
    if disk.get("labels", {}).get("purpose") != "synthea-generation":
        problems.append("boot disk lacks label purpose=synthea-generation")
    if [i.get("name") for i in list_labelled(ctx.run, "instances")] != [name]:
        problems.append("the instance label filter does not list exactly this instance")
    if [d.get("name") for d in list_labelled(ctx.run, "disks")] != [name]:
        problems.append("the disk label filter does not list exactly this disk")
    return problems


def positive_control_ok_message(name: str, max_run_seconds: int) -> str:
    """The pass line, naming exactly the checks positive_control makes, in its order."""
    return (
        f"POSITIVE CONTROL OK: {name} is labelled purpose=synthea-generation, runs the pinned startup script, is SPOT with "
        f"termination action DELETE and maxRunDuration {max_run_seconds}s, has no external IP and runs as {GENERATOR_SA}; "
        "its boot disk is labelled; both label filters list exactly this instance and disk"
    )


def instance_exists(ctx: LaunchContext, name: str) -> bool:
    return name in [i.get("name") for i in list_labelled(ctx.run, "instances")]


def wait_for_marker(ctx: LaunchContext, run: str, name: str, timeout_seconds: float, poll_seconds: float) -> str:
    """'done', 'failed', 'vanished' or 'timeout'."""
    deadline = ctx.monotonic() + timeout_seconds
    prefix = raw_prefix(run)
    while True:
        if object_exists(ctx.run, f"{prefix}/_DONE"):
            return "done"
        if object_exists(ctx.run, f"{prefix}/_FAILED"):
            return "failed"
        if not instance_exists(ctx, name):
            return "vanished"
        if ctx.monotonic() >= deadline:
            return "timeout"
        ctx.sleep(poll_seconds)


def teardown_and_prove(ctx: LaunchContext, name: str, zone: str) -> bool:
    """Deletes the instance if it still exists, then the proof: no labelled instance and no labelled disk remains. A gcloud
    failure here is never allowed to escape (a VM may still be running): it is reported with the manual commands, and the
    proof counts as failed."""
    try:
        return _teardown_and_prove(ctx, name, zone)
    except GcloudError as exc:
        ctx.echo(
            f"TEARDOWN COULD NOT BE PROVEN: {exc}\n  check and clean up by hand:\n"
            f"  gcloud compute instances delete {name} --zone={zone} --quiet\n"
            "  gcloud compute instances list --filter=labels.purpose=synthea-generation\n"
            "  gcloud compute disks list --filter=labels.purpose=synthea-generation"
        )
        return False


def _teardown_and_prove(ctx: LaunchContext, name: str, zone: str) -> bool:
    if instance_exists(ctx, name):
        run_ok(ctx.run, ["gcloud", "compute", "instances", "delete", name, f"--zone={zone}", "--quiet"])
    proven = True
    for kind in ("instances", "disks"):
        for item in list_labelled(ctx.run, kind):
            proven = False
            ctx.echo(
                f"LEFTOVER {kind[:-1]}: {item.get('name')} -- delete it: "
                f"gcloud compute {kind} delete {item.get('name')} --zone={zone} --quiet"
            )
    return proven


def missing_permissions(text: str) -> list[str]:
    """Sorted, de-duplicated IAM permission names that `text` reports as denied (both message forms)."""
    return sorted(set(_DENIED_PERMISSION.findall(text)) | set(_LACKS_PERMISSION.findall(text)))


def permission_hint(permissions: list[str], run: str) -> str:
    return (
        f"HINT: the VM ({GENERATOR_SA}) was denied {', '.join(permissions)}. If an IAM role or binding changed in the last "
        "minutes, it may not have propagated yet (the launcher only waits for changes the audit log already shows): wait, "
        f"delete the partial raw prefix (gcloud storage rm -r {raw_prefix(run)}/) and rerun. Otherwise grant the permission "
        "to the synthea-generator service account in infra/iam.tf. See docs/setup.md, 'Lessons from the first live run'."
    )


def vm_log_argv(name: str, since: datetime) -> list[str]:
    """The VM's startup-script lines that mention a denial or a startup.sh failure, oldest first."""
    query = (
        f'logName="{SCRIPT_RUNNER_LOG}" AND labels.instance_name="{name}" '
        f'AND timestamp>="{since.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}" '
        'AND (jsonPayload.message:"denied" OR jsonPayload.message:"does not have" OR jsonPayload.message:"startup.sh FAILED")'
    )
    return ["gcloud", "logging", "read", query, "--limit=200", "--order=asc"]


def vm_log_lines(entries: object) -> list[str]:
    """The startup-script lines in `gcloud logging read --format=json` output, in the given order: each entry's
    jsonPayload.message, with STARTUP_LINE_PREFIX removed when present (kept as is otherwise: the VM image, hence the guest
    agent's prefix format, is not pinned, and the query already narrowed the entries to this VM and to matching terms);
    entries that are not dicts, have no dict jsonPayload or no str message are skipped. Non-list input -> []."""
    if not isinstance(entries, list):
        return []
    lines = []
    for entry in entries:
        payload = entry.get("jsonPayload") if isinstance(entry, dict) else None
        message = payload.get("message") if isinstance(payload, dict) else None
        if isinstance(message, str):
            lines.append(message.removeprefix(STARTUP_LINE_PREFIX))
    return lines


def diagnose_failure(ctx: LaunchContext, run: str, name: str, state: str, since: datetime) -> None:
    """After a failed run: show the evidence and, when it names denied permissions, the hint. Never raises for its own reads:
    GcloudError/ValueError become one 'could not read ...' line, and a KeyboardInterrupt during the diagnosis is caught and
    reported, so the run's exit code stays 2 (failed) and teardown proceeds."""
    if state == "failed":
        source = "_FAILED"
    elif state in ("vanished", "timeout"):
        source = "the VM's startup-script log in Cloud Logging (lines mentioning a denial or a startup.sh failure)"
    else:
        return
    try:
        if state == "failed":
            text = run_ok(ctx.run, ["gcloud", "storage", "cat", f"{raw_prefix(run)}/_FAILED"])
        else:
            text = "\n".join(vm_log_lines(run_json(ctx.run, vm_log_argv(name, since - VM_LOG_MARGIN))))
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            if state == "failed":
                ctx.echo("(no lines in _FAILED)")
            else:
                ctx.echo(
                    "(no denial or startup.sh failure lines in the VM's startup-script log; the VM may have been preempted, "
                    "or still been working at the timeout)"
                )
            return
        if state == "failed":
            ctx.echo("Last lines of _FAILED:")
            shown = lines[-DIAGNOSIS_TAIL_LINES:]
        else:
            ctx.echo(f"First lines of {source}:")
            shown = lines[:DIAGNOSIS_TAIL_LINES]
        for line in shown:
            ctx.echo("  " + line)
        permissions = missing_permissions(text)
        if permissions:
            ctx.echo(permission_hint(permissions, run))
    except (GcloudError, ValueError) as exc:
        ctx.echo(f"(could not read {source} for a diagnosis: {str(exc).splitlines()[-1]})")
    except KeyboardInterrupt:
        ctx.echo("(diagnosis interrupted)")


def expected_raw_layout(run: str, prereg: Prereg) -> set[str]:
    prefix = raw_prefix(run)
    return {
        *(f"{prefix}/csv/{t}" for t in prereg.generation["included_files"]),
        f"{prefix}/generation_summary.json",
        f"{prefix}/run_provenance.json",
        f"{prefix}/_DONE",
    }


def check_raw_layout(run_: Runner | None, run: str, prereg: Prereg) -> list[str]:
    found = set(list_objects(run_, raw_prefix(run)))
    expected = expected_raw_layout(run, prereg)
    problems = []
    if expected - found:
        problems.append(f"missing: {sorted(expected - found)}")
    if found - expected:
        problems.append(f"unexpected: {sorted(found - expected)}")
    return problems


def launch(options: LaunchOptions, ctx: LaunchContext) -> int:
    try:
        prereg, record = preflight_local(options, ctx)
        zip_path, zip_sha = build_spark_zip(ctx.repo_root, ctx.repo_root / "dist")
        generation = prereg.generation
        artifacts = {
            "jar": Artifact(ctx.repo_root / options.jar_path, generation["synthea_jar_sha256"]),
            "jdk": Artifact(Path(options.jdk_tarball), generation["jdk"]["sha256"]),
            "zip": Artifact(zip_path, zip_sha),
            "gen_script": Artifact(ctx.repo_root / GEN_SCRIPT, sha256_file(ctx.repo_root / GEN_SCRIPT)),
        }
        name = instance_name(options.run, ctx.now())
        labels = {"purpose": "synthea-generation", "run": run_dashed(options.run), "prereg": record.prereg_commit[:12]}
        metadata = run_metadata(options, prereg, record, artifacts)
        if options.dry_run:
            for a in artifacts.values():
                ctx.echo(f"would upload: gcloud storage cp {a.path} {a.uri} --if-generation-match=0")
            ctx.echo("would run: " + " ".join(create_argv(name, options, "<from template>", labels, metadata, ctx.repo_root)))
            ctx.echo(
                f"would check the Admin Activity audit log for IAM changes in the last {IAM_PROPAGATION_SECONDS // 60} "
                "minutes, then create the VM"
            )
            return EXIT_OK
        preflight_remote(options, ctx)
        for a in artifacts.values():
            upload_artifact(ctx.run, a)
        resolve_template(ctx.run, options.run)  # fail fast: refuse a missing/ambiguous template before any IAM wait
        try:
            if wait_for_iam_propagation(ctx) > 0:
                recheck_after_wait(options, ctx, record)
        except KeyboardInterrupt:
            ctx.echo(
                "INTERRUPTED during the IAM propagation wait or its re-check: no VM was created (the uploaded artifacts "
                "are reused on the next run)"
            )
            return EXIT_INTERRUPTED
        # after the wait: a terraform apply (the usual cause of an IAM change) can also replace the instance template
        template, max_run_seconds = resolve_template(ctx.run, options.run)
    except (Refused, GcloudError, IamStillChanging) as exc:
        ctx.echo(f"REFUSED: {exc}")
        return EXIT_REFUSED

    started = ctx.monotonic()
    outcome = EXIT_OK
    try:
        created_at = ctx.now()
        run_ok(ctx.run, create_argv(name, options, template, labels, metadata, ctx.repo_root))
        problems = positive_control(ctx, name, options.zone, max_run_seconds)
        if problems:
            ctx.echo("POSITIVE CONTROL FAILED (deleting the VM): " + "; ".join(problems))
            outcome = EXIT_FAILED
        else:
            ctx.echo(positive_control_ok_message(name, max_run_seconds))
            state = wait_for_marker(ctx, options.run, name, max_run_seconds + WAIT_MARGIN_SECONDS, options.poll_seconds)
            if state != "done":
                messages = {
                    "failed": f"generation failed: see {raw_prefix(options.run)}/_FAILED",
                    "vanished": "the VM vanished without a marker: preempted or max-run-duration reached",
                    "timeout": "timed out waiting for a marker; deleting the VM",
                }
                ctx.echo(f"FAILED: {messages[state]}")
                outcome = EXIT_FAILED
                diagnose_failure(ctx, options.run, name, state, created_at)
            else:
                layout = check_raw_layout(ctx.run, options.run, prereg)
                if layout:
                    ctx.echo("FAILED: raw layout: " + "; ".join(layout))
                    outcome = EXIT_FAILED
    except KeyboardInterrupt:
        outcome = EXIT_INTERRUPTED
    except GcloudError as exc:
        ctx.echo(f"FAILED: {exc}")
        outcome = EXIT_FAILED
    finally:
        proven = teardown_and_prove(ctx, name, options.zone)
    if not proven:
        return EXIT_PROOF_FAILED
    if outcome == EXIT_OK:
        _report(ctx, options.run, ctx.monotonic() - started)
    return outcome


def _report(ctx: LaunchContext, run: str, vm_seconds: float) -> None:
    out = ctx.repo_root / "data" / "pilot" / run
    out.mkdir(parents=True, exist_ok=True)
    summary = json.loads(run_ok(ctx.run, ["gcloud", "storage", "cat", f"{raw_prefix(run)}/generation_summary.json"]))
    provenance = json.loads(run_ok(ctx.run, ["gcloud", "storage", "cat", f"{raw_prefix(run)}/run_provenance.json"]))
    (out / "generation_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out / "run_provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    gib = 1024**3
    ctx.echo(
        f"Generation done: run={run} seed={summary.get('seed')} clinician_seed={summary.get('clinician_seed')} "
        f"population={summary.get('population_size')} alive={summary.get('n_alive_patients')}\n"
        f"  generation_wall_seconds={provenance.get('generation_wall_seconds')} "
        f"peak_rss={summary.get('peak_child_rss_bytes', 0) / gib:.1f} GiB raw={provenance.get('raw_bytes', 0) / gib:.1f} GiB\n"
        f"  patients.csv={summary.get('row_counts', {}).get('patients.csv')} "
        f"encounters.csv={summary.get('row_counts', {}).get('encounters.csv')} VM total {vm_seconds:.0f}s\n"
        f"  saved to {out}"
    )

