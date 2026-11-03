"""The dashboard image's recipe (v3 slice 4, Verification V-C in notes/eg-new-feature/v3-slice4-cloud-run-deploy-2026-11-03.md).

A repository-file test at the tests/ root, like test_lockfiles.py. It pins the Dockerfile's contract (base by tag AND digest,
non-root, a hash-checked binary-only install of the dashboard lockfile alone, the package from source, the commit from the build)
and the .dockerignore allow-list. The CI job dashboard-image is the proof that the recipe builds and BuildKit matches the patterns.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text()
INSTRUCTIONS = [line for line in DOCKERFILE.split("\n") if line and not line.startswith("#")]
DOCKERIGNORE = """*
!requirements-dashboard.txt
!src/readmission_risk
!scripts/dashboard_app.py
!scripts/dashboard_image_check.py
!.streamlit/config.toml
**/__pycache__
**/*.pyc
"""


def _keyword(line: str) -> str:
    return line.split(" ", 1)[0]


def test_base_is_pinned_by_tag_and_digest():
    (base,) = [line for line in INSTRUCTIONS if _keyword(line) == "FROM"]
    assert re.fullmatch(r"FROM python:3\.14\.\d+-slim-trixie@sha256:[0-9a-f]{64}", base)
    assert not DOCKERFILE.startswith("# syntax=")  # a parser directive must be the first line


def test_the_only_install_is_the_hash_checked_binary_only_dashboard_lockfile():
    installs = [line for line in INSTRUCTIONS if "pip" in line and _keyword(line) == "RUN"]
    assert installs == ["RUN pip install --require-hashes --only-binary=:all: --no-deps -r requirements-dashboard.txt"]
    assert "requirements.txt" not in DOCKERFILE.replace("requirements-dashboard.txt", "")


def test_runs_as_non_root_after_every_build_step():
    keywords = [_keyword(line) for line in INSTRUCTIONS]
    user_at = keywords.index("USER")
    assert INSTRUCTIONS[user_at] == "USER 10001"
    assert all(k not in ("RUN", "COPY") for k in keywords[user_at:])


def test_cmd_runs_the_entrypoint_on_cloud_runs_port():
    (cmd,) = [line for line in INSTRUCTIONS if _keyword(line) == "CMD"]
    assert json.loads(cmd[len("CMD "):]) == [
        "sh", "-c", "exec streamlit run scripts/dashboard_app.py --server.port=${PORT:-8080} --server.address=0.0.0.0"
    ]  # fmt: skip


def test_package_from_source_and_commit_from_the_build():
    keywords = [_keyword(line) for line in INSTRUCTIONS]
    env = INSTRUCTIONS[keywords.index("ENV", keywords.index("ARG"))]
    assert "ARG GIT_COMMIT" in INSTRUCTIONS and keywords.index("ARG") < INSTRUCTIONS.index(env)
    assert env == "ENV PYTHONPATH=/app/src READMISSION_RISK_GIT_COMMIT=${GIT_COMMIT}"
    assert "WORKDIR /app" in INSTRUCTIONS and "COPY .streamlit/config.toml ./.streamlit/config.toml" in INSTRUCTIONS


def test_dockerignore_is_the_exact_allow_list_and_covers_every_copy():
    assert (ROOT / ".dockerignore").read_text() == DOCKERIGNORE
    allowed = {line[1:] for line in DOCKERIGNORE.split("\n") if line.startswith("!")}
    sources = [s for line in INSTRUCTIONS if _keyword(line) == "COPY" for s in line.split()[1:-1]]
    assert sources and all(s in allowed for s in sources), sorted(set(sources) - allowed)
