"""The Spark job's --py-files zip (v2 PRD FR3): exactly the git-tracked readmission_risk/__init__.py and
readmission_risk/pipeline/**/*.py, byte-deterministic on every platform (ZIP_STORED: no compression, so the bytes do not depend
on the zlib build; fixed timestamps and modes; sorted entries), named by its sha256. The main script, scripts/run_big_join.py,
ships separately as Dataproc's main-python-file. See notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md,
Interfaces section 10.
"""

from __future__ import annotations

import hashlib
import io
import subprocess
import zipfile
from pathlib import Path

ZIP_PREFIX = "readmission_risk_spark_job"
_EPOCH = (1980, 1, 1, 0, 0, 0)


def spark_job_package_files(repo_root: Path) -> list[str]:
    """Archive names, sorted: the TRACKED readmission_risk/__init__.py and readmission_risk/pipeline/**/*.py ('src/' stripped),
    so an untracked or ignored file can never ship."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z", "src/readmission_risk/__init__.py", "src/readmission_risk/pipeline"],
        capture_output=True,
        check=True,
    )
    names = [p for p in result.stdout.decode("utf-8").split("\0") if p.endswith(".py")]
    return sorted(name.removeprefix("src/") for name in names)


def build_spark_zip_bytes(repo_root: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in spark_job_package_files(repo_root):
            info = zipfile.ZipInfo(name, date_time=_EPOCH)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            archive.writestr(info, (Path(repo_root) / "src" / name).read_bytes())
    return buffer.getvalue()


def build_spark_zip(repo_root: Path, out_dir: Path) -> tuple[Path, str]:
    """Writes out_dir/readmission_risk_spark_job-<sha256[:12]>.zip (idempotent) and returns (path, sha256 hex)."""
    data = build_spark_zip_bytes(repo_root)
    digest = hashlib.sha256(data).hexdigest()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{ZIP_PREFIX}-{digest[:12]}.zip"
    if not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)
    return path, digest
