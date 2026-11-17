"""Per-site signals computed exactly like psa.fajaede.nl does, so benchmark percentiles
compare like with like:
- psa_score: port of app/api/scan/route.ts + lib/psa-score.ts (psa repo)
- ai_checks / ai_score: port of lib/generative-visibility.ts (psa repo)
- security_score: share of modern security headers (benchmark-only)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

AI_CRAWLERS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-SearchBot",
               "PerplexityBot", "Google-Extended", "Applebot-Extended", "Bytespider"]


# --- PSA ----------------------------------------------------------------------------------

def psa_levels(html: str, final_url: str, start_url: str, headers: Mapping[str, str]) -> dict[str, str]:
    lower = html.lower()
    has_trackers = any(s in lower for s in ("google-analytics", "gtag(", "fbq(", "hotjar"))
    has_banner = any(s in lower for s in ("cookiebot", "complianz", "borlabs", "cookieyes"))
    has_privacy_link = bool(
        re.search(r"<a[^>]*href=[^>]*>([^<]*(privacy|cookie|voorwaarden|gdpr|avg|policy)[^<]*)</a>", html, re.I)
        or re.search(r"href=[\"'][^\"']*(privacy|cookie|voorwaarden|gdpr|avg|policy)[\"']", html, re.I)
    )
    privacy = "P2"
    if has_trackers and not has_banner:
        privacy = "P1"
    elif not has_privacy_link:
        privacy = "P1"
    elif has_banner:
        privacy = "P3"

    lowered = {k.lower(): v for k, v in headers.items()}
    is_https = start_url.startswith("https://") or final_url.startswith("https://")
    has_cms_leak = re.search(r'<meta[^>]*name="generator"[^>]*content="([^"]*)"', html, re.I) is not None
    security = "S2"
    if not is_https or has_cms_leak:
        security = "S1"
    elif "strict-transport-security" in lowered and "x-frame-options" in lowered:
        security = "S3"

    adult_meta = 'name="rating" content="adult"' in lower or 'name="rating" content="rta' in lower
    adult_words = sum(1 for kw in ("gokken", "casino", "porn", "xxx", "escort", "18+", "betting", "onlyfans") if kw in lower)
    age = "A3" if adult_meta or adult_words >= 2 else "A2" if adult_words == 1 else "A1"
    return {"privacy": privacy, "security": security, "age": age}


def psa_score(levels: Mapping[str, str]) -> int:
    score = 100
    if levels.get("security") == "S1":
        score -= 30
    if levels.get("privacy") == "P1":
        score -= 20
    if levels.get("age") == "A3":
        score -= 10
    return score


# --- security headers (benchmark only) ----------------------------------------------------

SECURITY_HEADERS = ["strict-transport-security", "content-security-policy", "x-frame-options",
                    "x-content-type-options", "referrer-policy", "permissions-policy"]


def security_score(final_url: str, headers: Mapping[str, str]) -> int:
    if not final_url.startswith("https://"):
        return 0
    lowered = {k.lower() for k in headers.keys()}
    present = sum(1 for name in SECURITY_HEADERS if name in lowered)
    return round(40 + 60 * present / len(SECURITY_HEADERS))  # HTTPS alone = 40


# --- AI-zichtbaarheid -----------------------------------------------------------------------

def structured_types(html: str) -> set[str]:
    types: set[str] = set()

    def collect(value: Any, depth: int = 0) -> None:
        if depth > 20 or not isinstance(value, (dict, list)):
            return
        if isinstance(value, list):
            for item in value:
                collect(item, depth + 1)
            return
        raw = value.get("@type")
        if isinstance(raw, str):
            types.add(raw.lower())
        elif isinstance(raw, list):
            types.update(item.lower() for item in raw if isinstance(item, str))
        for nested in value.values():
            collect(nested, depth + 1)

    for match in re.finditer(r"<script\b[^>]*type=[\"']application/ld\+json[\"'][^>]*>([\s\S]*?)</script\s*>", html, re.I):
        try:
            collect(json.loads(match.group(1)))
        except (ValueError, RecursionError):
            continue
    return types


def blocked_ai_crawlers(robots_text: str) -> list[str]:
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    has_rules = False
    for raw in robots_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        directive, value = (part.strip() for part in line.split(":", 1))
        directive = directive.lower()
        if directive == "user-agent":
            if current is None or has_rules:
                if current is not None:
                    groups.append(current)
                current = {"agents": [], "disallows_root": False, "allows_root": False}
                has_rules = False
            current["agents"].append(value.lower())
        elif current is not None and directive in {"disallow", "allow"}:
            has_rules = True
            if value == "/":
                current["disallows_root" if directive == "disallow" else "allows_root"] = True
    if current is not None:
        groups.append(current)

    blocked = []
    for crawler in AI_CRAWLERS:
        specific = [g for g in groups if crawler.lower() in g["agents"]]
        applicable = specific or [g for g in groups if "*" in g["agents"]]
        if any(g["disallows_root"] and not g["allows_root"] for g in applicable):
            blocked.append(crawler)
    return blocked


def ai_checks(html: str, robots: tuple[int, str] | None, llms: tuple[int, str] | None) -> dict[str, str]:
    """code -> 'pass' | 'warning' | 'unknown' (same outcomes as psa)."""
    checks: dict[str, str] = {}
    if robots is None:
        checks["ai-crawlers"] = "unknown"
    elif robots[0] == 404:
        checks["ai-crawlers"] = "pass"
    elif robots[0] != 200:
        checks["ai-crawlers"] = "unknown"
    else:
        checks["ai-crawlers"] = "warning" if blocked_ai_crawlers(robots[1]) else "pass"

    if llms and llms[0] == 200 and llms[1].strip():
        checks["llms-txt"] = "pass"
    elif llms and llms[0] not in {200, 404}:
        checks["llms-txt"] = "unknown"
    else:
        checks["llms-txt"] = "warning"

    types = structured_types(html)
    useful = {"organization", "localbusiness", "article", "blogposting", "faqpage", "webpage", "product"}
    checks["structured-data"] = "pass" if types & useful else "warning"

    headings = len(re.findall(r"<h[2-6]\b", html.lower()))
    has_faq = "faqpage" in types or re.search(r"\b(faq|frequently asked questions|veelgestelde vragen)\b", html, re.I)
    checks["answer-structure"] = "pass" if headings >= 2 or has_faq else "warning"

    has_author = ("person" in types or re.search(r"<meta\b[^>]*name=[\"']author[\"']", html, re.I)
                  or re.search(r"\"author\"\s*:", html, re.I))
    has_date = re.search(r"\"datePublished\"\s*:", html, re.I) or re.search(r"<time\b[^>]*datetime=", html, re.I)
    checks["source-transparency"] = "pass" if has_author or has_date else "warning"
    return checks


def ai_score(checks: Mapping[str, str]) -> int | None:
    judged = [status for status in checks.values() if status in {"pass", "warning"}]
    if not judged:
        return None
    return round(sum(1 for status in judged if status == "pass") * 100 / len(judged))
