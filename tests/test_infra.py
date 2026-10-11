"""Static checks of the Terraform config in infra/ and the keyless smoke workflow (v2 slice 3).

Line/regex based like tests/test_docs.py: no HCL parser, no new dependency. CI's terraform-validate job checks that the
config is valid; these tests pin the security decisions so that changing one is deliberate (design note
notes/eg-new-feature/v2-slice3-terraform-wif-2026-10-11.md, Verification):
- the WIF provider's condition is the conjunction of the numeric repository and owner ids, refs/heads/main and the
  dispatch/schedule events, and every claim it uses is mapped;
- gha-smoke is bound to smoke.yml's workflow_ref only, can only list the bundle bucket, and smoke.yml uses exactly the
  provider, account and bucket that infra/ defines;
- bundle-writer can only create objects; IAM is additive only; no service-account key exists anywhere;
- bucket settings, lifecycle and prevent_destroy are as designed;
- id-token: write appears only at job level in dispatch/schedule workflows, and CI never plans or applies;
- the Terraform CLI, provider and lock-file versions agree.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
WORKFLOWS = ROOT / ".github" / "workflows"
SMOKE = WORKFLOWS / "smoke.yml"
CI = WORKFLOWS / "ci.yml"
_RESOURCE_RE = re.compile(r'^resource "(\w+)" "(\w+)" \{\n(.*?)^\}', flags=re.MULTILINE | re.DOTALL)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _tf(name: str) -> str:
    return _read(INFRA / name)


def _all_tf() -> str:
    return "\n".join(_read(p) for p in sorted(INFRA.glob("*.tf")))


def _resources(text: str) -> list[tuple[str, str, str]]:
    """(type, name, body) of every top-level resource block; blocks close with a `}` in column 0 (terraform fmt)."""
    return _RESOURCE_RE.findall(text)


def _resource(rtype: str, name: str) -> str:
    found = [body for t, n, body in _resources(_all_tf()) if (t, n) == (rtype, name)]
    assert len(found) == 1, f"expected exactly one {rtype}.{name} in infra/*.tf, found {len(found)}"
    return found[0]


def _args(body: str) -> dict[str, str]:
    """Top-level `key = value` arguments of a flat block body (one argument per line, as in iam.tf)."""
    return dict(re.findall(r"^  (\w+)\s*=\s*(.+?)\s*$", body, flags=re.MULTILINE))


def _variable_default(name: str) -> str:
    m = re.search(rf'^variable "{name}" \{{\n(.*?)^\}}', _tf("variables.tf"), flags=re.MULTILINE | re.DOTALL)
    assert m, f"variable {name} not found"
    default = re.search(r'^\s*default\s*=\s*"([^"]*)"', m.group(1), flags=re.MULTILINE)
    assert default, f"variable {name} has no string default"
    return default.group(1)


def _iam_members() -> list[dict[str, str]]:
    return [_args(body) | {"_type": t, "_name": n} for t, n, body in _resources(_all_tf()) if t.endswith("_iam_member")]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _block(lines: list[str], start: int) -> list[str]:
    """Lines from `start` up to (excluding) the next non-blank line indented at most as deep as lines[start]."""
    column = _indent(lines[start])
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() and _indent(lines[i]) <= column), len(lines))
    return lines[start:end]


def _workflow_files() -> list[Path]:
    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def _triggers(lines: list[str]) -> set[str]:
    on = lines.index("on:")
    return {line.strip().rstrip(":") for line in _block(lines, on)[1:] if line.strip() and _indent(line) == 2}


# ---------------------------------------------------------------- WIF


def test_wif_condition_pins_numeric_repo_and_owner_ids_main_and_event_names():
    wif = _tf("wif.tf")
    assert "||" not in wif, "the WIF condition must be a conjunction"
    lines = wif.split("\n")
    start = next(i for i, line in enumerate(lines) if _collapse(line) == 'attribute_condition = join(" && ", [')
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "])")
    fragments = [line.strip().rstrip(",") for line in lines[start + 1 : end] if line.strip()]
    assert fragments == [
        "\"assertion.repository_id == '${var.github_repository_id}'\"",
        "\"assertion.repository_owner_id == '${var.github_repository_owner_id}'\"",
        "\"assertion.ref == 'refs/heads/main'\"",
        "\"assertion.event_name in ['workflow_dispatch', 'schedule']\"",
    ]
    assert _variable_default("github_repository_id") == "1380503419"
    assert _variable_default("github_repository_owner_id") == "270004823"
    provider = _collapse(_resource("google_iam_workload_identity_pool_provider", "github_actions"))
    assert 'oidc { issuer_uri = "https://token.actions.githubusercontent.com" }' in provider
    body = _resource("google_iam_workload_identity_pool_provider", "github_actions")
    assert not re.search(r"^\s*allowed_audiences\s*=", body, flags=re.MULTILINE), "the default audience is the design"
    collapsed = _collapse(wif)
    for claim in set(re.findall(r"assertion\.([a-z_]+)", "\n".join(fragments))):
        assert f'"attribute.{claim}" = "assertion.{claim}"' in collapsed, f"claim {claim} is used but not mapped"


def test_smoke_sa_binding_is_keyed_to_the_smoke_workflow_on_main():
    iam = _collapse(_tf("iam.tf"))
    assert "pool_name = google_iam_workload_identity_pool.github.name" in iam
    assert 'smoke_workflow_ref = "${var.github_repository}/.github/workflows/smoke.yml@refs/heads/main"' in iam
    assert (
        'smoke_principal_set = "principalSet://iam.googleapis.com/${local.pool_name}/attribute.workflow_ref/'
        '${local.smoke_workflow_ref}"'
    ) in iam
    assert SMOKE.is_file()
    # A repository-wide binding would let any workflow on main impersonate the account: an owner decision, never an edit.
    assert [m for m in _iam_members() if "attribute.repository" in m.get("member", "")] == []


def test_smoke_workflow_matches_the_terraform_values():
    smoke = _read(SMOKE)
    pool_id = re.search(r'workload_identity_pool_id\s*=\s*"([^"]+)"', _resource("google_iam_workload_identity_pool", "github"))
    provider_id = re.search(
        r'workload_identity_pool_provider_id\s*=\s*"([^"]+)"',
        _resource("google_iam_workload_identity_pool_provider", "github_actions"),
    )
    account_id = re.search(r'account_id\s*=\s*"([^"]+)"', _resource("google_service_account", "gha_smoke"))
    bucket = re.search(r'^  name\s*=\s*"([^"]+)"', _resource("google_storage_bucket", "bundles"), flags=re.MULTILINE)
    assert pool_id and provider_id and account_id and bucket
    provider = (
        f"projects/{_variable_default('project_number')}/locations/global/workloadIdentityPools/{pool_id.group(1)}"
        f"/providers/{provider_id.group(1)}"
    )
    assert f"workload_identity_provider: {provider}\n" in smoke
    assert f"service_account: {account_id.group(1)}@{_variable_default('project_id')}.iam.gserviceaccount.com\n" in smoke
    assert f"run: gcloud storage ls gs://{bucket.group(1)}/\n" in smoke


# ---------------------------------------------------------------- IAM


def test_smoke_account_can_only_list():
    members = _iam_members()
    on_bucket = [m for m in members if m.get("member") == '"serviceAccount:${google_service_account.gha_smoke.email}"']
    assert [(m["role"], m.get("bucket")) for m in on_bucket] == [
        ('"roles/storage.legacyBucketReader"', "google_storage_bucket.bundles.name")
    ]
    on_account = [m for m in members if m.get("service_account_id") == "google_service_account.gha_smoke.name"]
    assert [(m["role"], m["member"]) for m in on_account] == [('"roles/iam.workloadIdentityUser"', "local.smoke_principal_set")]


def test_bundle_writer_is_create_only():
    members = _iam_members()
    on_bucket = [m for m in members if m.get("member") == '"serviceAccount:${google_service_account.bundle_writer.email}"']
    assert [(m["role"], m.get("bucket")) for m in on_bucket] == [
        ('"roles/storage.objectCreator"', "google_storage_bucket.bundles.name")
    ]
    on_account = [m for m in members if m.get("service_account_id") == "google_service_account.bundle_writer.name"]
    assert [(m["role"], m["member"]) for m in on_account] == [
        ('"roles/iam.serviceAccountTokenCreator"', "var.operator_member")
    ]


def test_iam_is_additive_only():
    types = {t for t, _, _ in _resources(_all_tf())}
    assert [t for t in types if t.endswith(("_iam_policy", "_iam_binding"))] == []
    assert len(_iam_members()) == 4


def test_no_service_account_keys_in_infra_or_workflows():
    assert "google_service_account_key" not in _all_tf()
    for path in _workflow_files():
        assert "credentials_json" not in _read(path), f"{path.name} uses a service-account key"


# ---------------------------------------------------------------- buckets and budgets


def test_buckets_enforce_uniform_access_and_public_access_prevention():
    buckets = [(n, _collapse(body)) for t, n, body in _resources(_all_tf()) if t == "google_storage_bucket"]
    assert sorted(n for n, _ in buckets) == ["bundles", "gold", "raw", "tfstate"]
    for name, body in buckets:
        assert "uniform_bucket_level_access = true" in body, name
        assert 'public_access_prevention = "enforced"' in body, name
        assert "location = upper(var.region)" in body, name


def test_raw_bucket_deletes_after_90_days():
    raw = _collapse(_resource("google_storage_bucket", "raw"))
    assert 'lifecycle_rule { condition { age = 90 } action { type = "Delete" } }' in raw
    assert "soft_delete_policy { retention_duration_seconds = 0 }" in raw


def test_state_and_bundle_buckets_and_monthly_budget_prevent_destroy():
    guarded = {("google_storage_bucket", "tfstate"), ("google_storage_bucket", "bundles"), ("google_billing_budget", "monthly")}
    for rtype, name, body in _resources(_all_tf()):
        has_guard = "lifecycle { prevent_destroy = true }" in _collapse(body)
        assert has_guard == ((rtype, name) in guarded), f"{rtype}.{name}: prevent_destroy is {has_guard}"
    for name in ("tfstate", "bundles"):
        assert "versioning { enabled = true }" in _collapse(_resource("google_storage_bucket", name)), name


def test_monthly_budget_is_imported_not_redefined():
    budgets = _collapse(_tf("budgets.tf"))
    assert (
        'import { to = google_billing_budget.monthly id = "billingAccounts/${var.billing_account}/budgets/'
        '${var.existing_budget_id}" }'
    ) in budgets
    amounts = re.findall(r'currency_code = "(\w+)" units = "(\d+)"', budgets)
    assert amounts == [("CHF", "20"), ("CHF", "5")]


# ---------------------------------------------------------------- workflows


def test_id_token_write_only_at_job_level_in_dispatch_or_schedule_workflows():
    for path in _workflow_files():
        text = _read(path)
        # write-all grants id-token implicitly; a quoted or differently spelled value would slip past the exact match.
        assert not re.search(r"permissions:\s*[\"']?write-all", text), f"{path.name}: write-all"
        assert [v for v in re.findall(r"^\s*id-token:\s*(.*?)\s*$", text, flags=re.MULTILINE) if v != "write"] == []
        lines = text.split("\n")
        # Flow style (`permissions: { id-token: write }`) would hide the grant from the line checks below.
        assert not re.search(r"permissions:\s*\{[^}]*id-token", text), f"{path.name}: use block-style permissions"
        top = [i for i, line in enumerate(lines) if line.startswith("permissions:")]
        assert len(top) == 1, f"{path.name}: needs exactly one top-level permissions block"
        assert not any("id-token" in line for line in _block(lines, top[0])), f"{path.name}: id-token at top level"
        if any("id-token" in line.split("#", 1)[0] for line in lines):
            assert _triggers(lines) <= {"workflow_dispatch", "schedule"}, f"{path.name}: id-token on another trigger"
    lines = _read(SMOKE).split("\n")
    assert "permissions: {}" in lines
    assert _triggers(lines) == {"workflow_dispatch"}
    job_permissions = [i for i, line in enumerate(lines) if line == "    permissions:"]
    assert len(job_permissions) == 1
    granted = {line.strip() for line in _block(lines, job_permissions[0])[1:] if line.strip()}
    assert granted == {"contents: read", "id-token: write"}


def test_ci_runs_terraform_validate_without_credentials():
    lines = _read(CI).split("\n")
    job = [line.strip() for line in _block(lines, lines.index("  terraform-validate:"))]
    code = [line for line in job if line and not line.startswith("#")]
    assert "run: terraform -chdir=infra fmt -check -recursive -diff" in code
    assert "run: terraform -chdir=infra init -backend=false -input=false -lockfile=readonly" in code
    assert "run: terraform -chdir=infra validate" in code
    assert any("sha256sum -c -" in line for line in code)
    assert [line for line in code if re.search(r"\bterraform\b.*\b(plan|apply)\b", line)] == []
    assert not any("id-token" in line for line in job)
    assert not any(line.startswith("permissions:") for line in code), "the job must inherit the top-level contents: read"
    for forbidden in ("secrets.", "google-github-actions/auth", "credentials_json"):
        assert not any(forbidden in line for line in code), f"terraform-validate must hold no credentials: {forbidden}"


def test_bootstrap_script_never_auto_approves():
    script = _read(INFRA / "bootstrap.sh")
    assert "auto-approve" not in script
    assert "\nset -euo pipefail\n" in script
    assert (INFRA / "bootstrap.sh").stat().st_mode & 0o111, "bootstrap.sh must be executable"


def test_bootstrap_script_keeps_its_state_safety_checks():
    lines = _read(INFRA / "bootstrap.sh").split("\n")
    code = [line for line in lines if not line.lstrip().startswith("#")]
    # The local state is deleted exactly once, and only after the state object in GCS has been read back and verified.
    deletes = [i for i, line in enumerate(code) if "rm -f terraform.tfstate" in line]
    verify = [i for i, line in enumerate(code) if 'gcloud storage cat "$STATE_OBJECT"' in line]
    assert len(deletes) == 1 and len(verify) == 1 and verify[0] < deletes[0]
    # -force-copy only after the pre-migration probe and the local state check.
    force = next(i for i, line in enumerate(code) if "-migrate-state -force-copy" in line)
    probe = next(i for i, line in enumerate(code) if 'object=$(probe objects "$STATE_OBJECT")' in line and i < force)
    local = next(i for i, line in enumerate(code) if "grep -x 'google_storage_bucket.tfstate'" in line and i < force)
    assert probe < local < force
    # Every override file name Terraform honours is refused.
    text = "\n".join(code)
    for pattern in ("[ -e override.tf ]", "[ -e override.tf.json ]", "compgen -G '*_override.tf'", "compgen -G '*_override.tf.json'"):
        assert pattern in text, pattern
    # grep -q can exit early and SIGPIPE its writer, which pipefail turns into a false failure.
    assert "grep -q" not in text.replace("grep -qiE '404|not found' <<<", "")


# ---------------------------------------------------------------- versions


def test_terraform_versions_agree():
    ci_version = re.search(r'TERRAFORM_VERSION: "([^"]+)"', _read(CI))
    required = re.search(r'required_version = ">= ([0-9.]+), < ', _tf("versions.tf"))
    provider = re.search(r'source\s*=\s*"hashicorp/google"\s*\n\s*version\s*=\s*"([0-9.]+)"', _tf("versions.tf"))
    assert ci_version and required and provider
    assert ci_version.group(1) == required.group(1)
    lock = _read(INFRA / ".terraform.lock.hcl")
    assert re.findall(r'^provider "([^"]+)"', lock, flags=re.MULTILINE) == ["registry.terraform.io/hashicorp/google"]
    assert re.search(r'^  version\s*=\s*"([^"]+)"', lock, flags=re.MULTILINE).group(1) == provider.group(1)
    # Platform coverage (linux_amd64 for CI, darwin_arm64 for the laptop) is enforced by CI's -lockfile=readonly init and
    # the recipe in docs/setup.md; h1: values are opaque, so only their number is checked here.
    assert len(re.findall(r'"h1:', lock)) >= 2
