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
from typing import Any
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from backend.benchmark import authority, backlinks, rules, store, worker
from backend.seo_geo_audit import SCAN_TOKEN, SCAN_USER_AGENT

router = APIRouter(prefix="/api/plugin", tags=["plugin"])

PLANS = ("free", "pro", "premium", "agency", "enterprise")
PAID = PLANS[1:]
MAX_LIFETIME = 13 * 3600          # tickets are issued for 12 hours; a little slack for clock skew
CLOCK_SKEW = 300
RATE_LIMIT = 120                   # requests per domain per hour (the plugin caches answers for a day)
COVERED_TLDS = ("nl", "be")
REPORTS_PER_DAY = 10              # "missing a link?" checks per site per day
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
    in_cc = authority.known(row["source_domain"] for row in rows)
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
    if plan in PAID and covered:
        links = backlinks.lookup(domain) or {}
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
    status: added | not_found | in_common_crawl | own_site | blocked | unreachable | invalid"""
    url = url.strip()
    parts = urlsplit(url)
    source = worker.registered_domain(parts.hostname or "")
    if parts.scheme not in ("http", "https") or not source or "." not in source:
        return {"status": "invalid", "source": None}
    if source == target:
        return {"status": "own_site", "source": source}
    if await asyncio.to_thread(authority.known, [source]):
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
