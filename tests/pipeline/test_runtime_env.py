"""pipeline/runtime_env.collect_runtime_facts (v2 slice 6a, FR14): one real local SparkSession for the JVM-backed steps, fakes
for everything Dataproc-specific (environment, conf, jar directories, classpath)."""

from __future__ import annotations

import platform

from readmission_risk.pipeline.runtime_env import (
    MAX_VALUE_CHARS,
    RUNTIME_KEY_PREFIX,
    collect_runtime_facts,
)

CONNECTOR = "spark-bigquery-with-dependencies_2.13-0.44.0.jar"
CONNECTOR_40 = "spark-4.0-bigquery-0.44.0.jar"
GCS = "gcs-connector-3.0.4.jar"
STORAGE_CLIENT = "google-cloud-bigquerystorage-3.11.0.jar"


class _System:
    def __init__(self, props: dict[str, str] | None, raises: bool = False) -> None:
        self._props, self._raises = props or {}, raises

    def getProperty(self, name: str):
        if self._raises:
            raise RuntimeError("no JVM")
        return self._props.get(name)


class _Conf:
    def __init__(self, pairs: dict[str, str]) -> None:
        self._pairs = pairs

    def getAll(self):
        return list(self._pairs.items())

    def get(self, key: str, default=None):
        return self._pairs.get(key, default)


class _Jvm:
    def __init__(self, system: _System) -> None:
        self.java = type("java", (), {"lang": type("lang", (), {"System": system})()})()


class FakeSpark:
    def __init__(self, props: dict[str, str] | None = None, conf: dict[str, str] | None = None, jvm_raises: bool = False):
        self.sparkContext = type("sc", (), {})()
        self.sparkContext._jvm = _Jvm(_System(props, raises=jvm_raises))
        self.sparkContext.getConf = lambda: _Conf(conf or {})


def _collect(spark=None, environ=None, jar_dirs=()):
    return collect_runtime_facts(spark or FakeSpark({"java.version": "21.0.8"}), environ or {}, list(jar_dirs))


def test_real_session_records_python_and_java(spark):
    facts = collect_runtime_facts(spark, {}, [])
    assert facts["runtime.python_version"] == platform.python_version()
    assert facts["runtime.java_version"]
    assert "runtime.collection_errors" not in facts


def test_every_key_prefixed_and_every_value_a_nonempty_str(tmp_path):
    (tmp_path / CONNECTOR).write_bytes(b"")
    facts = _collect(environ={"DATAPROC_IMAGE_VERSION": "3.0.14"}, jar_dirs=[tmp_path])
    assert facts
    for key, value in facts.items():
        assert key.startswith(RUNTIME_KEY_PREFIX)
        assert isinstance(value, str) and value


def test_env_keeps_dataproc_names_without_secret_markers():
    environ = {
        "DATAPROC_IMAGE_VERSION": "3.0.14",
        "DATAPROC_API_TOKEN": "secret",
        "DATAPROC_EMPTY": "",
        "SPARK_VERSION": "4.0.2",
        "dataproc_lower": "x",
    }
    facts = _collect(environ=environ)
    env = {k: v for k, v in facts.items() if k.startswith("runtime.env.")}
    assert env == {"runtime.env.DATAPROC_IMAGE_VERSION": "3.0.14"}


def test_conf_keeps_dataproc_version_keys_only():
    conf = {
        "spark.dataproc.runtime.Version": "3.0.14",
        "spark.dataproc.tier": "standard",
        "spark.executor.version": "9",
        "spark.dataproc.empty.version": "",
    }
    facts = _collect(spark=FakeSpark({"java.version": "21"}, conf=conf))
    got = {k: v for k, v in facts.items() if k.startswith("runtime.conf.")}
    assert got == {"runtime.conf.spark.dataproc.runtime.Version": "3.0.14"}


def test_jar_dirs_match_connectors_but_not_client_jars(tmp_path):
    for name in (CONNECTOR, CONNECTOR_40, GCS, STORAGE_CLIENT, "other.jar"):
        (tmp_path / name).write_bytes(b"")
    (tmp_path / "spark-bigquery-dir.jar").mkdir()  # a directory is not a jar
    facts = _collect(jar_dirs=[tmp_path, tmp_path / "missing"])
    jars = {k: v for k, v in facts.items() if k.startswith("runtime.jar.")}
    assert jars == {f"runtime.jar.{n}": str(tmp_path) for n in (CONNECTOR, CONNECTOR_40, GCS)}


def test_spark_home_jars_are_searched(tmp_path):
    (tmp_path / "jars").mkdir()
    (tmp_path / "jars" / CONNECTOR).write_bytes(b"")
    facts = _collect(environ={"SPARK_HOME": str(tmp_path)})
    assert facts[f"runtime.jar.{CONNECTOR}"] == str(tmp_path / "jars")


def test_no_matching_jar_records_no_jar_key(tmp_path):
    (tmp_path / "other.jar").write_bytes(b"")
    assert not [k for k in _collect(jar_dirs=[tmp_path]) if k.startswith("runtime.jar.")]


def test_classpath_and_conf_jars_are_recorded(tmp_path):
    (tmp_path / GCS).write_bytes(b"")
    props = {"java.version": "21", "java.class.path": f"/x/{CONNECTOR}:/y/other.jar"}
    spark = FakeSpark(props, conf={"spark.jars": f"/z/{GCS},/z/{STORAGE_CLIENT}"})
    facts = _collect(spark=spark, jar_dirs=[tmp_path])
    jars = {k: v for k, v in facts.items() if k.startswith("runtime.jar.")}
    # GCS is found in a directory first, so it keeps the directory
    assert jars == {f"runtime.jar.{CONNECTOR}": "classpath", f"runtime.jar.{GCS}": str(tmp_path)}


def test_many_jars_are_never_truncated_away(tmp_path):
    names = [f"spark-bigquery-connector-{i:02d}.jar" for i in range(40)]
    for name in names:
        (tmp_path / name).write_bytes(b"")
    facts = _collect(jar_dirs=[tmp_path])
    assert sorted(k for k in facts if k.startswith("runtime.jar.")) == sorted(f"runtime.jar.{n}" for n in names)


def test_failing_step_is_recorded_and_the_call_returns():
    facts = _collect(spark=FakeSpark(jvm_raises=True))
    assert facts["runtime.collection_errors"] == "java_version: RuntimeError; classpath: RuntimeError"
    assert facts["runtime.python_version"] == platform.python_version()
    assert "runtime.java_version" not in facts


def test_values_cut_at_200_characters():
    exact, longer = "a" * MAX_VALUE_CHARS, "b" * (MAX_VALUE_CHARS + 1)
    facts = _collect(environ={"DATAPROC_EXACT": exact, "DATAPROC_LONGER": longer})
    assert facts["runtime.env.DATAPROC_EXACT"] == exact
    assert facts["runtime.env.DATAPROC_LONGER"] == "b" * MAX_VALUE_CHARS
