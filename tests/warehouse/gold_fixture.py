"""A small v2 gold table for the gold-publish tests (v3 slice 1): six rows written as Spark-like Parquet (naive timestamp[ns],
int32 label) into a fake GCS prefix with valid version-2 metadata, a fake champion bundle whose manifest names that gold, and
a fake BigQuery that simulates the publish's load job (rows come back the way BigQuery returns them: tz-aware microsecond
timestamps, int64 integers), its transaction (staging -> serving.gold) and tabledata.list."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import bigquery

from readmission_risk.bundles.champion import champion_json
from readmission_risk.bundles.manifest import sha256_hex
from readmission_risk.pipeline.big_join import GOLD_TABLE_COLUMNS
from readmission_risk.pipeline.fingerprint import rowhash_fingerprint
from readmission_risk.warehouse.bigquery import load_schema, to_bq_schema
from readmission_risk.warehouse.gold import GoldPublishRequest, serving_gold_table_id
from tests.bundles.fake_gcs import FakeStorage
from tests.gold_helpers import write_gold_metadata_for_frame
from tests.warehouse.fake_bigquery import FakeBigQuery, FakeJob

GOLD_BUCKET = "synthea-readmission-risk-gold"
BUNDLE_BUCKET = "synthea-readmission-risk-bundles"
NAME = "fixture"
GOLD_URI = f"gs://{GOLD_BUCKET}/v2/{NAME}"
PART = f"v2/{NAME}/part-00000.parquet"
METADATA = f"v2/{NAME}/_gold_metadata.json"
BUNDLE_URI = f"gs://{BUNDLE_BUCKET}/bundle1"
PUBLISH_ID = "abcdef012345"
COUNT_COLUMNS = [c for c in GOLD_TABLE_COLUMNS if c.endswith(("_count", "_count_window"))]


def gold_frame() -> pd.DataFrame:
    """6 rows: 2 positives, one discharge before 1960 (the __UNPARTITIONED__ partition), one NULL admission_reason_code."""
    n = 6
    stops = [datetime(1955, 3, 1, 10), *(datetime(2019, m, 15, 12, 30, 5) for m in range(1, n))]
    frame = pd.DataFrame(
        {
            "patient_id": [f"p{i}" for i in range(n)],
            "encounter_id": [f"e{i}" for i in range(n)],
            "index_start": pd.to_datetime([s.replace(hour=1) for s in stops]),
            "index_stop": pd.to_datetime(stops),
            "is_readmitted": pd.array([1, 1, 0, 0, 0, 0], dtype="int32"),
            "label_horizon": pd.to_datetime([s.replace(day=28) for s in stops]),
            "admission_reason_code": ["84114007", None, "84114007", "195967001", "195967001", "22298006"],
            "admission_reason_description": ["Heart failure", None, "Heart failure", "Asthma", "Asthma", "MI"],
            "payer_ownership": ["PRIVATE"] * n,
            "provider_specialty": ["GENERAL PRACTICE"] * n,
            "organization_utilization": pd.array([10, 20, 30, 40, 50, 60], dtype="int32"),
            **{c: [i + k for i in range(n)] for k, c in enumerate(COUNT_COLUMNS)},
            "age_at_index_years": [64.25, 70.5, 81.125, 33.0, 45.75, 58.0625],
            "gender": ["F", "M", "F", "M", "F", "M"],
            "race": ["white"] * n,
            "ethnicity": ["nonhispanic"] * n,
            "marital_status": ["M", "S", None, "M", "S", "M"],
        }
    )
    return frame[list(GOLD_TABLE_COLUMNS)]


def parquet_bytes(frame: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), buffer)
    return buffer.getvalue()


def as_bigquery_returns(arrow: pa.Table) -> pa.Table:
    """The shape list_rows(...).to_arrow() has: TIMESTAMP as timestamp[us, UTC], INTEGER as int64."""
    columns = []
    for name, column in zip(arrow.column_names, arrow.columns, strict=True):
        if pa.types.is_timestamp(column.type):
            column = column.cast(pa.timestamp("us")).cast(pa.timestamp("us", tz="UTC"))
        elif pa.types.is_integer(column.type):
            column = column.cast(pa.int64())
        columns.append((name, column))
    return pa.table(dict(columns))


def serving_table(fingerprint: str, num_rows: int, **overrides) -> bigquery.Table:
    """serving.gold as Terraform creates it (a REAL client Table object, so the verify step reads real attributes)."""
    table = bigquery.Table(serving_gold_table_id(), schema=to_bq_schema(load_schema("gold")))
    table.time_partitioning = bigquery.TimePartitioning(type_="MONTH", field="index_stop")
    table.require_partition_filter = True
    table.clustering_fields = ["admission_reason_code"]
    table.description = f"Gold ... spark-rowhash-v1 fingerprint {fingerprint} (v2/main_200k)."
    for key, value in overrides.items():
        setattr(table, key, value)
    table._properties["numRows"] = str(num_rows)
    return table


@dataclass
class GoldWorld:
    storage: FakeStorage
    bq: FakeBigQuery
    frame: pd.DataFrame
    fingerprint: str
    champion_path: Path
    audit: Callable[[str], FakeJob] = lambda sql: FakeJob()  # the gold.sql job (pass by default)
    mutate_staging: Callable[[pa.Table], pa.Table] | None = None
    mutate_serving: Callable[[pa.Table], pa.Table] | None = None
    serving_overrides: dict = field(default_factory=dict)
    publish: Callable[[], FakeJob] | None = None  # replaces the transaction's outcome (after the copy, if copy_on_publish)
    copy_on_publish: bool = True

    def request(self, **overrides) -> GoldPublishRequest:
        values = {"gold_uri": GOLD_URI, "champion_path": self.champion_path, "expected_fingerprint": self.fingerprint}
        values.update(overrides)
        return GoldPublishRequest(**values)

    def staging_name(self) -> str:
        return f"synthea-readmission-risk.scratch_gold.gold_staging_{PUBLISH_ID}"

    def put_manifest(self, manifest: dict | bytes) -> None:
        data = manifest if isinstance(manifest, bytes) else json.dumps(manifest).encode()
        self.storage.put(BUNDLE_BUCKET, "bundle1/manifest.json", data)
        self.champion_path.write_text(champion_json(BUNDLE_URI, sha256_hex(data)))

    def queries(self) -> list[str]:
        return [c[1] for c in self.bq.calls if c[0] == "query"]


def build_world(tmp_path: Path, *, frame: pd.DataFrame | None = None, metadata_version: int = 2, **metadata) -> GoldWorld:
    frame = gold_frame() if frame is None else frame
    meta_dir = tmp_path / "meta"
    meta_dir.mkdir(parents=True)
    write_gold_metadata_for_frame(meta_dir, frame, version=metadata_version, **metadata)
    storage = FakeStorage()
    storage.put(GOLD_BUCKET, PART, parquet_bytes(frame))
    storage.put(GOLD_BUCKET, METADATA, (meta_dir / "_gold_metadata.json").read_bytes())
    storage.put(GOLD_BUCKET, f"v2/{NAME}/_SUCCESS", b"")
    fingerprint = rowhash_fingerprint(frame)
    world = GoldWorld(storage=storage, bq=FakeBigQuery(), frame=frame, fingerprint=fingerprint, champion_path=tmp_path / "champion.json")
    world.put_manifest({"gold_fingerprint": fingerprint, "bundle_id": "bundle1"})
    serving = serving_gold_table_id()
    world.bq.table_objects[serving] = serving_table(fingerprint, 0)  # Terraform-created, empty

    def on_load_uri(uris, destination, job_config, job_id):
        parts = []
        for uri in uris:
            bucket, _, name = uri[len("gs://") :].partition("/")
            parts.append(pq.read_table(io.BytesIO(storage.buckets[bucket][name].data)))
        arrow = as_bigquery_returns(pa.concat_tables(parts))
        if world.mutate_staging is not None:
            arrow = world.mutate_staging(arrow)
        world.bq.frames[destination] = arrow
        world.bq.table_objects[destination] = bigquery.Table(destination, schema=to_bq_schema(load_schema("gold")))
        return FakeJob()

    def on_query(sql, config, job_id):
        if not sql.startswith("BEGIN TRANSACTION"):
            return world.audit(sql)
        if world.copy_on_publish:
            arrow = world.bq.frames[world.staging_name()]
            if world.mutate_serving is not None:
                arrow = world.mutate_serving(arrow)
            world.bq.frames[serving] = arrow
            world.bq.table_objects[serving] = serving_table(world.fingerprint, arrow.num_rows, **world.serving_overrides)
        return world.publish() if world.publish is not None else FakeJob(total_bytes_billed=20971520)

    world.bq.on_load_uri = on_load_uri
    world.bq.on_query = on_query
    return world
