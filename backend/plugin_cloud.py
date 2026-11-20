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
      .nl/.be referrers (Common Crawl web graph, see backend/benchmark/authority.py and backlinks.py).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from collections import defaultdict, deque
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Header, HTTPException

from backend.benchmark import authority, backlinks

router = APIRouter(prefix="/api/plugin", tags=["plugin"])

PLANS = ("free", "pro", "premium", "agency", "enterprise")
PAID = PLANS[1:]
MAX_LIFETIME = 13 * 3600          # tickets are issued for 12 hours; a little slack for clock skew
CLOCK_SKEW = 300
RATE_LIMIT = 120                   # requests per domain per hour (the plugin caches answers for a day)
COVERED_TLDS = ("nl", "be")
_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_hits: dict[str, deque] = defaultdict(deque)


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


def _rate_limit(domain: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    hits = _hits[domain]
    while hits and hits[0] < now - 3600:
        hits.popleft()
    if len(hits) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests for this site; try again later.")
    hits.append(now)


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
    return report


@router.get("/authority")
async def plugin_authority(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    ticket = verify_ticket(authorization)
    _rate_limit(ticket["domain"])
    return await asyncio.to_thread(authority_report, ticket["domain"], ticket["plan"])
