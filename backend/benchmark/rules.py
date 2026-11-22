"""Pure rules for the benchmark crawl: categories, page limits, recrawl intervals,
URL priority, sitemap parsing and cohort names. No I/O, so everything is unit-testable."""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit


@dataclass(frozen=True)
class Category:
    label: str
    pages: int          # pages per domain in the benchmark
    recrawl_days: int   # fresh snapshot interval


CATEGORIES: dict[str, Category] = {
    "mkb": Category("Kleine bedrijven / MKB", 5, 30),
    "zakelijk": Category("Zakelijke dienstverlening", 10, 30),
    "saas": Category("SaaS / tech", 15, 14),
    "ecommerce": Category("E-commerce", 20, 7),
    "nieuws": Category("Nieuws / media", 20, 7),
    "publiek": Category("Overheid / onderwijs / zorg", 10, 30),
    "klein": Category("Kleine statische site", 5, 90),
    "onbekend": Category("Nog niet ingedeeld", 5, 30),
}
# Smallest cohort we report a percentile for (smaller cohorts fall back to a broader one).
MIN_COHORT_SIZE = int(os.getenv("BENCHMARK_MIN_COHORT", "30"))

# Growing pause after consecutive failures: index = number of earlier failures (last value repeats).
BACKOFF_DAYS: list[tuple[tuple[str, ...], list[int]]] = [
    (("blocked: HTTP 429", "blocked: HTTP 503"), [1, 3, 7, 30]),   # rate limited / overloaded: retry soon
    (("blocked",), [7, 30, 90]),                                   # 403, 401, robots.txt
    (("redirect",), [90]),                                         # alias of another domain
    (("failed",), [1, 7, 30, 90]),                                 # dns, timeout, tls, ...
]


def backoff_days(error: str, previous_failures: int) -> int:
    for prefixes, steps in BACKOFF_DAYS:
        if error.startswith(prefixes):
            return steps[min(previous_failures, len(steps) - 1)]
    return 30


def page_limit(category: str) -> int:
    return CATEGORIES.get(category, CATEGORIES["onbekend"]).pages


def recrawl_days(category: str) -> int:
    return CATEGORIES.get(category, CATEGORIES["onbekend"]).recrawl_days


def root_domain(value: str) -> str:
    """'https://www.Shop.nl/x' -> 'shop.nl' (host without www; no public-suffix magic)."""
    host = urlsplit(value if "://" in value else f"https://{value}").hostname or ""
    return host.lower().rstrip(".").removeprefix("www.")


def country_of(domain: str) -> str:
    """NL / BE from the TLD; everything else is international (INT)."""
    tld = domain.rsplit(".", 1)[-1].lower()
    return {"nl": "NL", "be": "BE"}.get(tld, "INT")


_PUBLIC = re.compile(
    r"(^|\.)(gov|overheid|rijksoverheid|gemeente|provincie|belgium|vlaanderen|wallonie|brussels)\.|"
    r"gemeente|ziekenhuis|zorg|huisarts|school|universiteit|hogeschool|\.ac\.be$|\.edu$|kuleuven|ugent|uva\.nl"
)
_NEWS_DOMAIN = re.compile(r"nieuws|news|krant|nos\.nl|vrt\.be|hln\.be|rtl|omroep|media")


NEWS_RECENT_PAGES = 100  # sitemap pages updated in the last 3 days that mark a news site


def classify(domain: str, html: str, schema_types: set[str], sitemap_url_count: int,
             recent_sitemap_pages: int = 0) -> str:
    """Rough category from domain, homepage and sitemap signals; good enough for cohorts.
    News is recognised by publishing pace: nu.nl updates hundreds of pages in 3 days,
    an active company blog a few dozen."""
    lower = html.lower()
    if _PUBLIC.search(domain):
        return "publiek"
    if (schema_types & {"newsarticle", "newsmediaorganization"}
            or (_NEWS_DOMAIN.search(domain) and ("<article" in lower or "rss" in lower))
            or recent_sitemap_pages >= NEWS_RECENT_PAGES
            or (recent_sitemap_pages >= 30 and lower.count("<time") >= 6)):
        return "nieuws"
    if schema_types & {"product", "offer", "aggregateoffer"} or re.search(
        r"winkelwagen|winkelmand|add to cart|in mijn mandje|shopping cart|/cart\b", lower
    ):
        return "ecommerce"
    if schema_types & {"softwareapplication", "webapplication"} or (
        re.search(r"\b(pricing|prijzen|tarieven)\b", lower) and re.search(r"\b(saas|software|api|platform|app)\b", lower)
    ):
        return "saas"
    if sitemap_url_count and sitemap_url_count < 15:
        return "klein"
    if schema_types & {"localbusiness", "professionalservice", "store", "restaurant"} or sitemap_url_count < 60:
        return "mkb"
    return "zakelijk"


def cohorts(country: str, category: str) -> list[str]:
    """A domain belongs to: its country+category, its category everywhere, its country, and all."""
    return [f"{country}:{category}", f"*:{category}", f"{country}:*", "*:*"]


def candidate_cohorts(rows: list[Any], domain: str, category: str | None) -> tuple[str, str | None, list[str]]:
    """Country, category and cohorts (most specific first) for a domain: taken from the benchmark
    when we crawled it, otherwise from its TLD and the category the caller gives (if valid)."""
    known = next((row for row in rows if row["root_domain"] == domain), None)
    country = known["country"] if known else country_of(domain)
    cat = known["category"] if known else (category if category in CATEGORIES else None)
    return country, cat, (cohorts(country, cat) if cat else [f"{country}:*", "*:*"])


# --- URL priority -------------------------------------------------------------------------

_SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|ico|zip|gz|mp4|mp3|css|js|xml|json|txt|docx?|xlsx?)$", re.I)
# WordPress system files: not pages of the site (login, API, installation docs)
_WP_SYSTEM = re.compile(r"/(wp-login\.php|wp-signup\.php|wp-activate\.php|wp-cron\.php|wp-trackback\.php|xmlrpc\.php"
                        r"|readme\.html|wp-json|wp-includes|wp-content/plugins)(/|$)", re.I)
_BAD_PATH = re.compile(r"/(wp-admin|wp-login|login|inloggen|account|cart|winkelwagen|checkout|afrekenen|zoeken|search)(/|$)", re.I)
_ARCHIVE = re.compile(r"/(tag|tags|category|categorie|author|auteur|page|pagina)/", re.I)
_SESSION = {"sessionid", "sid", "phpsessid", "utm_source", "utm_medium", "utm_campaign", "gclid", "fbclid",
            "sort", "order", "filter", "page", "p", "replytocom"}
_TYPES = [
    (re.compile(r"/(dienst|diensten|services?|oplossingen|solutions)(/|$)", re.I), 12),
    (re.compile(r"/(product|producten|products|shop|winkel|collections?)(/|$)", re.I), 10),
    (re.compile(r"/(contact|over-ons|over|about|about-us|team)(/|$)", re.I), 10),
    (re.compile(r"/(blog|nieuws|news|artikel|articles?|kennisbank)(/|$)", re.I), 8),
    (re.compile(r"/(prijzen|pricing|tarieven)(/|$)", re.I), 9),
]


def is_crawlable_url(url: str) -> bool:
    parts = urlsplit(url)
    return (parts.scheme in {"http", "https"} and not _SKIP_EXT.search(parts.path) and not _BAD_PATH.search(parts.path)
            and not _WP_SYSTEM.search(parts.path))


MAX_INLINK_BONUS = 15
_KEY_PAGES = re.compile(r"/(dienst|diensten|services?|product|producten|products|contact|prijzen|pricing)(/|$)", re.I)


def url_priority(url: str, *, sitemap_priority: float | None = None, lastmod: datetime | None = None,
                 depth: int = 1, now: datetime | None = None, is_new: bool = False,
                 previous_status: int | None = None, previous_words: int | None = None,
                 inlinks: int = 0, homepage_changed: bool = False) -> float:
    """Higher = crawl sooner. Homepage first; then valuable, recent, shallow, clean URLs.
    Recrawl signals: new in the sitemap (+15), earlier 404/410 (-60) or redirect (-20),
    earlier good page (+5), internal links from crawled pages (+3 each, max +15), and key
    pages when the homepage changed (+10)."""
    parts = urlsplit(url)
    path = parts.path or "/"
    if path in {"", "/"} and not parts.query:
        return 1000.0
    score = 0.0
    if is_new:
        score += 15
    if previous_status in {404, 410}:
        score -= 60
    elif previous_status in {301, 302, 307, 308}:
        score -= 20
    elif previous_status == 200 and (previous_words or 0) >= 300:
        score += 5
    score += min(MAX_INLINK_BONUS, 3 * max(0, inlinks))
    if homepage_changed and _KEY_PAGES.search(path):
        score += 10
    if sitemap_priority is not None:
        score += 20 * max(0.0, min(1.0, sitemap_priority))
    score += max(0, 15 - 5 * depth)
    for pattern, points in _TYPES:
        if pattern.search(path):
            score += points
            break
    if lastmod is not None:
        age_days = ((now or datetime.now(timezone.utc)) - lastmod).days
        score += 10 if age_days <= 30 else 5 if age_days <= 365 else 0
    if parts.query:
        keys = {key.lower() for key, _ in parse_qsl(parts.query, keep_blank_values=True)}
        score -= 30 + (20 if keys & _SESSION else 0)
    if _ARCHIVE.search(path):
        score -= 8
    score -= 2 * max(0, path.strip("/").count("/") - 2)  # very deep paths
    return score


# --- sitemaps -----------------------------------------------------------------------------

@dataclass(frozen=True)
class SitemapEntry:
    loc: str
    priority: float | None
    lastmod: datetime | None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    for candidate in (text, text[:10]):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_sitemap(xml_text: str) -> tuple[list[SitemapEntry], list[str]]:
    """Returns (page entries, child sitemap URLs). Invalid XML gives two empty lists."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], []
    kind = _local(root.tag)
    entries: list[SitemapEntry] = []
    children: list[str] = []
    for node in root:
        fields = {_local(child.tag): (child.text or "").strip() for child in node}
        loc = fields.get("loc")
        if not loc:
            continue
        if kind == "sitemapindex":
            children.append(loc)
            continue
        try:
            priority = float(fields["priority"]) if fields.get("priority") else None
        except ValueError:
            priority = None
        entries.append(SitemapEntry(loc, priority, _parse_date(fields.get("lastmod"))))
    return entries, children


def sitemaps_from_robots(robots_text: str) -> list[str]:
    return [line.split(":", 1)[1].strip() for line in robots_text.splitlines()
            if line.lower().startswith("sitemap:") and line.split(":", 1)[1].strip()]


# --- robots.txt (RFC 9309, as Google and Bing apply it) --------------------------------------

def _robots_pattern(pattern: str) -> re.Pattern[str]:
    """'*' = any characters, a trailing '$' = end of the URL path; everything else literal."""
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    return re.compile("".join(".*" if ch == "*" else re.escape(ch) for ch in body) + ("$" if anchored else ""))


def product_token(user_agent: str) -> str:
    """'FajaedeBot' from 'FajaedeBot/1.0 (...)' and from 'Mozilla/5.0 (compatible; FajaedeBot/1.0; ...)'."""
    match = re.search(r"compatible;\s*([^/;\s)]+)", user_agent)
    return (match.group(1) if match else user_agent.split("/", 1)[0]).strip().lower()


class RobotsTxt:
    """robots.txt rules per RFC 9309. Unlike urllib.robotparser it supports '*' and '$'
    wildcards and the most-specific-rule precedence: the longest matching pattern wins and
    Allow wins a tie. E.g. 'Allow: /$' + 'Disallow: /' allows only the homepage."""

    def __init__(self, text: str):
        self.groups: list[tuple[set[str], list[tuple[bool, str, re.Pattern[str]]]]] = []
        agents: set[str] | None = None
        rules_: list[tuple[bool, str, re.Pattern[str]]] = []
        in_rules = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            field, value = (part.strip() for part in line.split(":", 1))
            field = field.lower()
            if field == "user-agent":
                if agents is None or in_rules:
                    if agents is not None:
                        self.groups.append((agents, rules_))
                    agents, rules_, in_rules = set(), [], False
                agents.add(value.lower())
            elif field in {"allow", "disallow"} and agents is not None:
                in_rules = True
                if value:  # an empty Disallow allows everything: no rule
                    rules_.append((field == "allow", value, _robots_pattern(value)))
        if agents is not None:
            self.groups.append((agents, rules_))

    def _rules_for(self, user_agent: str) -> list[tuple[bool, str, re.Pattern[str]]]:
        token = product_token(user_agent)
        specific = [r for agents, rs in self.groups if token in agents for r in rs]
        if any(token in agents for agents, _ in self.groups):
            return specific
        return [r for agents, rs in self.groups if "*" in agents for r in rs]

    def can_fetch(self, user_agent: str, url: str) -> bool:
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: tuple[int, bool] | None = None
        for allow, pattern, regex in self._rules_for(user_agent):
            if regex.match(path):
                candidate = (len(pattern), allow)
                if best is None or candidate[0] > best[0] or (candidate[0] == best[0] and allow):
                    best = candidate
        return True if best is None else best[1]


MIN_GROUP_SIZE = 5


def percentile(score: float, sorted_group: list[float]) -> int | None:
    """Share of the group scoring lower (ties count half), 0-100. `sorted_group` must be
    sorted ascending. None for groups under MIN_GROUP_SIZE (too small to be meaningful)."""
    if len(sorted_group) < MIN_GROUP_SIZE:
        return None
    lower = bisect_left(sorted_group, score)
    ties = bisect_right(sorted_group, score) - lower
    return round((lower + ties / 2) * 100 / len(sorted_group))


MAX_TIE_SHARE = 0.7


def has_spread(sorted_group: list[float]) -> bool:
    """A percentile only means something when scores differ: if more than 70% of the group
    has the very same score (e.g. GEO 0 for sites without local-business data), skip it."""
    if not sorted_group:
        return False
    largest_tie = run = 1
    for previous, current in zip(sorted_group, sorted_group[1:]):
        run = run + 1 if current == previous else 1
        largest_tie = max(largest_tie, run)
    return largest_tie / len(sorted_group) <= MAX_TIE_SHARE


def rank(score: float, sorted_group: list[float]) -> int:
    """1 = best in the group."""
    return 1 + len(sorted_group) - bisect_right(sorted_group, score)
