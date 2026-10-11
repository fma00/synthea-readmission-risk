"""Shared test helpers for the scoring slice (plain module, not conftest.py, which holds fixtures only).
See notes/eg-new-feature/batch-scoring-cli-2026-09-20.md (Verification criteria > Test infrastructure) and, for the
bundle fixtures (tamper_bundle, BUNDLE_CASES, INTEGRITY_CASES), notes/eg-new-feature/v2-slice2-model-bundle-and-manifest-2026-10-11.md.
"""

from __future__ import annotations

import re
from datetime import date

import pandas as pd

from readmission_risk.scoring.ranking import build_predictions


def ts(*values: str) -> pd.DatetimeIndex:
    """UTC timestamps from ISO-8601 strings of mixed precision (a plain pd.to_datetime rejects mixed precision)."""
    return pd.to_datetime(list(values), utc=True, format="ISO8601")


def assert_count_phrase(message: str, n: int, phrase: str) -> None:
    """Asserts `message` contains f"{n} {phrase}" and that the count is not merely the tail of a longer number
    (\"3 in-sample\" must not be satisfied by \"13 in-sample\")."""
    assert re.search(rf"(?<![0-9]){n} {re.escape(phrase)}", message), f"{n} {phrase!r} not found in: {message}"


def make_batch(ids, *, stops=None, reasons=None, descriptions=None) -> pd.DataFrame:
    """A label-free batch with the columns build_predictions needs. Row i has patient p-<id>, a distinct discharge time
    (one day apart, from 2026-03-01), reason code R<i> and description 'reason <i>' unless overridden."""
    n = len(ids)
    stops = stops if stops is not None else ts(*[f"2026-03-{i + 1:02d}T12:00:00Z" for i in range(n)])
    return pd.DataFrame(
        {
            "patient_id": [f"p-{i}" for i in ids],
            "encounter_id": list(ids),
            "index_stop": stops,
            "admission_reason_code": reasons if reasons is not None else [f"R{i}" for i in range(n)],
            "admission_reason_description": descriptions if descriptions is not None else [f"reason {i}" for i in range(n)],
        }
    )


PREDICTION_KWARGS = {
    "run_date": date(2026, 6, 30),
    "top_n": 3,
    "window_days": 180,
    "model_bundle_id": "b" * 64,
    "model_run_id": "run-abc",
    "model_name": "logistic_regression",
    "label_definition": "unplanned_readmission_v1",
    "gold_fingerprint": "f" * 64,
    "gold_reference_date": "20260916",
}


def make_predictions(ids=("e", "c", "a", "b", "d"), scores=(0.2, 0.5, 0.5, 0.5, 0.1), **overrides) -> pd.DataFrame:
    """build_predictions on the tie fixture (input order e, c, a, b, d) with PREDICTION_KWARGS overridden by `overrides`."""
    kwargs = {**PREDICTION_KWARGS, **overrides}
    return build_predictions(make_batch(list(ids)), list(scores), **kwargs)


# ---------------------------------------------------------------------------------------------------------------
# Real-store fixtures: one trained store per test session, exported to two bundles, plus tampered bundle copies.
# ---------------------------------------------------------------------------------------------------------------
import json
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import mlflow
import numpy as np
from mlflow.tracking import MlflowClient

from readmission_risk.bundles.champion import champion_json
from readmission_risk.bundles.manifest import (
    compute_bundle_id,
    manifest_bytes,
    sha256_hex,
)
from readmission_risk.models.bundle_export import export_bundle
from readmission_risk.models.data import FEATURE_COLUMNS
from readmission_risk.models.tracking import mlflow_tracking_uri
from readmission_risk.models.training import TrainingConfig, train_and_evaluate
from readmission_risk.models.versions import current_python_version
from tests.models.helpers import (
    REFERENCE_DATE_STR,
    TEST_START_STR,
    make_gold_frame,
    write_test_gold_metadata,
)

SCORING_EXPERIMENT = "scoring-test"
DEFAULT_RUN_DATE = "20260630"
DEFAULT_WINDOW_DAYS = 180


def write_gold_dir(directory: Path, df: pd.DataFrame, *, n_parts: int = 2, shuffle_seed: int | None = None, **metadata_overrides) -> Path:
    """The gold frame as `n_parts` Parquet part files (rows optionally shuffled first) plus a valid _gold_metadata.json."""
    directory.mkdir(parents=True)
    frame = df if shuffle_seed is None else df.sample(frac=1, random_state=shuffle_seed)
    bounds = np.linspace(0, len(frame), n_parts + 1).astype(int)
    for i in range(n_parts):
        frame.iloc[bounds[i] : bounds[i + 1]].to_parquet(directory / f"part-{i}.parquet", index=False)
    write_test_gold_metadata(directory, df, **metadata_overrides)
    return directory


@dataclass(frozen=True, eq=False)
class ScoringEnv:
    gold_dir: Path
    tracking_dir: Path
    experiment_name: str
    lr_run_id: str
    xgb_run_id: str
    bundles_root: Path
    lr_bundle_uri: str
    xgb_bundle_uri: str
    df: pd.DataFrame  # the fixture gold frame (with the label)
    n_train: int  # from the LR run's params split_n_train / split_n_test, never hard-coded
    n_test: int

    def client(self) -> MlflowClient:
        return MlflowClient(tracking_uri=mlflow_tracking_uri(self.tracking_dir))

    def assignment_csv(self, run_id: str | None = None) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.client().download_artifacts(run_id or self.lr_run_id, "split_assignment.csv", tmp)
            return Path(path).read_text()

    def assignment(self, run_id: str | None = None) -> pd.Series:
        frame = pd.read_csv(tempfile_from_text(self.assignment_csv(run_id)), dtype=str, keep_default_na=False)
        return pd.Series(frame["partition"].to_numpy(dtype=object), index=pd.Index(frame["encounter_id"], dtype=object))

    def expected_batch(self, run_date: str, window_days: int, run_id: str | None = None) -> pd.DataFrame:
        """The independent boolean-mask batch: in-window AND in the run's test partition, straight from the fixture frame."""
        day = pd.Timestamp(f"{run_date[:4]}-{run_date[4:6]}-{run_date[6:]}", tz="UTC")
        end, start = day + pd.Timedelta(days=1), day - pd.Timedelta(days=window_days - 1)
        in_window = (self.df["index_stop"] >= start) & (self.df["index_stop"] < end)
        is_test = self.df["encounter_id"].map(self.assignment(run_id)) == "test"
        return self.df.loc[in_window & is_test].reset_index(drop=True)

    def manifest(self, bundle_uri: str | None = None) -> dict:
        return json.loads((Path(bundle_uri or self.lr_bundle_uri) / "manifest.json").read_text())


def tempfile_from_text(text: str):
    import io

    return io.StringIO(text)


def build_scoring_env(root: Path) -> ScoringEnv:
    """Trains both models once on make_gold_frame(300, seed=1) (652 rows; split 165 train / 310 test / 177 in neither) and
    exports both runs as bundles."""
    original_uri = mlflow.get_tracking_uri()
    try:
        df = make_gold_frame(300, seed=1)
        gold_dir = write_gold_dir(root / "gold", df)
        tracking_dir = root / "tracking"
        result = train_and_evaluate(
            TrainingConfig(
                gold_dir=gold_dir,
                reference_date=REFERENCE_DATE_STR,
                test_start_date=TEST_START_STR,
                tracking_dir=tracking_dir,
                experiment_name=SCORING_EXPERIMENT,
                n_bootstrap=0,
            )
        )
        lr, xgb = result.models["logistic_regression"], result.models["xgboost_calibrated"]
        bundles_root = root / "bundles"
        lr_bundle = export_bundle(tracking_dir, lr.run_id, bundles_root)
        xgb_bundle = export_bundle(tracking_dir, xgb.run_id, bundles_root)
    finally:
        mlflow.set_tracking_uri(original_uri)
    return ScoringEnv(
        gold_dir=gold_dir,
        tracking_dir=tracking_dir,
        experiment_name=SCORING_EXPERIMENT,
        lr_run_id=lr.run_id,
        xgb_run_id=xgb.run_id,
        bundles_root=bundles_root,
        lr_bundle_uri=lr_bundle.bundle_uri,
        xgb_bundle_uri=xgb_bundle.bundle_uri,
        df=df,
        n_train=int(result.split_summary["n_train"]),
        n_test=int(result.split_summary["n_test"]),
    )


# ---------------------------------------------------------------------------------------------------------------
# tamper_bundle: a copy of a bundle with chosen defects, written as ordinary files (never through write_bundle, which
# would refuse most of them). Steps: (a) non-"files" set/delete edits; (b) file byte edits; (c) if rehash, recompute the
# files table's sha256/size; (d) "files" set/delete edits; (e) if rehash, recompute bundle_id.
# ---------------------------------------------------------------------------------------------------------------
_MISSING = object()


def _path(key) -> tuple[str, ...]:
    return tuple(key) if isinstance(key, tuple) else tuple(key.split("."))


def _set(document: dict, path: tuple[str, ...], value) -> None:
    node = document
    for part in path[:-1]:
        node = node[part]  # the parent must exist (only top-level keys may be created)
    if len(path) > 1 and path[-1] not in node:
        raise KeyError(f"tamper_bundle: {'.'.join(path)} does not exist")
    node[path[-1]] = value


def _delete(document: dict, path: tuple[str, ...]) -> None:
    node = document
    for part in path[:-1]:
        node = node[part]
    del node[path[-1]]


def tamper_bundle(
    src_uri: str,
    dst_root: Path,
    *,
    set: Mapping | None = None,  # shadows the builtin inside this function only: the design's keyword name
    delete: tuple = (),
    files: Mapping[str, bytes | None] | None = None,
    rehash: bool = True,
    manifest_raw: bytes | None = None,
    dir_name: str | None = None,
    extra_entries: Mapping[str, bytes | None] | None = None,
    drop_manifest: bool = False,
    symlink_files: Mapping[str, Path] | None = None,
) -> str:
    src = Path(src_uri)
    manifest = json.loads((src / "manifest.json").read_text())
    contents = {p.name: p.read_bytes() for p in src.iterdir() if p.name != "manifest.json"}
    edits = [(_path(k), v) for k, v in (set or {}).items()]
    deletes = [_path(k) for k in delete]

    def is_files(path: tuple[str, ...]) -> bool:
        return path[0] == "files"

    # (a)
    for path, value in edits:
        if not is_files(path):
            _set(manifest, path, value)
    for path in deletes:
        if not is_files(path):
            _delete(manifest, path)
    # (b)
    for name, data in (files or {}).items():
        if data is None:
            contents.pop(name, None)
        else:
            contents[name] = data
    # (c)
    if rehash and isinstance(manifest.get("files"), dict):
        for name, data in contents.items():
            if name in manifest["files"]:
                manifest["files"][name]["sha256"] = sha256_hex(data)
                manifest["files"][name]["size"] = len(data)
    # (d)
    for path, value in edits:
        if is_files(path):
            _set(manifest, path, value)
    for path in deletes:
        if is_files(path):
            _delete(manifest, path)
    # (e)
    if rehash:
        manifest["bundle_id"] = compute_bundle_id(manifest)
    name = dir_name or (manifest.get("bundle_id") if isinstance(manifest.get("bundle_id"), str) else None) or src.name
    target = Path(dst_root) / name
    target.mkdir(parents=True)
    if not drop_manifest:
        if manifest_raw is not None:
            raw = manifest_raw
        elif rehash:
            raw = manifest_bytes(manifest)
        else:
            raw = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
        (target / "manifest.json").write_bytes(raw)
    for file_name, data in contents.items():
        (target / file_name).write_bytes(data)
    for entry, data in (extra_entries or {}).items():
        if entry.endswith("/"):
            (target / entry.rstrip("/")).mkdir()
        else:
            (target / entry).write_bytes(data)
    for file_name, link_target in (symlink_files or {}).items():
        (target / file_name).unlink()
        (target / file_name).symlink_to(link_target)
    return str(target)


def write_champion(path: Path, bundle_uri: str, sha256: str | None = None) -> Path:
    """A champion pointer at `path` for `bundle_uri`, pinning `sha256` or the bundle's real manifest sha."""
    if sha256 is None:
        sha256 = sha256_hex((Path(bundle_uri) / "manifest.json").read_bytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(champion_json(bundle_uri, sha256))
    return path


def forbid_model_loading(monkeypatch) -> None:
    """Makes every route that would deserialize a model raise AssertionError: cloudpickle.load/loads and pickle.load/loads
    (cloudpickle.loads IS pickle.loads). Broader than before on purpose; the gold-load and selection paths do not unpickle,
    so a trip for a non-model reason is a finding, not noise. Apply it only around a call that must never unpickle."""
    import pickle

    import cloudpickle

    def refuse(*args, **kwargs):
        raise AssertionError("a model was deserialized before every guard had passed")

    for owner, name in ((cloudpickle, "load"), (cloudpickle, "loads"), (pickle, "load"), (pickle, "loads")):
        monkeypatch.setattr(owner, name, refuse)


# ---------------------------------------------------------------------------------------------------------------
# BUNDLE_CASES (the 48 MLflow-run guard cases ported; see the design's case table) and INTEGRITY_CASES (I1-I5).
# build(env, tmp_path) -> (ScoringConfig overrides, extra substrings the message must contain).
# ---------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class BundleCase:
    case_id: str
    key: str
    build: Callable[[ScoringEnv, Path], tuple[dict, tuple[str, ...]]]
    forbidden: tuple[str, ...] = ()
    starts_with: bool = False  # integrity keys must START the message (they carry their colon)


def _tampered(extras: tuple[str, ...] = (), **kwargs):
    return lambda env, tmp: ({"bundle_uri": tamper_bundle(env.lr_bundle_uri, tmp / "bundles", **kwargs)}, extras)


def split_violation_sentence(env: ScoringEnv, assignment: pd.DataFrame) -> tuple[tuple[int, int, int], str]:
    """The three sub-rule counts of guard 12 for `assignment`, recomputed with plain pandas independently of the
    implementation, and the message sentence they must appear in."""
    t = pd.Timestamp(TEST_START_STR[:4] + "-" + TEST_START_STR[4:6] + "-" + TEST_START_STR[6:], tz="UTC")
    merged = assignment.merge(env.df[["encounter_id", "patient_id", "index_start"]], on="encounter_id")
    test, train = merged[merged["partition"] == "test"], merged[merged["partition"] == "train"]
    triple = (
        int((test["index_start"] < t).sum()),
        int((train["index_start"] >= t).sum()),
        len(set(test["patient_id"]) & set(train["patient_id"])),
    )
    sentence = (
        f"{triple[0]} test row(s) start before test_start_date, {triple[1]} train row(s) start on/after it, "
        f"{triple[2]} patient(s) appear in both partitions"
    )
    return triple, sentence


def _relabel_case(kind: str):
    def build(env: ScoringEnv, tmp: Path):
        frame = pd.read_csv(tempfile_from_text(env.assignment_csv()), dtype=str, keep_default_na=False)
        by_id = env.df.set_index("encounter_id")
        frame["patient_id"] = frame["encounter_id"].map(by_id["patient_id"])
        if kind == "train_to_test":  # the FIRST train row (by encounter_id) whose patient has >= 2 train rows
            train = frame[frame["partition"] == "train"].sort_values("encounter_id")
            counts = train.groupby("patient_id")["encounter_id"].transform("size")
            target = train[counts >= 2].iloc[0]["encounter_id"]
            frame.loc[frame["encounter_id"] == target, "partition"] = "test"
        elif kind == "test_to_train":  # the first test row whose patient has >= 2 test rows
            test = frame[frame["partition"] == "test"].sort_values("encounter_id")
            counts = test.groupby("patient_id")["encounter_id"].transform("size")
            target = test[counts >= 2].iloc[0]["encounter_id"]
            frame.loc[frame["encounter_id"] == target, "partition"] = "train"
        else:  # a gold row in neither partition, of a patient who has a test row, starting before T, added as train
            test_patients = set(frame.loc[frame["partition"] == "test", "patient_id"])
            t = pd.Timestamp("2023-01-01", tz="UTC")
            dropped = env.df[~env.df["encounter_id"].isin(frame["encounter_id"])]
            dropped = dropped[dropped["patient_id"].isin(test_patients) & (dropped["index_start"] < t)].sort_values("encounter_id")
            assert len(dropped), "fixture precondition: a dropped row of a test patient starting before T must exist"
            frame = pd.concat([frame, pd.DataFrame({"encounter_id": [dropped.iloc[0]["encounter_id"]], "partition": ["train"], "patient_id": [dropped.iloc[0]["patient_id"]]})])
        modified = frame[["encounter_id", "partition"]]
        triple, sentence = split_violation_sentence(env, modified)
        expected_pattern = {"train_to_test": triple[0] == 1 and triple[1] == 0, "test_to_train": triple[1] == 1 and triple[0] == 0, "dropped_to_train": triple[:2] == (0, 0) and triple[2] >= 1}[kind]
        assert expected_pattern, f"fixture precondition: unexpected violation triple {triple} for {kind}"
        csv = modified.to_csv(index=False).encode()
        return {"bundle_uri": tamper_bundle(env.lr_bundle_uri, tmp / "bundles", files={"split_assignment.csv": csv})}, (sentence,)

    return build


def _split_case(transform: Callable[[str], str]):
    def build(env: ScoringEnv, tmp: Path):
        text = (Path(env.lr_bundle_uri) / "split_assignment.csv").read_text()
        uri = tamper_bundle(env.lr_bundle_uri, tmp / "bundles", files={"split_assignment.csv": transform(text).encode()})
        return {"bundle_uri": uri}, ()

    return build


def _absent_bundle(env: ScoringEnv, tmp: Path):
    return {"bundle_uri": str(tmp / "bundles" / ("0" * 64))}, ()


def _signature_case(edit: Callable[[list[dict]], list[dict]]):
    def build(env: ScoringEnv, tmp: Path):
        signature = edit([dict(s) for s in env.manifest()["signature"]])
        return {"bundle_uri": tamper_bundle(env.lr_bundle_uri, tmp / "bundles", set={"signature": signature})}, ()

    return build


def _first_entry(**changes):
    return lambda signature: [{**signature[0], **changes}, *signature[1:]]


def _logged_by_other_run(env: ScoringEnv, tmp: Path):
    uri = tamper_bundle(env.lr_bundle_uri, tmp / "bundles", set={"mlflow.model_logged_by_run_id": env.xgb_run_id})
    return {"bundle_uri": uri}, ()


# Guard 14 versions, derived from the running interpreter so the cases hold under whichever interpreter runs the suite (CI: 3.14).
RUNNING_PYTHON = current_python_version()
OTHER_MINOR_PYTHON = f"{sys.version_info.major}.{sys.version_info.minor + 1}.0"
OTHER_PATCH_PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro + 1}"

_REV = list(reversed(FEATURE_COLUMNS))
BUNDLE_CASES: list[BundleCase] = [
    BundleCase("1-bogus-bundle", "bundle not found", _absent_bundle),
    BundleCase("2-status-failed", "not FINISHED", _tampered(set={"mlflow.run_status": "FAILED"})),
    BundleCase("2-status-not-a-string", "not FINISHED", _tampered(set={"mlflow.run_status": 1})),
    BundleCase("2-soft-deleted", "deleted", _tampered(set={"mlflow.run_lifecycle_stage": "deleted"})),
    BundleCase("4-unknown-model-name", "model_name", _tampered(set={"model_name": "random_forest"})),
    BundleCase("4-no-model-name", "model_name", _tampered(set={"model_name": None})),
    BundleCase("5-other-label-definition", "gold_label_definition", _tampered(set={"gold_label_definition": "all_cause_v0"})),
    BundleCase("5-no-label-definition", "gold_label_definition", _tampered(set={"gold_label_definition": None})),
    BundleCase("6-wrong-fingerprint", "gold_fingerprint", _tampered(set={"gold_fingerprint": "0" * 64})),
    BundleCase("6-no-fingerprint", "gold_fingerprint", _tampered(set={"gold_fingerprint": None})),
    BundleCase("6-unknown-fingerprint-definition", "gold_fingerprint", _tampered(set={"gold_fingerprint_definition": "spark-v9"})),
    BundleCase("6-fingerprint-definition-not-a-string", "gold_fingerprint", _tampered(set={"gold_fingerprint_definition": ["x"]})),
    BundleCase("7-reference-date-mismatch", "reference_date", _tampered(set={"params.reference_date": "20260917"}), ("readmission_window_days",)),
    BundleCase("7-window-mismatch", "readmission_window_days", _tampered(set={"params.readmission_window_days": "14"}), ("reference_date",)),
    BundleCase("7-no-reference-date", "reference_date", _tampered(set={"params.reference_date": None}), ("readmission_window_days",)),
    BundleCase("7-no-window", "readmission_window_days", _tampered(set={"params.readmission_window_days": None}), ("reference_date",)),
    BundleCase("8-old-sklearn", "library version", _tampered(set={"library_versions.sklearn_version": "0.0.0"})),
    BundleCase("8-no-numpy-tag", "library version", _tampered(set={"library_versions.numpy_version": None})),
    BundleCase("9-features-reversed", "feature_columns.json", _tampered(set={"feature_columns": _REV})),
    BundleCase("9-features-extra", "feature_columns.json", _tampered(set={"feature_columns": [*FEATURE_COLUMNS, "extra"]})),
    BundleCase("9-no-features-key", "feature_columns.json", _tampered(set={"feature_columns": None})),
    BundleCase("9-features-not-a-list", "feature_columns.json", _tampered(set={"feature_columns": "abc"})),
    BundleCase("10-signature-reversed", "signature", _signature_case(lambda s: list(reversed(s)))),
    BundleCase("10-no-signature", "signature", _tampered(set={"signature": None})),
    BundleCase("10-signature-dtype-mismatch", "signature", _signature_case(_first_entry(type="string"))),
    BundleCase("10-signature-optional-column", "signature", _signature_case(_first_entry(required=False))),
    BundleCase("11-wrong-header", "split_assignment.csv", _split_case(lambda t: t.replace("encounter_id,partition", "id,partition", 1))),
    BundleCase("11-duplicate-id", "split_assignment.csv", _split_case(lambda t: t + t.splitlines()[1] + "\n")),
    BundleCase("11-unknown-partition", "split_assignment.csv", _split_case(lambda t: t.replace(",test", ",validation", 1))),
    BundleCase("11-no-test-row", "split_assignment.csv", _split_case(lambda t: t.replace(",test", ",train"))),
    BundleCase("11-id-not-in-gold", "split_assignment.csv", _split_case(lambda t: t + "not-in-gold,test\n")),
    BundleCase("11-empty-file", "split_assignment.csv", _split_case(lambda t: "")),
    BundleCase("12-train-relabelled-test", "split consistency", _relabel_case("train_to_test")),
    BundleCase("12-test-relabelled-train", "split consistency", _relabel_case("test_to_train")),
    BundleCase("12-dropped-added-as-train", "split consistency", _relabel_case("dropped_to_train")),
    BundleCase("12-no-test-start-date", "split consistency", _tampered(set={"params.test_start_date": None})),
    BundleCase("12-bad-test-start-date", "split consistency", _tampered(set={"params.test_start_date": "not-a-date"})),
    BundleCase("13-model-of-another-run", "model provenance", _logged_by_other_run, ("signature",)),
    BundleCase("13-logged-by-null", "model provenance", _tampered(set={"mlflow.model_logged_by_run_id": None})),
    BundleCase(
        "14-other-minor", "python version",
        _tampered((f"trained under Python {OTHER_MINOR_PYTHON} (from the run tag)", f"running Python {RUNNING_PYTHON}"),
                  set={"python_version": OTHER_MINOR_PYTHON, "mlmodel_python_version": OTHER_MINOR_PYTHON}),
        ("library version", "inconsistent provenance"),
    ),
    BundleCase(
        "14-legacy-other-minor", "python version",
        _tampered((f"trained under Python {OTHER_MINOR_PYTHON} (from the logged model's MLmodel)",),
                  set={"python_version": None, "mlmodel_python_version": OTHER_MINOR_PYTHON}),
        ("library version", "inconsistent provenance"),
    ),
    BundleCase(
        "14-tag-vs-mlmodel", "python version",
        _tampered(("inconsistent provenance",), set={"python_version": OTHER_MINOR_PYTHON}),
        ("library version", "mismatch"),
    ),
    BundleCase(
        "14-malformed-tag", "python version",
        _tampered(("tag 'python_version' is '3.x'",), set={"python_version": "3.x"}),
        ("library version", "MLmodel python_version"),
    ),
    BundleCase(
        "14-malformed-mlmodel", "python version",
        _tampered(("MLmodel python_version 'garbage'",), set={"python_version": None, "mlmodel_python_version": "garbage"}),
        ("library version", "tag 'python_version'"),
    ),
    BundleCase(
        "14-no-version-anywhere", "python version",
        _tampered(("is unknown",), set={"python_version": None, "mlmodel_python_version": None}),
        ("library version", "not a version of the form"),
    ),
    BundleCase(
        "14-empty-tag", "python version",
        _tampered(("tag 'python_version' is ''",), set={"python_version": ""}),
        ("library version", "MLmodel python_version"),
    ),
    BundleCase(  # a float: how an unquoted `3.10` parses in YAML (the exporter copies it as a JSON number)
        "14-mlmodel-yaml-float", "python version",
        _tampered(("MLmodel python_version 3.1,",), set={"python_version": None, "mlmodel_python_version": 3.1}),
        ("library version", "tag 'python_version'"),
    ),
    BundleCase(  # mirror of 14-tag-vs-mlmodel: a correct tag in front of a foreign MLmodel; only step (v) refuses it
        "14-mlmodel-vs-tag", "python version",
        _tampered(("inconsistent provenance", f"records '{OTHER_MINOR_PYTHON}'"), set={"mlmodel_python_version": OTHER_MINOR_PYTHON}),
        ("library version", "mismatch"),
    ),
]


def _integrity(extras: tuple[str, ...] = (), **kwargs):
    return _tampered(extras, **kwargs)


def _not_a_directory(env: ScoringEnv, tmp: Path):
    file = tmp / "not-a-bundle"
    file.write_text("x")
    return {"bundle_uri": str(file)}, ()


def _symlinked_model(env: ScoringEnv, tmp: Path):
    outside = tmp / "outside-model.pkl"
    shutil.copyfile(Path(env.lr_bundle_uri) / "model.pkl", outside)
    return {"bundle_uri": tamper_bundle(env.lr_bundle_uri, tmp / "bundles", symlink_files={"model.pkl": outside})}, ()


def _flip_first_byte(data: bytes) -> bytes:
    return bytes([data[0] ^ 1]) + data[1:]


def _flipped_model(env: ScoringEnv, tmp: Path):
    data = (Path(env.lr_bundle_uri) / "model.pkl").read_bytes()
    uri = tamper_bundle(env.lr_bundle_uri, tmp / "bundles", files={"model.pkl": _flip_first_byte(data)}, rehash=False)
    return {"bundle_uri": uri}, ()


def _truncated_split(env: ScoringEnv, tmp: Path):
    data = (Path(env.lr_bundle_uri) / "split_assignment.csv").read_bytes()
    uri = tamper_bundle(env.lr_bundle_uri, tmp / "bundles", files={"split_assignment.csv": data[:-10]}, rehash=False)
    return {"bundle_uri": uri}, ()


def _non_canonical(env: ScoringEnv, tmp: Path):
    raw = json.dumps(env.manifest(), indent=2).encode()
    return {"bundle_uri": tamper_bundle(env.lr_bundle_uri, tmp / "bundles", manifest_raw=raw)}, ()


def _under_champion(sha256: str | None = None, **tamper_kwargs):
    """A champion pinning the ORIGINAL manifest's sha (or `sha256`) in front of a bundle tampered with `tamper_kwargs`."""

    def build(env: ScoringEnv, tmp: Path):
        original = sha256_hex((Path(env.lr_bundle_uri) / "manifest.json").read_bytes())
        uri = tamper_bundle(env.lr_bundle_uri, tmp / "bundles", **tamper_kwargs) if tamper_kwargs else env.lr_bundle_uri
        return {"champion_path": write_champion(tmp / "deploy" / "champion.json", uri, sha256 or original)}, ()

    return build


def _ic(case_id: str, key: str, build, forbidden: tuple[str, ...] = ()) -> BundleCase:
    return BundleCase(case_id, key, build, forbidden, starts_with=True)


INTEGRITY_CASES: list[BundleCase] = [
    _ic("I1-not-a-directory", "bundle not found: ", _not_a_directory),
    _ic("I1-gs-uri", "bundle not found: ", lambda env, tmp: ({"bundle_uri": "gs://b/x"}, ())),
    _ic("I1-other-scheme", "bundle not found: ", lambda env, tmp: ({"bundle_uri": "s3://b/x"}, ())),
    _ic("I1-no-manifest", "manifest: ", _integrity(drop_manifest=True)),
    _ic("I1-not-json", "manifest: ", _integrity(manifest_raw=b"not json")),
    _ic("I1-not-object", "manifest: ", _integrity(manifest_raw=b"[]\n")),
    _ic("I1-schema-version-2", "manifest: ", _integrity(set={"schema_version": 2})),
    _ic("I1-schema-version-true", "manifest: ", _integrity(set={"schema_version": True})),
    _ic("I3-edited-without-rehash", "bundle_id: ", _integrity(set={"model_name": "xgboost_calibrated"}, rehash=False)),
    _ic("I3-renamed-dir", "bundle_id: ", _integrity(dir_name="f" * 64)),
    _ic("I3-non-canonical-bytes", "bundle_id: ", _non_canonical),
    _ic("I3-no-bundle-id", "bundle_id: ", _integrity(delete=("bundle_id",), rehash=False)),
    _ic("I4-missing-model", "files: ", _integrity(files={"model.pkl": None})),
    _ic("I4-missing-split", "files: ", _integrity(files={"split_assignment.csv": None})),
    _ic("I4-model-byte-flipped", "files: ", _flipped_model),
    _ic("I4-split-truncated", "files: ", _truncated_split),
    _ic("I4-extra-file", "files: ", _integrity(extra_entries={"notes.txt": b"x"})),
    _ic("I4-subdirectory", "files: ", _integrity(extra_entries={"sub/": None})),
    _ic("I4-symlinked-model", "files: ", _symlinked_model),
    _ic("I4-wrong-role", "files: ", _integrity(set={("files", "model.pkl", "role"): "x"})),
    _ic("I4-size-is-bool", "files: ", _integrity(set={("files", "model.pkl", "size"): True})),
    _ic("I4-no-files-key", "files: ", _integrity(delete=("files",))),
    _ic("I5-missing-key", "manifest shape: ", _integrity(delete=("git",))),
    _ic("I5-unknown-key", "manifest shape: ", _integrity(set={"extra": 1})),
    _ic("I5-params-not-object", "manifest shape: ", _integrity(set={"params": "x"})),
    _ic("I5-run-id-null", "manifest shape: ", _integrity(set={"mlflow.run_id": None})),
    _ic("I5-run-id-not-hex", "manifest shape: ", _integrity(set={"mlflow.run_id": "run-1"})),
    _ic("I5-git-commit-int", "manifest shape: ", _integrity(set={"git.commit": 7})),
    _ic("I5-portable-not-null", "manifest shape: ", _integrity(set={"portable": {"x": 1}})),
    _ic("I2-champion-sha-mismatch", "champion sha256: ", _under_champion(sha256="0" * 64)),
    _ic(
        "I2-edited-without-rehash-under-champion", "champion sha256: ",
        _under_champion(set={"model_name": "xgboost_calibrated"}, rehash=False), ("bundle_id",),
    ),
    _ic("I2-invalid-json-under-champion", "champion sha256: ", _under_champion(manifest_raw=b"not json"), ("manifest:",)),
]


# ---------------------------------------------------------------------------------------------------------------
# A hand-built ScoringResult (report and CLI tests)
# ---------------------------------------------------------------------------------------------------------------
from readmission_risk.scoring.bundle_guard import ModelBundle
from readmission_risk.scoring.pipeline import ScoringResult

_IDS = [f"enc-000{i}" for i in range(1, 6)]
_SCORES = [0.4, 0.2, 0.5, 0.1, 0.3]  # rank order: enc-0003, enc-0001, enc-0005, enc-0002, enc-0004
_DESCRIPTIONS = [None, "other", "d" * 48 + "TAILTAIL", "other", "e" * 48]  # rank 2 null; rank 1 is 56 chars; rank 3 is 48


def make_result(*, git_commit="0123456789abcdef0123456789abcdef01234567", git_dirty="clean", observed=None) -> ScoringResult:
    batch = make_batch(_IDS, descriptions=_DESCRIPTIONS)
    predictions = build_predictions(batch, _SCORES, **{**PREDICTION_KWARGS, "top_n": 3, "window_days": 3, "run_date": date(2026, 3, 10)})
    bundle = ModelBundle(
        bundle_id="b" * 64,
        bundle_uri="bundles/" + "b" * 64,
        mlflow_run_id="run-abc",
        model_name="logistic_regression",
        test_start_date="20230101",
        gold_fingerprint="f" * 64,
        git_commit=git_commit,
        git_dirty=git_dirty,
        partition_of=pd.Series(dtype=object),
        model_bytes=b"",
    )
    return ScoringResult(
        predictions=predictions,
        partition_path=Path("out/run_date=20260310/predictions.parquet"),
        bundle=bundle,
        window_start=pd.Timestamp("2026-03-08T00:00Z"),
        window_end=pd.Timestamp("2026-03-11T00:00Z"),
        window_days=3,
        observed=observed,
    )
