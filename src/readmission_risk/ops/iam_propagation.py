"""The pre-submit IAM-propagation wait shared by the Big Join submitter and the generation launcher: reads the Admin Activity
audit log and waits until IAM changes by non-Google principals are ~8 minutes old. Imports neither CLI module (they import it).
Design: notes/eg-new-feature/ops-iam-propagation-and-interrupt-2026-10-12.md and notes/eg-new-feature/ops-launcher-iam-wait-2026-10-13.md.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Protocol

from .gcloud import PROJECT, GcloudError, Runner, parse_gcloud_time, run_json

IAM_PROPAGATION_SECONDS = 480  # the ~8-minute rule (docs/setup.md); 5b measured < 5.6 min once
MAX_IAM_WAITS = 3  # waits for successive changes before refusing
AUDIT_LOG = f"projects/{PROJECT}/logs/cloudaudit.googleapis.com%2Factivity"  # Admin Activity, always on
IAM_CHANGE_METHODS = (  # methodName values that change who may do what (5b audit log, 2026-10-12, plus bucket IAM)
    "SetIamPolicy",  # project policy (cloudresourcemanager)
    "google.iam.admin.v1.SetIAMPolicy",  # a service account's policy (actAs, token creation)
    "google.iam.admin.v1.CreateRole",
    "google.iam.admin.v1.UpdateRole",
    "google.iam.admin.v1.DeleteRole",
    "google.iam.admin.v1.UndeleteRole",
    "storage.setIamPermissions",  # bucket policy: dataproc-runtime's grants are bucket-level
)
# Google-managed principals change IAM as a side effect of our own batches (5b: service-agent-manager@system... at 13:37:29)
GOOGLE_MANAGED_PRINCIPAL = re.compile(
    r".*@system\.gserviceaccount\.com"  # e.g. service-agent-manager@system.gserviceaccount.com
    r"|service-[0-9]+@gcp-sa-[a-z0-9-]+\.iam\.gserviceaccount\.com"  # per-service agents
    r"|[0-9]+@cloudservices\.gserviceaccount\.com"  # the Google APIs service agent
)


class IamStillChanging(Exception):
    """IAM kept changing across MAX_IAM_WAITS waits (callers exit 1, refused)."""


class IamWaitContext(Protocol):
    """What the wait needs from a CLI's context (generation_vm.LaunchContext satisfies it)."""

    run: Runner | None
    now: Callable[[], datetime]
    sleep: Callable[[float], None]
    echo: Callable[[str], None]


@dataclass(frozen=True)
class IamChange:
    timestamp: datetime  # UTC, aware
    method: str
    principal: str  # "" when the entry has none
    resource: str  # protoPayload.resourceName, "" when absent


def parse_iam_changes(entries: object, *, after: datetime) -> list[IamChange]:
    """IAM changes strictly after `after`, by principals that are not Google-managed, oldest first. `entries` is `gcloud logging
    read --format=json` output (a list; run_json returns None for empty output, treated as no entries)."""
    if not isinstance(entries, list):
        return []
    changes = []
    for element in entries:
        if not isinstance(element, dict) or not isinstance(element.get("protoPayload"), dict):
            continue
        payload = element["protoPayload"]
        method = payload.get("methodName")
        if method not in IAM_CHANGE_METHODS:
            continue
        auth = payload.get("authenticationInfo")
        principal = auth.get("principalEmail") if isinstance(auth, dict) else None
        principal = principal if isinstance(principal, str) else ""
        if GOOGLE_MANAGED_PRINCIPAL.fullmatch(principal):
            continue
        raw = element.get("timestamp")
        if not isinstance(raw, str):  # before parsing: parse_gcloud_time of a non-str raises AttributeError
            continue
        try:
            timestamp = parse_gcloud_time(raw)
        except (ValueError, TypeError):
            continue
        if timestamp is None or timestamp.tzinfo is None or not timestamp > after:
            continue
        resource = payload.get("resourceName")
        changes.append(IamChange(timestamp, method, principal, resource if isinstance(resource, str) else ""))
    return sorted(changes, key=lambda change: change.timestamp)


def iam_changes_argv(since: datetime) -> list[str]:
    """The audit-log read; the filter only narrows the query, parse_iam_changes is the source of truth."""
    methods = " OR ".join(f'"{m}"' for m in IAM_CHANGE_METHODS)
    query = (
        f'logName="{AUDIT_LOG}" AND timestamp>="{since.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}" '
        f"AND protoPayload.methodName=({methods})"
    )
    return ["gcloud", "logging", "read", query, "--limit=100"]


def wait_for_iam_propagation(ctx: IamWaitContext) -> float:
    """Before a CLI starts paid work (a Dataproc batch, a generation VM): if a non-Google principal changed IAM within IAM_PROPAGATION_SECONDS, wait until that much time has
    passed since the latest change, then look again for newer changes. Refused after MAX_IAM_WAITS waits that each found newer
    changes. An unreadable audit log is a warning, not a refusal (the Big Join submitter's failure-path hint still applies; the launcher has none). Returns the total seconds
    slept (0.0 when it did not wait)."""
    cutoff = ctx.now() - timedelta(seconds=IAM_PROPAGATION_SECONDS)
    waits = 0
    slept = 0.0
    while True:
        try:
            entries = run_json(ctx.run, iam_changes_argv(cutoff))
        except (GcloudError, ValueError) as exc:  # ValueError: json.JSONDecodeError on non-JSON stdout
            ctx.echo(
                "WARNING: could not read the Admin Activity audit log for recent IAM changes; continuing without the "
                f"propagation check (it needs roles/logging.viewer and the Logging API): {str(exc).splitlines()[-1]}"
            )
            return slept
        changes = parse_iam_changes(entries, after=cutoff)
        if not changes:
            return slept
        latest = changes[-1]
        if waits == MAX_IAM_WAITS:
            raise IamStillChanging(
                f"IAM kept changing across {MAX_IAM_WAITS} propagation waits (latest: {latest.method} by "
                f"{latest.principal or '<unknown principal>'} at {latest.timestamp:%H:%M:%S}Z); rerun once IAM is stable"
            )
        ctx.echo(
            f"IAM changed {len(changes)} time(s) since {cutoff:%H:%M:%S}Z; latest: {latest.method} on "
            f"{latest.resource or '<unknown resource>'} by {latest.principal or '<unknown principal>'} at {latest.timestamp:%H:%M:%S}Z"
        )
        remaining = (latest.timestamp + timedelta(seconds=IAM_PROPAGATION_SECONDS) - ctx.now()).total_seconds()
        if remaining > 0:
            ctx.echo(f"Waiting {ceil(remaining)} s for IAM propagation (Ctrl-C aborts; nothing has been started yet)")
            ctx.sleep(remaining)
            slept += remaining
        waits += 1
        cutoff = latest.timestamp
