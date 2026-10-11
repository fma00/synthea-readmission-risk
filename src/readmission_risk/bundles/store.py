"""Bundles on local disk: <bundles-root>/<bundle_id>/{manifest.json, model.pkl, split_assignment.csv}. The one module
that knows the storage layout (the same seam as scoring/store.write_partition). Bundles are never overwritten (FR7):
written to a temp directory, renamed into place, made read-only. gs:// URIs are dispatched to bundles.gcs (imported
lazily, so this module still imports nothing outside the standard library). See
notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md (Interfaces > bundles/store.py) and
notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md (Interfaces > bundles/store.py changes).
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import uuid
from collections.abc import Mapping
from pathlib import Path

from .manifest import FILE_ROLES, MANIFEST_FILENAME, file_entry, manifest_bytes

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def local_bundle_path(bundle_uri: str) -> Path:
    """A bundle_uri is a local path string, absolute or relative to the current working directory -- also when it comes
    from a champion file elsewhere. ValueErrors carry no key prefix (the caller adds one)."""
    if not bundle_uri:
        raise ValueError("empty bundle URI")
    if bundle_uri.startswith("gs://"):
        raise ValueError(f"not a local bundle path: {bundle_uri}")
    match = _SCHEME_RE.match(bundle_uri)
    if match:
        raise ValueError(f"unsupported bundle URI scheme {match.group(0)!r}: {bundle_uri}")
    return Path(bundle_uri)


def check_bundle_uri(bundle_uri: str) -> None:
    """Scheme check only: a well-formed gs:// URI or a local path passes; existence is list_bundle_entries' job."""
    if not bundle_uri:
        raise ValueError("empty bundle URI")
    if bundle_uri.startswith("gs://"):
        from . import gcs

        gcs.parse_gs_uri(bundle_uri)
        return
    local_bundle_path(bundle_uri)


def bundle_uri_leaf(bundle_uri: str) -> str:
    """The last path segment of a bundle URI: the directory (or object prefix) a bundle lives in, which I3 compares with
    its bundle_id."""
    if bundle_uri.startswith("gs://"):
        from . import gcs

        return gcs.parse_gs_uri(bundle_uri)[1].rsplit("/", 1)[-1]
    return local_bundle_path(bundle_uri).resolve().name


def _expected_bytes(files: Mapping[str, bytes], manifest: dict) -> dict[str, bytes]:
    """Every check that can fail, before the filesystem is touched; returns {name: bytes} including manifest.json."""
    if set(files) != set(FILE_ROLES):
        raise ValueError(f"a bundle holds exactly the files {sorted(FILE_ROLES)}; got {sorted(files)}")
    expected_table = {name: file_entry(name, data) for name, data in files.items()}
    if manifest.get("files") != expected_table:
        raise ValueError("the manifest's files table does not match the files' sha256 and size")
    return {MANIFEST_FILENAME: manifest_bytes(manifest), **files}


def _is_identical(target: Path, expected: dict[str, bytes]) -> bool:
    if target.is_symlink() or not target.is_dir():
        return False
    entries = sorted(os.listdir(target))
    if entries != sorted(expected):
        return False
    for name, data in expected.items():
        path = target / name
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
            return False
    return True


def _remove_tree(path: Path) -> None:
    """Removes a (possibly read-only) temp directory."""

    def make_writable(func, failed_path, _exc):
        os.chmod(failed_path, stat.S_IRWXU)
        parent = os.path.dirname(failed_path)
        os.chmod(parent, stat.S_IRWXU)
        func(failed_path)

    if path.exists():
        os.chmod(path, stat.S_IRWXU)
        shutil.rmtree(path, onexc=make_writable)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # some platforms cannot fsync a directory; durability is best-effort there
    finally:
        os.close(fd)


def write_bundle(files: Mapping[str, bytes], manifest: dict, bundles_root: Path | str) -> tuple[str, bool]:
    """Writes bundles_root/<bundle_id>/ and returns (bundle_uri, created). An identical existing bundle is an idempotent
    no-op (created=False); any other existing entry at that path is a ValueError -- bundles are never overwritten.
    Known limit, accepted for a single-user local store (design note, write_bundle): POSIX rename() silently replaces an
    EMPTY directory, so an empty bundles_root/<bundle_id>/ created by another process between the existence check and the
    rename is replaced rather than refused (nothing is lost: it was empty). A gs:// root goes to bundles.gcs.write_files
    (create-only, if_generation_match=0, no idempotent comparison: the writer cannot read)."""
    expected = _expected_bytes(files, manifest)
    bundle_id = manifest["bundle_id"]
    if isinstance(bundles_root, str) and bundles_root.startswith("gs://"):
        from . import gcs

        return gcs.write_files(expected, bundles_root, bundle_id)
    bundles_root = Path(bundles_root)
    target = bundles_root / bundle_id
    uri = str(target)

    def compare_existing() -> tuple[str, bool]:
        if _is_identical(target, expected):
            return uri, False
        raise ValueError(
            f"bundle {bundle_id} already exists at {uri} with different content; bundles are never overwritten"
        )

    if os.path.lexists(target):
        return compare_existing()

    bundles_root.mkdir(parents=True, exist_ok=True)
    tmp = bundles_root / f".tmp-{bundle_id}-{uuid.uuid4().hex}"
    tmp.mkdir()
    try:
        for name, data in expected.items():
            path = tmp / name
            with open(path, "xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(path, 0o444)
        try:
            os.rename(tmp, target)
        except OSError:
            _remove_tree(tmp)
            if os.path.lexists(target):
                return compare_existing()
            raise
    except BaseException:
        _remove_tree(tmp)
        raise
    # read+traverse for everyone, like a normal directory minus write; the bundle is immutable, not secret
    os.chmod(target, 0o555)  # noqa: S103
    _fsync_dir(bundles_root)
    return uri, True


def list_bundle_entries(bundle_uri: str) -> list[str]:
    """Sorted names of ALL entries in the bundle directory (files, subdirectories, symlinks alike)."""
    if bundle_uri.startswith("gs://"):
        from . import gcs

        return gcs.list_entries(bundle_uri)
    path = local_bundle_path(bundle_uri)
    if not os.path.lexists(path):
        raise FileNotFoundError(f"bundle not found: no bundle at {bundle_uri}")
    if not path.is_dir():
        raise ValueError(f"bundle not found: {bundle_uri} is not a directory")
    return sorted(os.listdir(path))


def read_bundle_file(bundle_uri: str, name: str) -> bytes:
    """Bytes of one regular file in the bundle directory; a symlink or directory is refused, so a bundle cannot point
    outside itself."""
    if name in ("", ".", "..") or "/" in name or "\\" in name:
        raise ValueError(f"invalid bundle file name {name!r}")
    if bundle_uri.startswith("gs://"):
        from . import gcs

        return gcs.read_file(bundle_uri, name)
    path = local_bundle_path(bundle_uri) / name
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        raise FileNotFoundError(f"bundle at {bundle_uri} has no {name}") from None
    if not stat.S_ISREG(mode):
        raise ValueError(f"{name} of bundle at {bundle_uri} is not a regular file (symlinks and directories are refused)")
    return path.read_bytes()
