"""BigQuery identifiers, the packaged table schemas, and THE query-config factory: every query this package runs carries
maximum_bytes_billed (PRD cost NFR). google.cloud.bigquery is imported inside the functions. See
notes/eg-new-feature/v2-slice4-score-to-bigquery-2026-10-11.md (Interfaces > warehouse/bigquery.py).
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import TYPE_CHECKING

from readmission_risk.gcp import PROJECT_ID

if TYPE_CHECKING:  # pragma: no cover
    from google.cloud import bigquery

SERVING_DATASET = "serving"
SCRATCH_DATASET = "scratch"
SCRATCH_GOLD_DATASET = "scratch_gold"  # the gold publish's staging tables (v3 slice 1; 1-day expiry)
GOLD_TABLE = "gold"
DASHBOARD_DATASET = "dashboard"  # authorized views, the only data the dashboard runtime SA reads (v3 slice 2)
# The gold table serving.gold holds; infra/bigquery.tf's description names it (tests/test_infra.py keeps them equal) and the
# publish refuses any other gold (notes/eg-new-feature/v3-slice1-gold-bigquery-2026-10-31.md, Interfaces section 5).
SERVING_GOLD_FINGERPRINT = "80edd13dcc967d69b6fa4cfa17b4d7fa40188f8e7e17b2031b833332a59c63f3"
MAX_BYTES_BILLED = 100 * 1024**2  # per query job; the on-demand minimum is 10 MB per referenced table
GOLD_PUBLISH_MAX_BYTES_BILLED = 1024**3  # the gold audit and publish read ~185k rows (estimated 30-75 MB; 100 MiB is too close)
_SCHEMA_NAMES = ("predictions", "scoring_runs", "monitoring", "gold")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,1023}")


def load_schema(name: str) -> list[dict]:
    """The BigQuery JSON schema of a table (warehouse/schemas/<name>.json; Terraform reads the same file)."""
    if name not in _SCHEMA_NAMES:
        raise ValueError(f"unknown table schema {name!r}; expected one of {_SCHEMA_NAMES}")
    return json.loads(resources.files("readmission_risk.warehouse").joinpath("schemas", f"{name}.json").read_text())


def column_names(name: str) -> list[str]:
    return [field["name"] for field in load_schema(name)]


def to_bq_schema(fields: list[dict]) -> list[bigquery.SchemaField]:
    from google.cloud import bigquery

    return [bigquery.SchemaField(f["name"], f["type"], mode=f["mode"], description=f["description"]) for f in fields]


def table_id(dataset: str, table: str) -> str:
    for part in (dataset, table):
        if not isinstance(part, str) or not _IDENTIFIER_RE.fullmatch(part):
            raise ValueError(f"invalid BigQuery identifier {part!r}")
    return f"{PROJECT_ID}.{dataset}.{table}"


def quoted(identifier: str) -> str:
    """Back-quotes a table id or a column name for SQL built in Python (reserved words such as `partition`)."""
    if "`" in identifier:
        raise ValueError(f"identifier must not contain a back-quote: {identifier!r}")
    return f"`{identifier}`"


def query_config(*, max_bytes_billed: int = MAX_BYTES_BILLED, **kwargs) -> bigquery.QueryJobConfig:
    """The only QueryJobConfig constructor in src/: maximum_bytes_billed on every query, standard SQL."""
    from google.cloud import bigquery

    return bigquery.QueryJobConfig(maximum_bytes_billed=max_bytes_billed, use_legacy_sql=False, **kwargs)
