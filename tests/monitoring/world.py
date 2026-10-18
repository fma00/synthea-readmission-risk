"""A scripted BigQuery + gold world for the monitoring tick's tests: monitoring rows, scoring_runs facts, published
prediction partitions and a small gold label table, answered by SQL pattern. No GCP."""

from __future__ import annotations

import contextlib
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from readmission_risk.monitoring.config import parse_monitoring_config
from tests.warehouse.fake_bigquery import FakeBigQuery, FakeJob

ROOT = Path(__file__).resolve().parents[2]
CONFIG = parse_monitoring_config(json.loads((ROOT / "deploy" / "monitoring.json").read_text()))
BUNDLE = CONFIG.model_bundle_id
DEFINITION = "pandas-csv-sha256-v1"


class Metadata:
    readmission_window_days = 30


def _params(config) -> dict:
    out = {}
    for p in getattr(config, "query_parameters", []) or []:
        out[p.name] = list(p.values) if hasattr(p, "values") else p.value
    return out


@dataclass
class World:
    """`ticks` holds monitoring rows (dicts); `runs` scoring_runs facts by id; `partitions` {as_of_date: (run_id, rows)}."""

    ticks: list[dict] = field(default_factory=list)
    runs: dict[str, dict] = field(default_factory=dict)
    partitions: dict[date, tuple[str, list[dict]]] = field(default_factory=dict)
    gold: pd.DataFrame | None = None
    history_failure: str | None = None
    inserted: list[dict] = field(default_factory=list)
    fake: FakeBigQuery = None

    def __post_init__(self):
        self.fake = FakeBigQuery(on_query=self.on_query)

    # ------------------------------------------------------------------ building
    def publish(self, day: date, *, rng, n: int = 40, positives: float = 0.1, score: float = 0.1, window_days: int = 7,
                bundle: str = BUNDLE, run_id: str | None = None, fingerprint: str | None = None) -> str:  # fmt: skip
        run_id = run_id or f"run{day:%Y%m%d}"
        rows, gold_rows = [], []
        for i in range(n):
            stop = pd.Timestamp(day.isoformat(), tz="UTC") - pd.Timedelta(days=int(i % window_days)) + pd.Timedelta(hours=10)
            encounter = f"{day:%Y%m%d}-{i:03d}"
            rows.append({"encounter_id": encounter, "discharge_time": stop, "risk_score": score, "as_of_date": day,
                         "scoring_run_id": run_id, "gold_fingerprint": fingerprint, "gold_fingerprint_definition": DEFINITION})  # fmt: skip
            gold_rows.append({"encounter_id": encounter, "is_readmitted": int(rng.random() < positives),
                              "label_horizon": stop + pd.Timedelta(days=30)})  # fmt: skip
        self.partitions[day] = (run_id, rows)
        self.runs[run_id] = {"scoring_run_id": run_id, "run_date": day, "status": "succeeded", "published": True,
                             "n_predictions": n, "model_bundle_id": bundle, "window_days": window_days, "top_n": CONFIG.top_n,
                             "gold_fingerprint": fingerprint, "gold_fingerprint_definition": DEFINITION}  # fmt: skip
        extra = pd.DataFrame(gold_rows)
        extra["is_readmitted"] = extra["is_readmitted"].astype("int8")
        self.gold = extra if self.gold is None else pd.concat([self.gold, extra], ignore_index=True)
        return run_id

    @staticmethod
    def fingerprint_of(gold: pd.DataFrame) -> str:
        """A stand-in for the real definitions (which need all 24 gold columns): sha256 over the label columns."""
        text = gold.sort_values("encounter_id")[["encounter_id", "is_readmitted"]].to_csv(index=False)
        return hashlib.sha256(text.encode()).hexdigest()

    def finalize_fingerprints(self) -> str:
        fingerprint = self.fingerprint_of(self.gold)
        for run_id, rows in self.partitions.values():
            for row in rows:
                row["gold_fingerprint"] = row["gold_fingerprint"] or fingerprint
            self.runs[run_id]["gold_fingerprint"] = fingerprint
        return fingerprint

    def succeed(self, day: date):
        self.ticks.append({"status": "succeeded", "sim_date": day, "model_bundle_id": BUNDLE,
                           "started_at": pd.Timestamp("2026-10-18", tz="UTC"), "monitoring_run_id": f"m{day:%Y%m%d}"})  # fmt: skip

    # ------------------------------------------------------------------ BigQuery
    def on_query(self, sql, config, job_id):
        params = _params(config)
        if sql.startswith("INSERT INTO `synthea-readmission-risk.serving.monitoring`"):
            self.inserted.append(params)
            return FakeJob()
        if "DECLARE c DEFAULT" in sql:
            if self.history_failure:
                from tests.warehouse.fake_bigquery import assertion_error

                return FakeJob(raise_on_result=assertion_error(self.history_failure))
            return FakeJob()
        if "FROM `synthea-readmission-risk.serving.monitoring`" in sql:
            return FakeJob(rows=list(self.ticks))
        if "COUNT(*) AS `n_rows`" in sql:
            lo, hi = params["lo"], params["hi"]
            return FakeJob(rows=[{"as_of_date": d, "scoring_run_id": rid, "n_rows": len(rows)}
                                 for d, (rid, rows) in self.partitions.items()
                                 if lo <= d <= hi and rows and rows[0] and self.runs[rid]["model_bundle_id"] == params["bundle"]])  # fmt: skip
        if "FROM `synthea-readmission-risk.serving.scoring_runs`" in sql and "UNNEST(@ids)" in sql:
            return FakeJob(rows=[self.runs[i] for i in params["ids"] if i in self.runs])
        if "FROM `synthea-readmission-risk.serving.predictions`" in sql:
            ids = set(params["ids"])
            return FakeJob(rows=[r for d, (rid, rows) in self.partitions.items() if params["lo"] <= d <= params["hi"]
                                 for r in rows if rid in ids])  # fmt: skip
        raise AssertionError(f"unexpected query: {sql[:200]}")

    # ------------------------------------------------------------------ gold
    def patch_gold(self, monkeypatch, tick_module):
        @contextlib.contextmanager
        def materialized(uri):
            yield Path("unused")

        monkeypatch.setattr(tick_module, "materialized_gold_dir", materialized)
        monkeypatch.setattr(tick_module, "load_gold_table_with_metadata", lambda _: (self.gold.copy(), Metadata()))
        monkeypatch.setattr(tick_module, "compute_gold_fingerprint", lambda definition, gold: self.fingerprint_of(gold))


def full_world(sim_date: date, *, positives: float = 0.1, seed: int = 0) -> World:
    """Every grid date from first_sim_date to sim_date published and succeeded, except sim_date itself (published only:
    the tick under test is the one evaluating it)."""
    rng = np.random.default_rng(seed)
    world = World()
    day = CONFIG.first_sim_date
    while day <= sim_date:
        world.publish(day, rng=rng, positives=positives)
        if day < sim_date:
            world.succeed(day)
        day += timedelta(days=7)
    world.finalize_fingerprints()
    return world
