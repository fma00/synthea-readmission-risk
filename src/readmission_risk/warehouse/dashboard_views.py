"""The dashboard dataset's authorized views (v3 slice 2, PRD FR6/V8): their names, which base-table columns each exposes, the
"why" rate's constants, a renderer of the packaged view SQL (Terraform's templatefile renders the same files), and a pandas
reference of the call sheet's reason rate for the live parity check. Imports nothing from models, scoring or bundles (the
dashboard's import boundary, PRD FR18). See notes/eg-new-feature/v3-slice2-authorized-views-2026-10-31.md (Interfaces 3-4).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from importlib import resources

import pandas as pd

from readmission_risk.gcp import PROJECT_ID

from .bigquery import GOLD_TABLE, SERVING_DATASET, column_names

VIEW_NAMES = ("v_call_sheet", "v_scoring_runs", "v_monitoring")

CALL_SHEET_COLUMNS = (
    "as_of_date", "rank", "is_top_n", "top_n", "patient_id", "encounter_id", "discharge_time",
    "admission_reason_code", "admission_reason_description", "risk_score", "age_at_index_years", "length_of_stay_days",
    "prior_inpatient_count", "prior_emergency_count", "active_condition_count", "reason_history_n", "reason_history_rate",
    "model_bundle_id", "scoring_run_id", "scoring_window_days",
)  # fmt: skip
# Every serving.predictions column is either exposed by v_call_sheet under its own name or not exposed (tests keep the two
# a partition of the schema, so a new column must be classified deliberately).
PREDICTIONS_EXPOSED = frozenset({
    "as_of_date", "rank", "is_top_n", "top_n", "patient_id", "encounter_id", "admission_reason_code",
    "admission_reason_description", "risk_score", "model_bundle_id", "scoring_run_id", "scoring_window_days",
})  # fmt: skip
PREDICTIONS_NOT_EXPOSED = frozenset({
    "discharge_time",  # gold's index_stop is exposed instead: it is gold's partition column
    "model_run_id", "model_name", "partition", "label_definition",
    "gold_fingerprint",  # a constant filter of the view
    "gold_reference_date", "gold_fingerprint_definition",
})  # fmt: skip
# serving.gold columns the main SELECT/JOIN reads, and the ones only the reason-rate CTE reads (labels included).
GOLD_READ = frozenset({
    "encounter_id", "index_stop", "index_start", "age_at_index_years", "prior_inpatient_count", "prior_emergency_count",
    "active_condition_count",
})  # fmt: skip
GOLD_RATE_INPUTS = frozenset({"admission_reason_code", "is_readmitted", "label_horizon", "index_stop"})
GOLD_NOT_READ = frozenset(set(column_names(GOLD_TABLE)) - GOLD_READ)
FORBIDDEN_OUTPUT_COLUMNS = frozenset(
    {"is_readmitted", "label_horizon", "organization_utilization", "gender", "race", "ethnicity", "marital_status"}
)

SCORING_RUNS_EXCLUDED = frozenset({"error_message", "gold_uri", "predictions_table"})  # owner decision D4
MONITORING_EXCLUDED = frozenset({"error_message"})
SCORING_RUNS_COLUMNS = tuple(c for c in column_names("scoring_runs") if c not in SCORING_RUNS_EXCLUDED)
MONITORING_COLUMNS = tuple(c for c in column_names("monitoring") if c not in MONITORING_EXCLUDED)
VIEW_COLUMNS = {"v_call_sheet": CALL_SHEET_COLUMNS, "v_scoring_runs": SCORING_RUNS_COLUMNS, "v_monitoring": MONITORING_COLUMNS}

# The "why" rate (owner decision D2): discharges before the champion's test window whose label was settled before it.
REASON_RATE_CUTOFF = datetime(2023, 1, 1, tzinfo=UTC)
REASON_RATE_MIN_N = 30
MISSING_REASON_KEY = "MISSING"

VIEW_PLACEHOLDERS = {"v_call_sheet": ("predictions", "gold"), "v_scoring_runs": ("scoring_runs",), "v_monitoring": ("monitoring",)}
_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_]+)\}")
_ESCAPES = ("$${", "%{", "%%{")


def view_template(name: str) -> str:
    """The packaged view SQL, as Terraform reads it."""
    if name not in VIEW_NAMES:
        raise ValueError(f"unknown view {name!r}; expected one of {VIEW_NAMES}")
    return resources.files("readmission_risk.warehouse").joinpath("sql", "views", f"{name}.sql").read_text()


def view_sql(name: str, *, project_id: str = PROJECT_ID) -> str:
    """The view SQL with each `${key}` replaced by `<project_id>.serving.<key>`, as infra/bigquery.tf's
    local.dashboard_view_vars does. ValueError for an unknown name, a templatefile escape/directive, or a placeholder left over."""
    text = view_template(name)
    for escape in _ESCAPES:
        if escape in text:
            raise ValueError(f"{name}.sql contains {escape!r}, which templatefile would interpret")
    for key in VIEW_PLACEHOLDERS[name]:
        text = text.replace("${" + key + "}", f"{project_id}.{SERVING_DATASET}.{key}")
    if "${" in text:
        raise ValueError(f"{name}.sql has a placeholder outside {VIEW_PLACEHOLDERS[name]}")
    return text


def placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(text))


def strip_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.split("\n"))


def select_list(sql: str) -> list[str]:
    """Output column names of the LAST top-level SELECT: the text between it and its FROM (after removing `--` comments), split on
    commas at parenthesis depth 0; each item's name is the identifier after a final `AS`, else the part after the last `.`."""
    text = strip_comments(sql)
    depth, select_at, from_at = 0, None, None
    for match in re.finditer(r"\(|\)|\bSELECT\b|\bFROM\b", text, flags=re.IGNORECASE):
        word = match.group().upper()
        if word == "(":
            depth += 1
        elif word == ")":
            depth -= 1
        elif depth == 0 and word == "SELECT":
            select_at, from_at = match.end(), None
        elif depth == 0 and word == "FROM" and select_at is not None and from_at is None:
            from_at = match.start()
    if select_at is None or from_at is None:
        raise ValueError("no top-level SELECT ... FROM found")
    body = text[select_at:from_at]
    items, depth, start = [], 0, 0
    for i, char in enumerate(body):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            items.append(body[start:i])
            start = i + 1
    items.append(body[start:])
    names = []
    for item in (i.strip() for i in items):
        alias = re.search(r"\bAS\s+(\w+)\s*$", item, flags=re.IGNORECASE)
        names.append(alias.group(1) if alias else item.rsplit(".", 1)[-1].strip())
    return names


def reason_rates(gold: pd.DataFrame) -> pd.DataFrame:
    """Pure pandas reference of v_call_sheet's reason_rates CTE: reason_key (str), n (int64), rate (float64, NaN when n <
    REASON_RATE_MIN_N), over rows with index_stop < cutoff AND label_horizon < cutoff (both strict); a NULL code is
    MISSING_REASON_KEY; sorted by reason_key. Does not mutate gold."""
    cutoff = pd.Timestamp(REASON_RATE_CUTOFF)
    settled = gold[(gold["index_stop"] < cutoff) & (gold["label_horizon"] < cutoff)]
    keys = settled["admission_reason_code"].astype(object).where(settled["admission_reason_code"].notna(), MISSING_REASON_KEY)
    stats = (
        pd.DataFrame({"reason_key": keys.astype(str), "y": settled["is_readmitted"].astype("float64")})
        .groupby("reason_key", sort=True)["y"]
        .agg(n="size", rate="mean")  # size = COUNT(*)
        .reset_index()
    )
    stats["n"] = stats["n"].astype("int64")
    stats["rate"] = stats["rate"].where(stats["n"] >= REASON_RATE_MIN_N).astype("float64")
    return stats
