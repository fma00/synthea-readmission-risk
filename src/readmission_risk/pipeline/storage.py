"""Where the Big Join reads and writes (v2 PRD FR11): a local path stays on pathlib, exactly as before; a gs:// URI goes through
Spark's own Hadoop FileSystem (py4j), so the existence checks, the summary read and the metadata write use the same filesystem
and credentials as Spark's reads and writes (the GCS connector on Dataproc). No google-cloud-storage: the Spark job may use only
the Dataproc runtime's bundled libraries (FR3). See notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md,
Interfaces section 3.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

# Schemes routed to HadoopStorage. A test seam (tests add "file" to drive the gs:// code path offline); production never changes it.
_HADOOP_SCHEMES: tuple[str, ...] = ("gs",)
_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://")


class Storage(Protocol):
    def join(self, base: str, *parts: str) -> str: ...
    def exists(self, uri: str) -> bool: ...
    def is_file(self, uri: str) -> bool: ...
    def has_data_row(self, uri: str) -> bool: ...
    def read_text(self, uri: str) -> str: ...
    def write_text_new(self, uri: str, text: str) -> None: ...


class LocalStorage:
    """pathlib; the pre-slice-5 behaviour."""

    def join(self, base: str, *parts: str) -> str:
        return str(Path(base, *parts))

    def exists(self, uri: str) -> bool:
        return Path(uri).exists()

    def is_file(self, uri: str) -> bool:
        return Path(uri).is_file()

    def has_data_row(self, uri: str) -> bool:
        """True iff the file has a header line and at least one more line (reads at most two lines)."""
        with Path(uri).open("r", encoding="utf-8", errors="replace") as f:
            return f.readline() != "" and f.readline() != ""

    def read_text(self, uri: str) -> str:
        return Path(uri).read_text(encoding="utf-8")

    def write_text_new(self, uri: str, text: str) -> None:
        path = Path(uri)
        if path.exists():
            raise FileExistsError(f"refusing to overwrite: {uri}")
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)


class HadoopStorage:
    """Spark's Hadoop FileSystem through py4j. Needs a classic (non-Connect) SparkSession: a Dataproc Serverless PySpark batch is one."""

    def __init__(self, spark: SparkSession) -> None:
        if not (hasattr(spark, "_jvm") and hasattr(spark, "_jsc")) or spark._jvm is None:
            raise RuntimeError("HadoopStorage needs a classic SparkSession (no _jvm on this session)")
        self._spark = spark

    def join(self, base: str, *parts: str) -> str:
        return "/".join([base.rstrip("/"), *parts])

    def _fs_path(self, uri: str):
        from py4j.protocol import Py4JJavaError

        jvm = self._spark._jvm
        path = jvm.org.apache.hadoop.fs.Path(uri)
        try:
            return path.getFileSystem(self._spark._jsc.hadoopConfiguration()), path
        except Py4JJavaError as exc:
            raise _os_error(uri, exc) from exc

    def _call(self, uri: str, fn):
        from py4j.protocol import Py4JJavaError

        try:
            return fn()
        except Py4JJavaError as exc:
            raise _os_error(uri, exc) from exc

    def exists(self, uri: str) -> bool:
        fs, path = self._fs_path(uri)
        return bool(self._call(uri, lambda: fs.exists(path)))

    def is_file(self, uri: str) -> bool:
        fs, path = self._fs_path(uri)
        return bool(self._call(uri, lambda: fs.exists(path) and fs.getFileStatus(path).isFile()))

    def _reader(self, uri: str):
        fs, path = self._fs_path(uri)
        if not self._call(uri, lambda: fs.exists(path)):
            raise FileNotFoundError(f"No such file: {uri}")
        jvm = self._spark._jvm
        return self._call(uri, lambda: jvm.java.io.BufferedReader(jvm.java.io.InputStreamReader(fs.open(path), "UTF-8")))

    def has_data_row(self, uri: str) -> bool:
        reader = self._reader(uri)
        try:
            return self._call(uri, lambda: reader.readLine() is not None and reader.readLine() is not None)
        finally:
            self._call(uri, reader.close)

    def read_text(self, uri: str) -> str:
        reader = self._reader(uri)
        lines = []
        try:
            while (line := self._call(uri, reader.readLine)) is not None:
                lines.append(line)
        finally:
            self._call(uri, reader.close)
        return "\n".join(lines) + ("\n" if lines else "")

    def write_text_new(self, uri: str, text: str) -> None:
        fs, path = self._fs_path(uri)
        if self._call(uri, lambda: fs.exists(path)):
            raise FileExistsError(f"refusing to overwrite: {uri}")
        data = bytearray(text.encode("utf-8"))

        def write() -> None:
            out = fs.create(path, False)
            try:
                out.write(data)
            finally:
                out.close()

        self._call(uri, write)


def _os_error(uri: str, exc: Exception) -> OSError:
    java = getattr(exc, "java_exception", None)
    message = str(java.toString()) if java is not None else str(exc)
    first = message.strip().splitlines()[0] if message.strip() else type(exc).__name__
    if "No FileSystem for scheme" in first:
        first += " (local Spark has no GCS connector; run this on Dataproc: the PRD's local[*]-on-a-VM fallback is not built)"
    return OSError(f"{uri}: {first}")


def storage_for(location: str | Path, spark: SparkSession | None) -> tuple[Storage, str]:
    """(storage, location as a str). A Path, or a str with no "<scheme>://" prefix -> LocalStorage. A scheme in _HADOOP_SCHEMES
    (production: gs only) -> HadoopStorage(spark), with one trailing '/' stripped; spark is required. Any other scheme ->
    ValueError. Each location gets its own storage, so local input with gs:// output (or the reverse) is allowed."""
    if isinstance(location, Path):
        if str(location).startswith("gs:/"):
            raise ValueError(f"gs:// locations must be passed as str, not Path (Path collapses '//'): {location}")
        return LocalStorage(), str(location)
    match = _SCHEME_RE.match(location)
    if match is None:
        return LocalStorage(), location
    scheme = match.group(1).lower()
    if scheme not in _HADOOP_SCHEMES:
        raise ValueError(f"unsupported location scheme {scheme + '://'!r}: {location} (supported: a local path, gs://)")
    if spark is None:
        raise ValueError(f"a SparkSession is required for {location}")
    return HadoopStorage(spark), location[:-1] if location.endswith("/") else location
