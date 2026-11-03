"""Static checks of the Terraform config in infra/ and the keyless smoke workflow (v2 slice 3).

Line/regex based like tests/test_docs.py: no HCL parser, no new dependency. CI's terraform-validate job checks that the
config is valid; these tests pin the security decisions so that changing one is deliberate (design note
notes/eg-new-feature/v2-slice3-terraform-wif-2026-10-11.md, Verification):
- the WIF provider's condition is the conjunction of the numeric repository and owner ids, refs/heads/main and the
  dispatch/schedule events, and every claim it uses is mapped;
- gha-smoke is bound to smoke.yml's workflow_ref only, can only list the bundle bucket, and smoke.yml uses exactly the
  provider, account and bucket that infra/ defines;
- bundle-writer can only create objects; IAM is additive only, except one pinned removal of the default compute
  SA's roles/editor; no service-account key exists anywhere;
- bucket settings, lifecycle and prevent_destroy are as designed;
- id-token: write appears only at job level in dispatch/schedule workflows, and CI never plans or applies;
- the Terraform CLI, provider and lock-file versions agree;
- (v2 slice 4) the scorer has exactly its designed grants, is bound to score.yml on main, and cannot create or drop
  serving tables; the BigQuery tables are partitioned, filter-required and deletion-protected, and read their schemas
  from the package; every resource waits for the API set.
- (v3 slice 1) serving.gold is MONTH-partitioned on index_stop, filter-required, clustered and deletion-protected, and its
  description names the one gold it may hold; scratch_gold expires its tables after 1 day; gold-publisher has exactly its
  designed grants (nothing on the serving dataset, no WIF binding).
- (v3 slice 2) the dashboard dataset holds exactly the three authorized views (packaged SQL, deletion-protected), serving
  authorizes them ONLY through one google_bigquery_dataset_access (FR7: no dataset `access` block, no dataset IAM on a
  dataset that grants views), and dashboard-runtime has exactly dataViewer on dashboard + jobUser (nothing on serving).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from readmission_risk.pipeline.gold_metadata import LABEL_DEFINITION
from readmission_risk.warehouse.bigquery import SERVING_GOLD_FINGERPRINT

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
WORKFLOWS = ROOT / ".github" / "workflows"
SMOKE = WORKFLOWS / "smoke.yml"
SCORE = WORKFLOWS / "score.yml"
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


def expected_gold_uri() -> str:
    """The gold table the scheduled scorer reads: the gold bucket's name plus the 200k champion's prefix (v2 slice 7;
    tests/test_tooling.py compares the Makefile's GOLD_URI default with it, test_monitoring_* the monitoring config)."""
    bucket = re.search(r'^  name\s*=\s*"([^"]+)"', _resource("google_storage_bucket", "gold"), flags=re.MULTILINE)
    assert bucket
    return f"gs://{bucket.group(1)}/v2/main_200k"


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
    # 12 (slices 3-4) + 14 (slice 5: synthea-generator 6, dataproc-runtime 8; the 2 staging grants removed 2026-10-14)
    # + 14 (slice 7: monitor 8, ledger-reader 5, scorer's monitor.yml binding 1)
    # + 7 (v3 slice 1: gold-publisher 6, its impersonation 1) + 3 (v3 slice 2: dashboard-runtime 2, its impersonation 1)
    # + 10 (v3 slice 4: dashboard-deploy 4, dashboard-kill 1, dashboard-kill-trigger 1, function-builder 3, Billing's publisher 1)
    # + 1 (v3 slice 4's P8 at L1: function-builder reads Cloud Functions' own gcf-v2-sources bucket)
    assert len(_iam_members()) == 61
    assert [(t, n) for t, n, _ in _resources(_all_tf()) if t.endswith("_iam_member_remove")] == [
        ("google_project_iam_member_remove", "default_compute_no_editor")
    ]


def test_no_service_account_keys_in_infra_or_workflows():
    assert "google_service_account_key" not in _all_tf()
    for path in _workflow_files():
        assert "credentials_json" not in _read(path), f"{path.name} uses a service-account key"


# ---------------------------------------------------------------- buckets and budgets


def test_buckets_enforce_uniform_access_and_public_access_prevention():
    buckets = [(n, _collapse(body)) for t, n, body in _resources(_all_tf()) if t == "google_storage_bucket"]
    assert sorted(n for n, _ in buckets) == ["artifacts", "bundles", "gold", "raw", "tfstate"]
    for name, body in buckets:
        assert "uniform_bucket_level_access = true" in body, name
        assert 'public_access_prevention = "enforced"' in body, name
        assert "location = upper(var.region)" in body, name


def test_raw_bucket_lifecycle_pilot_90_main_365():
    raw = _collapse(_resource("google_storage_bucket", "raw"))
    rules = re.findall(r"lifecycle_rule \{ condition \{ (.*?) \} action \{ (.*?) \} \}", raw)
    assert rules == [
        ('age = 90 matches_prefix = ["v2/pilot_"]', 'type = "Delete"'),
        ('age = 365 matches_prefix = ["v2/main_"]', 'type = "Delete"'),
    ]
    assert "soft_delete_policy { retention_duration_seconds = 0 }" in raw


def test_generation_templates_default_has_pilot_and_main():
    variables = _collapse(_tf("variables.tf"))
    assert 'variable "generation_templates" {' in variables
    assert "default = { pilot = 7200, main = 7200 }" in variables


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
    assert ci_version and required
    assert ci_version.group(1) == required.group(1)
    pins = dict(re.findall(r'source\s*=\s*"hashicorp/(\w+)"\s*\n\s*version\s*=\s*"([0-9.]+)"', _tf("versions.tf")))
    assert set(pins) == {"google", "archive"}  # archive since v3 slice 4 (the kill function's zip)
    lock = _read(INFRA / ".terraform.lock.hcl")
    blocks = dict(re.findall(r'^provider "registry\.terraform\.io/hashicorp/(\w+)" \{\n  version\s*=\s*"([^"]+)"', lock, flags=re.MULTILINE))
    assert re.findall(r'^provider "([^"]+)"', lock, flags=re.MULTILINE) == sorted(
        f"registry.terraform.io/hashicorp/{name}" for name in pins
    )
    assert blocks == pins  # each versions.tf pin equals the version inside that provider's own lock block
    # Platform coverage (linux_amd64 for CI, darwin_arm64 for the laptop) is enforced by CI's -lockfile=readonly init and
    # the recipe in docs/setup.md; h1: values are opaque, so only their number is checked here.
    assert len(re.findall(r'"h1:', lock)) >= 2


# ---------------------------------------------------------------- scorer and BigQuery (v2 slice 4)

SCORER = '"serviceAccount:${google_service_account.scorer.email}"'


def test_scorer_grants_are_exactly():
    grants = sorted(
        (m["_type"], m["role"], m.get("bucket") or m.get("table_id") or m.get("dataset_id") or m.get("project"))
        for m in _iam_members()
        if m.get("member") == SCORER
    )
    assert grants == sorted([
        ("google_storage_bucket_iam_member", '"roles/storage.objectViewer"', "google_storage_bucket.bundles.name"),
        ("google_storage_bucket_iam_member", '"roles/storage.objectViewer"', "google_storage_bucket.gold.name"),
        ("google_bigquery_table_iam_member", "google_project_iam_custom_role.scorer_table_writer.id",
         "google_bigquery_table.predictions.table_id"),
        ("google_bigquery_table_iam_member", "google_project_iam_custom_role.scorer_table_writer.id",
         "google_bigquery_table.scoring_runs.table_id"),
        ("google_bigquery_dataset_iam_member", '"roles/bigquery.dataEditor"', "google_bigquery_dataset.scratch.dataset_id"),
        ("google_project_iam_member", '"roles/bigquery.jobUser"', "var.project_id"),
    ])  # fmt: skip
    on_account = sorted(
        (m["role"], m["member"]) for m in _iam_members() if m.get("service_account_id") == "google_service_account.scorer.name"
    )
    assert on_account == [
        ('"roles/iam.serviceAccountTokenCreator"', "var.operator_member"),
        ('"roles/iam.workloadIdentityUser"', "local.monitor_principal_set"),  # v2 slice 7: monitor.yml's score job
        ('"roles/iam.workloadIdentityUser"', "local.score_principal_set"),
    ]
    # nothing on the serving dataset itself (no dataset-level grant: D6)
    assert [m for m in _iam_members() if "serving" in m.get("dataset_id", "")] == []


def test_custom_role_is_exactly_get_getdata_updatedata():
    role = _collapse(_resource("google_project_iam_custom_role", "scorer_table_writer"))
    permissions = re.search(r"permissions = \[ (.*?),? \]", role)
    assert permissions is not None
    assert [p.strip().strip('"') for p in permissions.group(1).split(",")] == [
        "bigquery.tables.get",
        "bigquery.tables.getData",
        "bigquery.tables.updateData",
    ]


def test_scorer_sa_binding_is_keyed_to_the_score_workflow_on_main():
    iam = _collapse(_tf("iam.tf"))
    assert 'score_workflow_ref = "${var.github_repository}/.github/workflows/score.yml@refs/heads/main"' in iam
    assert (
        'score_principal_set = "principalSet://iam.googleapis.com/${local.pool_name}/attribute.workflow_ref/'
        '${local.score_workflow_ref}"'
    ) in iam
    assert SCORE.is_file()


def test_score_workflow_matches_the_terraform_values():
    score, smoke = _read(SCORE), _read(SMOKE)
    provider = re.search(r"workload_identity_provider: (\S+)\n", smoke)
    account_id = re.search(r'account_id\s*=\s*"([^"]+)"', _resource("google_service_account", "scorer"))
    assert provider and account_id
    assert f"workload_identity_provider: {provider.group(1)}\n" in score
    assert f"service_account: {account_id.group(1)}@{_variable_default('project_id')}.iam.gserviceaccount.com\n" in score
    assert f"--gold-dir {expected_gold_uri()} " in score


def test_score_workflow_is_dispatch_only_and_input_reaches_shell_only_via_env():
    text = _read(SCORE)
    lines = text.split("\n")
    assert _triggers(lines) == {"workflow_dispatch"} and "permissions: {}" in lines
    job_permissions = [i for i, line in enumerate(lines) if line == "    permissions:"]
    granted = {line.strip() for line in _block(lines, job_permissions[0])[1:] if line.strip()}
    assert granted == {"contents: read", "id-token: write"}
    for i, line in enumerate(lines):
        if "${{" in line:
            assert line.strip() == "RUN_DATE: ${{ inputs.run_date }}", f"score.yml:{i + 1}: expression outside env"
    assert '--run-date "$RUN_DATE"' in text
    assert "cache:" not in text  # no pip cache in a job that holds cloud credentials


def test_bigquery_tables_partitioned_require_filter_and_deletion_protected():
    tables = (
        ("predictions", "DAY", "as_of_date"),
        ("scoring_runs", "DAY", "started_at"),
        ("monitoring", "DAY", "started_at"),
        ("gold", "MONTH", "index_stop"),  # v3 slice 1, owner decision D2: DAY would need ~22,000 partitions
    )
    for name, unit, field in tables:
        body = _collapse(_resource("google_bigquery_table", name))
        assert f'time_partitioning {{ type = "{unit}" field = "{field}" }}' in body, name
        assert "require_partition_filter = true" in body and "deletion_protection = true" in body, name
        assert f'schema = file("${{path.module}}/../src/readmission_risk/warehouse/schemas/{name}.json")' in body, name
        assert (ROOT / "src" / "readmission_risk" / "warehouse" / "schemas" / f"{name}.json").is_file()
    assert 'location = var.region' in _collapse(_resource("google_bigquery_dataset", "serving"))
    scratch = _collapse(_resource("google_bigquery_dataset", "scratch"))
    assert "default_table_expiration_ms = 86400000" in scratch
    gold = _collapse(_resource("google_bigquery_table", "gold"))
    assert 'clustering = ["admission_reason_code"]' in gold
    assert "dataset_id = google_bigquery_dataset.serving.dataset_id" in gold
    scratch_gold = _collapse(_resource("google_bigquery_dataset", "scratch_gold"))
    assert "default_table_expiration_ms = 86400000" in scratch_gold and "location = var.region" in scratch_gold


def test_every_bigquery_table_is_partitioned_filtered_and_protected():
    """No future base table escapes the rule (v3 slice 1). Views (v3 slice 2, owner decision D3) cannot be partitioned or
    filter-required (they inherit the base tables' requirement); they have their own rule below."""
    tables = [(n, _collapse(body)) for t, n, body in _resources(_all_tf()) if t == "google_bigquery_table" and "view {" not in body]
    assert {n for n, _ in tables} == {"predictions", "scoring_runs", "monitoring", "gold"}
    for name, body in tables:
        assert "require_partition_filter = true" in body and "deletion_protection = true" in body, name
        assert "time_partitioning {" in body, name


# ---------------------------------------------------------------- v3 slice 2: the dashboard's authorized views

DASHBOARD_SA = '"serviceAccount:${google_service_account.dashboard_runtime.email}"'
DEPLOY_SA = '"serviceAccount:${google_service_account.dashboard_deploy.email}"'  # v3 slice 4


def test_bigquery_views_are_exactly_the_dashboard_views():
    from readmission_risk.warehouse.dashboard_views import VIEW_NAMES

    views = {n: _collapse(body) for t, n, body in _resources(_all_tf()) if t == "google_bigquery_table" and "view {" in body}
    assert sorted(views) == sorted(VIEW_NAMES)
    for name, body in views.items():
        assert "dataset_id = google_bigquery_dataset.dashboard.dataset_id" in body, name
        assert f'table_id = "{name}"' in body and "deletion_protection = true" in body, name
        assert (
            f'view {{ query = templatefile("${{path.module}}/../src/readmission_risk/warehouse/sql/views/{name}.sql", '
            "local.dashboard_view_vars) use_legacy_sql = false }"
        ) in body, name
        for forbidden in ("schema =", "time_partitioning", "require_partition_filter"):
            assert forbidden not in body, (name, forbidden)
    local = re.search(r"dashboard_view_vars = \{\n(.*?)\n  \}", _tf("bigquery.tf"), flags=re.DOTALL)
    assert local is not None
    entries = dict(re.findall(r'^\s+(\w+)\s*=\s*"(.+)"$', local.group(1), flags=re.MULTILINE))
    assert entries == {
        t: f"${{var.project_id}}.${{google_bigquery_table.{t}.dataset_id}}.${{google_bigquery_table.{t}.table_id}}"
        for t in ("predictions", "gold", "scoring_runs", "monitoring")
    }


def _dataset_named(expression: str, resources) -> str:
    """The BigQuery dataset id an HCL `dataset_id` expression names, whatever its spelling: a string literal, a dataset's
    `.dataset_id`, or (recursively) a table's `.dataset_id`, the spelling table-level IAM uses; a `"${X}"` interpolation is
    unwrapped to X. Unresolvable → the raw text, which callers must refuse (fail closed: see _declared_datasets)."""
    expression = expression.strip()
    wrapped = re.fullmatch(r'"\$\{([^}]*)\}"', expression)
    if wrapped:
        return _dataset_named(wrapped.group(1), resources)
    if re.fullmatch(r'"[^"]*"', expression):
        return expression.strip('"')
    ref = re.fullmatch(r"(google_bigquery_dataset|google_bigquery_table)\.(\w+)\.dataset_id", expression)
    if ref is None:
        return expression
    bodies = [b for t, n, b in resources if (t, n) == (ref.group(1), ref.group(2))]
    if len(bodies) != 1:
        return expression
    return _dataset_named(_args(bodies[0])["dataset_id"], resources)


def _declared_datasets(resources) -> set[str]:
    return {_dataset_named(_args(b)["dataset_id"], resources) for t, _, b in resources if t == "google_bigquery_dataset"}


def test_dataset_named_resolves_every_spelling():
    resources = _resources(_all_tf())
    for spelling in ('"serving"', "google_bigquery_dataset.serving.dataset_id", "google_bigquery_table.gold.dataset_id",
                     "google_bigquery_table.predictions.dataset_id"):  # fmt: skip
        assert _dataset_named(spelling, resources) == "serving", spelling
    assert _dataset_named("google_bigquery_dataset.dashboard.dataset_id", resources) == "dashboard"
    assert _dataset_named("google_bigquery_table.v_call_sheet.dataset_id", resources) == "dashboard"
    # a dataset-IAM member on serving written in the table-reference style would be caught by the FR7 test
    extra = _resources('resource "google_bigquery_dataset_iam_member" "sneaky" {\n  dataset_id = google_bigquery_table.gold.dataset_id\n}\n')
    assert _dataset_named(_args(extra[0][2])["dataset_id"], resources + extra) == "serving"
    assert _dataset_named('"${google_bigquery_dataset.serving.dataset_id}"', resources) == "serving"
    # spellings it cannot resolve are not declared datasets, so the FR7 test's fail-closed assertion refuses them
    known = _declared_datasets(resources)
    assert known == {"serving", "scratch", "scratch_gold", "dashboard"}
    for unresolvable in ("local.serving_dataset", "google_bigquery_dataset.serving.id", "var.dataset"):
        assert _dataset_named(unresolvable, resources) not in known, unresolvable


def test_views_authorized_only_by_dataset_access():
    """FR7 (PRD Risks: "Authorized views silently lost"): a dataset `access` block or any google_bigquery_dataset_iam_* on a
    dataset that grants views rewrites its ACL and drops the authorization (google provider docs)."""
    resources = _resources(_all_tf())
    grants = [(n, _collapse(body)) for t, n, body in resources if t == "google_bigquery_dataset_access"]
    assert [n for n, _ in grants] == ["serving_authorizes_dashboard_views"]
    (_, body), = grants
    assert "dataset_id = google_bigquery_dataset.serving.dataset_id" in body
    assert (
        "dataset { dataset { project_id = var.project_id dataset_id = google_bigquery_dataset.dashboard.dataset_id } "
        'target_types = ["VIEWS"] }'
    ) in body
    granting = {_dataset_named(_args(b)["dataset_id"], resources) for t, _, b in resources if t == "google_bigquery_dataset_access"}
    assert granting == {"serving"}
    dataset_iam = [(t, n, _dataset_named(_args(b).get("dataset_id", ""), resources))
                   for t, n, b in resources if t.startswith("google_bigquery_dataset_iam_")]  # fmt: skip
    # fail closed: a dataset_id spelling the resolver does not understand must not pass as "some other dataset"
    known = _declared_datasets(resources)
    assert [x for x in dataset_iam if x[2] not in known] == []
    assert [x for x in dataset_iam if x[2] in granting] == []
    for t, n, b in resources:
        if t == "google_bigquery_dataset":
            assert "access {" not in _collapse(b), n  # authoritative on any dataset: would also fight the *_iam_member grants


def test_dashboard_dataset_settings_and_outputs():
    body = _collapse(_resource("google_bigquery_dataset", "dashboard"))
    assert 'dataset_id = "dashboard"' in body and "location = var.region" in body
    assert "delete_contents_on_destroy = false" in body and "default_table_expiration_ms" not in body
    outputs = _tf("outputs.tf")
    block = re.search(r'output "bigquery_datasets" \{\n  value = \{\n(.*?)\n  \}', outputs, flags=re.DOTALL)
    assert block is not None
    listed = dict(re.findall(r"^\s+(\w+)\s*=\s*google_bigquery_dataset\.(\w+)\.dataset_id$", block.group(1), flags=re.MULTILINE))
    datasets = sorted(n for t, n, _ in _resources(_all_tf()) if t == "google_bigquery_dataset")
    assert sorted(listed) == datasets and all(k == v for k, v in listed.items())
    assert 'output "dashboard_runtime_service_account_email" {\n  value = google_service_account.dashboard_runtime.email\n}' in outputs


def test_dashboard_runtime_grants_are_exactly():
    assert _grants(DASHBOARD_SA) == sorted([
        ("google_bigquery_dataset_iam_member", '"roles/bigquery.dataViewer"', "google_bigquery_dataset.dashboard.dataset_id"),
        ("google_project_iam_member", '"roles/bigquery.jobUser"', "var.project_id"),
    ])  # fmt: skip
    assert _on_account("dashboard_runtime") == sorted([
        ('"roles/iam.serviceAccountTokenCreator"', "var.operator_member"),
        ('"roles/iam.serviceAccountUser"', DEPLOY_SA),  # v3 slice 4: a revision runs as dashboard-runtime
    ])  # fmt: skip
    mine = [m for m in _iam_members() if m.get("member") == DASHBOARD_SA]
    assert all("table_id" not in m for m in mine)  # nothing on any serving (or other) table
    assert [m for m in _iam_members() if "readSessionUser" in m.get("role", "")] == []  # FR9: no Storage read sessions
    body = _collapse(_resource("google_service_account", "dashboard_runtime"))
    assert 'account_id = "dashboard-runtime"' in body


def test_gold_table_description_names_the_gold_fingerprint():
    body = _resource("google_bigquery_table", "gold")
    assert SERVING_GOLD_FINGERPRINT in body and LABEL_DEFINITION in body


GOLD_PUBLISHER = '"serviceAccount:${google_service_account.gold_publisher.email}"'


def test_gold_publisher_grants_are_exactly():
    grants = sorted(
        (m["_type"], m["role"], m.get("bucket") or m.get("table_id") or m.get("dataset_id") or m.get("project"))
        for m in _iam_members()
        if m.get("member") == GOLD_PUBLISHER
    )
    assert grants == sorted([
        ("google_storage_bucket_iam_member", '"roles/storage.objectViewer"', "google_storage_bucket.gold.name"),
        ("google_storage_bucket_iam_member", '"roles/storage.objectViewer"', "google_storage_bucket.bundles.name"),
        ("google_bigquery_dataset_iam_member", '"roles/bigquery.dataEditor"', "google_bigquery_dataset.scratch_gold.dataset_id"),
        ("google_bigquery_table_iam_member", "google_project_iam_custom_role.scorer_table_writer.id",
         "google_bigquery_table.gold.table_id"),
        ("google_bigquery_table_iam_member", '"roles/bigquery.dataViewer"', "google_bigquery_table.predictions.table_id"),
        ("google_project_iam_member", '"roles/bigquery.jobUser"', "var.project_id"),
    ])  # fmt: skip
    on_account = sorted(
        (m["role"], m["member"])
        for m in _iam_members()
        if m.get("service_account_id") == "google_service_account.gold_publisher.name"
    )
    assert on_account == [('"roles/iam.serviceAccountTokenCreator"', "var.operator_member")]  # laptop only: no WIF binding
    # no one else gets anything on serving.gold (it holds the label columns)
    on_gold = {m["member"] for m in _iam_members() if m.get("table_id") == "google_bigquery_table.gold.table_id"}
    assert on_gold == {GOLD_PUBLISHER}


def test_predictions_table_description_is_demo_notice():
    from readmission_risk.scoring.constants import DEMO_NOTICE

    body = _resource("google_bigquery_table", "predictions")
    heredoc = re.search(r"chomp\(<<-EOT\n(.*?)\n\s*EOT\n", body, flags=re.DOTALL)
    assert heredoc is not None and heredoc.group(1).strip() == DEMO_NOTICE


def test_every_resource_depends_on_enabled_apis():
    for rtype, name, body in _resources(_all_tf()):
        if (rtype, name) == ("google_project_service", "enabled"):
            continue
        assert "depends_on = [google_project_service.enabled]" in _collapse(body), f"{rtype}.{name}"


def test_bigquery_api_is_enabled():
    assert '"bigquery.googleapis.com",' in _tf("apis.tf")


# ---------------------------------------------------------------- v2 slice 5: generation VM and Dataproc runtime


def _members_of(sa: str) -> list[dict[str, str]]:
    return [m for m in _iam_members() if f"google_service_account.{sa}.email" in m.get("member", "")]


def test_generator_cannot_delete_raw():
    """Create + read + list on raw, never a role with storage.objects.delete (objectAdmin/objectUser/admin)."""
    raw = [m["role"] for m in _members_of("synthea_generator") if m.get("bucket") == "google_storage_bucket.raw.name"]
    assert sorted(raw) == ['"roles/storage.legacyBucketReader"', '"roles/storage.objectCreator"', '"roles/storage.objectViewer"']
    assert len(_members_of("synthea_generator")) == 6


def test_gold_object_admin_is_conditioned_on_v2_prefix():
    body = _collapse(_resource("google_storage_bucket_iam_member", "dataproc_writes_gold_v2"))
    assert 'role = "roles/storage.objectAdmin"' in body
    assert "condition { title = \"gold-v2-prefix-only\"" in body
    assert 'resource.name.startsWith(\\"projects/_/buckets/${google_storage_bucket.gold.name}/objects/v2/\\")' in body
    unconditioned_admin = [
        m for m in _members_of("dataproc_runtime")
        if m["role"] == '"roles/storage.objectAdmin"' and m.get("bucket") == "google_storage_bucket.gold.name"
        and m["_name"] != "dataproc_writes_gold_v2"
    ]  # fmt: skip
    assert unconditioned_admin == []
    assert len(_members_of("dataproc_runtime")) == 8


# roles/dataproc.worker minus its storage.* permissions (read with `gcloud iam roles describe roles/dataproc.worker`,
# 2026-10-12: 61 permissions, 28 of them storage.*; all 33 are supported in custom roles), plus
# dataprocrm.nodePools.update, which runtime 3.0's permission validation requires although no predefined role has it.
DATAPROC_RUNTIME_PERMISSIONS = [
    "cloudprofiler.profiles.create", "cloudprofiler.profiles.update",
    "datalineage.locations.processOpenLineageMessage",
    "dataproc.agents.create", "dataproc.agents.delete", "dataproc.agents.get", "dataproc.agents.list",
    "dataproc.agents.update", "dataproc.batches.computeTuningConfig", "dataproc.batches.sparkApplicationWrite",
    "dataproc.sessions.sparkApplicationWrite", "dataproc.tasks.lease", "dataproc.tasks.listInvalidatedLeases",
    "dataproc.tasks.reportStatus",
    "dataprocrm.nodePools.create", "dataprocrm.nodePools.delete", "dataprocrm.nodePools.deleteNodes",
    "dataprocrm.nodePools.get", "dataprocrm.nodePools.list", "dataprocrm.nodePools.resize", "dataprocrm.nodePools.update",
    "dataprocrm.nodes.get",
    "dataprocrm.nodes.heartbeat", "dataprocrm.nodes.list", "dataprocrm.nodes.mintOAuthToken",
    "dataprocrm.operations.get",
    "logging.logEntries.create", "logging.logEntries.route",
    "monitoring.metricDescriptors.create", "monitoring.metricDescriptors.get", "monitoring.metricDescriptors.list",
    "monitoring.monitoredResourceDescriptors.get", "monitoring.monitoredResourceDescriptors.list",
    "monitoring.timeSeries.create",
]  # fmt: skip


def test_dataproc_runtime_has_no_project_level_storage_access():
    """5b L2 finding: project roles/dataproc.worker carries storage.objects.* on every bucket (raw, gold incl.
    full_run_2b, bundles, tfstate), which made the gold v2/ condition moot. Storage comes only from bucket-level grants."""
    project = [m["role"] for m in _members_of("dataproc_runtime") if m["_type"] == "google_project_iam_member"]
    assert project == ["google_project_iam_custom_role.dataproc_runtime_worker.id"]
    role = _collapse(_resource("google_project_iam_custom_role", "dataproc_runtime_worker"))
    assert 'role_id = "dataprocRuntimeWorker"' in role
    permissions = re.search(r"permissions = \[ (.*?),? \]", role)
    assert permissions is not None
    granted = [p.strip().strip('"') for p in permissions.group(1).split(",")]
    assert granted == DATAPROC_RUNTIME_PERMISSIONS
    assert not [p for p in granted if p.startswith("storage.")]
    assert '"roles/dataproc.worker"' not in _all_tf()


def test_default_compute_sa_editor_is_removed():
    remove = _collapse(_resource("google_project_iam_member_remove", "default_compute_no_editor"))
    assert 'role = "roles/editor"' in remove
    assert 'member = "serviceAccount:${data.google_compute_default_service_account.default.email}"' in remove
    assert 'data "google_compute_default_service_account" "default"' in _all_tf()


def test_no_staging_bucket_left():
    assert "dataproc_staging" not in _all_tf()
    assert "dataproc-staging" not in _all_tf()


def test_no_resource_uses_default_compute_sa():
    assert "-compute@developer.gserviceaccount.com" not in _all_tf()
    assert "service_account { email = google_service_account.synthea_generator.email" in _collapse(
        _resource("google_compute_instance_template", "synthea_generator")
    )


def test_template_has_cost_bounds():
    body = _collapse(_resource("google_compute_instance_template", "synthea_generator"))
    for fragment in (
        'provisioning_model = "SPOT"',
        'instance_termination_action = "DELETE"',
        "max_run_duration { seconds = each.value }",
        "auto_delete = true",
        'machine_type = "n2-standard-32"',
        'name = "synthea-generator-${each.key}-${each.value}s"',
    ):
        assert fragment in body, fragment
    assert "access_config" not in body  # no external IP
    assert "create_before_destroy" not in body  # a same-name replacement created first fails with alreadyExists
    assert "startup-script" not in body  # passed per instance by the launcher


def test_spark_subnet_has_private_google_access_and_internal_firewall():
    subnet = _collapse(_resource("google_compute_subnetwork", "spark_us_central1"))
    assert "private_ip_google_access = true" in subnet
    firewall = _collapse(_resource("google_compute_firewall", "spark_internal"))
    assert 'direction = "INGRESS"' in firewall and 'allow { protocol = "all" }' in firewall
    assert "source_ranges = [google_compute_subnetwork.spark_us_central1.ip_cidr_range]" in firewall



# ---------------------------------------------------------------- v2 slice 7: the monitoring tick
MONITOR = WORKFLOWS / "monitor.yml"
MONITOR_SA = '"serviceAccount:${google_service_account.monitor.email}"'
LEDGER_SA = '"serviceAccount:${google_service_account.ledger_reader.email}"'


def _grants(member: str) -> list[tuple[str, str, str]]:
    # name/repository/topic/service_account_id (v3 slice 4) come BEFORE project: Cloud Run and Artifact Registry members also
    # carry project/location, which would otherwise make every binding of that kind look the same.
    return sorted(
        (m["_type"], m["role"], m.get("bucket") or m.get("table_id") or m.get("dataset_id") or m.get("name")
         or m.get("repository") or m.get("topic") or m.get("service_account_id") or m.get("project"))
        for m in _iam_members()
        if m.get("member") == member
    )


def _on_account(name: str) -> list[tuple[str, str]]:
    return sorted((m["role"], m["member"]) for m in _iam_members() if m.get("service_account_id") == f"google_service_account.{name}.name")


def test_monitor_grants_are_exactly():
    assert _grants(MONITOR_SA) == sorted([
        ("google_bigquery_table_iam_member", "google_project_iam_custom_role.scorer_table_writer.id",
         "google_bigquery_table.monitoring.table_id"),
        ("google_bigquery_table_iam_member", '"roles/bigquery.dataViewer"', "google_bigquery_table.predictions.table_id"),
        ("google_bigquery_table_iam_member", '"roles/bigquery.dataViewer"', "google_bigquery_table.scoring_runs.table_id"),
        ("google_bigquery_dataset_iam_member", '"roles/bigquery.dataEditor"', "google_bigquery_dataset.scratch.dataset_id"),
        ("google_storage_bucket_iam_member", '"roles/storage.objectViewer"', "google_storage_bucket.gold.name"),
        ("google_project_iam_member", '"roles/bigquery.jobUser"', "var.project_id"),
    ])  # fmt: skip
    assert _on_account("monitor") == [
        ('"roles/iam.serviceAccountTokenCreator"', "var.operator_member"),
        ('"roles/iam.workloadIdentityUser"', "local.monitor_principal_set"),
    ]


def test_ledger_reader_grants_are_exactly_read_only():
    assert _grants(LEDGER_SA) == sorted([
        ("google_bigquery_table_iam_member", '"roles/bigquery.dataViewer"', "google_bigquery_table.scoring_runs.table_id"),
        ("google_bigquery_table_iam_member", '"roles/bigquery.dataViewer"', "google_bigquery_table.monitoring.table_id"),
        ("google_project_iam_member", '"roles/bigquery.jobUser"', "var.project_id"),
    ])  # fmt: skip
    assert _on_account("ledger_reader") == [
        ('"roles/iam.serviceAccountTokenCreator"', "var.operator_member"),
        ('"roles/iam.workloadIdentityUser"', "local.monitor_principal_set"),
    ]


def test_monitor_bindings_are_keyed_to_the_monitor_workflow_on_main():
    iam = _collapse(_tf("iam.tf"))
    assert 'monitor_workflow_ref = "${var.github_repository}/.github/workflows/monitor.yml@refs/heads/main"' in iam
    assert (
        'monitor_principal_set = "principalSet://iam.googleapis.com/${local.pool_name}/attribute.workflow_ref/'
        '${local.monitor_workflow_ref}"'
    ) in iam
    bound = sorted(m["service_account_id"] for m in _iam_members() if m.get("member") == "local.monitor_principal_set")
    assert bound == ["google_service_account.ledger_reader.name", "google_service_account.monitor.name",
                     "google_service_account.scorer.name"]  # fmt: skip


def test_monitoring_table_is_partitioned_clustered_and_protected():
    body = _collapse(_resource("google_bigquery_table", "monitoring"))
    assert 'time_partitioning { type = "DAY" field = "started_at" }' in body
    assert "require_partition_filter = true" in body and "deletion_protection = true" in body
    assert 'clustering = ["sim_date"]' in body
    assert 'schema = file("${path.module}/../src/readmission_risk/warehouse/schemas/monitoring.json")' in body


def _monitor_jobs() -> dict[str, list[str]]:
    lines = _read(MONITOR).split("\n")
    jobs_at = lines.index("jobs:")
    starts = [i for i in range(jobs_at + 1, len(lines)) if re.fullmatch(r"  [a-z-]+:", lines[i])]
    return {lines[i].strip().rstrip(":"): _block(lines, i) for i in starts}


def _job_value(job: list[str], key: str) -> str | None:
    line = next((x for x in job if x.startswith(f"    {key}:")), None)
    return None if line is None else line.split(":", 1)[1].strip()


def _job_permissions(job: list[str]) -> set[str]:
    at = job.index("    permissions:")
    return {line.strip() for line in _block(job, at)[1:] if line.strip()}


def test_monitor_workflow_permissions_and_identities():
    lines = _read(MONITOR).split("\n")
    assert "permissions: {}" in lines and _triggers(lines) <= {"workflow_dispatch", "schedule"}
    jobs = _monitor_jobs()
    assert list(jobs) == ["clock", "score", "evaluate", "ledger-render", "ledger-commit", "issues"]
    credentialed = {"clock": "monitor", "score": "scorer", "evaluate": "monitor", "ledger-render": "ledger-reader"}
    for name, job in jobs.items():
        text = "\n".join(job)
        if name in credentialed:
            assert _job_permissions(job) == {"contents: read", "id-token: write"}, name
            assert f"service_account: {credentialed[name]}@synthea-readmission-risk.iam.gserviceaccount.com" in text, name
        else:
            assert "id-token" not in text and "google-github-actions/auth" not in text, name
    assert _job_permissions(jobs["ledger-commit"]) == {"contents: write"}
    assert _job_permissions(jobs["issues"]) == {"contents: read", "issues: write"}
    for name in ("ledger-commit", "issues"):
        text = "\n".join(jobs[name])
        script = "publish_ledger.py" if name == "ledger-commit" else "tick_issues.py"
        assert f"sparse-checkout: scripts/{script}" in text and "sparse-checkout-cone-mode: false" in text
        assert f"python3 scripts/{script}" in text and "pip install" not in text
    assert "cache:" not in _read(MONITOR)


def test_monitor_workflow_job_graph():
    jobs = _monitor_jobs()
    expected = {
        "clock": (None, None),
        "score": ("clock", "needs.clock.outputs.skip != 'true'"),
        "evaluate": ("[clock, score]", "always() && needs.clock.result == 'success' && needs.clock.outputs.skip != 'true'"),
        "ledger-render": ("[clock, evaluate]", "always() && needs.clock.outputs.skip != 'true'"),
        "ledger-commit": ("ledger-render", "always() && needs.ledger-render.result == 'success'"),
        "issues": ("[clock, score, evaluate, ledger-render, ledger-commit]", "always() && needs.clock.outputs.skip != 'true'"),
    }
    for name, (needs, condition) in expected.items():
        assert (_job_value(jobs[name], "needs"), _job_value(jobs[name], "if")) == (needs, condition), name
    # the weekly retry slots: a skipped clock ends the run green with every later job skipped (weekly.py)
    assert "      skip: ${{ steps.clock.outputs.skip }}" in jobs["clock"]


def test_monitor_workflow_cron_state_and_literals_match_the_config():
    config = json.loads(_read(ROOT / "deploy" / "monitoring.json"))
    text = _read(MONITOR)
    crons = re.findall(r'^\s*- cron: "([^"]+)"\s*$', text, flags=re.MULTILINE)
    assert crons == [config["crons"]["weekly"]] == ["17 * * * 1-3"]  # Part 2: the weekly retry slots are live
    assert "schedule:" in text
    assert config["gold_uri"] == expected_gold_uri()
    assert (
        f'--gold-dir {config["gold_uri"]} --run-date "$SIM_DATE" --window-days {config["window_days"]} --top-n {config["top_n"]}'
    ) in text
    assert re.findall(r"--gold-dir (\S+)", _read(SCORE)) == [config["gold_uri"]]
    for path in (MONITOR, SCORE):
        assert re.search(r"^concurrency:\n  group: score\n  cancel-in-progress: false$", _read(path), flags=re.MULTILINE), path.name


def _burn_in_run_name() -> str:
    from readmission_risk.monitoring.burn_in import BURN_IN_TITLE

    return f"run-name: ${{{{ github.event_name == 'workflow_dispatch' && inputs.cadence == 'burn_in' && '{BURN_IN_TITLE}' || 'Monitoring tick' }}}}"


def test_monitor_workflow_burn_in_dispatch():
    """The laptop burn-in timer (2026-10-20): a cadence input, a run title the timer and m3_check match exactly, and the
    input reaching the clock only through env, guarded by the event so a schedule run never carries it."""
    text = _read(MONITOR)
    assert (
        '      cadence:\n        description: "dispatch (default) or burn_in (used only by the laptop burn-in timer, make burn-in-timer)"\n'
        "        required: false\n        type: choice\n        options: [dispatch, burn_in]\n        default: dispatch\n"
    ) in text
    assert text.split("\n")[1] == _burn_in_run_name()
    assert "          REQUESTED_CADENCE: ${{ github.event_name == 'workflow_dispatch' && inputs.cadence || '' }}\n" in text
    assert not (WORKFLOWS / "schedule-probe.yml").exists()  # the 2026-10-19 schedule probe is removed


def test_monitor_workflow_expressions_only_where_allowed():
    allowed = (
        re.compile(r"^ {10}[A-Z_]+: (?:\$\{\{ [^}]+ \}\}|[^$])+$"),  # step env (one or more expressions)
        re.compile(r"^ {6}[a-z_]+: \$\{\{ steps\.[a-z]+\.outputs\.[a-z_]+ \}\}$"),  # job outputs
        re.compile(r"^ {4}if: "),  # job conditions
    )
    for i, line in enumerate(_read(MONITOR).split("\n")):
        if "${{" in line:
            assert line == _burn_in_run_name() or any(p.match(line) for p in allowed), f"monitor.yml:{i + 1}: {line.strip()}"



# ---------------------------------------------------------------- v3 slice 4: Cloud Run, deploy, kill switch
# Design: notes/eg-new-feature/v3-slice4-cloud-run-deploy-2026-11-03.md (Interfaces sections 6-8, Verification V-T).
DEPLOY = WORKFLOWS / "deploy.yml"
KILL_SA = '"serviceAccount:${google_service_account.dashboard_kill.email}"'
TRIGGER_SA = '"serviceAccount:${google_service_account.dashboard_kill_trigger.email}"'
BUILDER_SA = '"serviceAccount:${google_service_account.function_builder.email}"'
DASHBOARD_IGNORE_CHANGES = (
    "ignore_changes = [ template[0].containers[0].image, client, client_version, "
    'template[0].labels["client.knative.dev/nonce"], template[0].annotations["run.googleapis.com/client-name"], '
    'template[0].annotations["run.googleapis.com/client-version"], ]'
)


def test_dashboard_service_settings():
    body = _collapse(_resource("google_cloud_run_v2_service", "dashboard"))
    for fragment in (
        'name = "dashboard"', "location = var.region", "deletion_protection = true", 'ingress = "INGRESS_TRAFFIC_ALL"',
        "invoker_iam_disabled = true", "labels = local.dashboard_label",
        'scaling { scaling_mode = "AUTOMATIC" min_instance_count = 0 max_instance_count = 1 }',
        "service_account = google_service_account.dashboard_runtime.email",
        'execution_environment = "EXECUTION_ENVIRONMENT_GEN1"', 'timeout = "900s"', "max_instance_request_concurrency = 80",
        "session_affinity = true", "image = local.dashboard_placeholder", "ports { container_port = 8080 }",
        'limits = { cpu = "1" memory = "512Mi" }', "cpu_idle = true", "startup_cpu_boost = true",
        'traffic { type = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST" percent = 100 }', DASHBOARD_IGNORE_CHANGES,
    ):  # fmt: skip
        assert fragment in body, fragment
    assert re.search(r"template \{ labels = local\.dashboard_label", body)  # F4: billing reads template labels
    ignored = re.search(r"ignore_changes = \[(.*?)\]", body, flags=re.DOTALL).group(1)
    assert "template[0].revision" not in ignored  # provider issue #14569
    for probe in ("startup_probe", "liveness_probe"):  # the default TCP startup probe only
        assert probe not in body
    assert 'dashboard_label = { component = "dashboard" }' in _collapse(_tf("dashboard.tf"))


def test_dashboard_repositories_keep_the_newest_five():
    for name in ("dashboard", "functions"):
        body = _collapse(_resource("google_artifact_registry_repository", name))
        assert f'repository_id = "{name}"' in body and 'format = "DOCKER"' in body
        # F12: dashboard immutable; functions mutable by the API default (an explicit false is a perpetual diff, L1)
        assert ("docker_config { immutable_tags = true }" in body) == (name == "dashboard") and "immutable_tags = false" not in body
        assert "cleanup_policy_dry_run = false" in body
        assert 'cleanup_policies { id = "delete-all" action = "DELETE" condition { tag_state = "ANY" } }' in body
        assert 'cleanup_policies { id = "keep-5-newest" action = "KEEP" most_recent_versions { keep_count = 5 } }' in body


def test_dashboard_deploy_grants_are_exactly():
    assert _grants(DEPLOY_SA) == sorted([
        ("google_cloud_run_v2_service_iam_member", '"roles/run.developer"', "google_cloud_run_v2_service.dashboard.name"),
        ("google_service_account_iam_member", '"roles/iam.serviceAccountUser"', "google_service_account.dashboard_runtime.name"),
        ("google_artifact_registry_repository_iam_member", '"roles/artifactregistry.writer"',
         "google_artifact_registry_repository.dashboard.name"),
    ])  # fmt: skip
    assert _on_account("dashboard_deploy") == [('"roles/iam.workloadIdentityUser"', "local.deploy_principal_set")]


def test_dashboard_kill_and_trigger_grants_are_exactly():
    # F20/FR24: the function's identity may only update the dashboard; the trigger's may only invoke the function.
    assert _grants(KILL_SA) == [
        ("google_cloud_run_v2_service_iam_member", '"roles/run.developer"', "google_cloud_run_v2_service.dashboard.name")
    ]
    assert _grants(TRIGGER_SA) == [
        ("google_cloud_run_v2_service_iam_member", '"roles/run.invoker"', "google_cloudfunctions2_function.dashboard_kill.name")
    ]
    assert _on_account("dashboard_kill") == [] and _on_account("dashboard_kill_trigger") == []


def test_function_builder_grants_are_exactly():
    assert _grants(BUILDER_SA) == sorted([
        ("google_project_iam_member", '"roles/logging.logWriter"', "var.project_id"),
        ("google_artifact_registry_repository_iam_member", '"roles/artifactregistry.writer"',
         "google_artifact_registry_repository.functions.name"),
        ("google_storage_bucket_iam_member", '"roles/storage.objectViewer"', "google_storage_bucket.artifacts.name"),
        ("google_storage_bucket_iam_member", '"roles/storage.objectViewer"', '"gcf-v2-sources-${var.project_number}-${var.region}"'),
    ])  # fmt: skip


def test_no_admin_owner_or_editor_role_is_granted():
    roles = [m.get("role", "") for m in _iam_members()]
    assert not [r for r in roles if r in ('"roles/run.admin"', '"roles/owner"', '"roles/editor"')]


def test_deploy_sa_binding_is_keyed_to_the_deploy_workflow_on_main():
    iam = _collapse(_tf("iam.tf"))
    assert 'deploy_workflow_ref = "${var.github_repository}/.github/workflows/deploy.yml@refs/heads/main"' in iam
    assert (
        'deploy_principal_set = "principalSet://iam.googleapis.com/${local.pool_name}/attribute.workflow_ref/'
        '${local.deploy_workflow_ref}"'
    ) in iam


def test_billing_publishes_to_the_dashboard_budget_topic():
    body = _collapse(_resource("google_pubsub_topic_iam_member", "billing_publishes_dashboard_budget"))
    assert 'role = "roles/pubsub.publisher"' in body and "topic = google_pubsub_topic.dashboard_budget.id" in body
    assert 'member = "serviceAccount:billing-budget-alert@system.gserviceaccount.com"' in body


def test_dashboard_budget_and_variable():
    body = _collapse(_resource("google_billing_budget", "dashboard"))
    for fragment in (
        'projects = ["projects/${var.project_number}"]', "labels = local.dashboard_label", 'calendar_period = "MONTH"',
        'currency_code = "CHF" units = tostring(var.dashboard_budget_chf)',
        "all_updates_rule { pubsub_topic = google_pubsub_topic.dashboard_budget.id schema_version = \"1.0\" }",
        'threshold_rules { threshold_percent = 1.0 spend_basis = "CURRENT_SPEND" }',
    ):  # fmt: skip
        assert fragment in body, fragment
    variable = _collapse(re.search(r'^variable "dashboard_budget_chf" \{\n(.*?)^\}', _tf("variables.tf"), re.M | re.S).group(1))
    assert "type = number" in variable and "default = 3" in variable
    assert "var.dashboard_budget_chf == floor(var.dashboard_budget_chf)" in variable
    assert "var.dashboard_budget_chf >= 1 && var.dashboard_budget_chf <= 100" in variable


def test_dashboard_kill_function_settings():
    body = _collapse(_resource("google_cloudfunctions2_function", "dashboard_kill"))
    for fragment in (
        'runtime = "python314"', 'entry_point = "on_budget_message"',
        "service_account = google_service_account.function_builder.id",
        "docker_repository = google_artifact_registry_repository.functions.id",
        "object = google_storage_bucket_object.dashboard_kill_source.name",
        "service_account_email = google_service_account.dashboard_kill.email", 'ingress_settings = "ALLOW_INTERNAL_ONLY"',
        "max_instance_count = 1", "TARGET_SERVICE = google_cloud_run_v2_service.dashboard.id",
        "EXPECTED_BUDGET = google_billing_budget.dashboard.name",
        'event_type = "google.cloud.pubsub.topic.v1.messagePublished"', "pubsub_topic = google_pubsub_topic.dashboard_budget.id",
        'retry_policy = "RETRY_POLICY_DO_NOT_RETRY"',
        "service_account_email = google_service_account.dashboard_kill_trigger.email",
    ):  # fmt: skip
        assert fragment in body, fragment
    archive = _collapse(re.search(r'^data "archive_file" "dashboard_kill" \{\n(.*?)^\}', _tf("dashboard.tf"), re.M | re.S).group(1))
    assert 'excludes = ["__pycache__", ".pytest_cache", ".DS_Store"]' in archive and 'output_file_mode = "0644"' in archive
    source = _collapse(_resource("google_storage_bucket_object", "dashboard_kill_source"))
    assert 'name = "sha256/${data.archive_file.dashboard_kill.output_sha256}/dashboard_kill.zip"' in source
    assert "infra/.build/" in _read(ROOT / ".gitignore").split("\n")


def test_slice4_apis_are_enabled():
    apis = _tf("apis.tf")
    for api in ("run", "artifactregistry", "pubsub", "cloudfunctions", "cloudbuild", "eventarc", "logging"):
        assert f'"{api}.googleapis.com",' in apis, api


def test_function_builder_reads_only_the_function_sources():
    body = _collapse(_resource("google_storage_bucket_iam_member", "function_builder_reads_artifacts"))
    assert (
        'expression = "resource.name.startsWith(\\"projects/_/buckets/${google_storage_bucket.artifacts.name}/objects/sha256/\\") '
        '&& resource.name.endsWith(\\"/dashboard_kill.zip\\")"'
    ) in body


def test_dashboard_budget_name_output_is_sensitive():
    block = re.search(r'^output "dashboard_budget_name" \{\n(.*?)^\}', _tf("outputs.tf"), flags=re.M | re.S).group(1)
    assert re.search(r"^  sensitive\s*=\s*true", block, flags=re.M)  # F16: the name carries the billing account id


def test_dashboard_outputs():
    outputs = _collapse(_tf("outputs.tf"))
    for fragment in (
        'output "dashboard_urls" {', "value = google_cloud_run_v2_service.dashboard.urls",
        'output "dashboard_budget_name" {', "value = google_billing_budget.dashboard.name",
        'output "dashboard_budget_chf" { value = var.dashboard_budget_chf }',
    ):  # fmt: skip
        assert fragment in outputs, fragment


def test_deploy_workflow_matches_the_terraform_values():
    lines = _read(DEPLOY).split("\n")
    text = "\n".join(line for line in lines if not line.lstrip().startswith("#"))  # the comments name what is forbidden
    assert _triggers(lines) == {"workflow_dispatch"} and "permissions: {}" in lines
    assert "          service_account: dashboard-deploy@synthea-readmission-risk.iam.gserviceaccount.com" in lines
    assert (
        "          workload_identity_provider: "
        "projects/271458030145/locations/global/workloadIdentityPools/github/providers/github-actions"
    ) in lines
    assert "--allow-unauthenticated" not in text and "update-traffic" not in text and "credentials_json" not in text
    deploys = [line.strip() for line in lines if "gcloud run deploy" in line]
    assert deploys == ['run: gcloud run deploy "$SERVICE" --region "$REGION" --image "$IMAGE" --quiet']
    assert "          IMAGE: ${{ steps.push.outputs.image }}" in lines
    assert 'case "$ref" in "$REPOSITORY/dashboard@sha256:"*) ;;' in text  # only a digest reference is deployed
    assert "      REPOSITORY: us-central1-docker.pkg.dev/synthea-readmission-risk/dashboard" in lines
    assert "token_format" not in text


def test_dashboard_image_builds_are_single_manifest():
    for path in (DEPLOY, CI):
        builds = [line for line in _read(path).split("\n") if "docker build" in line]
        assert builds and all("--provenance=false --sbom=false" in b and "--platform linux/amd64" in b for b in builds), path
