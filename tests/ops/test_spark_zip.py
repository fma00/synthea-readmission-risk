"""The --py-files zip (FR3): exactly the tracked Spark-job sources, byte-deterministic, importable with the stdlib alone."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

from readmission_risk.ops.spark_zip import (
    build_spark_zip,
    build_spark_zip_bytes,
    spark_job_package_files,
)
from tests.ops.conftest import REPO, git
from tests.pipeline.test_runtime_dependencies import SPARK_JOB_SOURCES


def _tracked(paths) -> set[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", *map(str, paths)], capture_output=True, check=True).stdout
    return {p for p in out.decode().split("\0") if p}


def test_zip_contents_equal_spark_job_sources():
    expected = {str(p.relative_to(REPO / "src")) for p in SPARK_JOB_SOURCES if p.is_relative_to(REPO / "src")}
    expected &= {p.removeprefix("src/") for p in _tracked(SPARK_JOB_SOURCES)}
    assert set(spark_job_package_files(REPO)) == expected
    assert "readmission_risk/pipeline/fingerprint.py" in expected and "readmission_risk/__init__.py" in expected


def test_zip_is_byte_deterministic_and_pinned_format(tmp_path):
    (a, sha_a), (b, sha_b) = build_spark_zip(REPO, tmp_path / "a"), build_spark_zip(REPO, tmp_path / "b")
    assert sha_a == sha_b and a.read_bytes() == b.read_bytes()
    assert a.name == f"readmission_risk_spark_job-{sha_a[:12]}.zip"
    with zipfile.ZipFile(a) as archive:
        for info in archive.infolist():
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.external_attr >> 16 == 0o644


def test_no_pycache_or_dirs():
    names = spark_job_package_files(REPO)
    assert all(n.endswith(".py") and "__pycache__" not in n for n in names)


def test_zip_importable_isolated(tmp_path):
    """-I -S: no site-packages, no PYTHONPATH, no cwd: the generation path needs only the stdlib and the zip (as on the VM)."""
    path, _ = build_spark_zip(REPO, tmp_path)
    code = (
        f"import sys; sys.path.insert(0, {str(path)!r}); "
        "import readmission_risk.pipeline.generation, readmission_risk.pipeline.gold_metadata; print('ok')"
    )
    result = subprocess.run([sys.executable, "-I", "-S", "-c", code], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.stdout.strip() == "ok", result.stderr


def test_untracked_file_not_shipped(git_repo):
    src = Path(git_repo) / "src" / "readmission_risk" / "pipeline"
    src.mkdir(parents=True)
    (src.parent / "__init__.py").write_text("")
    (src / "__init__.py").write_text("")
    (src / "real.py").write_text("X = 1\n")
    git(git_repo, "add", "src")
    git(git_repo, "commit", "-q", "-m", "src")
    (src / "untracked.py").write_text("Y = 2\n")
    assert spark_job_package_files(git_repo) == [
        "readmission_risk/__init__.py",
        "readmission_risk/pipeline/__init__.py",
        "readmission_risk/pipeline/real.py",
    ]
    assert b"Y = 2" not in build_spark_zip_bytes(git_repo)
