"""Repository tooling checks: the Makefile, the pre-commit hooks and the dev lockfile they depend on.

This file sits at the ``tests/`` root, like test_docs.py, because it tests repository tooling, not a package under
``src/``. It needs no JVM, no data and no network. The Makefile tests drive the real `make` (most with `make -n`, which
prints recipes without running them) and are skipped where `make` is missing; the file checks run everywhere.
Design: notes/eg-new-feature/v2-slice1-tooling-and-dataproc-runtime-leg-2026-10-11.md.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = ROOT / "Makefile"
PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"

EXPECTED_TARGETS = {
    "help", "setup", "hooks", "lint", "test", "pre-commit", "train", "export", "score", "score-bq", "m5-check",
    "setup-runtime", "test-runtime", "lock-dev", "lock-runtime", "tf-validate", "spark-zip", "generate-vm", "bigjoin-batch",
}
# Every variable the Makefile reads, plus make's own propagation variables: a pytest started by `make test X=y` (or a
# shell exporting VENV=...) must not change what these tests see.
MAKEFILE_VARIABLES = ("PYTHON", "PYTHON312", "VENV", "RUNTIME_VENV", "LOCK_VENV", "GOLD_DIR", "REFERENCE_DATE",
                      "TEST_START_DATE", "EXPERIMENT", "RUN_DATE", "TRAIN_EXPERIMENT", "TRAIN_ARGS", "SCORE_ARGS",
                      "EXPORT_MODEL", "EXPORT_ARGS", "TERRAFORM", "GOLD_URI", "SCORER_SA", "RUN", "JDK_TARBALL", "GEN_ARGS",
                      "BATCH_ARGS")
SCRUBBED_ENV = ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "GNUMAKEFLAGS", "MAKEFILES", *MAKEFILE_VARIABLES)
EXPECTED_PRE_COMMIT = [
    "repos:",
    "  - repo: local",
    "    hooks:",
    "      - id: ruff",
    "        name: ruff check",
    """        entry: sh -c 'exec "$(git rev-parse --git-common-dir)/../.venv/bin/ruff" check --force-exclude "$@"' --""",
    "        language: unsupported",
    "        types_or: [python, pyi, jupyter]",
    "      - id: zizmor",
    "        name: zizmor (offline)",
    """        entry: sh -c 'exec "$(git rev-parse --git-common-dir)/../.venv/bin/zizmor" --offline .' --""",
    "        language: unsupported",
    "        files: ^(\\.github/|\\.pre-commit-config\\.yaml$)",
    "        pass_filenames: false",
]

needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="needs make")


def run_make(*args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_ENV}
    return subprocess.run(["make", "--no-print-directory", *args], cwd=ROOT, env=env, capture_output=True, text=True, check=False)


def _dry_run(*args: str) -> str:
    result = run_make("-n", *args)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _pins(path: Path) -> dict[str, str]:
    """`name==version` lines of a pip-compile lockfile, keyed by the PEP 503 normalised name."""
    found = re.findall(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)", path.read_text(), flags=re.MULTILINE)
    return {re.sub(r"[-_.]+", "-", name).lower(): version for name, version in found}


# ---------------------------------------------------------------- Makefile


def test_makefile_declares_the_agreed_targets():
    lines = MAKEFILE.read_text().split("\n")
    phony = {t for line in lines if line.startswith(".PHONY:") for t in line.split(":", 1)[1].split()}
    assert phony == EXPECTED_TARGETS
    for target in EXPECTED_TARGETS:
        assert any(re.match(rf"^{re.escape(target)}:.*## ", line) for line in lines), f"{target} has no `## ` description"


def test_makefile_avoids_features_gnu_make_3_81_lacks():
    # Comment lines are skipped: the Makefile's header names these features to warn against them.
    text = "\n".join(line for line in MAKEFILE.read_text().split("\n") if not line.lstrip().startswith("#"))
    assert not re.search(r"^define \S+ *=", text, flags=re.MULTILINE), "`define NAME =` needs GNU Make 3.82+"
    assert not re.search(r"^\S+ *!=", text, flags=re.MULTILINE), "`!=` assignment needs GNU Make 4.0+"
    for feature in (".ONESHELL", "$(file ", ".RECIPEPREFIX"):
        assert feature not in text, f"{feature} is not available in GNU Make 3.81"


@needs_make
def test_make_help_lists_every_target():
    result = run_make("help")
    assert result.returncode == 0, result.stderr
    listed = {line.split()[0] for line in result.stdout.split("\n") if line.strip()}
    assert listed == EXPECTED_TARGETS


@needs_make
def test_make_lint_runs_ruff_and_offline_zizmor_from_the_venv():
    assert [line for line in _dry_run("lint").split("\n") if line.strip()] == [
        ".venv/bin/ruff check .",
        ".venv/bin/zizmor --offline .",
    ]


@needs_make
def test_make_score_passes_the_reference_defaults():
    assert (
        ".venv/bin/python scripts/score_discharges.py --gold-dir data/gold/full_run_2b --run-date 20250630"
    ) in _dry_run("score")
    assert "--experiment-name" not in _dry_run("score")  # the scorer reads the champion bundle, not MLflow
    assert "--run-date 20240115 --show-observed-outcomes" in _dry_run("score", "RUN_DATE=20240115", "SCORE_ARGS=--show-observed-outcomes")


@needs_make
def test_make_score_bq_and_m5_check_run_as_the_scorer_sa():
    sa = "--impersonate-service-account scorer@synthea-readmission-risk.iam.gserviceaccount.com"
    assert (
        ".venv/bin/python scripts/score_to_bigquery.py --gold-dir gs://synthea-readmission-risk-gold/full_run_2b "
        f"--run-date 20250630 {sa}"
    ) in _dry_run("score-bq")
    assert "--bundle bundles/x" in _dry_run("score-bq", "SCORE_ARGS=--bundle bundles/x")
    assert f".venv/bin/python scripts/seeded_data_test_check.py --run-date 20250630 {sa}" in _dry_run("m5-check")


def test_gold_uri_default_matches_infra():
    from tests.test_infra import expected_gold_uri

    match = re.search(r"^GOLD_URI\s+\?=\s+(\S+)$", MAKEFILE.read_text(), re.MULTILINE)
    assert match is not None and match.group(1) == expected_gold_uri()


def test_make_tf_validate_runs_fmt_init_without_backend_and_validate():
    # The same three commands as ci.yml's terraform-validate job; never a backend, plan or apply.
    assert [line for line in _dry_run("tf-validate").split("\n") if line] == [
        "terraform -chdir=infra fmt -check -recursive -diff",
        "terraform -chdir=infra init -backend=false -input=false -lockfile=readonly",
        "terraform -chdir=infra validate",
    ]


@needs_make
def test_make_export_passes_the_reference_defaults():
    assert (
        ".venv/bin/python scripts/export_bundle.py --experiment-name readmission-risk-v1 --model logistic_regression"
    ) in _dry_run("export")
    assert "--model xgboost_calibrated --bundles-dir x" in _dry_run("export", "EXPORT_MODEL=xgboost_calibrated", "EXPORT_ARGS=--bundles-dir x")


@needs_make
def test_make_train_refuses_a_missing_or_reference_experiment_name(tmp_path):
    missing = run_make("train", f"VENV={tmp_path}/novenv")
    assert missing.returncode != 0
    assert "set TRAIN_EXPERIMENT" in missing.stderr
    reference = run_make("train", f"VENV={tmp_path}/novenv", "TRAIN_EXPERIMENT=readmission-risk-v1")
    assert reference.returncode != 0
    assert "must differ" in reference.stderr
    assert "--experiment-name x --seed=7" in _dry_run("train", "TRAIN_EXPERIMENT=x", "TRAIN_ARGS=--seed=7")


@needs_make
def test_make_hooks_tolerates_branches_without_a_config():
    assert ".venv/bin/pre-commit install --allow-missing-config" in _dry_run("hooks")


@needs_make
def test_make_test_runtime_runs_only_the_pipeline_tests_in_the_runtime_venv():
    assert ".venv-dataproc/bin/pytest tests/pipeline" in _dry_run("test-runtime")


@needs_make
def test_make_runtime_and_lock_venvs_are_recreated_and_guarded(tmp_path):
    assert 'rm -rf ".venv-dataproc" && python3.12 -m venv ".venv-dataproc"' in _dry_run("setup-runtime")
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("must survive")
    for value in (str(tmp_path), f".venv-x/../{os.path.relpath(tmp_path, ROOT)}", ".venv-a..b"):
        result = run_make("setup-runtime", f"RUNTIME_VENV={value}")
        assert result.returncode != 0, value
        assert "must name a .venv-* directory" in result.stderr, value
        assert sentinel.exists(), value


@needs_make
def test_make_lock_targets_compile_under_python_3_12():
    runtime = _dry_run("lock-runtime")
    assert 'python3.12 -m venv ".venv-lock312"' in runtime
    assert (
        ".venv-lock312/bin/pip-compile --upgrade --generate-hashes"
        " --output-file=requirements-dataproc-3.0.txt requirements-dataproc-3.0.in"
    ) in runtime
    assert (
        ".venv-lock312/bin/pip-compile --allow-unsafe --generate-hashes"
        " --output-file=requirements-dev.txt requirements-dev.in"
    ) in _dry_run("lock-dev")


# ---------------------------------------------------------------- pre-commit and the dev lockfile


def test_pre_commit_config_is_local_hooks_from_the_main_checkouts_venv_only():
    lines = [line.rstrip() for line in PRE_COMMIT_CONFIG.read_text().split("\n")]
    assert [line for line in lines if line.strip() and not line.lstrip().startswith("#")] == EXPECTED_PRE_COMMIT


def test_pre_commit_is_a_hash_pinned_dev_dependency():
    assert "pre-commit" in [line.strip() for line in (ROOT / "requirements-dev.in").read_text().split("\n")]
    lock = (ROOT / "requirements-dev.txt").read_text().split("\n")
    at = next((i for i, line in enumerate(lock) if line.startswith("pre-commit==")), None)
    assert at is not None, "pre-commit is not pinned in requirements-dev.txt"
    assert lock[at + 1].strip().startswith("--hash=sha256:")


def test_dev_and_main_lockfiles_agree_on_shared_pins():
    # Both are installed into the same .venv by separate `pip install` calls, so a divergence would silently replace the
    # main lockfile's pin. requirements-dataproc-3.0.txt is never co-installed with them and is not compared.
    main, dev = _pins(ROOT / "requirements.txt"), _pins(ROOT / "requirements-dev.txt")
    assert {name: (main[name], dev[name]) for name in main.keys() & dev.keys() if main[name] != dev[name]} == {}


@pytest.mark.skipif(importlib.util.find_spec("pre_commit") is None, reason="pre-commit is not installed")
def test_pre_commit_config_validates():
    result = subprocess.run(
        [sys.executable, "-m", "pre_commit", "validate-config", str(PRE_COMMIT_CONFIG)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "deprecat" not in (result.stdout + result.stderr).lower()


@needs_make
def test_live_targets_refuse_without_required_variables(tmp_path):
    """v2 slice 5: the two targets that spend money refuse to run without their variables, and pass them through."""
    for target, needed in (("generate-vm", "set RUN="), ("bigjoin-batch", "set RUN=")):
        result = run_make(target, f"VENV={tmp_path}/novenv")
        assert result.returncode != 0 and needed in result.stderr, target
    no_jdk = run_make("generate-vm", f"VENV={tmp_path}/novenv", "RUN=pilot_20k")
    assert no_jdk.returncode != 0 and "set JDK_TARBALL=" in no_jdk.stderr
    assert "scripts/launch_generation.py --run pilot_20k --jdk-tarball /x/jdk.tgz --dry-run" in _dry_run(
        "generate-vm", "RUN=pilot_20k", "JDK_TARBALL=/x/jdk.tgz", "GEN_ARGS=--dry-run"
    )
    assert "scripts/submit_big_join_batch.py --run pilot_20k --dry-run" in _dry_run("bigjoin-batch", "RUN=pilot_20k", "BATCH_ARGS=--dry-run")
    assert ".venv/bin/python scripts/build_spark_zip.py" in _dry_run("spark-zip")

