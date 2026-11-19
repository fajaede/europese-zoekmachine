"""Benchmark endpoints.

Admin (X-Admin-Key):
  POST /api/benchmark/domains      {"source": "index"|"tranco"|"list", "limit": 5000, "domains": [...]}
  GET  /api/benchmark/status
  POST /api/benchmark/recalculate
  GET  /api/benchmark/queue
  GET  /api/benchmark/index-stats
psa.fajaede.nl (X-Benchmark-Key = BENCHMARK_API_KEY):
  GET  /api/benchmark/compare?domain=voorbeeld.nl&psa=82&seo=67&geo=40&ai=50
  GET  /api/benchmark/competitors?domain=voorbeeld.nl&psa=82&seo=67&geo=40&ai=50&limit=3
       (+ "distribution": sites per score 0-100 for overall/psa/seo/geo/ai, groups of 30+;
          "your_authority" / entry "authority": Common Crawl authority 0-100, see authority.py;
          "your_referring_domains" / entry "referring_domains": see backlinks.py)
  GET  /api/benchmark/backlinks?domain=voorbeeld.nl   (authority + referring domains + strongest .nl/.be linkers)
"""

from __future__ import annotations

import asyncio
import hmac
import os
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.benchmark import authority, backlinks, rules, store, worker
from backend.benchmark.competitors import find_competitors

router = APIRouter(prefix="/api/benchmark", tags=["benchmark"])

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


def _read(fn: Any) -> Any:
    store.init()
    with store.connect() as conn:
        return fn(conn)


@router.get("/queue")
async def queue(request: Request) -> dict[str, Any]:
    """Jobs per status, next jobs, failure reasons (24h), throughput and domains in backoff."""
    _check_key(request, "ADMIN_API_KEY", "X-Admin-Key")
    return await asyncio.to_thread(_read, store.queue_overview)


@router.get("/index-stats")
async def index_stats(request: Request) -> dict[str, Any]:
    """Coverage and freshness of the benchmark index, unchanged-page share, cohort sizes."""
    _check_key(request, "ADMIN_API_KEY", "X-Admin-Key")
    return await asyncio.to_thread(_read, store.index_stats)


def compare_scores(rows: list[Any], domain: str, scores: dict[str, int], category: str | None) -> dict[str, Any]:
    """Percentile per metric in the most specific cohort that has at least rules.MIN_COHORT_SIZE sites
    and real spread (rules.has_spread). A metric without such a cohort is left out."""
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
            if len(values) >= rules.MIN_COHORT_SIZE and rules.has_spread(values):
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


@router.get("/competitors")
async def competitors(request: Request, domain: str, category: str | None = None, psa: int | None = None,
                      seo: int | None = None, geo: int | None = None, ai: int | None = None,
                      limit: int = 3) -> dict[str, Any]:
    """Sites in the same cohort that score just above you, the leader, and where they are ahead."""
    _check_key(request, "BENCHMARK_API_KEY", "X-Benchmark-Key")
    scores = {k: v for k, v in {"psa": psa, "seo": seo, "geo": geo, "ai": ai}.items() if v is not None}
    if any(not 0 <= value <= 100 for value in scores.values()) or not 1 <= limit <= 10:
        raise HTTPException(status_code=422, detail="Scores must be 0-100 and limit 1-10")

    def _rows() -> list[Any]:
        store.init()
        with store.connect() as conn:
            return [dict(row) for row in store.latest_scores(conn)]

    result = find_competitors(await asyncio.to_thread(_rows), rules.root_domain(domain), scores, category, limit)
    result["cohort_label"] = cohort_label(result["cohort"]) if result["cohort"] else None
    add_authority(result)
    return result


def add_authority(result: dict[str, Any]) -> None:
    """Common Crawl authority (0-100, share of the country's domains with fewer/weaker links) for
    you and each named competitor; None when unknown or not imported yet."""
    entries = [e for e in (*result.get("ahead", []), result.get("leader"), result.get("behind")) if e]
    domains = [result["domain"], *(e["domain"] for e in entries)]
    found = authority.lookup(domains)
    links = backlinks.counts(domains)
    score = lambda domain: (found.get(domain) or {}).get("score")
    result["your_authority"] = score(result["domain"])
    result["your_referring_domains"] = links.get(result["domain"])
    for entry in entries:
        entry["authority"] = score(entry["domain"])
        entry["referring_domains"] = links.get(entry["domain"])


@router.get("/backlinks")
async def backlink_profile(request: Request, domain: str) -> dict[str, Any]:
    """Authority and referring domains for one domain, from the Common Crawl web graph.
    Fields are null when the domain is unknown or the import has not run."""
    _check_key(request, "BENCHMARK_API_KEY", "X-Benchmark-Key")
    root = rules.root_domain(domain)
    if not root or "." not in root:
        raise HTTPException(status_code=422, detail="Invalid domain")

    def _read() -> dict[str, Any]:
        info: dict[str, Any] = {}
        try:
            with authority.connect() as conn:
                info = authority.meta(conn)
        except Exception:
            pass
        found = authority.lookup([root]).get(root) or {}
        links = backlinks.lookup(root) or {}
        return {
            "domain": root,
            "authority": found.get("score"),
            "authority_rank": found.get("rank"),
            "authority_of": found.get("of"),
            "referring_domains": links.get("referring_domains", 0 if info.get("last_backlinks") else None),
            "referring_local": links.get("referring_local", 0 if info.get("last_backlinks") else None),
            "top_referring": links.get("top", []),
            "release": (info.get("last_backlinks") or info.get("last_import") or {}).get("release"),
        }

    return await asyncio.to_thread(_read)


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
