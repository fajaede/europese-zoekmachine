"""Benchmark endpoints.

Admin (X-Admin-Key):
  POST /api/benchmark/domains      {"source": "index"|"tranco"|"list", "limit": 5000, "domains": [...]}
  GET  /api/benchmark/status
  POST /api/benchmark/recalculate
psa.fajaede.nl (X-Benchmark-Key = BENCHMARK_API_KEY):
  GET  /api/benchmark/compare?domain=voorbeeld.nl&psa=82&seo=67&geo=40&ai=50
"""

from __future__ import annotations

import asyncio
import hmac
import os
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.benchmark import rules, store, worker

router = APIRouter(prefix="/api/benchmark", tags=["benchmark"])

# Smallest group we report a percentile for; smaller cohorts fall back to a broader one.
MIN_COHORT_SIZE = int(os.getenv("BENCHMARK_MIN_COHORT", "30"))
METRIC_PARAMS = {"psa": "psa_score", "seo": "seo_score", "geo": "geo_score", "ai": "ai_visibility_score",
                 "security": "security_score", "overall": "overall_score"}


def _check_key(request: Request, env_name: str, header: str) -> None:
    expected = os.getenv(env_name)
    provided = request.headers.get(header)
    if not expected or not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Authentication required")


class DomainItem(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    category: str | None = None
    country: str | None = Field(default=None, max_length=3)


class AddDomains(BaseModel):
    source: Literal["index", "tranco", "list"]
    limit: int = Field(default=1000, ge=1, le=100_000)
    domains: list[DomainItem] = Field(default_factory=list, max_length=100_000)


@router.post("/domains")
async def add_domains(body: AddDomains, request: Request) -> dict[str, Any]:
    _check_key(request, "ADMIN_API_KEY", "X-Admin-Key")
    await asyncio.to_thread(store.init)
    if body.source == "index":
        meili_index = getattr(request.app.state, "meili_index", None)
        if meili_index is None:
            raise HTTPException(status_code=503, detail="Search index unavailable")
        items = await worker.domains_from_index(meili_index, body.limit)
    elif body.source == "tranco":
        items = await worker.domains_from_tranco(body.limit)
    else:
        items = [item.model_dump() for item in body.domains[: body.limit]]

    def _add() -> int:
        with store.connect() as conn:
            return store.add_domains(conn, items, body.source)

    added = await asyncio.to_thread(_add)
    return {"source": body.source, "offered": len(items), "added": added}


@router.get("/status")
async def benchmark_status(request: Request) -> dict[str, Any]:
    _check_key(request, "ADMIN_API_KEY", "X-Admin-Key")

    def _status() -> dict[str, Any]:
        store.init()
        with store.connect() as conn:
            return store.status(conn)

    return await asyncio.to_thread(_status)


@router.post("/recalculate")
async def recalculate(request: Request) -> dict[str, Any]:
    _check_key(request, "ADMIN_API_KEY", "X-Admin-Key")

    def _run() -> int:
        store.init()
        with store.connect() as conn:
            return store.recalculate_benchmarks(conn)

    return {"benchmarks_written": await asyncio.to_thread(_run)}


def compare_scores(rows: list[Any], domain: str, scores: dict[str, int], category: str | None) -> dict[str, Any]:
    """Percentile per metric in the most specific cohort that has at least MIN_COHORT_SIZE sites."""
    known = next((row for row in rows if row["root_domain"] == domain), None)
    country = known["country"] if known else rules.country_of(domain)
    cat = known["category"] if known else (category if category in rules.CATEGORIES else None)
    candidates = rules.cohorts(country, cat) if cat else [f"{country}:*", "*:*"]
    groups = store.cohort_groups(rows)
    newest = max((row["calculated_at"] for row in rows), default=None)
    results: dict[str, Any] = {}
    for param, metric in METRIC_PARAMS.items():
        if param not in scores:
            continue
        for cohort in candidates:
            values = groups.get((cohort, metric), [])
            if len(values) >= MIN_COHORT_SIZE:
                pct = rules.percentile(scores[param], values)
                results[param] = {
                    "score": scores[param], "percentile": pct, "top_percent": max(1, 100 - pct),
                    "rank": rules.rank(scores[param], values), "group_size": len(values), "cohort": cohort,
                    "cohort_label": cohort_label(cohort),
                }
                break
    return {"domain": domain, "country": country, "category": cat, "metrics": results, "data_as_of": newest}


def cohort_label(cohort: str) -> str:
    country, category = cohort.split(":", 1)
    where = {"NL": "Nederlandse", "BE": "Belgische", "INT": "internationale", "*": ""}.get(country, "")
    what = rules.CATEGORIES[category].label.lower() if category in rules.CATEGORIES else "websites"
    return " ".join(part for part in (where, what if category != "*" else "websites") if part)


@router.get("/compare")
async def compare(request: Request, domain: str, category: str | None = None, psa: int | None = None,
                  seo: int | None = None, geo: int | None = None, ai: int | None = None,
                  security: int | None = None, overall: int | None = None) -> dict[str, Any]:
    _check_key(request, "BENCHMARK_API_KEY", "X-Benchmark-Key")
    scores = {k: v for k, v in {"psa": psa, "seo": seo, "geo": geo, "ai": ai, "security": security,
                                "overall": overall}.items() if v is not None}
    if any(not 0 <= value <= 100 for value in scores.values()):
        raise HTTPException(status_code=422, detail="Scores must be 0-100")

    def _rows() -> list[Any]:
        store.init()
        with store.connect() as conn:
            return [dict(row) for row in store.latest_scores(conn)]

    rows = await asyncio.to_thread(_rows)
    return compare_scores(rows, rules.root_domain(domain), scores, category)
