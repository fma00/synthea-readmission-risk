"""The generation launcher (ops/generation_vm.py) against FakeGcloud: argv shapes, every preflight refusal, the positive control,
every way a run can end, and the teardown proof. Design: notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md
(Interfaces section 11)."""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from readmission_risk.ops import generation_vm as gv
from readmission_risk.ops.prereg import PreregError, PreregRecord, load_prereg
from tests.ops.conftest import REPO
from tests.ops.fake_gcloud import FakeGcloud, audit_entry

PREREG = load_prereg(REPO / "prereg.yaml")
RECORD = PreregRecord(head_commit="a" * 40, prereg_commit="b" * 40, prereg_committed_at="2026-10-12T10:00:00+02:00", sha256="c" * 64)
RUN = "pilot_20k"
PREFIX = gv.raw_prefix(RUN)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A launchable repo layout in tmp_path with the local pins satisfied (the real check functions are tested separately)."""
    repo = tmp_path / "repo"
    for rel in ("prereg.yaml", "infra/generation/startup.sh", "scripts/generate_synthea_data.py"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, repo / rel)
    jar = repo / "tools/synthea/synthea-with-dependencies.jar"
    jar.parent.mkdir(parents=True)
    jar.write_bytes(b"jar")
    jdk = tmp_path / "jdk.tar.gz"
    jdk.write_bytes(b"jdk")
    pins = {jar.name: PREREG.generation["synthea_jar_sha256"], jdk.name: PREREG.generation["jdk"]["sha256"]}
    real_sha = gv.sha256_file
    monkeypatch.setattr(gv, "sha256_file", lambda p: pins.get(Path(p).name) or real_sha(p))
    monkeypatch.setattr(gv, "check_prereg_committed", lambda repo_root, **kw: RECORD)

    def fake_zip(repo_root, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "readmission_risk_spark_job-000000000000.zip"
        path.write_bytes(b"zip")
        return path, "d" * 64

    monkeypatch.setattr(gv, "build_spark_zip", fake_zip)
    fake = FakeGcloud(on_create=finish_generation)
    echoed: list[str] = []
    clock = {"t": 0.0}

    def sleep(seconds):
        clock["t"] += seconds

    ctx = gv.LaunchContext(
        repo_root=repo, run=fake, sleep=sleep, monotonic=lambda: clock["t"],
        now=lambda: datetime(2026, 10, 13, 9, 5, tzinfo=UTC), echo=echoed.append,
    )  # fmt: skip
    options = gv.LaunchOptions(run=RUN, jdk_tarball=jdk, poll_seconds=30)
    return {"fake": fake, "ctx": ctx, "options": options, "echoed": echoed, "repo": repo}


def finish_generation(fake: FakeGcloud, name: str) -> None:
    """What a successful VM leaves behind: the exact raw layout, _DONE last."""
    for table in PREREG.generation["included_files"]:
        fake.objects[f"{PREFIX}/csv/{table}"] = b"h\n1\n"
    summary = {"seed": 17, "population_size": 20000, "n_alive_patients": 20000, "peak_child_rss_bytes": 2**33,
               "row_counts": {"patients.csv": 23000, "encounters.csv": 1300000}}  # fmt: skip
    fake.objects[f"{PREFIX}/generation_summary.json"] = json.dumps(summary).encode()
    fake.objects[f"{PREFIX}/run_provenance.json"] = json.dumps({"generation_wall_seconds": 300, "raw_bytes": 2**32}).encode()
    fake.objects[f"{PREFIX}/_DONE"] = b""


def launch(env) -> int:
    return gv.launch(env["options"], env["ctx"])


def text(env) -> str:
    return "\n".join(env["echoed"])


# --- happy path and argv ---


def test_done_marker_then_delete_then_proof(env):
    assert launch(env) == gv.EXIT_OK, text(env)
    fake = env["fake"]
    assert fake.instances == {} and fake.disks == {}
    assert len(fake.ran("gcloud", "compute", "instances", "delete")) == 1
    saved = env["repo"] / "data/pilot/pilot_20k"
    assert json.loads((saved / "generation_summary.json").read_text())["seed"] == 17
    assert "generation_wall_seconds=300" in text(env)
    assert gv.positive_control_ok_message(gv.instance_name("pilot_20k", env["ctx"].now()), 7200) in text(env)


def test_create_argv_uses_template_prereg_values_and_explicit_purpose(env):
    launch(env)
    (create,) = env["fake"].ran("gcloud", "compute", "instances", "create")
    assert create[4] == "synthea-gen-pilot-20k-202610130905"
    assert "--source-instance-template=projects/synthea-readmission-risk/global/instanceTemplates/synthea-generator-pilot-7200s" in create
    labels = next(a for a in create if a.startswith("--labels=")).removeprefix("--labels=")
    assert labels == f"purpose=synthea-generation,run=pilot-20k,prereg={'b' * 12}"
    assert f"--metadata-from-file=startup-script={env['repo'] / 'infra/generation/startup.sh'}" in create
    metadata = dict(kv.split("=", 1) for kv in next(a for a in create if a.startswith("--metadata=")).removeprefix("--metadata=").split(","))
    pop, gen = PREREG.populations[RUN], PREREG.generation
    assert (metadata["seed"], metadata["clinician_seed"], metadata["population"]) == (str(pop.seed), str(pop.clinician_seed), str(pop.population))
    assert (metadata["reference_date"], metadata["end_date"], metadata["jvm_timezone"]) == (gen["reference_date"], gen["end_date"], "UTC")
    assert metadata["enable-oslogin"] == "TRUE" and metadata["block-project-ssh-keys"] == "true"


def test_metadata_has_every_key(env):
    launch(env)
    (create,) = env["fake"].ran("gcloud", "compute", "instances", "create")
    metadata = dict(kv.split("=", 1) for kv in next(a for a in create if a.startswith("--metadata=")).removeprefix("--metadata=").split(","))
    expected = {"run", "seed", "clinician_seed", "end_date", "jvm_timezone", "population", "reference_date", "state",
                "years_of_history", "included_files", "java_max_heap", "raw_prefix", "head_commit", "prereg_commit",
                "prereg_sha256", "jar_uri", "jar_sha256", "jdk_uri", "jdk_sha256", "zip_uri", "zip_sha256",
                "gen_script_uri", "gen_script_sha256", "enable-oslogin", "block-project-ssh-keys"}  # fmt: skip
    assert set(metadata) == expected
    assert all("," not in v for v in metadata.values())


def test_included_files_joined_with_semicolons(env):
    launch(env)
    (create,) = env["fake"].ran("gcloud", "compute", "instances", "create")
    assert f"included_files={';'.join(PREREG.generation['included_files'])}" in next(a for a in create if a.startswith("--metadata="))


def test_labels_sanitised():
    for run in PREREG.populations:
        assert re.fullmatch(r"[a-z0-9_-]{0,63}", gv.run_dashed(run))
        assert gv.INSTANCE_NAME_RE.fullmatch(gv.instance_name(run, datetime(2026, 10, 13, 9, 5, tzinfo=UTC)))
    assert gv.run_dashed("pilot_20k") == "pilot-20k"


def test_artifacts_uploaded_create_only(env):
    launch(env)
    uploads = env["fake"].ran("gcloud", "storage", "cp")
    assert len(uploads) == 4 and all(u[-1] == "--if-generation-match=0" for u in uploads)
    assert all(u[4].startswith("gs://synthea-readmission-risk-artifacts/sha256/") for u in uploads)


def test_existing_artifact_with_same_sha_accepted_other_refused(env, tmp_path):
    fake = env["fake"]
    artifact = gv.Artifact(env["repo"] / "scripts/generate_synthea_data.py", "e" * 64)
    gv.upload_artifact(fake, artifact)
    gv.upload_artifact(fake, artifact)  # identical content: reused
    assert len(fake.ran("gcloud", "storage", "cp")) == 1
    other = tmp_path / "generate_synthea_data.py"
    other.write_text("different")
    with pytest.raises(gv.Refused, match="name collision"):
        gv.upload_artifact(fake, gv.Artifact(other, "e" * 64))


def test_dry_run_makes_no_runner_call(env):
    env["options"].dry_run = True
    assert launch(env) == gv.EXIT_OK
    assert env["fake"].calls == []
    assert "would check the Admin Activity audit log" in text(env)
    assert "would run: gcloud compute instances create" in text(env)


# --- preflight refusals (exit 1, nothing created) ---


def _refused(env, match: str) -> None:
    assert launch(env) == gv.EXIT_REFUSED, text(env)
    assert re.search(match, text(env)), text(env)
    assert env["fake"].ran("gcloud", "compute", "instances", "create") == []


def test_p1_prereg_not_committed(env, monkeypatch):
    def refuse(*a, **k):
        raise PreregError("the working tree is not clean")

    monkeypatch.setattr(gv, "check_prereg_committed", refuse)
    _refused(env, "not clean")


def test_p1b_prereg_disagrees_with_code(env, monkeypatch):
    def refuse(prereg):
        raise PreregError("prereg.yaml disagrees with the code")

    monkeypatch.setattr(gv, "check_prereg_against_code", refuse)
    _refused(env, "disagrees with the code")


def test_p2_unknown_run(env):
    env["options"].run = "pilot_2k"
    _refused(env, "not in prereg.yaml")


def test_main_run_requires_flag(env):
    env["options"].run = "main_200k"
    _refused(env, "--allow-main-run")


def test_p2_zone_outside_region(env):
    env["options"].zone = "europe-west6-a"
    _refused(env, "not in us-central1")


def test_p3_leftover_disk(env):
    env["fake"].disks["old"] = {"labels": {"purpose": "synthea-generation"}}
    _refused(env, "still exist: \\['old'\\]")


def test_p3_active_batch(env):
    env["fake"].batches["bigjoin-x"] = {"state": "RUNNING", "states": []}
    _refused(env, "Dataproc batch is active")


def test_p4_raw_prefix_not_empty(env):
    env["fake"].objects[f"{PREFIX}/csv/patients.csv"] = b"x"
    _refused(env, "is not empty; to retry, the owner deletes it")


def test_p5_jar_hash_mismatch(env, monkeypatch):
    monkeypatch.setattr(gv, "sha256_file", lambda p: "0" * 64)
    _refused(env, "synthea_jar_sha256")


def test_p6_quota(env):
    env["fake"].quotas["N2_CPUS"] = (24, 0)
    _refused(env, "quota too small: N2_CPUS")


def test_no_template_for_class(env):
    env["fake"].templates = {}
    _refused(env, "exactly one instance template")


# --- how a run can end ---


def test_positive_control_mismatch_deletes_and_exits_2(env):
    env["fake"].instance_overrides = {"scheduling": {"provisioningModel": "STANDARD"}}
    assert launch(env) == gv.EXIT_FAILED
    assert "POSITIVE CONTROL FAILED" in text(env) and env["fake"].instances == {}
    assert "POSITIVE CONTROL OK" not in text(env)


def test_positive_control_missing_disk_label(env):
    env["fake"].disk_labels = {}
    assert launch(env) == gv.EXIT_FAILED
    assert "boot disk lacks label" in text(env)


def test_vanished_without_marker_exit_2(env):
    """Spot preemption (or max_run_duration) while generating: GCE deletes the VM and its disk; no marker is ever written."""
    fake = env["fake"]
    fake.on_create = None

    def preempt(seconds):
        fake.instances.clear()
        fake.disks.clear()

    env["ctx"].sleep = preempt
    assert launch(env) == gv.EXIT_FAILED
    assert "preempted or max-run-duration" in text(env)


def test_failed_marker_exit_2_and_proof_runs(env):
    env["fake"].on_create = lambda fake, name: fake.objects.__setitem__(f"{PREFIX}/_FAILED", b"log tail")
    assert launch(env) == gv.EXIT_FAILED
    assert env["fake"].instances == {} and env["fake"].disks == {}


def test_timeout_exit_2_deletes(env):
    env["fake"].on_create = None  # nothing ever appears
    assert launch(env) == gv.EXIT_FAILED
    assert "timed out" in text(env) and env["fake"].instances == {}


def test_bad_raw_layout_exit_2(env):
    def partial(fake, name):
        finish_generation(fake, name)
        del fake.objects[f"{PREFIX}/csv/observations.csv"]

    env["fake"].on_create = partial
    assert launch(env) == gv.EXIT_FAILED
    assert "raw layout" in text(env) and "observations.csv" in text(env)


def test_leftover_disk_exit_3(env):
    env["fake"].keep_disk_after_delete = True
    assert launch(env) == gv.EXIT_PROOF_FAILED
    assert "LEFTOVER disk" in text(env) and "gcloud compute disks delete" in text(env)


def test_keyboard_interrupt_tears_down_exit_130(env):
    env["fake"].on_create = None

    def interrupt(seconds):
        raise KeyboardInterrupt

    env["ctx"].sleep = interrupt
    assert launch(env) == gv.EXIT_INTERRUPTED
    assert env["fake"].instances == {} and env["fake"].disks == {}


def test_proof_failure_wins_over_interrupt(env):
    env["fake"].on_create = None
    env["fake"].keep_disk_after_delete = True

    def interrupt(seconds):
        raise KeyboardInterrupt

    env["ctx"].sleep = interrupt
    assert launch(env) == gv.EXIT_PROOF_FAILED


# --- ties to Terraform ---


def test_constants_match_terraform():
    tf = "\n".join(p.read_text() for p in (REPO / "infra").glob("*.tf"))
    for value in (gv.RAW_BUCKET, gv.GOLD_BUCKET, gv.ARTIFACTS_BUCKET, gv.STAGING_BUCKET, gv.TEMPLATE_PREFIX, gv.SUBNET_NAME,
                  "synthea-generator", "dataproc-runtime", "synthea-generation", gv.MACHINE_TYPE, gv.REGION):  # fmt: skip
        assert value in tf, value


def test_default_compute_sa_never_used():
    assert "-compute@developer.gserviceaccount.com" not in (REPO / "infra" / "compute.tf").read_text()
    assert gv.GENERATOR_SA.startswith("synthea-generator@")


def test_gcloud_failure_during_teardown_is_proof_failure_exit_3(env):
    env["fake"].fail_on = ("gcloud compute instances delete",)
    assert launch(env) == gv.EXIT_PROOF_FAILED
    assert "TEARDOWN COULD NOT BE PROVEN" in text(env) and "gcloud compute instances delete" in text(env)


# --- IAM propagation wait before the VM is created (notes/eg-new-feature/ops-launcher-iam-wait-2026-10-13.md) ---

NOW_G = datetime(2026, 10, 13, 9, 5, tzinfo=UTC)  # the env fixture's clock
RECENT = audit_entry("google.iam.admin.v1.UpdateRole", NOW_G - timedelta(seconds=60))


def _indexes(env, *prefix: str) -> list[int]:
    return [i for i, call in enumerate(env["fake"].calls) if call[: len(prefix)] == list(prefix)]


def _recording_sleep(env) -> list[float]:
    slept: list[float] = []
    original = env["ctx"].sleep

    def sleep(seconds):
        slept.append(seconds)
        original(seconds)

    env["ctx"].sleep = sleep
    return slept


def _interrupt(*args, **kwargs):
    raise KeyboardInterrupt


def test_launch_waits_for_recent_iam_change_before_create(env):
    env["fake"].logging_responses = [[RECENT], [RECENT]]
    slept = _recording_sleep(env)
    assert launch(env) == gv.EXIT_OK, text(env)
    assert slept[0] == 420.0
    assert _indexes(env, "gcloud", "logging", "read")[-1] < _indexes(env, "gcloud", "compute", "instances", "create")[0]
    assert "Waiting 420 s for IAM propagation" in text(env)


def test_ctrl_c_during_iam_wait_creates_no_vm(env):
    env["fake"].logging_responses = [[RECENT]]
    env["ctx"].sleep = _interrupt
    assert launch(env) == gv.EXIT_INTERRUPTED
    assert _indexes(env, "gcloud", "compute", "instances", "create") == []
    assert _indexes(env, "gcloud", "compute", "instances", "delete") == []
    assert "INTERRUPTED during the IAM propagation wait or its re-check: no VM was created" in text(env)


def test_iam_keeps_changing_refuses_launch(env):
    env["fake"].logging_responses = [
        [audit_entry("google.iam.admin.v1.UpdateRole", NOW_G - timedelta(seconds=60 - i))] for i in range(4)
    ]
    assert launch(env) == gv.EXIT_REFUSED
    assert _indexes(env, "gcloud", "compute", "instances", "create") == []
    assert "IAM kept changing across 3 propagation waits" in text(env)


def test_commit_change_during_iam_wait_refuses_launch(env, monkeypatch):
    env["fake"].logging_responses = [[RECENT], [RECENT]]
    records = [RECORD, dataclasses.replace(RECORD, head_commit="e" * 40)]
    monkeypatch.setattr(gv, "check_prereg_committed", lambda repo_root, **kw: records.pop(0) if len(records) > 1 else records[0])
    assert launch(env) == gv.EXIT_REFUSED
    assert _indexes(env, "gcloud", "compute", "instances", "create") == []
    assert "HEAD or the prereg commit changed during the IAM wait" in text(env)


def _count_preflight_remote(monkeypatch) -> dict:
    calls = {"n": 0}
    real = gv.preflight_remote

    def counting(options, ctx):
        calls["n"] += 1
        real(options, ctx)

    monkeypatch.setattr(gv, "preflight_remote", counting)
    return calls


def test_preflight_runs_once_without_a_wait(env, monkeypatch):
    calls = _count_preflight_remote(monkeypatch)
    assert launch(env) == gv.EXIT_OK
    assert calls["n"] == 1


def test_preflights_rerun_after_a_wait(env, monkeypatch):
    calls = _count_preflight_remote(monkeypatch)
    env["fake"].logging_responses = [[RECENT], [RECENT]]
    assert launch(env) == gv.EXIT_OK
    assert calls["n"] == 2


def test_recheck_refuses_when_a_batch_started_during_the_wait(env):
    fake = env["fake"]
    fake.logging_responses = [[RECENT], [RECENT]]
    original = env["ctx"].sleep

    def batch_starts(seconds):
        fake.batches["other"] = {"state": "RUNNING", "states": []}
        original(seconds)

    env["ctx"].sleep = batch_starts
    assert launch(env) == gv.EXIT_REFUSED
    assert "a Dataproc batch is active" in text(env)
    assert _indexes(env, "gcloud", "compute", "instances", "create") == []


def test_ctrl_c_during_recheck_creates_no_vm(env, monkeypatch):
    env["fake"].logging_responses = [[RECENT], [RECENT]]
    monkeypatch.setattr(gv, "recheck_after_wait", _interrupt)
    assert launch(env) == gv.EXIT_INTERRUPTED
    assert _indexes(env, "gcloud", "compute", "instances", "create") == []
    assert "INTERRUPTED during the IAM propagation wait or its re-check: no VM was created" in text(env)


def test_audit_log_unreadable_warns_and_launches(env):
    env["fake"].fail_on = ("gcloud logging read",)
    assert launch(env) == gv.EXIT_OK
    assert len(_indexes(env, "gcloud", "compute", "instances", "create")) == 1
    assert "WARNING: could not read the Admin Activity audit log" in text(env)


def test_template_is_resolved_after_the_wait(env):
    fake = env["fake"]
    fake.logging_responses = [[RECENT], [RECENT]]
    original = env["ctx"].sleep

    def terraform_replaced_the_template(seconds):
        fake.templates = {"synthea-generator-pilot-3600s": 3600}
        original(seconds)

    env["ctx"].sleep = terraform_replaced_the_template
    assert launch(env) == gv.EXIT_OK, text(env)
    (create,) = [c for c in fake.calls if c[:4] == ["gcloud", "compute", "instances", "create"]]
    assert any(arg.endswith("/instanceTemplates/synthea-generator-pilot-3600s") for arg in create)
    assert gv.positive_control_ok_message(gv.instance_name("pilot_20k", env["ctx"].now()), 3600) in text(env)


def test_missing_template_is_refused_before_any_iam_wait(env):
    env["fake"].templates = {}
    env["fake"].logging_responses = [[RECENT], [RECENT]]
    slept = _recording_sleep(env)
    assert launch(env) == gv.EXIT_REFUSED
    assert slept == []
    assert _indexes(env, "gcloud", "logging", "read") == []
    assert "need exactly one instance template" in text(env)

