"""The promotion gate's verification, prediction and decision run (v2 PRD FR15, FR16): everything before and around the pure
decision functions in models/gate.py. It verifies the analysis lock, the gold, the challenger and frozen-v1 runs, the Python and
library versions, and both bundles; scores the verified bundle bytes; and returns the report body. Lives in ops/ (not models/,
as the design note first said) because it needs ops.prereg and ops.generation_vm, and models/ never imports ops/.
Design: notes/eg-new-feature/v2-slice6b-promotion-gate-frozen-v1-2026-10-16.md (Interfaces > S3).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import warnings
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import cloudpickle
import numpy as np
import pandas as pd
from sklearn.exceptions import InconsistentVersionWarning

from readmission_risk.bundles.champion import read_champion
from readmission_risk.bundles.manifest import compute_bundle_id
from readmission_risk.bundles.store import bundle_uri_leaf
from readmission_risk.models.baselines import reason_code_lookup
from readmission_risk.models.constants import MODEL_NAMES, ROWHASH_TAG
from readmission_risk.models.data import (
    GROUP_COLUMN,
    LABEL_COLUMN,
    load_gold_table_with_metadata,
    prepare_features,
)
from readmission_risk.models.evaluate import evaluate_probabilities
from readmission_risk.models.gate import (
    FROZEN_REPORT_METRICS,
    SUPPORTED_BOOTSTRAP_KIND,
    evaluate_frozen_v1,
    evaluate_gate,
)
from readmission_risk.models.split import SPLIT_SUMMARY_KEYS, chronological_group_split
from readmission_risk.models.tracking import (
    load_logged_model,
    mlflow_tracking_uri,
    resolve_run_id,
)
from readmission_risk.models.versions import (
    LIBRARY_VERSION_DISTRIBUTIONS,
    current_python_version,
    installed_versions,
    parse_python_major_minor,
)
from readmission_risk.ops.generation_vm import MAIN_RUN, gold_prefix
from readmission_risk.ops.prereg import (
    SPEC,
    Prereg,
    PreregError,
    check_prereg_against_code,
    parse_prereg,
)
from readmission_risk.pipeline.gold_metadata import (
    FINGERPRINT_DEFINITION_ROWHASH_V1,
    parse_yyyymmdd,
)
from readmission_risk.warehouse.gold_source import materialized_gold_dir

PILOT_RUN = "pilot_20k"
CODE_PATHS: tuple[str, ...] = ("src", "scripts", "Makefile", "requirements.txt", "requirements-linux-extras.txt")
LOCK_MARKER = "analysis_lock:"
CHALLENGER_MODEL = "logistic_regression"  # owner decision D1
CHAMPION_MODEL = "logistic_regression"  # the champion bundle is the frozen-v1 LR run
REPORTED_MODEL = "xgboost_calibrated"
NEGATIVE_CONTROL = "constant_train_prevalence"
FROZEN_V1_ROWS = "modeling_rows"
REPRODUCTION_TOLERANCE = 1e-9
_HEX40_RE = re.compile(r"[0-9a-f]{40}")


class GateRunError(RuntimeError):
    """A verification step refused; the message names the step."""


@dataclass(frozen=True)
class GateRunConfig:
    run: str
    challenger_experiment: str
    challenger_bundle: Path
    repo_root: Path
    champion_path: Path = Path("deploy/champion.json")
    bundles_root: Path = Path("bundles")
    frozen_experiment: str = "readmission-risk-v1"
    tracking_dir: Path = Path("mlflow")
    v1_gold_dir: Path = Path("data/gold/full_run_2b")
    rehearsal: bool = False
    negative_control: bool = False

    def path(self, value: Path) -> Path:
        return value if value.is_absolute() else self.repo_root / value


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    tags: dict
    params: dict
    metrics: dict
    start_time: int  # ms since the epoch
    lifecycle_stage: str
    experiment_id: str


def _fail(step: str, message: str) -> GateRunError:
    return GateRunError(f"{step}: {message}")


def _client(tracking_dir: Path):
    from mlflow.tracking import MlflowClient

    if not (tracking_dir / "mlflow.db").is_file():
        raise _fail("mlflow", f"no MLflow store at {tracking_dir / 'mlflow.db'}")
    return MlflowClient(tracking_uri=mlflow_tracking_uri(tracking_dir))


def _read_run(tracking_dir: Path, run_id: str) -> RunRecord:
    """The single seam through which run data is read (tests replace it for immutable-param cases)."""
    run = _client(tracking_dir).get_run(run_id)
    return RunRecord(
        run_id=run.info.run_id,
        tags=dict(run.data.tags),
        params=dict(run.data.params),
        metrics=dict(run.data.metrics),
        start_time=int(run.info.start_time),
        lifecycle_stage=run.info.lifecycle_stage,
        experiment_id=run.info.experiment_id,
    )


def runs_on_gold(tracking_dir: Path, fingerprint: str) -> list[dict]:
    """Every run in the store, deleted runs and deleted experiments included, whose rowhash tag equals `fingerprint`; each as
    {experiment, experiment_stage, run_id, run_stage, start_time}. Paginated over experiments and runs."""
    from mlflow.entities import ViewType

    client = _client(tracking_dir)
    experiments, token = [], None
    while True:
        page = client.search_experiments(view_type=ViewType.ALL, max_results=1000, page_token=token)
        experiments.extend(page)
        token = page.token
        if not token:
            break
    found = []
    for experiment in experiments:
        token = None
        while True:
            page = client.search_runs(
                experiment_ids=[experiment.experiment_id],
                filter_string=f"tags.`{ROWHASH_TAG}` = '{fingerprint}'",
                run_view_type=ViewType.ALL,
                max_results=1000,
                page_token=token,
            )
            found.extend(
                {
                    "experiment": experiment.name,
                    "experiment_stage": experiment.lifecycle_stage,
                    "run_id": run.info.run_id,
                    "run_stage": run.info.lifecycle_stage,
                    "start_time": int(run.info.start_time),
                }
                for run in page
            )
            token = page.token
            if not token:
                break
    return found


def _git(run_git: Callable, repo_root: Path, *args: str):
    result = run_git(["git", "-C", str(repo_root), *args], capture_output=True, check=False)
    return result


def _text(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value or "")


def _bytes(value) -> bytes:
    return value if isinstance(value, bytes) else str(value).encode("utf-8")


def verify_bundle_dir(directory: Path) -> tuple[dict, bytes, bytes]:
    """The scorer's I1-I4 for a local bundle directory: the manifest's bundle_id is its content hash and the directory name;
    the directory holds exactly manifest.json plus the manifest's files; each file's sha256 and size match. Returns
    (manifest, verified model.pkl bytes, verified split_assignment.csv bytes or b"")."""
    step = f"bundle {directory}"
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise _fail(step, "no manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if compute_bundle_id(manifest) != manifest.get("bundle_id") or manifest.get("bundle_id") != directory.name:
        raise _fail(step, "bundle_id is not the manifest's content hash or not the directory name")
    files = manifest.get("files") or {}
    present = {p.name for p in directory.iterdir() if p.is_file()}
    if present != {"manifest.json", *files}:
        raise _fail(step, f"holds {sorted(present)}; the manifest lists {sorted(files)}")
    contents = {}
    for name, entry in files.items():
        data = (directory / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry.get("sha256") or len(data) != entry.get("size"):
            raise _fail(step, f"{name} does not match its manifest entry (sha256/size)")
        contents[name] = data
    if "model.pkl" not in contents:
        raise _fail(step, "no model.pkl")
    return manifest, contents["model.pkl"], contents.get("split_assignment.csv", b"")


def _load_pickle(data: bytes, who: str):
    with warnings.catch_warnings():
        warnings.simplefilter("error", InconsistentVersionWarning)
        try:
            return cloudpickle.loads(data)  # trusted: sha256-verified against the manifest
        except InconsistentVersionWarning as exc:
            raise _fail("load", f"{who}: {exc}") from exc


def _load_logged(tracking_dir: Path, run_id: str, who: str):
    with warnings.catch_warnings():
        warnings.simplefilter("error", InconsistentVersionWarning)
        try:
            return load_logged_model(tracking_dir, run_id)
        except InconsistentVersionWarning as exc:
            raise _fail("load", f"{who}: {exc}") from exc


def _predict(model, frame: pd.DataFrame, who: str) -> np.ndarray:
    proba = np.asarray(model.predict_proba(prepare_features(frame)))
    if proba.shape != (len(frame), 2):
        raise _fail("predict", f"{who}: predict_proba returned shape {proba.shape}")
    scores = proba[:, 1].astype("float64")
    if not np.isfinite(scores).all() or (scores < 0).any() or (scores > 1).any():
        raise _fail("predict", f"{who}: a score is non-finite or outside [0, 1]")
    return scores


def _prereg_split(frame: pd.DataFrame, prereg: Prereg):
    train_start = prereg.split["train_start_date"]
    return chronological_group_split(
        frame,
        reference_date=parse_yyyymmdd(prereg.generation["reference_date"], "reference_date"),
        test_start_date=parse_yyyymmdd(prereg.split["test_start_date_resolved"], "test_start_date_resolved"),
        readmission_window_days=prereg.label["readmission_window_days"],
        train_start_date=None if train_start is None else parse_yyyymmdd(train_start, "train_start_date"),
    )


def _check_reproduces(record: RunRecord, y: np.ndarray, p: np.ndarray, who: str, step: str) -> None:
    metrics = evaluate_probabilities(y, p, train_prevalence=float(y.mean())).metrics
    for key in ("roc_auc", "brier_score"):
        logged = record.metrics.get(f"test_{key}")
        if logged is None or abs(metrics[key] - logged) > REPRODUCTION_TOLERANCE:
            raise _fail(step, f"{who}: test {key} {metrics[key]!r} does not reproduce the logged {logged!r}")


def _check_versions(record: RunRecord, who: str) -> None:
    tag = record.tags.get("python_version")
    running = parse_python_major_minor(current_python_version())
    if parse_python_major_minor(tag) != running:
        raise _fail("python", f"{who}: trained under Python {tag!r}, running {current_python_version()} (guard 14's rule)")
    installed = installed_versions()
    for key in LIBRARY_VERSION_DISTRIBUTIONS:
        if record.tags.get(key) != installed[key]:
            raise _fail("libraries", f"{who}: {key} is {record.tags.get(key)!r} on the run, {installed[key]!r} installed")


def _sanitize(value):
    """Tuples -> lists, numpy scalars -> Python, NaN/inf -> None, so the report is strict JSON."""
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return f if np.isfinite(f) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def run_gate(
    config: GateRunConfig,
    *,
    prereg: Prereg,
    run_git: Callable,
    gold_opener: Callable[[str], AbstractContextManager[Path]] = materialized_gold_dir,
) -> dict:
    root = config.repo_root
    tracking_dir = config.path(config.tracking_dir)
    lock = None if config.rehearsal else prereg.analysis_lock

    # 1. mode, prereg, lock
    if config.rehearsal and config.run != PILOT_RUN:
        raise _fail("mode", f"--rehearsal is only for {PILOT_RUN}")
    if not config.rehearsal and config.run != MAIN_RUN:
        raise _fail("mode", f"a non-rehearsal gate runs only on {MAIN_RUN}")
    if prereg.bootstrap["kind"] != SUPPORTED_BOOTSTRAP_KIND:
        raise _fail("prereg", f"bootstrap.kind {prereg.bootstrap['kind']!r} is not {SUPPORTED_BOOTSTRAP_KIND!r}")
    if prereg.promotion["comparators"] != ["champion", "reason_code_lookup"]:
        raise _fail("prereg", f"promotion.comparators {prereg.promotion['comparators']!r} are not the implemented ones")
    if prereg.frozen_v1["report"] != list(FROZEN_REPORT_METRICS):
        raise _fail("prereg", f"frozen_v1.report {prereg.frozen_v1['report']!r} is not {list(FROZEN_REPORT_METRICS)}")
    try:
        check_prereg_against_code(prereg)
    except PreregError as exc:
        raise _fail("prereg", f"P1b: {exc}") from exc
    lock_time = None
    if not config.rehearsal:
        if lock is None:
            raise _fail("lock", "prereg.yaml has no analysis_lock (run make prereg-lock before training)")
        expected = {"challenger_model": CHALLENGER_MODEL, "experiment_name": config.challenger_experiment,
                    "frozen_v1_rows": FROZEN_V1_ROWS, "negative_control": NEGATIVE_CONTROL}  # fmt: skip
        for key, value in expected.items():
            if lock[key] != value:
                raise _fail("lock", f"analysis_lock.{key} is {lock[key]!r}, expected {value!r}")
        if _git(run_git, root, "diff", "--quiet", lock["code_commit"], "HEAD", "--", *CODE_PATHS).returncode != 0:
            raise _fail("lock", f"the code at HEAD differs from the locked code_commit {lock['code_commit']} in {list(CODE_PATHS)}")
        found = _git(run_git, root, "log", "-1", "--format=%H %ct", f"-S{LOCK_MARKER}", "--", "prereg.yaml")
        parts = _text(found.stdout).split()
        if found.returncode != 0 or len(parts) != 2 or not _HEX40_RE.fullmatch(parts[0]):
            raise _fail("lock", "cannot find the commit that added analysis_lock to prereg.yaml")
        lock_commit, lock_time = parts[0], int(parts[1])
        locked = _git(run_git, root, "show", "--end-of-options", f"{lock_commit}:prereg.yaml")
        try:
            locked_block = parse_prereg(_bytes(locked.stdout).decode("utf-8")).analysis_lock if locked.returncode == 0 else None
        except PreregError:
            locked_block = None
        if locked_block != lock:
            raise _fail("lock", f"analysis_lock at HEAD differs from the block committed in {lock_commit}: the lock was edited")
        later = _git(run_git, root, "log", "--format=%H", f"{lock_commit}..HEAD", "--", "prereg.yaml")
        if later.returncode != 0 or _text(later.stdout).strip():
            raise _fail("lock", f"prereg.yaml changed after the lock commit {lock_commit}")
        if _git(run_git, root, "fetch", "origin").returncode != 0:
            raise _fail("lock", "git fetch origin failed")
        if not _text(_git(run_git, root, "branch", "-r", "--contains", lock_commit).stdout).strip():
            raise _fail("lock", f"the lock commit {lock_commit} is not on any remote branch")

    # 2. gold, Phase A, split, power
    stack = contextlib.ExitStack()
    try:
        gold_dir = stack.enter_context(gold_opener(gold_prefix(config.run)))
    except Exception as exc:  # any failure to obtain the gold is this step's refusal
        raise _fail("gold", f"cannot read {gold_prefix(config.run)}: {type(exc).__name__}: {exc}") from exc
    with stack:
        try:
            frame, metadata = load_gold_table_with_metadata(Path(gold_dir))
        except (ValueError, FileNotFoundError) as exc:
            raise _fail("gold", str(exc)) from exc
    label = prereg.label
    checks = {
        "metadata_version": (metadata.metadata_version, 2),
        "gold_fingerprint_definition": (metadata.gold_fingerprint_definition, FINGERPRINT_DEFINITION_ROWHASH_V1),
        "label_definition": (metadata.label_definition, label["definition"]),
        "planned_procedure_codes": (tuple(metadata.planned_procedure_codes), tuple(label["planned_procedure_codes"])),
        "readmission_window_days": (metadata.readmission_window_days, label["readmission_window_days"]),
        "lookback_years": (metadata.lookback_years, label["lookback_years"]),
        "reference_date": (metadata.reference_date, prereg.generation["reference_date"]),
    }
    for key, (got, want) in checks.items():
        if got != want:
            raise _fail("gold", f"metadata {key} is {got!r}, prereg says {want!r}")
    phase_a_commit = (metadata.generation_summary or {}).get("provenance", {}).get("prereg_commit")
    if not isinstance(phase_a_commit, str) or not _HEX40_RE.fullmatch(phase_a_commit):
        raise _fail("phase_a", f"the gold's generation provenance has no valid prereg_commit: {phase_a_commit!r}")
    shown = _git(run_git, root, "show", "--end-of-options", f"{phase_a_commit}:prereg.yaml")
    if shown.returncode != 0:
        raise _fail("phase_a", f"cannot read prereg.yaml at {phase_a_commit}")
    try:
        old = parse_prereg(_bytes(shown.stdout).decode("utf-8"))
    except PreregError as exc:
        raise _fail("phase_a", f"prereg.yaml at {phase_a_commit} does not parse: {exc}") from exc
    changed = [key for key in SPEC if old.raw[key] != prereg.raw[key]]
    if changed:
        raise _fail("phase_a", f"Phase A changed since generation ({phase_a_commit}) in {changed}; no change is allowed after main_200k exists")
    split = _prereg_split(frame, prereg)
    n_test_positive = int(split.summary["n_test_positive"])
    floor = prereg.power["min_test_positives"]
    if not config.rehearsal and n_test_positive < floor:
        raise _fail("power", f"n_test_positive {n_test_positive} < min_test_positives {floor}")
    y_test = split.test[LABEL_COLUMN].to_numpy().astype(int)
    train_prevalence = float(split.train[LABEL_COLUMN].mean())

    # 3. challenger runs and gold usage
    challenger_ids = {name: _resolve(tracking_dir, config.challenger_experiment, name) for name in MODEL_NAMES}
    records = {name: _read_run(tracking_dir, run_id) for name, run_id in challenger_ids.items()}
    training = prereg.training
    for name, record in records.items():
        who = f"challenger run {record.run_id} ({name})"
        tags, params = record.tags, record.params
        if tags.get("model_name") != name:
            raise _fail("challenger", f"{who}: model_name tag is {tags.get('model_name')!r}")
        if tags.get(ROWHASH_TAG) != metadata.gold_fingerprint:
            raise _fail("challenger", f"{who}: trained on gold {tags.get(ROWHASH_TAG)!r}, not {metadata.gold_fingerprint}")
        if tags.get("git_dirty") != "clean":
            raise _fail("challenger", f"{who}: git_dirty is {tags.get('git_dirty')!r}")
        if not _HEX40_RE.fullmatch(str(tags.get("mlflow.source.git.commit", ""))):
            raise _fail("challenger", f"{who}: no mlflow.source.git.commit tag")
        expected_params = {
            "seed": str(training["seed"]),
            "reference_date": prereg.generation["reference_date"],
            "test_start_date": prereg.split["test_start_date_resolved"],
            "train_start_date": "none" if prereg.split["train_start_date"] is None else prereg.split["train_start_date"],
            "readmission_window_days": str(label["readmission_window_days"]),
            "gold_lookback_years": str(label["lookback_years"]),
        }
        if name == REPORTED_MODEL:
            expected_params["calibration_method"] = training["calibration"]["method"]
            expected_params["calibration_cv_folds"] = str(training["calibration"]["cv_folds"])
        for key, value in expected_params.items():
            if params.get(key) != value:
                raise _fail("challenger", f"{who}: param {key} is {params.get(key)!r}, prereg says {value!r}")
        if not config.rehearsal:
            run_commit = tags["mlflow.source.git.commit"]
            if _git(run_git, root, "diff", "--quiet", run_commit, lock["code_commit"], "--", *CODE_PATHS).returncode != 0:
                raise _fail("challenger", f"{who}: trained from code ({run_commit}) that differs from the locked code_commit")
            if record.start_time <= lock_time * 1000:
                raise _fail("challenger", f"{who}: started before the analysis lock was committed")
            # timestamp-free: a run whose clean commit contains the lock commit cannot predate the lock
            if _git(run_git, root, "merge-base", "--is-ancestor", lock_commit, run_commit).returncode != 0:
                raise _fail("challenger", f"{who}: its commit {run_commit} does not contain the lock commit {lock_commit}")
    gold_usage = None
    if not config.rehearsal:
        used = runs_on_gold(tracking_dir, metadata.gold_fingerprint)
        gold_usage = {}
        for entry in used:
            counts = gold_usage.setdefault(entry["experiment"], {"active": 0, "deleted": 0})
            counts["deleted" if entry["run_stage"] == "deleted" or entry["experiment_stage"] == "deleted" else "active"] += 1
        for entry in used:
            if entry["experiment"] != config.challenger_experiment:
                raise _fail("gold_usage", f"run {entry['run_id']} in experiment {entry['experiment']!r} was trained on this gold")
            if entry["start_time"] <= lock_time * 1000:
                raise _fail("gold_usage", f"run {entry['run_id']} on this gold started before the analysis lock")

    # 4. frozen-v1 runs; 5. Python and library versions
    frozen_names = list(prereg.frozen_v1["models"])
    frozen_ids = {name: _resolve(tracking_dir, config.frozen_experiment, name) for name in frozen_names}
    frozen_records = {name: _read_run(tracking_dir, run_id) for name, run_id in frozen_ids.items()}
    for name, record in {**{f"challenger {k}": v for k, v in records.items()},
                         **{f"frozen {k}": v for k, v in frozen_records.items()}}.items():  # fmt: skip
        _check_versions(record, f"{name} run {record.run_id}")

    # 6. champion bundle; 7. challenger bundle
    champion = read_champion(config.path(config.champion_path))
    champion_dir = config.path(config.bundles_root) / bundle_uri_leaf(champion.bundle_uri)
    manifest_file = champion_dir / "manifest.json"
    if not manifest_file.is_file():
        raise _fail("champion", f"no local copy at {champion_dir} (gcloud storage cp -r {champion.bundle_uri} {config.bundles_root}/)")
    if hashlib.sha256(manifest_file.read_bytes()).hexdigest() != champion.sha256:
        raise _fail("champion", "the local manifest.json does not hash to deploy/champion.json's sha256")
    champion_manifest, champion_bytes, _ = verify_bundle_dir(champion_dir)
    if champion_manifest["mlflow"]["run_id"] != frozen_ids[CHAMPION_MODEL]:
        raise _fail("champion", f"the champion bundle's run {champion_manifest['mlflow']['run_id']} is not the frozen-v1 LR run")
    challenger_dir = config.path(config.challenger_bundle)
    challenger_manifest, challenger_bytes, assignment = verify_bundle_dir(challenger_dir)
    lr_record = records[CHALLENGER_MODEL]
    if challenger_manifest["mlflow"]["run_id"] != lr_record.run_id:
        raise _fail("challenger_bundle", "the bundle's MLflow run is not the challenger LR run")
    if challenger_manifest.get("git", {}).get("commit") != lr_record.tags["mlflow.source.git.commit"]:
        raise _fail("challenger_bundle", "the bundle's git commit differs from the run's")
    _check_assignment(assignment, split)

    # 8. load; 9. predict
    challenger_model = _load_pickle(challenger_bytes, "challenger bundle")
    champion_model = _load_pickle(champion_bytes, "champion bundle")
    xgb_model = _load_logged(tracking_dir, records[REPORTED_MODEL].run_id, "challenger xgboost")
    frozen_models = {name: _load_logged(tracking_dir, run_id, f"frozen {name}") for name, run_id in frozen_ids.items()}

    v1_gold_dir = config.path(config.v1_gold_dir)
    if not v1_gold_dir.is_dir():
        raise _fail("frozen", f"no v1 gold at {v1_gold_dir} (gcloud storage cp -r gs://synthea-readmission-risk-gold/full_run_2b data/gold/)")
    v1_frame, _ = load_gold_table_with_metadata(v1_gold_dir)
    v1_params = frozen_records[CHAMPION_MODEL].params
    v1_split = chronological_group_split(
        v1_frame,
        reference_date=parse_yyyymmdd(v1_params["reference_date"], "reference_date"),
        test_start_date=parse_yyyymmdd(v1_params["test_start_date"], "test_start_date"),
        readmission_window_days=int(v1_params["readmission_window_days"]),
        train_start_date=None if v1_params["train_start_date"] == "none" else parse_yyyymmdd(v1_params["train_start_date"], "train_start_date"),
    )
    v1_y = v1_split.test[LABEL_COLUMN].to_numpy().astype(int)
    for name, model in frozen_models.items():
        _check_reproduces(frozen_records[name], v1_y, _predict(model, v1_split.test, f"frozen {name}"), f"frozen {name}", "frozen")

    test = split.test
    challenger_p = _predict(challenger_model, test, "challenger")
    xgb_p = _predict(xgb_model, test, "challenger xgboost")
    _check_reproduces(lr_record, y_test, challenger_p, "challenger bundle", "reproduction")
    _check_reproduces(records[REPORTED_MODEL], y_test, xgb_p, "challenger xgboost", "reproduction")
    champion_p = _predict(champion_model, test, "champion")
    lookup_p = reason_code_lookup(
        split.train["admission_reason_code"], split.train[LABEL_COLUMN].to_numpy(), test["admission_reason_code"],
        smoothing=prereg.promotion["reason_code_lookup_smoothing"],
    )  # fmt: skip
    if config.negative_control:
        challenger_p = np.full(len(test), train_prevalence)

    modeling = pd.concat([split.train, split.test], ignore_index=True)
    frozen_p = {name: _predict(model, modeling, f"frozen {name}") for name, model in frozen_models.items()}
    if not np.array_equal(_predict(champion_model, modeling, "champion"), frozen_p[CHAMPION_MODEL]):
        raise _fail("champion", "the champion bundle and the frozen-v1 LR run predict differently")

    # 10. decide
    boot = prereg.bootstrap
    gate = evaluate_gate(
        y_test, test[GROUP_COLUMN].to_numpy(),
        {"challenger": challenger_p, "champion": champion_p, "reason_code_lookup": lookup_p},
        train_prevalence=train_prevalence, n_resamples=boot["n_resamples"], seed=boot["seed"],
        ci_level=boot["ci_level"],
        brier_margin_fraction=prereg.promotion["brier_noninferiority"]["margin_fraction_of_champion_brier"],
    )  # fmt: skip
    acceptance = prereg.frozen_v1["acceptance"]
    frozen = evaluate_frozen_v1(
        modeling[LABEL_COLUMN].to_numpy().astype(int), modeling[GROUP_COLUMN].to_numpy(), frozen_p,
        n_resamples=boot["n_resamples"], seed=boot["seed"], ci_level=boot["ci_level"],
        roc_auc_ci_lower_min=acceptance["roc_auc_ci_lower_min"],
        oe_band=tuple(acceptance["observed_expected_ci_must_overlap"]),
    )  # fmt: skip
    xgb_point = evaluate_probabilities(y_test, xgb_p, train_prevalence=train_prevalence).metrics

    return _sanitize({
        "run": config.run,
        "mode": "negative_control" if config.negative_control else "main",
        "rehearsal": config.rehearsal,
        "analysis_lock": lock,
        "lock_time_utc": None if lock_time is None else datetime.fromtimestamp(lock_time, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "phase_a_commit": phase_a_commit,
        "power": {"n_test": int(split.summary["n_test"]), "n_test_positive": n_test_positive, "min_test_positives": floor},
        "gold_fingerprint": metadata.gold_fingerprint,
        "split_summary": {k: split.summary[k] for k in SPLIT_SUMMARY_KEYS
                          if isinstance(split.summary.get(k), int) and not isinstance(split.summary.get(k), bool)},
        "runs_on_this_gold": gold_usage,
        "challenger": {"run_id": lr_record.run_id, "bundle_id": challenger_manifest["bundle_id"],
                       "git_commit": lr_record.tags["mlflow.source.git.commit"],
                       "replaced_by": NEGATIVE_CONTROL if config.negative_control else None},
        "champion": {"bundle_id": champion_manifest["bundle_id"], "run_id": frozen_ids[CHAMPION_MODEL]},
        "gate": {"point": gate.point, "differences": gate.differences, "rules": [asdict(r) for r in gate.rules],
                 "decision": gate.decision, "lookup_still_wins": gate.lookup_still_wins,
                 "n_resamples_used": gate.n_resamples_used, "n_resamples_skipped": gate.n_resamples_skipped},
        "frozen_v1": {"rows": FROZEN_V1_ROWS, "n_rows": len(modeling), "n_positive": int(modeling[LABEL_COLUMN].sum()),
                      "models": {name: {"run_id": frozen_ids[name], **asdict(result)} for name, result in frozen.items()}},
        "xgboost_reported": {"run_id": records[REPORTED_MODEL].run_id, "point": xgb_point},
    })  # fmt: skip


def _resolve(tracking_dir: Path, experiment: str, name: str) -> str:
    try:
        return resolve_run_id(tracking_dir, experiment, name)
    except (ValueError, FileNotFoundError) as exc:
        raise _fail("runs", f"{experiment}/{name}: {exc}") from exc


def _check_assignment(data: bytes, split) -> None:
    import io

    try:
        table = pd.read_csv(io.BytesIO(data), dtype=str)
    except (ValueError, pd.errors.ParserError) as exc:
        raise _fail("split", f"the challenger bundle's split_assignment.csv does not parse: {exc}") from exc
    if list(table.columns) != ["encounter_id", "partition"] or table["encounter_id"].duplicated().any():
        raise _fail("split", "the challenger bundle's split_assignment.csv has unexpected columns or duplicate encounter ids")
    if set(table["partition"]) - {"train", "test"}:
        raise _fail("split", f"unexpected partitions {sorted(set(table['partition']) - {'train', 'test'})}")
    for partition, part in (("train", split.train), ("test", split.test)):
        listed = set(table.loc[table["partition"] == partition, "encounter_id"])
        if listed != set(part["encounter_id"].astype(str)):
            raise _fail("split", f"the {partition} partition differs from the pre-registered split")


REHEARSAL_BANNER = "REHEARSAL: pipeline sanity only, not a result"


def _fmt(value) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def render_gate_table(report: dict, *, ci_level: float, frozen_models: list[str]) -> str:
    """The gate_table.md text for a successful report (design note, Interfaces > S3)."""
    pct = f"{ci_level:.0%}"
    lines = []
    if report["rehearsal"]:
        lines += [REHEARSAL_BANNER, ""]
    gate = report["gate"]
    lines += [f"# Promotion gate: {report['run']} ({report['mode']})", "", f"Decision: **{gate['decision']}**", "",
              "| Rule | Statistic | Value | Threshold | Result |", "|---|---|---|---|---|"]  # fmt: skip
    for rule in gate["rules"]:
        op = "<=" if rule["name"].startswith("brier") else ">"
        lines.append(f"| {rule['name']} | {rule['statistic']} | {_fmt(rule['value'])} | {op} {_fmt(rule['threshold'])} | "
                     f"{'PASS' if rule['passed'] else 'FAIL'} |")  # fmt: skip
    frozen = report["frozen_v1"]
    lines += ["", f"## Frozen v1 (all modeling rows, n={frozen['n_rows']}, positives={frozen['n_positive']})", "",
              f"| Model | ROC-AUC [{pct} CI] | Brier [{pct} CI] | O/E [{pct} CI] | Accepted |", "|---|---|---|---|---|"]  # fmt: skip
    for name in frozen_models:
        entry = frozen["models"][name]
        cells = [f"{_fmt(entry['point'][m])} [{_fmt(entry['intervals'][m][0])}, {_fmt(entry['intervals'][m][1])}]"
                 for m in FROZEN_REPORT_METRICS]  # fmt: skip
        lines.append(f"| {name} | {' | '.join(cells)} | {'yes' if entry['accepted'] else 'no'} |")
    return "\n".join(lines) + "\n"
