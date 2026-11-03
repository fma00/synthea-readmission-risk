"""Live proof of the gold access limits (v3 slice 1; slice 4's precedent of measuring "impossible by design"): each probe
must be DENIED. gold-publisher cannot create a table in serving, cannot change serving.predictions (it may only read it)
and cannot read serving.scoring_runs; the scorer and monitor service accounts cannot read serving.gold (it holds labels).
A probe that is allowed is undone where it changed something. See
notes/eg-new-feature/v3-slice1-gold-bigquery-2026-10-31.md (Interfaces section 6).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .bigquery import SERVING_DATASET, query_config, quoted, table_id

PROBE_TABLE = "gold_access_probe"


@dataclass(frozen=True)
class ProbeResult:
    sa: str
    probe: str
    outcome: str  # "denied" | "allowed" | "error"; v3 slice 2 adds "ok" | "failed" for checks that must pass
    detail: str


def is_denied(exc: BaseException) -> bool:
    """Forbidden, or a BadRequest that reports a refused permission (BigQuery reports some DML refusals that way)."""
    from google.api_core import exceptions as api

    if isinstance(exc, api.Forbidden):
        return True
    return isinstance(exc, api.BadRequest) and ("Access Denied" in str(exc) or "Permission" in str(exc))


def probe(sa: str, probe: str, action: Callable[[], None], undo: Callable[[], None] | None = None) -> ProbeResult:
    """An expected-denied action: "denied" if is_denied(its exception), "allowed" (then undone) if it succeeds, else "error"."""
    try:
        action()
    except Exception as exc:  # noqa: BLE001 -- every outcome is a result
        return ProbeResult(sa, probe, "denied" if is_denied(exc) else "error", f"{type(exc).__name__}: {exc}"[:500])
    detail = "ALLOWED"
    if undo is not None:
        try:
            undo()
            detail = "ALLOWED (undone)"
        except Exception as exc:  # noqa: BLE001 -- reported, never raised
            detail = f"ALLOWED, undo failed: {exc}"
    return ProbeResult(sa, probe, "allowed", detail)


_probe = probe  # the v3 slice 1 name


def check(sa: str, name: str, fn: Callable[[], str | None]) -> ProbeResult:
    """A check that must pass (v3 slice 2): "ok" if fn returns None, "failed" with the message it returns, "error" on an
    exception."""
    try:
        message = fn()
    except Exception as exc:  # noqa: BLE001 -- every outcome is a result
        return ProbeResult(sa, name, "error", f"{type(exc).__name__}: {exc}"[:500])
    return ProbeResult(sa, name, "ok", "ok") if message is None else ProbeResult(sa, name, "failed", message[:500])


def run_access_checks(make_client: Callable[[str], object], *, gold_sa: str, scorer_sa: str, monitor_sa: str) -> list[ProbeResult]:
    """Every probe's result; a client that cannot be built (no ADC, a bad email) is an "error" probe, never a raise."""
    results = []
    try:
        client = make_client(gold_sa)
    except Exception as exc:  # noqa: BLE001 -- e.g. no ADC: recorded, the other accounts are still probed
        results.append(ProbeResult(gold_sa, "build client", "error", f"{type(exc).__name__}: {exc}"[:500]))
        client = None
    probe_id = table_id(SERVING_DATASET, PROBE_TABLE)
    if client is not None:
        results.extend(_publisher_probes(client, gold_sa, probe_id))
    gold = quoted(table_id(SERVING_DATASET, "gold"))
    for sa in (scorer_sa, monitor_sa):
        try:
            reader = make_client(sa)
        except Exception as exc:  # noqa: BLE001 -- recorded as an error probe
            results.append(ProbeResult(sa, "build client", "error", f"{type(exc).__name__}: {exc}"[:500]))
            continue
        results.append(
            _probe(
                sa,
                "read serving.gold",
                lambda reader=reader: reader.query(
                    f"SELECT COUNT(*) AS n FROM {gold} WHERE `index_stop` >= TIMESTAMP '0001-01-01'",  # noqa: S608 -- static identifier
                    job_config=query_config(),
                ).result(),
            )
        )
    return results


def _publisher_probes(client, gold_sa: str, probe_id: str) -> list[ProbeResult]:
    from google.cloud import bigquery

    results = []
    results.append(
        _probe(
            gold_sa,
            "create a table in serving",
            lambda: client.create_table(bigquery.Table(probe_id, schema=[bigquery.SchemaField("x", "STRING")])),
            lambda: client.delete_table(probe_id, not_found_ok=True),
        )
    )
    predictions = quoted(table_id(SERVING_DATASET, "predictions"))
    results.append(
        _probe(
            gold_sa,
            "change serving.predictions",
            # matches no row even if allowed
            lambda: client.query(
                f"DELETE FROM {predictions} WHERE `as_of_date` = DATE '1900-01-01'",  # noqa: S608 -- static identifier
                job_config=query_config(),
            ).result(),
        )
    )
    results.append(_probe(gold_sa, "read serving.scoring_runs", lambda: client.get_table(table_id(SERVING_DATASET, "scoring_runs"))))
    return results


def exit_code(results: list[ProbeResult]) -> int:
    """3 if any probe was allowed or any check failed (a FAIL, wins), else 5 if any errored, else 0."""
    if any(r.outcome in ("allowed", "failed") for r in results):
        return 3
    if any(r.outcome == "error" for r in results):
        return 5
    return 0
