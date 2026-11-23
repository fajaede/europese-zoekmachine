"""Fajaede Cloud for the FajaedeSEO AI WordPress plugin.

Access with short-lived "API tickets" instead of API keys: the licence server (fajaede.nl, where the
licences live) signs a ticket with Ed25519 after checking the licence and its site; this server only holds
the PUBLIC key, so a compromise here cannot mint tickets. A ticket names one domain and one plan, and every
answer is limited to that domain.

Ticket: base64url(payload JSON) "." base64url(signature over the payload bytes)
payload: {"v": 1, "domain": "example.nl", "plan": "free|pro|premium|agency|enterprise",
          "lic": licence id or null, "iat": unix time, "exp": unix time}

  GET /api/plugin/authority   Authorization: Fajaede-Ticket <ticket>
      free: authority score of the domain; pro and higher: also referring domains and the strongest
      .nl/.be referrers (Common Crawl web graph, see backend/benchmark/authority.py and backlinks.py),
      plus "fajaede_links": links from sites Common Crawl does not know, from our own link index
      (benchmark.db `links`, filled by FajaedeBot and by reports below). Never counted twice.

  POST /api/plugin/report-link   {"url": "https://andere-site.nl/pagina"}   (pro and higher)
      "Missing a link?": FajaedeScan fetches that page (robots.txt respected, public addresses only) and
      stores the link only when the page really links to the ticket domain.

  POST /api/plugin/site-audit    start a full site audit of the ticket domain (pro and higher; limits per plan)
  GET  /api/plugin/site-audit    the latest audit (status, report) and the remaining quota
      The audit crawls the site from outside as FajaedeScan (backend/benchmark/site_audit.py).

  GET  /api/plugin/competitors   sites in your sector that score just above/below you, the leader, and
      where they are ahead (pro and higher; from the Fajaede benchmark, like psa.fajaede.nl)

  GET  /api/plugin/history       authority per Common Crawl release, benchmark scores over time and new links
      per month (pro and higher)
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sqlite3
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from backend.benchmark import authority, backlinks, ccdomains, rules, site_audit, store, worker
from backend.benchmark.competitors import METRICS as COMPETITOR_METRICS, find_competitors
from backend.benchmark.competitors import overall as competitor_overall
from backend.benchmark.routes import _latest_rows, add_authority, cohort_label
from backend.seo_geo_audit import SCAN_TOKEN, SCAN_USER_AGENT, validate_public_url

router = APIRouter(prefix="/api/plugin", tags=["plugin"])

PLANS = ("free", "pro", "premium", "agency", "enterprise")
PAID = PLANS[1:]
MAX_LIFETIME = 13 * 3600          # tickets are issued for 12 hours; a little slack for clock skew
CLOCK_SKEW = 300
RATE_LIMIT = 120                   # requests per domain per hour (the plugin caches answers for a day)
COVERED_TLDS = ("nl", "be")
REPORTS_PER_DAY = 10              # "missing a link?" checks per site per day
# site audit per plan: (max pages, audits per window, window in days)
AUDIT_PLANS: dict[str, tuple[int, int, int]] = {
    "pro": (25, 2, 7), "premium": (100, 1, 1), "agency": (100, 3, 1), "enterprise": (100, 3, 1),
}
OWN_TOP = 10
_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_hits: dict[str, deque] = defaultdict(deque)
_reports: dict[str, deque] = defaultdict(deque)
_store_ready: str | None = None   # benchmark.db path whose tables exist


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def public_key() -> Ed25519PublicKey | None:
    raw = os.getenv("PLUGIN_TICKET_PUBLIC_KEY", "").strip()
    if not raw:
        return None
    try:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(raw))
    except ValueError:
        return None


def verify_ticket(header: str | None, now: float | None = None) -> dict[str, Any]:
    """The verified ticket payload; HTTPException 401 for anything that is not a valid, current ticket."""
    key = public_key()
    if key is None:
        raise HTTPException(status_code=503, detail="Fajaede Cloud is not configured.")
    if not header or not header.startswith("Fajaede-Ticket "):
        raise HTTPException(status_code=401, detail="Missing ticket.")
    token = header[len("Fajaede-Ticket "):].strip()
    try:
        payload_part, signature_part = token.split(".")
        payload_bytes, signature = _b64url_decode(payload_part), _b64url_decode(signature_part)
        key.verify(signature, payload_bytes)
        payload = json.loads(payload_bytes)
    except (ValueError, InvalidSignature, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="Invalid ticket.") from None
    now = time.time() if now is None else now
    iat, exp = payload.get("iat"), payload.get("exp")
    if (payload.get("v") != 1 or not isinstance(iat, int) or not isinstance(exp, int)
            or iat > now + CLOCK_SKEW or exp <= now or exp - iat > MAX_LIFETIME):
        raise HTTPException(status_code=401, detail="Ticket expired or not valid yet.")
    domain = str(payload.get("domain", "")).lower()
    if not _DOMAIN.match(domain) or payload.get("plan") not in PLANS:
        raise HTTPException(status_code=401, detail="Invalid ticket.")
    payload["domain"] = domain
    return payload


def _rate_limit(domain: str, now: float | None = None, *, window: int = 3600, limit: int | None = None,
                book: dict[str, deque] | None = None) -> None:
    now = time.time() if now is None else now
    hits = (_hits if book is None else book)[domain]
    while hits and hits[0] < now - window:
        hits.popleft()
    if len(hits) >= (RATE_LIMIT if limit is None else limit):
        raise HTTPException(status_code=429, detail="Too many requests for this site; try again later.")
    hits.append(now)


def _benchmark_store() -> None:
    """benchmark.db tables exist (the crawler may be switched off on this server)."""
    global _store_ready  # noqa: PLW0603
    if _store_ready != store.DB_PATH:
        store.init()
        _store_ready = store.DB_PATH


def own_links(domain: str) -> dict[str, Any] | None:
    """Links to `domain` from the own index, minus sources that are (by now) in the Common Crawl graph:
    those are already in the Common Crawl count. None when the index is not available."""
    try:
        _benchmark_store()
        with store.connect() as conn:
            rows = [dict(row) for row in store.own_backlinks(conn, domain)]
    except sqlite3.Error:
        return None
    # any extension, but never a site Common Crawl already counts (.nl/.be via the ranks, others via the
    # Bloom filter of the whole graph; without that filter other extensions are left out)
    in_cc = ccdomains.counted_by_common_crawl(row["source_domain"] for row in rows)
    rows = [row for row in rows if row["source_domain"] not in in_cc]
    return {
        "referring_domains": len(rows),
        "top": [{"domain": row["source_domain"], "url": row["sample_url"], "first_seen": row["first_seen_at"][:10],
                 "nofollow": bool(row["nofollow"]), "verified": bool(row["verified"])} for row in rows[:OWN_TOP]],
    }


def queue_site(domain: str, source: str, priority: int = 5) -> None:
    """Put a site in the crawl queue (no-op when it is known already)."""
    try:
        _benchmark_store()
        with store.connect() as conn:
            store.add_domains(conn, [{"domain": domain, "priority": priority}], source)
    except sqlite3.Error:
        pass


def authority_report(domain: str, plan: str) -> dict[str, Any]:
    """What the plugin shows in the "Links & authority" card (pure data, no secrets)."""
    covered = domain.rsplit(".", 1)[-1] in COVERED_TLDS
    found = authority.lookup([domain]).get(domain) if covered else None
    info: dict[str, Any] = {}
    try:
        with authority.connect() as conn:
            info = authority.meta(conn)
    except Exception:  # noqa: BLE001 - no data yet is not an error for the plugin
        info = {}
    release = (info.get("last_backlinks") or info.get("last_import") or {}).get("release")
    report: dict[str, Any] = {
        "domain": domain, "plan": plan, "covered": covered, "release": release,
        "authority": {"score": found["score"], "rank": found["rank"], "of": found["of"]} if found else None,
        "links": None, "locked": plan not in PAID,
    }
    links = (backlinks.lookup(domain) or {}) if covered else {}
    if found and release:
        try:
            authority.record_history(domain, release, found["score"], found["rank"],
                                     links.get("referring_domains"), links.get("referring_local"))
        except sqlite3.Error:
            pass                                       # history is a bonus: never fail the report for it
    if plan in PAID and covered:
        report["links"] = {
            "referring_domains": links.get("referring_domains", 0 if info.get("last_backlinks") else None),
            "referring_local": links.get("referring_local", 0 if info.get("last_backlinks") else None),
            "top": links.get("top", [])[:5],
        }
        report["fajaede_links"] = own_links(domain)
    return report


@router.get("/authority")
async def plugin_authority(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    ticket = verify_ticket(authorization)
    _rate_limit(ticket["domain"])
    if ticket["domain"].rsplit(".", 1)[-1] in COVERED_TLDS:
        await asyncio.to_thread(queue_site, ticket["domain"], "plugin")
    return await asyncio.to_thread(authority_report, ticket["domain"], ticket["plan"])


class LinkReport(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


async def check_reported_link(url: str, target: str) -> dict[str, Any]:
    """Fetch the reported page as FajaedeScan and store the link when it is really there.
    status: added | not_found | in_common_crawl | not_covered | own_site | blocked | unreachable | invalid"""
    url = url.strip()
    parts = urlsplit(url)
    source = worker.registered_domain(parts.hostname or "")
    if parts.scheme not in ("http", "https") or not source or "." not in source:
        return {"status": "invalid", "source": None}
    if source == target:
        return {"status": "own_site", "source": source}
    local = source.rsplit(".", 1)[-1] in COVERED_TLDS
    if not local and not await asyncio.to_thread(ccdomains.available):
        return {"status": "not_covered", "source": source}       # cannot rule out double counting yet
    if await asyncio.to_thread(ccdomains.counted_by_common_crawl, [source]):
        return {"status": "in_common_crawl", "source": source}
    origin = f"{parts.scheme}://{parts.netloc}"
    robots = await worker.fetch(f"{origin}/robots.txt", source, max_bytes=500_000, user_agent=SCAN_USER_AGENT)
    if robots and robots[0] == 200 and not rules.RobotsTxt(robots[1]).can_fetch(SCAN_TOKEN, url):
        return {"status": "blocked", "source": source}
    page = await worker.fetch(url, source, user_agent=SCAN_USER_AGENT)
    if not page or page[0] >= 400:
        return {"status": "unreachable", "source": source}
    links = worker.external_links(page[1], page[2], source)
    if target not in links:
        return {"status": "not_found", "source": source}

    def save() -> None:
        _benchmark_store()
        with store.connect() as conn:
            store.save_links(conn, source, {target: (page[2], links[target])}, verified=True)
            if local:   # the benchmark crawl stays .nl/.be: other sites are never queued
                store.add_domains(conn, [{"domain": source, "priority": 5}], "report")

    await asyncio.to_thread(save)
    return {"status": "added", "source": source, "nofollow": links[target]}


@router.post("/report-link")
async def plugin_report_link(body: LinkReport, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    ticket = verify_ticket(authorization)
    if ticket["plan"] not in PAID:
        raise HTTPException(status_code=403, detail="Reporting links is part of Pro and higher.")
    if ticket["domain"].rsplit(".", 1)[-1] not in COVERED_TLDS:
        raise HTTPException(status_code=400, detail="Only .nl and .be sites are covered.")
    _rate_limit(ticket["domain"], window=86400, limit=REPORTS_PER_DAY, book=_reports)
    return await check_reported_link(body.url, ticket["domain"])


# --- site audit -----------------------------------------------------------------------------

def _audit_owner(domain: str) -> str:
    return f"plugin:{domain}"


def audit_quota(domain: str, plan: str, now: datetime | None = None) -> dict[str, Any] | None:
    """Pages, limit and use of the current window; None for the free plan."""
    if plan not in AUDIT_PLANS:
        return None
    pages, limit, days = AUDIT_PLANS[plan]
    now = now or datetime.now(timezone.utc)
    used = site_audit.audits_since(_audit_owner(domain), now - timedelta(days=days))
    next_at = None
    if len(used) >= limit:
        next_at = (datetime.fromisoformat(used[0]) + timedelta(days=days)).isoformat()
    return {"max_pages": pages, "limit": limit, "window_days": days, "used": len(used),
            "remaining": max(0, limit - len(used)), "next_at": next_at}


def audit_state(domain: str, plan: str) -> dict[str, Any]:
    _benchmark_store()
    return {"domain": domain, "plan": plan, "locked": plan not in AUDIT_PLANS,
            "quota": audit_quota(domain, plan),
            "audit": site_audit.latest_audit(_audit_owner(domain)) if plan in AUDIT_PLANS else None}


@router.get("/site-audit")
async def plugin_site_audit_status(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    ticket = verify_ticket(authorization)
    _rate_limit(ticket["domain"])
    return await asyncio.to_thread(audit_state, ticket["domain"], ticket["plan"])


@router.post("/site-audit", status_code=202)
async def plugin_site_audit_start(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    ticket = verify_ticket(authorization)
    domain, plan = ticket["domain"], ticket["plan"]
    if plan not in AUDIT_PLANS:
        raise HTTPException(status_code=403, detail="The full site audit is part of Pro and higher.")
    _rate_limit(domain)
    state = await asyncio.to_thread(audit_state, domain, plan)
    current = state["audit"]
    if current and current["status"] in ("queued", "running"):
        return state                                  # one audit at a time: report the running one
    quota = state["quota"]
    if quota["remaining"] <= 0:
        raise HTTPException(status_code=429, detail={"message": "Audit limit reached for this period.",
                                                     "next_at": quota["next_at"]})
    try:
        url = await validate_public_url(f"https://{domain}/")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await asyncio.to_thread(site_audit.create_audit, url, quota["max_pages"], _audit_owner(domain))
    return await asyncio.to_thread(audit_state, domain, plan)


# --- competitors ----------------------------------------------------------------------------

def competitors_report(domain: str, plan: str) -> dict[str, Any]:
    """Your benchmark scores and the competitors around you. `measured` is False until FajaedeBot has
    crawled the site (plugin sites are queued with priority)."""
    covered = domain.rsplit(".", 1)[-1] in COVERED_TLDS
    base: dict[str, Any] = {"domain": domain, "plan": plan, "covered": covered, "locked": plan not in PAID,
                            "measured": False}
    if not covered:
        return base
    rows = _latest_rows()
    mine = next((row for row in rows if row["root_domain"] == domain), None)
    if mine is None:
        queue_site(domain, "plugin")
        return base
    scores = {param: mine[column] for param, column in COMPETITOR_METRICS.items() if mine[column] is not None}
    base.update(measured=True, measured_at=mine["calculated_at"],
                your_scores={param: round(value) for param, value in scores.items()})
    if base["locked"]:
        return base
    result = find_competitors(rows, domain, scores, mine["category"], limit=3)
    result.pop("distribution", None)
    result["cohort_label"] = cohort_label(result["cohort"]) if result["cohort"] else None
    add_authority(result)
    return {**base, **result}


@router.get("/competitors")
async def plugin_competitors(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    ticket = verify_ticket(authorization)
    _rate_limit(ticket["domain"])
    return await asyncio.to_thread(competitors_report, ticket["domain"], ticket["plan"])


# --- history --------------------------------------------------------------------------------

HISTORY_SCORES = 24      # benchmark measurements shown
HISTORY_MONTHS = 12      # months of new links


def _score_history(domain: str) -> list[dict[str, Any]]:
    _benchmark_store()
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT s.psa_score, s.seo_score, s.geo_score, s.ai_visibility_score, s.calculated_at FROM scores s"
            " JOIN domains d ON d.id = s.domain_id WHERE d.root_domain=? ORDER BY s.calculated_at DESC LIMIT ?",
            (domain, HISTORY_SCORES)).fetchall()
    points = []
    for row in reversed(rows):
        scores = {"psa": row["psa_score"], "seo": row["seo_score"], "geo": row["geo_score"], "ai": row["ai_visibility_score"]}
        points.append({"date": row["calculated_at"][:10], "overall": competitor_overall(scores),
                       **{k: (round(v) if v is not None else None) for k, v in scores.items()}})
    return points


def _new_links_per_month(domain: str, now: datetime | None = None) -> list[dict[str, Any]]:
    """New referring sites per month from the own index (the same sites the Links card counts)."""
    now = now or datetime.now(timezone.utc)
    months = []
    year, month = now.year, now.month
    for _ in range(HISTORY_MONTHS):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    months.reverse()
    _benchmark_store()
    with store.connect() as conn:
        rows = conn.execute("SELECT source_domain, first_seen_at FROM links WHERE target_domain=?", (domain,)).fetchall()
    in_cc = ccdomains.counted_by_common_crawl(r["source_domain"] for r in rows)
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        if row["source_domain"] not in in_cc:
            counts[row["first_seen_at"][:7]] += 1
    return [{"month": m, "count": counts.get(m, 0)} for m in months]


def history_report(domain: str, plan: str) -> dict[str, Any]:
    covered = domain.rsplit(".", 1)[-1] in COVERED_TLDS
    report: dict[str, Any] = {"domain": domain, "plan": plan, "covered": covered, "locked": plan not in PAID}
    if report["locked"] or not covered:
        return report
    report.update(authority=authority.history(domain), scores=_score_history(domain),
                  new_links=_new_links_per_month(domain))
    return report


@router.get("/history")
async def plugin_history(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    ticket = verify_ticket(authorization)
    _rate_limit(ticket["domain"])
    return await asyncio.to_thread(history_report, ticket["domain"], ticket["plan"])
