"""prereg.yaml (v2 PRD FR10, M6): the strict loader, its schema, the run-time code-equality check (preflight P1b) and the
"committed before generation" check (preflight P1). See notes/eg-new-feature/v2-slice5-gs-big-join-fingerprint-pilot-2026-10-12.md,
Interfaces section 9. Imports only stdlib + pyyaml at module level; the code constants P1b compares against are imported inside
check_prereg_against_code (they pull in pyspark and scikit-learn, present in the laptop environment the CLIs run in).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

PREREG_FILENAME = "prereg.yaml"
V1_POPULATION_SEED = 42
V1_REFERENCE_DATE = "20260916"
_RUN_NAME_RE = re.compile(r"[a-z0-9_]{1,30}")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
_HEX40_RE = re.compile(r"[0-9a-f]{40}")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class PreregError(ValueError):
    """prereg.yaml is malformed, disagrees with the code, or is not committed as required."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader that refuses a duplicate mapping key at any level (PyYAML otherwise keeps the last one silently)."""


def _construct_unique_mapping(loader: _UniqueKeySafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen: set = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise PreregError(f"{PREREG_FILENAME}: duplicate key {key!r} (line {key_node.start_mark.line + 1})")
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_UniqueKeySafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


@dataclass(frozen=True)
class Population:
    name: str
    seed: int
    clinician_seed: int
    population: int
    role: str


@dataclass(frozen=True)
class Prereg:
    raw: dict  # the whole validated document
    populations: dict[str, Population]

    @property
    def generation(self) -> dict:
        return self.raw["generation"]

    @property
    def label(self) -> dict:
        return self.raw["label"]

    @property
    def split(self) -> dict:
        return self.raw["split"]

    @property
    def power(self) -> dict:
        return self.raw["power"]


# --- schema -------------------------------------------------------------------------------------------------------------
# A spec is a dict (exact key set, recursed) or a leaf checker (path, value) -> None that raises PreregError.


def _fail(path: str, message: str) -> None:
    raise PreregError(f"{PREREG_FILENAME}: {path}: {message}")


def _literal(expected: object) -> Callable[[str, object], None]:
    def check(path: str, value: object) -> None:
        if value != expected or type(value) is not type(expected):
            _fail(path, f"must be {expected!r}; got {value!r}")

    return check


def _one_of(*allowed: str) -> Callable[[str, object], None]:
    def check(path: str, value: object) -> None:
        if value not in allowed:
            _fail(path, f"must be one of {list(allowed)}; got {value!r}")

    return check


def _int(minimum: int | None = None) -> Callable[[str, object], None]:
    def check(path: str, value: object) -> None:
        if type(value) is not int or (minimum is not None and value < minimum):
            _fail(path, f"must be an int{'' if minimum is None else f' >= {minimum}'}; got {value!r}")

    return check


def _number(path: str, value: object) -> None:
    if type(value) not in (int, float):
        _fail(path, f"must be a number; got {value!r}")


def _str(path: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        _fail(path, f"must be a non-empty string; got {value!r}")


def _bool_true(path: str, value: object) -> None:
    if value is not True:
        _fail(path, f"must be true (the code always does this); got {value!r}")


def _null(path: str, value: object) -> None:
    if value is not None:
        _fail(path, f"must be null; got {value!r}")


def _hex64(path: str, value: object) -> None:
    if not isinstance(value, str) or not _HEX64_RE.fullmatch(value):
        _fail(path, f"must be 64 lowercase hex characters; got {value!r}")


def _yyyymmdd(path: str, value: object) -> None:
    try:
        _as_date(value)
    except ValueError as exc:
        _fail(path, str(exc))


def _iso_date(path: str, value: object) -> None:
    if not isinstance(value, str) or not _ISO_DATE_RE.fullmatch(value):
        _fail(path, f"must be a 'YYYY-MM-DD' string; got {value!r}")


def _str_list(path: str, value: object) -> None:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value) or len(set(value)) != len(value):
        _fail(path, f"must be a non-empty list of distinct strings; got {value!r}")


def _band(path: str, value: object) -> None:
    if not (isinstance(value, list) and len(value) == 2 and all(type(v) in (int, float) for v in value) and value[0] < 1 < value[1]):
        _fail(path, f"must be [low, high] with low < 1 < high; got {value!r}")


def _population(path: str, value: object) -> None:
    keys = {"seed", "clinician_seed", "population", "role"}
    if not isinstance(value, dict) or set(value) != keys:
        _fail(path, f"must have exactly the keys {sorted(keys)}; got {value!r}")
    _int(0)(f"{path}.seed", value["seed"])
    _int(0)(f"{path}.clinician_seed", value["clinician_seed"])
    _int(1)(f"{path}.population", value["population"])
    _one_of("operational_pilot", "validation")(f"{path}.role", value["role"])


SPEC: dict = {
    "prereg_version": _literal(1),
    "phase": _literal("A"),
    "registered_on": _iso_date,
    "populations": {"pilot_20k": _population, "main_200k": _population},
    "generation": {
        "reference_date": _yyyymmdd,
        "end_date": _yyyymmdd,
        "jvm_timezone": _literal("UTC"),
        "state": _str,
        "years_of_history": _int(1),
        "included_files": _str_list,
        "synthea_jar_sha256": _hex64,
        "synthea_build_version": _str,
        "jdk": {"distribution": _literal("temurin"), "version": _str, "file": _str, "sha256": _hex64},
    },
    "label": {
        "definition": _str,
        "planned_procedure_codes": _str_list,
        "readmission_window_days": _int(1),
        "lookback_years": _int(1),
    },
    "split": {
        "test_start_rule": {"kind": _literal("jan1_of_reference_year_minus_years"), "years": _int(1)},
        "test_start_date_resolved": _yyyymmdd,
        "train_start_date": _null,
        "patient_disjoint": _bool_true,
        "label_horizon_purge": _bool_true,
    },
    "training": {
        "seed": _int(0),
        "logistic_regression": {"C": _number, "solver": _str, "max_iter": _int(1), "class_weight": _null},
        "xgboost": {
            "n_estimators": _int(1),
            "max_depth": _int(1),
            "learning_rate": _number,
            "subsample": _number,
            "colsample_bytree": _number,
            "min_child_weight": _number,
            "reg_lambda": _number,
            "tree_method": _str,
            "n_jobs": _int(1),
        },
        "calibration": {"method": _one_of("sigmoid", "isotonic"), "cv_folds": _int(2), "ensemble": _bool_true},
    },
    "metrics": {
        "primary": _literal("roc_auc"),
        "secondary": _str_list,
        "definitions": {
            "observed_expected_ratio": _literal("sum(y_true) / sum(y_prob)"),
            "expected_calibration_error": {"n_bins": _int(2), "binning": _literal("quantile")},
            "brier_skill_score_reference": _literal("train_prevalence"),
        },
        "reported_quantities": _str_list,
    },
    "bootstrap": {
        "kind": _literal("patient_clustered_paired_percentile"),
        "n_resamples": _int(1000),
        "seed": _int(0),
        "ci_level": _number,
    },
    "power": {
        "min_test_positives": _int(1),
        "population_adjustment": {
            "projected_test_positives": _literal("pilot_test_window_positives * main_population / pilot_population"),
            "trigger_below": _int(1),
            "action": _str,
            "cap": _int(1),
            "pilot_test_window_positives": _str,
        },
    },
    "promotion": {
        "comparators": _literal(["champion", "reason_code_lookup"]),
        "roc_auc_rule": _literal("paired_ci_lower_gt_zero"),
        "brier_noninferiority": {"margin_fraction_of_champion_brier": _number},
        "reason_code_lookup_smoothing": _number,
    },
    "frozen_v1": {
        "models": _str_list,
        "report": _str_list,
        "acceptance": {"roc_auc_ci_lower_min": _number, "observed_expected_ci_must_overlap": _band},
    },
    "monitoring": {
        "matured_cohort_weeks": _int(1),
        "min_positives_floor": _int(1),
        "alert_rule": {
            "metric": _literal("observed_expected_ratio"),
            "ci": _literal("exact_poisson_95_on_observed"),
            "condition": _literal("ci_excludes_1_and_point_outside_band"),
            "band": _band,
        },
        "auc": _literal("report_only"),
    },
    "outcomes": {"lookup_still_wins": _str},
    "deviations": None,  # checked by _check_deviations
}


def _walk(spec: dict, value: object, path: str) -> None:
    if not isinstance(value, dict):
        _fail(path or "<top>", f"must be a mapping; got {type(value).__name__}")
    missing, unknown = sorted(set(spec) - set(value)), sorted(set(value) - set(spec))
    if missing:
        _fail(path or "<top>", f"missing key(s) {missing}")
    if unknown:
        _fail(path or "<top>", f"unknown key(s) {unknown}")
    for key, sub in spec.items():
        child = f"{path}.{key}" if path else key
        if isinstance(sub, dict):
            _walk(sub, value[key], child)
        elif sub is not None:
            sub(child, value[key])


def _has_path(doc: dict, dotted: str) -> bool:
    node: object = doc
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def _check_deviations(doc: dict) -> None:
    deviations = doc["deviations"]
    if not isinstance(deviations, list):
        _fail("deviations", f"must be a list; got {deviations!r}")
    keys = {"date", "commit", "field", "old", "new", "reason"}
    for i, entry in enumerate(deviations):
        path = f"deviations[{i}]"
        if not isinstance(entry, dict) or set(entry) != keys:
            _fail(path, f"must have exactly the keys {sorted(keys)}; got {entry!r}")
        _iso_date(f"{path}.date", entry["date"])
        commit = entry["commit"]
        if commit == "pending":
            if i != len(deviations) - 1:
                _fail(f"{path}.commit", "'pending' is allowed only on the last entry")
        elif not isinstance(commit, str) or not _HEX40_RE.fullmatch(commit):
            _fail(f"{path}.commit", f"must be a 40-hex commit or 'pending'; got {commit!r}")
        if not isinstance(entry["field"], str) or not _has_path(doc, entry["field"]):
            _fail(f"{path}.field", f"must be a dotted path that exists in the document; got {entry['field']!r}")
        _str(f"{path}.reason", entry["reason"])


def _as_date(value: object) -> date:
    if not (isinstance(value, str) and value.isascii() and value.isdigit() and len(value) == 8):
        raise ValueError(f"must be a YYYYMMDD string; got {value!r}")
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def resolve_test_start(reference_date: str, years: int) -> str:
    """jan1_of_reference_year_minus_years: 1 January of (reference year - years), as YYYYMMDD."""
    return f"{_as_date(reference_date).year - years:04d}0101"


def _check_cross_field(doc: dict) -> None:
    populations = doc["populations"]
    roles = sorted(p["role"] for p in populations.values())
    if roles != ["operational_pilot", "validation"]:
        _fail("populations", f"need exactly one operational_pilot and one validation population; got roles {roles}")
    for name in populations:
        if not _RUN_NAME_RE.fullmatch(name):
            _fail(f"populations.{name}", "run names must match [a-z0-9_]{1,30}")
    seeds = [p["seed"] for p in populations.values()]
    if len(set(seeds)) != len(seeds):
        _fail("populations", f"population seeds must differ (same seed = same first patients); got {seeds}")
    if V1_POPULATION_SEED in seeds:
        _fail("populations", f"seed {V1_POPULATION_SEED} is v1's population seed")
    gen = doc["generation"]
    if gen["reference_date"] == V1_REFERENCE_DATE:
        _fail("generation.reference_date", f"{V1_REFERENCE_DATE} is v1's reference date")
    if (_as_date(gen["end_date"]) - _as_date(gen["reference_date"])).days != 1:
        _fail("generation.end_date", "must be reference_date + 1 day (the Big Join observes through the whole reference day)")
    split = doc["split"]
    resolved = resolve_test_start(gen["reference_date"], split["test_start_rule"]["years"])
    if split["test_start_date_resolved"] != resolved:
        _fail("split.test_start_date_resolved", f"the rule gives {resolved}; got {split['test_start_date_resolved']}")
    margin = doc["promotion"]["brier_noninferiority"]["margin_fraction_of_champion_brier"]
    if not 0 < margin < 1:
        _fail("promotion.brier_noninferiority.margin_fraction_of_champion_brier", f"must be in (0, 1); got {margin}")
    if not 0 < doc["bootstrap"]["ci_level"] < 1:
        _fail("bootstrap.ci_level", f"must be in (0, 1); got {doc['bootstrap']['ci_level']}")
    power = doc["power"]
    if power["population_adjustment"]["trigger_below"] != round(1.25 * power["min_test_positives"]):
        _fail("power.population_adjustment.trigger_below", "must be 1.25 x min_test_positives")
    main = next(p for p in populations.values() if p["role"] == "validation")
    if power["population_adjustment"]["cap"] < main["population"]:
        _fail("power.population_adjustment.cap", "must be >= the validation population")


def parse_prereg(text: str) -> Prereg:
    try:
        doc = yaml.load(text, Loader=_UniqueKeySafeLoader)  # noqa: S506 -- a SafeLoader subclass
    except yaml.YAMLError as exc:
        raise PreregError(f"{PREREG_FILENAME}: invalid YAML: {exc}") from exc
    _walk(SPEC, doc, "")
    _check_deviations(doc)
    _check_cross_field(doc)
    populations = {name: Population(name=name, **values) for name, values in doc["populations"].items()}
    return Prereg(raw=doc, populations=populations)


def load_prereg(path: Path) -> Prereg:
    return parse_prereg(Path(path).read_text(encoding="utf-8"))


# --- P1b: the pre-registration agrees with the code that will run ---------------------------------------------------------


def check_prereg_against_code(prereg: Prereg) -> None:
    """PreregError listing EVERY mismatch between prereg.yaml and the code constants (run at launch, not only in tests:
    CI is a signal, not a merge gate)."""
    import dataclasses
    import inspect

    import numpy as np

    from readmission_risk.models.baselines import reason_code_lookup
    from readmission_risk.models.constants import MODEL_NAMES
    from readmission_risk.models.evaluate import evaluate_probabilities
    from readmission_risk.models.modeling import XGB_PARAMS, build_logistic_regression
    from readmission_risk.models.split import SPLIT_SUMMARY_KEYS
    from readmission_risk.models.training import TrainingConfig
    from readmission_risk.pipeline.big_join import BigJoinConfig
    from readmission_risk.pipeline.generation import EXPECTED_CSV_TABLES
    from readmission_risk.pipeline.gold_metadata import (
        LABEL_DEFINITION,
        PLANNED_PROCEDURE_CODES,
    )

    doc = prereg.raw
    defaults = {f.name: f.default for f in dataclasses.fields(TrainingConfig)}
    big_join_defaults = {f.name: f.default for f in dataclasses.fields(BigJoinConfig)}
    lr = build_logistic_regression(0).named_steps["model"].get_params()
    metric_names = set(
        evaluate_probabilities(np.array([0, 1, 0, 1]), np.array([0.1, 0.9, 0.2, 0.8]), train_prevalence=0.5).metrics
    ) | {"observed_expected_ratio"}
    expected = {
        "label.definition": LABEL_DEFINITION,
        "label.planned_procedure_codes": list(PLANNED_PROCEDURE_CODES),
        "label.readmission_window_days": big_join_defaults["readmission_window_days"],
        "label.lookback_years": big_join_defaults["lookback_years"],
        "generation.included_files": list(EXPECTED_CSV_TABLES),
        "training.seed": defaults["seed"],
        "training.calibration.method": defaults["calibration_method"],
        "training.calibration.cv_folds": defaults["calibration_cv_folds"],
        "training.xgboost": {k: v for k, v in XGB_PARAMS.items() if k not in ("objective", "eval_metric")},
        "training.logistic_regression": {k: lr[k] for k in ("C", "solver", "max_iter", "class_weight")},
        "split.train_start_date": defaults["train_start_date"],
        "promotion.reason_code_lookup_smoothing": inspect.signature(reason_code_lookup).parameters["smoothing"].default,
        "metrics.definitions.expected_calibration_error.n_bins": inspect.signature(evaluate_probabilities).parameters["n_bins"].default,
        "frozen_v1.models": list(MODEL_NAMES),
    }
    problems = []
    for dotted, want in expected.items():
        node: object = doc
        for part in dotted.split("."):
            node = node[part]
        if node != want:
            problems.append(f"{dotted}: prereg.yaml has {node!r}, the code has {want!r}")
    for name, allowed, values in (
        ("metrics.secondary", metric_names, doc["metrics"]["secondary"]),
        ("metrics.reported_quantities", set(SPLIT_SUMMARY_KEYS), doc["metrics"]["reported_quantities"]),
    ):
        unknown = sorted(set(values) - allowed)
        if unknown:
            problems.append(f"{name}: {unknown} are not names the code reports")
    if problems:
        raise PreregError(f"{PREREG_FILENAME} disagrees with the code:\n  " + "\n  ".join(problems))


# --- P1: committed, unmodified, pushed -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PreregRecord:
    head_commit: str  # HEAD: the code that is launched
    prereg_commit: str  # the last commit touching prereg.yaml
    prereg_committed_at: str  # its committer date, ISO 8601 (settable by hand; the PR merge time is the stronger evidence)
    sha256: str  # of prereg.yaml's committed bytes


def _git(run_git: Callable, repo_root: Path, *args: str) -> str:
    result = run_git(["git", "-C", str(repo_root), *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise PreregError(f"git {' '.join(args)} failed: {(result.stderr or '').strip()}")
    return result.stdout


def check_prereg_committed(repo_root: Path, *, run_git: Callable = subprocess.run) -> PreregRecord:
    """Refuses (PreregError) unless prereg.yaml is tracked, the whole working tree is clean (so the launched code and the zip
    are exactly HEAD), and, after `git fetch origin` (so a stale remote-tracking ref cannot vouch for HEAD), HEAD is contained
    in at least one origin/* branch."""
    repo_root = Path(repo_root)
    if not _git(run_git, repo_root, "ls-files", "--", PREREG_FILENAME).strip():
        raise PreregError(f"{PREREG_FILENAME} is not tracked by git: commit it before generating anything")
    dirty = _git(run_git, repo_root, "status", "--porcelain").strip()
    if dirty:
        raise PreregError(f"the working tree is not clean (commit or stash first):\n{dirty}")
    _git(run_git, repo_root, "fetch", "--quiet", "origin")
    remotes = [line.strip() for line in _git(run_git, repo_root, "branch", "-r", "--contains", "HEAD").splitlines()]
    if not any(r.startswith("origin/") for r in remotes):
        raise PreregError("HEAD is not on any origin/* branch: push it (and merge the PR) before launching")
    head = _git(run_git, repo_root, "rev-parse", "HEAD").strip()
    prereg_commit, committed_at = _git(
        run_git, repo_root, "log", "-1", "--format=%H %cI", "--", PREREG_FILENAME
    ).split()
    blob = run_git(
        ["git", "-C", str(repo_root), "show", f"HEAD:{PREREG_FILENAME}"], capture_output=True, check=False
    ).stdout
    data = blob if isinstance(blob, bytes) else blob.encode("utf-8")
    return PreregRecord(head_commit=head, prereg_commit=prereg_commit, prereg_committed_at=committed_at, sha256=hashlib.sha256(data).hexdigest())
