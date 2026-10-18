"""The shared pre-submit IAM-propagation wait (ops/iam_propagation.py): the audit-log query, the entry parser, the wait loop
and the guarantee that the module imports neither CLI module (the reason it exists). The submitter's and the launcher's use of
it are tested through their CLIs in test_dataproc_batch.py and test_generation_vm.py."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from readmission_risk.ops import iam_propagation as iam
from tests.ops.conftest import REPO
from tests.ops.fake_gcloud import AUDIT_ROLE, FakeGcloud, audit_entry

NOW = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)  # the moved parse test's naive-timestamp case relies on this exact value
ROLE = AUDIT_ROLE


def _seconds(n: int) -> timedelta:
    return timedelta(seconds=n)


def test_iam_changes_argv():
    assert iam.iam_changes_argv(datetime(2026, 10, 12, 13, 24, tzinfo=UTC)) == [
        "gcloud", "logging", "read",
        'logName="projects/synthea-readmission-risk/logs/cloudaudit.googleapis.com%2Factivity" AND '
        'timestamp>="2026-10-12T13:24:00Z" AND protoPayload.methodName=("SetIamPolicy" OR '
        '"google.iam.admin.v1.SetIAMPolicy" OR "google.iam.admin.v1.CreateRole" OR "google.iam.admin.v1.UpdateRole" OR '
        '"google.iam.admin.v1.DeleteRole" OR "google.iam.admin.v1.UndeleteRole" OR "storage.setIamPermissions")',
        "--limit=100",
    ]  # fmt: skip


def test_parse_iam_changes():
    after = NOW - _seconds(480)
    update = "google.iam.admin.v1.UpdateRole"
    no_auth = audit_entry("SetIamPolicy", NOW - _seconds(90))
    del no_auth["protoPayload"]["authenticationInfo"]
    bad_auth = audit_entry(update, NOW - _seconds(50))
    bad_auth["protoPayload"]["authenticationInfo"] = "x"
    bad_principal = audit_entry(update, NOW - _seconds(40))
    bad_principal["protoPayload"]["authenticationInfo"]["principalEmail"] = 7
    garbage = audit_entry(update, NOW - _seconds(70))
    garbage["timestamp"] = "garbage"
    naive = audit_entry(update, NOW - _seconds(30))
    naive["timestamp"] = "2026-10-13T09:59:30"
    at_cutoff = audit_entry(update, after)
    at_cutoff["timestamp"] = after.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"
    entries = [
        audit_entry(update, NOW - _seconds(60)),
        audit_entry("SetIamPolicy", NOW - _seconds(30), principal="service-agent-manager@system.gserviceaccount.com"),
        audit_entry("storage.setIamPermissions", NOW - _seconds(120)),
        audit_entry("google.iam.admin.v1.GetRole", NOW - _seconds(10)),
        audit_entry(update, NOW - _seconds(600)),
        no_auth,
        garbage,
        "x",
        naive,
        bad_auth,
        bad_principal,
        audit_entry("SetIamPolicy", NOW - _seconds(20), principal="service-271458030145@gcp-sa-dataprocrmnode.iam.gserviceaccount.com"),
        audit_entry("SetIamPolicy", NOW - _seconds(15), principal="271458030145@cloudservices.gserviceaccount.com"),
        at_cutoff,
    ]
    changes = iam.parse_iam_changes(entries, after=after)
    assert [(NOW - c.timestamp).total_seconds() for c in changes] == pytest.approx([120, 90, 60, 50, 40], abs=1e-3)
    assert [c.principal for c in changes] == ["operator@example.com", "", "operator@example.com", "", ""]
    assert changes[0].method == "storage.setIamPermissions" and changes[2].resource == ROLE
    assert iam.parse_iam_changes(None, after=after) == []
    assert iam.parse_iam_changes({"a": 1}, after=after) == []


def test_module_imports_neither_cli_module():
    tree = ast.parse((REPO / "src/readmission_risk/ops/iam_propagation.py").read_text())
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names += [node.module or "", *(alias.name for alias in node.names)]
        elif isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
    assert not [n for n in names if "generation_vm" in n or "dataproc_batch" in n]


def _ctx(fake: FakeGcloud, slept: list[float]) -> SimpleNamespace:
    echoed: list[str] = []
    return SimpleNamespace(run=fake, now=lambda: NOW, sleep=slept.append, echo=echoed.append, echoed=echoed)


def test_wait_returns_zero_without_changes():
    fake, slept = FakeGcloud(), []
    assert iam.wait_for_iam_propagation(_ctx(fake, slept)) == 0.0
    assert slept == []
    assert len([c for c in fake.calls if c[1:3] == ["logging", "read"]]) == 1


def test_wait_raises_iam_still_changing():
    fake, slept = FakeGcloud(), []
    fake.logging_responses = [[audit_entry("google.iam.admin.v1.UpdateRole", NOW - _seconds(60 - i))] for i in range(4)]
    with pytest.raises(iam.IamStillChanging, match="IAM kept changing across 3 propagation waits"):
        iam.wait_for_iam_propagation(_ctx(fake, slept))
    assert len(slept) == 3
