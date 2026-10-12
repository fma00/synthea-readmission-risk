"""Fixtures for the slice-5 operations tests: a hermetic temporary git repository with a bare `origin` (no global/system git
config, no signing, no hooks), so check_prereg_committed's `git fetch origin` and `branch -r --contains` work offline."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_GIT_C = ["-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", "-c", "core.hooksPath=/dev/null"]


@pytest.fixture
def hermetic_git(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.invalid")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *_GIT_C, "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def git_repo(tmp_path, hermetic_git):
    """A repo holding the real prereg.yaml, committed and pushed to a bare origin. Returns its path."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", *_GIT_C, "init", "--bare", "-q", str(origin)], check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    shutil.copy(REPO / "prereg.yaml", repo / "prereg.yaml")
    git(repo, "add", "prereg.yaml")
    git(repo, "commit", "-q", "-m", "prereg")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "HEAD:main")
    return repo
