"""The Big Join submitter (ops/dataproc_batch.py) against FakeGcloud: the submit argv (FR14 cap, runtime 3.0, prereg-derived
arguments, provenance), every preflight refusal including P7 (raw run vs prereg), usage parsing and the failure paths."""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from readmission_risk.ops import dataproc_batch as db
from readmission_risk.ops import generation_vm as gv
from readmission_risk.ops.prereg import PreregRecord, load_prereg
from tests.ops.conftest import REPO
from tests.ops.fake_gcloud import AUDIT_ROLE, FakeGcloud, audit_entry

PREREG = load_prereg(REPO / "prereg.yaml")
RECORD = PreregRecord(head_commit="a" * 40, prereg_commit="b" * 40, prereg_committed_at="2026-10-12T10:00:00+02:00", sha256="c" * 64)
RUN = "pilot_20k"
RAW = gv.raw_prefix(RUN)


def raw_summary(**overrides) -> dict:
    gen, pop = PREREG.generation, PREREG.populations[RUN]
    summary = {
        "seed": pop.seed, "clinician_seed": pop.clinician_seed, "population_size": pop.population,
        "reference_date": gen["reference_date"], "end_date": gen["end_date"], "jvm_timezone": gen["jvm_timezone"],
        "state": gen["state"], "years_of_history": gen["years_of_history"], "included_files": gen["included_files"],
        "n_alive_patients": pop.population,
        "provenance": {"synthea_jar_sha256": gen["synthea_jar_sha256"], "prereg_commit": "b" * 40},
    }  # fmt: skip
    summary.update(overrides)
    return summary


@pytest.fixture
def env(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    for rel in ("prereg.yaml", "scripts/run_big_join.py"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, repo / rel)
    monkeypatch.setattr(db, "check_prereg_committed", lambda repo_root, **kw: RECORD)

    def fake_zip(repo_root, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "readmission_risk_spark_job-000000000000.zip"
        path.write_bytes(b"zip")
        return path, "d" * 64

    monkeypatch.setattr(db, "build_spark_zip", fake_zip)
    fake = FakeGcloud()
    for table in PREREG.generation["included_files"]:
        fake.objects[f"{RAW}/csv/{table}"] = b"h\n1\n"
    fake.objects[f"{RAW}/generation_summary.json"] = json.dumps(raw_summary()).encode()
    fake.objects[f"{RAW}/run_provenance.json"] = b"{}"
    fake.objects[f"{RAW}/_DONE"] = b""
    echoed: list[str] = []
    ancestors = {"b" * 40}

    def run_git(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0 if argv[-2] in ancestors else 1, stdout="", stderr="")

    ctx = gv.LaunchContext(repo_root=repo, run=fake, sleep=lambda s: None, now=lambda: datetime(2026, 10, 13, 10, 0, tzinfo=UTC),
                           run_git=run_git, echo=echoed.append)  # fmt: skip
    return {"fake": fake, "ctx": ctx, "options": db.SubmitOptions(run=RUN), "echoed": echoed, "repo": repo, "ancestors": ancestors}


def submit(env) -> int:
    return db.submit(env["options"], env["ctx"])


def text(env) -> str:
    return "\n".join(env["echoed"])


def test_submit_argv(env):
    assert submit(env) == db.EXIT_OK, text(env)
    (argv,) = env["fake"].ran("gcloud", "dataproc", "batches", "submit", "pyspark")
    assert argv[5].startswith("gs://synthea-readmission-risk-artifacts/sha256/") and argv[5].endswith("/run_big_join.py")
    for flag in ("--async", "--batch=bigjoin-pilot-20k-202610131000", "--region=us-central1", "--version=3.0",
                 "--service-account=dataproc-runtime@synthea-readmission-risk.iam.gserviceaccount.com",
                 "--subnet=projects/synthea-readmission-risk/regions/us-central1/subnetworks/spark-us-central1",
                 "--ttl=2h"):  # fmt: skip
        assert flag in argv, flag
    # 5b: runtime 3.0 rejects the field ("3.0 runtime version does not support configuring Dataproc staging bucket via
    # the staging_bucket field"; 3.0+ runtimes don't use Cloud Storage staging buckets)
    assert not [a for a in argv if a.startswith("--staging-bucket")]
    properties = next(a for a in argv if a.startswith("--properties=")).removeprefix("--properties=").split(",")
    assert {"dataproc.tier=standard", "spark.dynamicAllocation.maxExecutors=7", "spark.sql.shuffle.partitions=128"} <= set(properties)
    job = argv[argv.index("--") + 1 :]
    assert job[:6] == ["--input-dir", RAW, "--output-dir", gv.gold_prefix(RUN), "--reference-date", "20261012"]
    assert "--spark-master" in job and job[job.index("--spark-master") + 1] == "external"
    provenance = [job[i + 1] for i, a in enumerate(job) if a == "--provenance"]
    assert {p.split("=")[0] for p in provenance} == {"head_commit", "zip_sha256", "main_sha256", "prereg_commit", "batch_id"}


def test_job_arguments_come_from_the_prereg():
    raw = json.loads(json.dumps(PREREG.raw))
    raw["label"]["lookback_years"], raw["label"]["readmission_window_days"] = 2, 14
    prereg = type(PREREG)(raw=raw, populations=PREREG.populations)
    main, zip_ = gv.Artifact(REPO / "scripts/run_big_join.py", "e" * 64), gv.Artifact(REPO / "prereg.yaml", "d" * 64)
    job = db.submit_argv("b1", RUN, prereg, RECORD, main, zip_)
    assert job[job.index("--lookback-years") + 1] == "2" and job[job.index("--readmission-window-days") + 1] == "14"


def test_batch_record_and_usage_printed(env):
    assert submit(env) == db.EXIT_OK
    record = json.loads((env["repo"] / "data/pilot/pilot_20k/batch_bigjoin-pilot-20k-202610131000.json").read_text())
    assert record["usage"]["dcu_hours"] == pytest.approx(2.0)
    assert record["provenance"]["head_commit"] == "a" * 40
    assert record["never_ran"] is False
    assert record["interrupted"] is False and record["usage_wait_error"] is None
    assert "DCU-hours=2.000" in text(env) and "runtime=3.0.14" in text(env)


def test_parse_batch_usage():
    usage = db.parse_batch_usage({
        "state": "SUCCEEDED", "createTime": "2026-10-13T10:00:00Z", "stateTime": "2026-10-13T10:12:30.5Z",
        "runtimeConfig": {"version": "3.0.14"},
        "runtimeInfo": {"approximateUsage": {"milliDcuSeconds": "5400000", "shuffleStorageGbSeconds": 7200}},
    })  # fmt: skip
    assert usage == db.BatchUsage("SUCCEEDED", "3.0.14", 750.5, 1.5, 2.0)


def test_parse_batch_usage_missing_usage():
    usage = db.parse_batch_usage({"state": "SUCCEEDED"})
    assert (usage.dcu_hours, usage.shuffle_gb_hours, usage.wall_seconds) == (None, None, None)


def test_usage_not_yet_populated_is_reported(env):
    env["fake"].batch_usage = None
    assert submit(env) == db.EXIT_OK
    assert "not yet populated" in text(env)


def test_failed_batch_exit_2_prints_cleanup(env):
    env["fake"].batch_states = ["RUNNING", "FAILED"]
    assert submit(env) == db.EXIT_FAILED
    assert "gcloud storage rm -r gs://synthea-readmission-risk-gold/v2/pilot_20k/" in text(env)


# --- a batch that never ran (ops bundle, notes/eg-new-feature/ops-submitter-launcher-polish-2026-10-12.md) ---

# Verbatim from the 5b pilot's refused batch bigjoin-pilot-20k-202610121318 (2026-10-12)
PERMISSION_FAILURE_5B = (
    "Permission validation failed for operation e5cc8693-6dc6-3f0c-b137-92d436f891e2: Service Account "
    "'dataproc-runtime@synthea-readmission-risk.iam.gserviceaccount.com' is missing required permissions: "
    "[dataprocrm.nodePools.update]."
)


def _history(*states: str) -> list[dict]:
    return [{"state": s} for s in states]


@pytest.mark.parametrize(
    ("describe", "expected"),
    [
        ({"state": "FAILED", "stateHistory": _history("PENDING")}, True),
        ({"state": "CANCELLED", "stateHistory": _history("PENDING")}, True),
        ({"state": "FAILED", "stateHistory": _history("PENDING", "RUNNING")}, False),
        ({"state": "SUCCEEDED", "stateHistory": _history("PENDING", "RUNNING")}, False),
        ({"state": "SUCCEEDED", "stateHistory": _history("PENDING")}, False),  # a SUCCEEDED batch always ran
        ({"state": "RUNNING", "stateHistory": _history("PENDING")}, False),  # not terminal
        ({"state": "FAILED"}, False),  # no history: wait as before
        ({"state": "FAILED", "stateHistory": []}, False),
        ({"state": "FAILED", "stateHistory": "PENDING"}, False),
        ({"state": "FAILED", "stateHistory": ["PENDING"]}, False),
        ({"state": "FAILED", "stateHistory": [{}]}, False),
        ({"state": "FAILED", "stateHistory": [{"state": None}]}, False),
    ],
)
def test_never_ran(describe, expected):
    assert db.never_ran(describe) is expected


def test_permission_hint():
    assert db.permission_hint(PERMISSION_FAILURE_5B) == db.PERMISSION_HINT
    for message in ("simulated", "", None, 42, "permission validation failed"):
        assert db.permission_hint(message) is None, message


def test_fake_state_history_matches_real_shape():
    fake = FakeGcloud(batch_states=["RUNNING", "SUCCEEDED"])
    fake(["gcloud", "dataproc", "batches", "submit", "pyspark", "gs://x/main.py", "--batch=b1"])
    describe = ["gcloud", "dataproc", "batches", "describe", "b1", "--region=us-central1"]
    fake(describe)
    second = json.loads(fake(describe).stdout)
    assert second["state"] == "SUCCEEDED"
    assert [h["state"] for h in second["stateHistory"]] == ["PENDING", "RUNNING"]


def _record(env) -> dict:
    return json.loads((env["repo"] / "data/pilot/pilot_20k/batch_bigjoin-pilot-20k-202610131000.json").read_text())


def test_never_ran_batch_skips_usage_wait(env):
    fake = env["fake"]
    fake.batch_states, fake.batch_usage, fake.batch_state_message = ["FAILED"], None, PERMISSION_FAILURE_5B
    slept: list[float] = []
    env["ctx"].sleep = slept.append
    assert submit(env) == db.EXIT_FAILED
    assert len(fake.ran("gcloud", "dataproc", "batches", "describe")) == 1
    assert slept == []
    out = text(env)
    assert "stateHistory never reached RUNNING: skipping the usage wait" in out
    assert "DCU-hours=none (stateHistory never reached RUNNING" in out
    assert "holds partial output" in out  # the cleanup line is always printed
    assert db.PERMISSION_HINT in out
    assert _record(env)["never_ran"] is True


def test_usage_present_wins_over_never_ran(env):
    env["fake"].batch_states = ["FAILED"]  # the fake's default usage is present
    assert submit(env) == db.EXIT_FAILED
    assert len(env["fake"].ran("gcloud", "dataproc", "batches", "describe")) == 1
    out = text(env)
    assert "stateHistory never reached RUNNING: skipping the usage wait" in out
    assert "DCU-hours=2.000" in out and "DCU-hours=none" not in out
    assert _record(env)["never_ran"] is True


def test_failed_after_running_still_waits_for_usage(env):
    env["fake"].batch_states, env["fake"].batch_usage = ["RUNNING", "FAILED"], None
    assert submit(env) == db.EXIT_FAILED
    assert len(env["fake"].ran("gcloud", "dataproc", "batches", "describe")) == 2 + db.USAGE_WAIT_SECONDS // 30
    out = text(env)
    assert "holds partial output" in out and db.PERMISSION_HINT not in out
    assert "skipping the usage wait" not in out
    assert _record(env)["never_ran"] is False


def test_keyboard_interrupt_exit_130_prints_cancel(env):
    env["fake"].batch_states = ["RUNNING", "RUNNING", "SUCCEEDED"]

    def interrupt(seconds):
        raise KeyboardInterrupt

    env["ctx"].sleep = interrupt
    assert submit(env) == db.EXIT_INTERRUPTED
    assert "gcloud dataproc batches cancel bigjoin-pilot-20k-202610131000" in text(env)


def test_dry_run_makes_no_runner_call(env):
    env["options"] = db.SubmitOptions(run=RUN, dry_run=True)
    assert submit(env) == db.EXIT_OK
    assert env["fake"].calls == []
    assert "would run: gcloud dataproc batches submit pyspark" in text(env)
    assert "would check the Admin Activity audit log" in text(env)


# --- refusals ---


def _refused(env, match: str) -> None:
    assert submit(env) == db.EXIT_REFUSED, text(env)
    assert match in text(env), text(env)
    assert env["fake"].ran("gcloud", "dataproc", "batches", "submit") == []


def test_main_run_requires_flag(env):
    env["options"] = db.SubmitOptions(run="main_200k")
    _refused(env, "--allow-main-run")


def test_refuses_while_generation_vm_exists(env):
    env["fake"].instances["synthea-gen-x"] = {"labels": {"purpose": "synthea-generation"}}
    _refused(env, "share the 32-vCPU quota")


def test_refuses_another_active_batch(env):
    env["fake"].batches["other"] = {"state": "RUNNING", "states": []}
    _refused(env, "another Dataproc batch is active")


def test_refuses_incomplete_raw(env):
    del env["fake"].objects[f"{RAW}/_DONE"]
    _refused(env, "does not hold exactly a finished generation run")


def test_refuses_non_empty_gold(env):
    env["fake"].objects[f"{gv.gold_prefix(RUN)}/part-0.parquet"] = b"x"
    _refused(env, "is not empty; to retry, the owner deletes it")


def test_refuses_disk_quota(env):
    env["fake"].quotas["DISKS_TOTAL_GB"] = (4096, 2000)
    _refused(env, "DISKS_TOTAL_GB")


@pytest.mark.parametrize(
    "field, value",
    [("seed", 18), ("clinician_seed", 18), ("population_size", 19999), ("end_date", "20261012"), ("jvm_timezone", None),
     ("included_files", ["patients.csv"]), ("n_alive_patients", 19999)],
)  # fmt: skip
def test_p7_refuses_seed_mismatch(env, field, value):
    env["fake"].objects[f"{RAW}/generation_summary.json"] = json.dumps(raw_summary(**{field: value})).encode()
    _refused(env, field)


def test_p7_refuses_wrong_jar(env):
    summary = raw_summary(provenance={"synthea_jar_sha256": "0" * 64, "prereg_commit": "b" * 40})
    env["fake"].objects[f"{RAW}/generation_summary.json"] = json.dumps(summary).encode()
    _refused(env, "synthea_jar_sha256")


def test_p7_refuses_raw_from_other_prereg(env):
    env["ancestors"].clear()
    _refused(env, "is not an ancestor of HEAD")


def test_p7_accepts_raw_from_an_ancestor_prereg(env):
    summary = raw_summary(provenance={"synthea_jar_sha256": PREREG.generation["synthea_jar_sha256"], "prereg_commit": "f" * 40})
    env["fake"].objects[f"{RAW}/generation_summary.json"] = json.dumps(summary).encode()
    env["ancestors"].add("f" * 40)
    assert submit(env) == db.EXIT_OK, text(env)


def test_runtime_sa_and_subnet_match_terraform():
    tf = "\n".join(p.read_text() for p in (REPO / "infra").glob("*.tf"))
    assert 'account_id   = "dataproc-runtime"' in tf
    assert 'name                     = "spark-us-central1"' in tf
    assert db.MAX_EXECUTORS == 7 and db.RUNTIME_VERSION == "3.0"


# --- IAM propagation wait and finished-batch records (notes/eg-new-feature/ops-iam-propagation-and-interrupt-2026-10-12.md) ---

NOW = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)  # the env fixture's clock
ROLE = AUDIT_ROLE


def _seconds(n: int) -> timedelta:
    return timedelta(seconds=n)


def _calls(env, *prefix: str) -> list[int]:
    return [i for i, call in enumerate(env["fake"].calls) if call[: len(prefix)] == list(prefix)]


def test_submit_waits_for_recent_iam_change(env):
    fake = env["fake"]
    fake.batch_states = ["SUCCEEDED"]
    entry = audit_entry("google.iam.admin.v1.UpdateRole", NOW - _seconds(60))
    # the re-check returns the same change again (its filter is rounded down to the second); it must not count as newer
    fake.logging_responses = [[entry], [entry]]
    slept: list[float] = []
    env["ctx"].sleep = slept.append
    assert submit(env) == db.EXIT_OK, text(env)
    assert slept == [420.0]
    reads = _calls(env, "gcloud", "logging", "read")
    assert len(reads) == 2
    assert 'timestamp>="2026-10-13T09:59:00Z"' in fake.calls[reads[1]][3]
    assert reads[-1] < _calls(env, "gcloud", "dataproc", "batches", "submit")[0]
    out = text(env)
    assert f"google.iam.admin.v1.UpdateRole on {ROLE} by operator@example.com at 09:59:00Z" in out
    assert "Waiting 420 s for IAM propagation" in out


def test_system_principal_change_does_not_wait(env):
    fake = env["fake"]
    fake.batch_states = ["SUCCEEDED"]
    fake.logging_responses = [
        [audit_entry("SetIamPolicy", NOW - _seconds(30), principal="service-agent-manager@system.gserviceaccount.com")]
    ]
    slept: list[float] = []
    env["ctx"].sleep = slept.append
    assert submit(env) == db.EXIT_OK
    assert slept == []
    assert "IAM changed" not in text(env)


def test_iam_keeps_changing_refuses(env):
    fake = env["fake"]
    fake.batch_states = ["SUCCEEDED"]
    fake.logging_responses = [[audit_entry("google.iam.admin.v1.UpdateRole", NOW - _seconds(60 - i))] for i in range(4)]
    slept: list[float] = []
    env["ctx"].sleep = slept.append
    assert submit(env) == db.EXIT_REFUSED
    assert len(slept) == 3
    assert len(_calls(env, "gcloud", "logging", "read")) == 4
    assert _calls(env, "gcloud", "dataproc", "batches", "submit") == []
    assert "IAM kept changing across 3 propagation waits" in text(env)


def test_audit_log_unreadable_warns_and_submits(env):
    env["fake"].fail_on = ("gcloud logging read",)
    assert submit(env) == db.EXIT_OK
    assert len(_calls(env, "gcloud", "dataproc", "batches", "submit")) == 1
    assert "WARNING: could not read the Admin Activity audit log" in text(env)
    assert "ERROR: simulated failure" in text(env)


def test_audit_log_non_json_warns_and_submits(env):
    env["fake"].logging_responses = ["not json"]
    assert submit(env) == db.EXIT_OK
    assert len(_calls(env, "gcloud", "dataproc", "batches", "submit")) == 1
    assert "WARNING: could not read the Admin Activity audit log" in text(env)


def test_ctrl_c_during_iam_wait_submits_nothing(env):
    env["fake"].logging_responses = [[audit_entry("google.iam.admin.v1.UpdateRole", NOW - _seconds(60))]]

    def interrupt(seconds):
        raise KeyboardInterrupt

    env["ctx"].sleep = interrupt
    assert submit(env) == db.EXIT_INTERRUPTED
    assert _calls(env, "gcloud", "dataproc", "batches", "submit") == []
    assert "INTERRUPTED during the IAM propagation wait or its re-check: no batch was submitted" in text(env)


def test_ctrl_c_during_recheck_after_wait_submits_nothing(env, monkeypatch):
    fake = env["fake"]
    entry = audit_entry("google.iam.admin.v1.UpdateRole", NOW - _seconds(60))
    fake.logging_responses = [[entry], [entry]]

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(db, "recheck_after_wait", interrupt)
    assert submit(env) == db.EXIT_INTERRUPTED
    assert _calls(env, "gcloud", "dataproc", "batches", "submit") == []
    assert "INTERRUPTED during the IAM propagation wait or its re-check: no batch was submitted" in text(env)


def test_describe_error_while_running_says_batch_may_still_run(env):
    fake = env["fake"]
    fake.batch_states, fake.describe_failures_after = ["RUNNING", "SUCCEEDED"], 1
    assert submit(env) == db.EXIT_FAILED
    out = text(env)
    assert "FAILED to monitor batch bigjoin-pilot-20k-202610131000: ERROR: simulated failure" in out
    assert "gcloud dataproc batches cancel bigjoin-pilot-20k-202610131000" in out
    assert not (env["repo"] / "data/pilot/pilot_20k/batch_bigjoin-pilot-20k-202610131000.json").exists()


def test_preflight_rechecked_after_iam_wait(env):
    fake = env["fake"]
    fake.batch_states = ["SUCCEEDED"]
    entry = audit_entry("google.iam.admin.v1.UpdateRole", NOW - _seconds(60))
    fake.logging_responses = [[entry], [entry]]

    def another_batch_starts(seconds):
        fake.batches["other"] = {"state": "RUNNING", "states": []}

    env["ctx"].sleep = another_batch_starts
    assert submit(env) == db.EXIT_REFUSED
    assert "another Dataproc batch is active" in text(env)
    assert _calls(env, "gcloud", "dataproc", "batches", "submit") == []


def test_commit_change_during_iam_wait_refuses(env, monkeypatch):
    fake = env["fake"]
    fake.batch_states = ["SUCCEEDED"]
    entry = audit_entry("google.iam.admin.v1.UpdateRole", NOW - _seconds(60))
    fake.logging_responses = [[entry], [entry]]
    records = [RECORD, dataclasses.replace(RECORD, head_commit="e" * 40)]
    monkeypatch.setattr(db, "check_prereg_committed", lambda repo_root, **kw: records.pop(0) if len(records) > 1 else records[0])
    assert submit(env) == db.EXIT_REFUSED
    assert _calls(env, "gcloud", "dataproc", "batches", "submit") == []
    assert "HEAD or the prereg commit changed during the IAM wait" in text(env)


def test_no_wait_no_second_preflight(env):
    assert submit(env) == db.EXIT_OK
    assert len(_calls(env, "gcloud", "dataproc", "batches", "list")) == 1


def test_ctrl_c_during_usage_wait_after_succeeded_writes_record(env):
    env["fake"].batch_states, env["fake"].batch_usage = ["SUCCEEDED"], None

    def interrupt(seconds):
        raise KeyboardInterrupt

    env["ctx"].sleep = interrupt
    assert submit(env) == db.EXIT_INTERRUPTED
    record = _record(env)
    assert record["interrupted"] is True and record["never_ran"] is False
    assert "had already ended SUCCEEDED" in text(env) and "keeps running" not in text(env)


def test_ctrl_c_during_usage_wait_after_failed_returns_2(env):
    env["fake"].batch_states, env["fake"].batch_usage = ["RUNNING", "FAILED"], None
    calls = {"n": 0}

    def second_sleep_interrupts(seconds):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt

    env["ctx"].sleep = second_sleep_interrupts
    assert submit(env) == db.EXIT_FAILED
    assert _record(env)["interrupted"] is True
    assert "holds partial output" in text(env) and "had already ended FAILED" in text(env)


def test_describe_error_during_usage_wait_still_writes_record(env):
    fake = env["fake"]
    fake.batch_states, fake.batch_usage, fake.describe_failures_after = ["SUCCEEDED"], None, 1
    assert submit(env) == db.EXIT_OK
    assert _record(env)["interrupted"] is False
    assert _record(env)["usage_wait_error"] == "ERROR: simulated failure"
    assert "WARNING: describe failed during the usage wait" in text(env)
    assert "ERROR: simulated failure" in text(env)


@pytest.mark.parametrize("output", ["not json", ""])
def test_malformed_describe_during_usage_wait_still_writes_record(env, output):
    fake = env["fake"]
    fake.batch_states, fake.batch_usage, fake.describe_failures_after = ["SUCCEEDED"], None, 1
    fake.describe_failure_output = output
    assert submit(env) == db.EXIT_OK
    assert _record(env)["batch_id"] == "bigjoin-pilot-20k-202610131000"
    assert "not a JSON object" in _record(env)["usage_wait_error"] or "Expecting value" in _record(env)["usage_wait_error"]
    assert "WARNING: describe failed during the usage wait" in text(env)

