"""A stateful fake of the gcloud CLI for the slice-5 operations tests: it understands exactly the argv shapes ops/ builds and keeps
instances, disks, objects, batches and templates in memory. Every call is recorded in .calls."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime

GENERATOR_SA = "synthea-generator@synthea-readmission-risk.iam.gserviceaccount.com"


def _ok(stdout: object = "") -> subprocess.CompletedProcess:
    text = stdout if isinstance(stdout, str) else json.dumps(stdout)
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=text, stderr="")


def _no_match() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="ERROR: One or more URLs matched no objects.")


@dataclass
class FakeGcloud:
    objects: dict[str, bytes] = field(default_factory=dict)  # gs:// uri -> content
    instances: dict[str, dict] = field(default_factory=dict)
    disks: dict[str, dict] = field(default_factory=dict)
    batches: dict[str, dict] = field(default_factory=dict)
    templates: dict[str, int] = field(default_factory=lambda: {"synthea-generator-pilot-7200s": 7200})
    quotas: dict[str, tuple[float, float]] = field(
        default_factory=lambda: {
            "CPUS": (200, 0), "N2_CPUS": (200, 0), "DISKS_TOTAL_GB": (4096, 0), "CPUS_ALL_REGIONS": (32, 0),
        }
    )  # fmt: skip
    calls: list[list[str]] = field(default_factory=list)
    # behaviour switches
    on_create: object = None  # callable(fake, name) run after an instance is created (e.g. write _DONE)
    instance_overrides: dict = field(default_factory=dict)  # merged into a created instance's describe
    disk_labels: dict = field(default_factory=lambda: {"purpose": "synthea-generation"})
    keep_disk_after_delete: bool = False
    batch_states: list[str] = field(default_factory=lambda: ["RUNNING", "SUCCEEDED"])
    batch_usage: dict | None = field(default_factory=lambda: {"milliDcuSeconds": "7200000", "shuffleStorageGbSeconds": "36000"})
    batch_state_message: str | None = None  # replaces "simulated" as a non-SUCCEEDED batch's stateMessage
    logging_responses: list = field(default_factory=list)  # one per `gcloud logging read`; a str is returned verbatim; [] when empty
    describe_failures_after: int | None = None  # each batch's describes after this many fail (or return describe_failure_output)
    describe_failure_output: str | None = None  # when set, the failing describe exits 0 with this stdout (malformed output)
    fail_on: tuple[str, ...] = ()  # argv prefixes (joined with spaces) that exit 1 with a generic error

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        joined = " ".join(argv)
        if any(joined.startswith(prefix) for prefix in self.fail_on):
            return subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr="ERROR: simulated failure")
        args = [a for a in argv if a != "--format=json"]
        head = tuple(args[1:4])
        if args[1:3] == ["storage", "ls"]:
            if "--recursive" in args:
                prefix = args[-1].removesuffix("/**") + "/"
                found = sorted(u for u in self.objects if u.startswith(prefix))
                return _ok("\n".join(found)) if found else _no_match()
            return _ok(args[-1]) if args[-1] in self.objects else _no_match()
        if args[1:3] == ["logging", "read"]:
            return _ok(self.logging_responses.pop(0) if self.logging_responses else [])
        if args[1:3] == ["storage", "cat"]:
            return _ok(self.objects[args[3]].decode()) if args[3] in self.objects else _no_match()
        if args[1:3] == ["storage", "cp"]:
            src, dst = args[3], args[4]
            if dst in self.objects:
                return subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr="412 Precondition Failed")
            with open(src, "rb") as f:
                self.objects[dst] = f.read()
            return _ok()
        if head == ("storage", "objects", "describe"):
            import base64
            import hashlib

            data = self.objects[args[4]]
            return _ok({"name": args[4], "size": len(data), "md5_hash": base64.b64encode(hashlib.md5(data).digest()).decode()})  # noqa: S324
        if head in (("compute", "instances", "list"), ("compute", "disks", "list")):
            store = self.instances if head[1] == "instances" else self.disks
            return _ok([{"name": n, **v} for n, v in store.items() if v.get("labels", {}).get("purpose") == "synthea-generation"])
        if head == ("compute", "instances", "create"):
            name = args[4]
            labels = dict(kv.split("=", 1) for kv in _flag(args, "--labels").split(","))
            items = [{"key": k, "value": v} for k, v in (kv.split("=", 1) for kv in _flag(args, "--metadata").split(","))]
            script_path = _flag(args, "--metadata-from-file").split("=", 1)[1]
            with open(script_path, encoding="utf-8") as f:
                items.append({"key": "startup-script", "value": f.read()})
            template = _flag(args, "--source-instance-template").rsplit("/", 1)[1]
            self.instances[name] = {
                "labels": labels,
                "metadata": {"items": items},
                "scheduling": {"provisioningModel": "SPOT", "instanceTerminationAction": "DELETE",
                               "maxRunDuration": {"seconds": str(self.templates[template])}},
                "networkInterfaces": [{"subnetwork": "spark-us-central1"}],
                "serviceAccounts": [{"email": GENERATOR_SA}],
                **self.instance_overrides,
            }  # fmt: skip
            self.disks[name] = {"labels": dict(self.disk_labels), "sizeGb": "200"}
            if self.on_create is not None:
                self.on_create(self, name)
            return _ok()
        if head in (("compute", "instances", "describe"), ("compute", "disks", "describe")):
            store = self.instances if head[1] == "instances" else self.disks
            if args[4] not in store:
                return subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr=f"ERROR: {args[4]} was not found")
            return _ok(store[args[4]])
        if head == ("compute", "instances", "delete"):
            self.instances.pop(args[4], None)
            if not self.keep_disk_after_delete:
                self.disks.pop(args[4], None)
            return _ok()
        if head == ("compute", "regions", "describe") or head == ("compute", "project-info", "describe"):
            regional = head[1] == "regions"
            quotas = [
                {"metric": m, "limit": limit, "usage": usage}
                for m, (limit, usage) in self.quotas.items()
                if (m != "CPUS_ALL_REGIONS") == regional
            ]
            return _ok({"quotas": quotas})
        if head == ("compute", "instance-templates", "list"):
            return _ok([{"name": n} for n in self.templates])
        if head == ("compute", "instance-templates", "describe"):
            return _ok({"properties": {"scheduling": {"maxRunDuration": {"seconds": str(self.templates[args[4]])}}}})
        if head == ("dataproc", "batches", "list"):
            return _ok([{"name": n} for n, b in self.batches.items() if b["state"] in ("RUNNING", "PENDING")])
        if head == ("dataproc", "batches", "submit"):
            batch_id = _flag(args, "--batch")
            self.batches[batch_id] = {"state": "PENDING", "states": list(self.batch_states), "history": ["PENDING"], "describes": 0}
            return _ok()
        if head == ("dataproc", "batches", "describe"):
            batch = self.batches[args[4]]
            batch["describes"] = batch.get("describes", 0) + 1
            if self.describe_failures_after is not None and batch["describes"] > self.describe_failures_after:
                if self.describe_failure_output is not None:
                    return subprocess.CompletedProcess(args=argv, returncode=0, stdout=self.describe_failure_output, stderr="")
                return subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr="ERROR: simulated failure")
            previous = batch["state"]
            if batch["states"]:
                batch["state"] = batch["states"].pop(0)
            # Like Dataproc's stateHistory: past states only, the current one excluded
            if batch["state"] != previous and previous not in ("SUCCEEDED", "FAILED", "CANCELLED") and previous != batch["history"][-1]:
                batch["history"].append(previous)
            describe = {
                "state": batch["state"],
                "createTime": "2026-10-13T10:00:00.000Z",
                "stateTime": "2026-10-13T10:12:30.000Z",
                "runtimeConfig": {"version": "3.0.14"},
                "stateMessage": ("simulated" if self.batch_state_message is None else self.batch_state_message) if batch["state"] != "SUCCEEDED" else "",
                "stateHistory": [{"state": s, "stateStartTime": "2026-10-13T10:00:00.000Z"} for s in batch["history"]],
            }
            if batch["state"] in ("SUCCEEDED", "FAILED", "CANCELLED") and self.batch_usage is not None:
                describe["runtimeInfo"] = {"approximateUsage": dict(self.batch_usage)}
            return _ok(describe)
        raise AssertionError(f"FakeGcloud does not understand: {joined}")

    def ran(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if c[: len(prefix)] == list(prefix)]


def _flag(args: list[str], name: str) -> str:
    for a in args:
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    raise AssertionError(f"{name} not in {args}")


AUDIT_ROLE = "projects/synthea-readmission-risk/roles/dataprocRuntimeWorker"


def audit_entry(method: str, at: datetime, principal: str = "operator@example.com", resource: str = AUDIT_ROLE) -> dict:
    """An Admin Activity entry shaped like gcloud's JSON, with a nanosecond-style timestamp on purpose."""
    return {
        "timestamp": at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "123Z",
        "protoPayload": {"methodName": method, "authenticationInfo": {"principalEmail": principal}, "resourceName": resource},
    }
