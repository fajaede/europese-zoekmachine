"""Benchmark crawler: crawls a few priority pages per domain and stores scores.

Domain scores (PSA, SEO, GEO, AI, security) come from the HOMEPAGE, because psa.fajaede.nl
also scores one URL: percentiles then compare like with like. The other pages are stored
for site-wide statistics (content, indexability, structure)."""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import io
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from backend.benchmark import rules, signals, store
from backend.seo_geo_audit import USER_AGENT, audit_seo_geo, fetch_public_resource

PAGE_DELAY_SECONDS = float(os.getenv("BENCHMARK_PAGE_DELAY", "1.0"))   # politeness per domain
CONCURRENCY = int(os.getenv("BENCHMARK_CONCURRENCY", "4"))             # domains in parallel
MAX_SITEMAP_ENTRIES = 2000
MAX_CHILD_SITEMAPS = 5
TRANCO_URL = "https://tranco-list.eu/top-1m.csv.zip"

FetchResult = tuple[int, str, str, httpx.Headers]  # status, text, final_url, headers


def decode(body: bytes, headers: httpx.Headers) -> str:
    content_type = headers.get("content-type", "").lower()
    charset = "utf-8"
    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip(" \"'") or charset
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def failure_reason(exc: BaseException) -> str:
    """Short, groupable reason for the crawl report ('failed: dns', 'redirect: other domain', ...)."""
    text = str(exc).lower()
    if "left the allowed crawl domain" in text:
        return "redirect: other domain"
    if "could not be resolved" in text:
        return "failed: dns"
    if "private and local" in text:
        return "failed: private address"
    if "redirect limit" in text or "redirect without" in text:
        return "failed: redirect loop"
    if "size limit" in text:
        return "failed: page too large"
    if isinstance(exc, httpx.TimeoutException):
        return "failed: timeout"
    if "certificate" in text or "ssl" in text or "tls" in text:
        return "failed: tls certificate"
    if isinstance(exc, (httpx.ConnectError, OSError)):
        return "failed: connection refused"
    return f"failed: {type(exc).__name__}"


async def fetch(url: str, domain: str, *, max_bytes: int = 2_000_000,
                reasons: list[str] | None = None) -> FetchResult | None:
    """Safe fetch (public addresses only, redirects must stay on the domain). None on any
    failure; the reason is appended to `reasons` when given."""
    try:
        body, final_url, status, headers = await fetch_public_resource(
            url, timeout_seconds=10, max_bytes=max_bytes, allowed_domain=domain,
            headers={"Accept": "text/html,application/xhtml+xml,application/xml,text/plain;q=0.9,*/*;q=0.5"},
        )
    except (ValueError, httpx.HTTPError, OSError) as exc:
        if reasons is not None:
            reasons.append(failure_reason(exc))
        return None
    return status, decode(body, headers), final_url, headers


def page_fields(url: str, status: int, html: str, audit: dict[str, Any], digest: str | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", attrs={"name": lambda v: isinstance(v, str) and v.lower() == "description"})
    robots = soup.find("meta", attrs={"name": lambda v: isinstance(v, str) and v.lower() == "robots"})
    canonical = soup.find("link", rel=lambda v: v and "canonical" in [str(r).lower() for r in (v if isinstance(v, list) else [v])])
    html_tag = soup.find("html")
    h1 = soup.find("h1")
    return {
        "url": url,
        "http_status": status,
        "title": (soup.title.get_text(" ", strip=True) if soup.title else "")[:300] or None,
        "meta_description": (str(meta.get("content", "")).strip()[:500] or None) if meta else None,
        "h1": (h1.get_text(" ", strip=True)[:300] or None) if h1 else None,
        "word_count": len(soup.get_text(" ", strip=True).split()),
        "language": (str(html_tag.get("lang", "")).strip()[:20] or None) if html_tag else None,
        "canonical_url": (str(canonical.get("href", ""))[:1000] or None) if canonical else None,
        "robots_directive": (str(robots.get("content", ""))[:200] or None) if robots else None,
        "schema_types": ",".join(sorted(signals.structured_types(html))) or None,
        "seo_score": audit["seo_score"],
        "geo_score": audit["geo_score"],
        "content_hash": digest,
        "fetched_at": store.now_iso(),
    }


async def read_sitemaps(origin: str, domain: str, robots_text: str) -> tuple[list[rules.SitemapEntry], int | None, bool | None]:
    """Entries from robots.txt sitemaps or /sitemap.xml (one index level deep), plus the
    status/validity of the first sitemap for the SEO audit."""
    queue = rules.sitemaps_from_robots(robots_text) or [f"{origin}/sitemap.xml"]
    entries: list[rules.SitemapEntry] = []
    first_status: int | None = None
    first_valid: bool | None = None
    seen: set[str] = set()
    level_children: list[str] = []
    for level in range(2):
        for sitemap_url in (queue if level == 0 else level_children[:MAX_CHILD_SITEMAPS]):
            if sitemap_url in seen or len(entries) >= MAX_SITEMAP_ENTRIES:
                continue
            seen.add(sitemap_url)
            result = await fetch(sitemap_url, domain, max_bytes=5_000_000)
            if first_status is None:
                first_status = result[0] if result else None
            if not result or result[0] >= 400:
                continue
            found, children = rules.parse_sitemap(result[1])
            if first_valid is None:
                first_valid = bool(found or children)
            entries.extend(found[: MAX_SITEMAP_ENTRIES - len(entries)])
            level_children.extend(children)
    return entries, first_status, first_valid


_HREF = re.compile(r"""<a\b[^>]*?href\s*=\s*["']([^"'#]+)""", re.I)


def internal_links(html: str, base_url: str, host: str) -> set[str]:
    """Same-host links on a page (cheap regex, no full parse), fragments removed."""
    bare = host.lower().removeprefix("www.")
    links = set()
    for href in _HREF.findall(html):
        url = urljoin(base_url, href.strip()).split("#", 1)[0]
        if (urlsplit(url).hostname or "").lower().removeprefix("www.") == bare:
            links.add(url)
    return links


class Frontier:
    """Priority queue of candidate URLs for one domain. Scores use the static signals plus
    internal link authority, which grows while pages are crawled (lazy re-push in a heap)."""

    def __init__(self, home_url: str, host: str, robots: rules.RobotsTxt | None,
                 known: dict[str, dict[str, Any]], homepage_changed: bool,
                 last_crawled: datetime | None = None):
        self.home_key = home_url.rstrip("/")
        self.host = host
        self.robots = robots
        self.known = known
        self.homepage_changed = homepage_changed
        self.last_crawled = last_crawled
        self.signals: dict[str, dict[str, Any]] = {}
        self.inlinks: dict[str, int] = {}
        self.heap: list[tuple[float, str]] = []
        self.done: set[str] = set()

    def _allowed(self, url: str) -> bool:
        return (url.rstrip("/") != self.home_key and rules.is_crawlable_url(url)
                and (self.robots is None or self.robots.can_fetch(USER_AGENT, url)))

    def score(self, url: str) -> float:
        return rules.url_priority(url, inlinks=self.inlinks.get(url, 0), **self.signals[url])

    def add(self, url: str, *, from_sitemap: bool = False, priority: float | None = None,
            lastmod: datetime | None = None, depth: int = 1) -> None:
        if url in self.signals or url in self.done or not self._allowed(url):
            return
        previous = self.known.get(url) or {}
        self.signals[url] = {
            "sitemap_priority": priority, "lastmod": lastmod, "depth": depth,
            # "Sitemap changed": added or updated (lastmod) since this domain's previous crawl.
            "is_new": bool(from_sitemap and self.last_crawled and lastmod and lastmod > self.last_crawled),
            "previous_status": previous.get("http_status"), "previous_words": previous.get("word_count"),
            "homepage_changed": self.homepage_changed,
        }
        heapq.heappush(self.heap, (-self.score(url), url))

    def link_from(self, page_url: str, links: set[str]) -> None:
        """A crawled page links to these URLs: one more inlink each (once per page)."""
        for url in links:
            if url == page_url or url in self.done:
                continue
            if url not in self.signals:
                self.add(url, depth=2)
                if url not in self.signals:
                    continue
            self.inlinks[url] = self.inlinks.get(url, 0) + 1
            heapq.heappush(self.heap, (-self.score(url), url))

    def pop(self) -> str | None:
        while self.heap:
            negative, url = heapq.heappop(self.heap)
            if url in self.done:
                continue
            current = self.score(url)
            if -negative < current:   # stale entry: a fresher, higher one is in the heap
                continue
            self.done.add(url)
            return url
        return None


def build_frontier(home_url: str, home_html: str, sitemap: list[rules.SitemapEntry], host: str,
                   robots: rules.RobotsTxt | None, known: dict[str, dict[str, Any]],
                   homepage_changed: bool, last_crawled: datetime | None = None) -> Frontier:
    frontier = Frontier(home_url, host, robots, known, homepage_changed, last_crawled)
    bare = host.lower().removeprefix("www.")
    for entry in sitemap:
        if (urlsplit(entry.loc).hostname or "").lower().removeprefix("www.") == bare:
            frontier.add(entry.loc, from_sitemap=True, priority=entry.priority, lastmod=entry.lastmod,
                         depth=max(0, urlsplit(entry.loc).path.strip("/").count("/")))
    home_links = internal_links(home_html, home_url, host)
    for url in home_links:
        frontier.add(url, depth=1)
    frontier.link_from(home_url, home_links)
    return frontier


def pick_pages(home_url: str, home_html: str, sitemap: list[rules.SitemapEntry], host: str,
               robots: rules.RobotsTxt | None, limit: int) -> list[str]:
    """Static pick (no recrawl signals): the first limit-1 URLs of a fresh frontier."""
    frontier = build_frontier(home_url, home_html, sitemap, host, robots, {}, False)
    picked = []
    while len(picked) < max(0, limit - 1) and (url := frontier.pop()):
        picked.append(url)
    return picked


_JSONLD = re.compile(r"<script\b[^>]*application/ld\+json[^>]*>([\s\S]*?)</script\s*>", re.I)
_NOISE = re.compile(r"<(script|style|noscript|template|svg)\b[\s\S]*?</\1\s*>|<!--[\s\S]*?-->", re.I)
_META = re.compile(r"<meta\b[^>]*name=[\"'](description|robots)[\"'][^>]*>|<link\b[^>]*rel=[\"']canonical[\"'][^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")


def content_hash(html: str) -> str:
    """Fingerprint of what matters for the audit: visible text, title, description/robots/
    canonical tags and JSON-LD. Scripts, styles, comments and markup are ignored, so CMS
    noise (nonces, cache-busting, timestamps in scripts) does not count as a change."""
    parts = [m.strip() for m in _JSONLD.findall(html)]
    parts += [m.group(0) for m in _META.finditer(html)]
    text = _TAG.sub(" ", _NOISE.sub(" ", html))
    parts.append(" ".join(text.split()))
    return hashlib.sha256("\n".join(parts).encode("utf-8", "replace")).hexdigest()


async def crawl_domain(domain_row: dict[str, Any], known: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Crawl one domain. `known` = pages from the previous crawl (store.known_pages).
    Returns {category, language, scores, pages, unchanged, homepage_hash, error}: `pages` are
    analysed pages, `unchanged` URLs whose content hash did not change (no re-analysis)."""
    known = known or {}
    domain = domain_row["root_domain"]
    host = domain_row["hostname"] or domain
    result: dict[str, Any] = {"category": domain_row["category"], "language": None, "scores": None, "pages": [],
                              "unchanged": [], "homepage_hash": None, "error": None}

    home = None
    reasons: list[str] = []
    for start in (f"https://{host}/", f"http://{host}/"):
        home = await fetch(start, domain, reasons=reasons)
        if home:
            break
    if not home:
        # The HTTPS attempt says most (http:// often just redirects back to https).
        result["error"] = reasons[0] if reasons else "failed: homepage not reachable"
        return result
    status, html, final_url, headers = home
    if status in {401, 403, 429, 503}:
        result["error"] = f"blocked: HTTP {status}"
        return result
    if status >= 400 or "html" not in headers.get("content-type", "html").lower():
        result["error"] = f"failed: HTTP {status}"
        return result

    origin = f"{urlsplit(final_url).scheme}://{urlsplit(final_url).netloc}"
    robots_res = await fetch(f"{origin}/robots.txt", domain, max_bytes=500_000)
    robots_tuple = (robots_res[0], robots_res[1] if robots_res[0] == 200 else "") if robots_res else None
    parser: rules.RobotsTxt | None = None
    if robots_tuple and robots_tuple[0] == 200:
        parser = rules.RobotsTxt(robots_tuple[1])
        if not parser.can_fetch(USER_AGENT, final_url):
            result["error"] = "blocked: robots.txt"
            return result
    llms_res = await fetch(f"{origin}/llms.txt", domain, max_bytes=200_000)
    sitemap, sitemap_status, sitemap_valid = await read_sitemaps(origin, domain, robots_tuple[1] if robots_tuple else "")

    audit_kwargs = {"robots_status": robots_tuple[0] if robots_tuple else None, "sitemap_status": sitemap_status,
                    "sitemap_valid": sitemap_valid, "robots_allowed": True}

    home_hash = content_hash(html)
    homepage_changed = bool(domain_row.get("homepage_hash")) and domain_row.get("homepage_hash") != home_hash

    def analyse_home() -> tuple[str, dict[str, Any], dict[str, Any], Frontier]:
        # CPU work (HTML parsing) runs in a thread so the search API stays responsive.
        category = domain_row["category"]
        if category == "onbekend":
            recent_since = datetime.now(timezone.utc) - timedelta(days=3)
            recent = sum(1 for entry in sitemap if entry.lastmod and entry.lastmod >= recent_since)
            category = rules.classify(domain, html, signals.structured_types(html), len(sitemap), recent)
        home_audit = audit_seo_geo(html, final_url, response_status=status, **audit_kwargs)
        checks = signals.ai_checks(html, robots_tuple, (llms_res[0], llms_res[1]) if llms_res else None)
        levels = signals.psa_levels(html, final_url, f"https://{host}/", headers)
        scores = {
            "psa_score": signals.psa_score(levels),
            "seo_score": home_audit["seo_score"],
            "geo_score": home_audit["geo_score"],
            "ai_visibility_score": signals.ai_score(checks),
            "security_score": signals.security_score(final_url, headers),
        }
        last_crawled = None
        if domain_row.get("last_crawled_at"):
            last_crawled = datetime.fromisoformat(domain_row["last_crawled_at"])
        frontier = build_frontier(final_url, html, sitemap, urlsplit(final_url).hostname or host, parser,
                                  known, homepage_changed, last_crawled)
        return category, page_fields(final_url, status, html, home_audit, home_hash), scores, frontier

    category, home_page, scores, frontier = await asyncio.to_thread(analyse_home)
    result.update(category=category, language=home_page["language"], scores=scores, homepage_hash=home_hash)
    result["pages"].append(home_page)

    hashes = {home_hash}
    limit = rules.page_limit(category) - 1
    attempts = 0
    while len(result["pages"]) - 1 + len(result["unchanged"]) < limit and attempts < limit * 2:
        url = frontier.pop()
        if url is None:
            break
        attempts += 1
        await asyncio.sleep(PAGE_DELAY_SECONDS)
        page = await fetch(url, domain)
        if not page or "html" not in page[3].get("content-type", "").lower():
            continue
        p_status, p_html, p_url = page[0], page[1], page[2]
        digest = content_hash(p_html)
        if digest in hashes:  # duplicate content within this crawl
            continue
        hashes.add(digest)
        frontier.link_from(p_url, internal_links(p_html, p_url, frontier.host))
        previous = known.get(p_url)
        if previous and previous.get("content_hash") == digest:
            result["unchanged"].append(p_url)   # same content as last time: no re-analysis
            continue
        result["pages"].append(await asyncio.to_thread(
            lambda: page_fields(p_url, p_status, p_html,
                                audit_seo_geo(p_html, p_url, response_status=p_status, **audit_kwargs), digest)
        ))
    return result


def _store_result(job: dict[str, Any], outcome: dict[str, Any]) -> None:
    with store.connect() as conn:
        for page in outcome["pages"]:
            store.save_page(conn, job["id"], page)
        unchanged = outcome.get("unchanged", [])
        stamp = store.now_iso()
        for url in unchanged:
            store.touch_page(conn, job["id"], url, stamp)
        store.finish_job(conn, job["job_id"], job["id"], category=outcome["category"], language=outcome["language"],
                         scores=outcome["scores"], pages_crawled=len(outcome["pages"]) + len(unchanged),
                         pages_unchanged=len(unchanged), homepage_hash=outcome.get("homepage_hash"),
                         error=outcome["error"])


def _known(domain_id: int) -> dict[str, dict[str, Any]]:
    with store.connect() as conn:
        return store.known_pages(conn, domain_id)


def _claim(n: int) -> list[dict[str, Any]]:
    jobs = []
    with store.connect() as conn:
        for _ in range(n):
            row = store.claim_job(conn)
            if row is None:
                break
            jobs.append(dict(row))
    return jobs


def _housekeeping(recalculate: bool) -> None:
    with store.connect() as conn:
        store.schedule_due(conn)
        if recalculate:
            store.recalculate_benchmarks(conn)


async def process_job(job: dict[str, Any]) -> None:
    """Crawl one claimed job and store the outcome (never raises)."""
    try:
        known = await asyncio.to_thread(_known, job["id"])
        outcome = await asyncio.wait_for(crawl_domain(job, known), timeout=300)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        outcome = {"category": job["category"], "language": None, "scores": None, "pages": [],
                   "error": f"failed: {type(exc).__name__}"}
    await asyncio.to_thread(_store_result, job, outcome)


async def run_forever(stop: asyncio.Event | None = None, concurrency: int | None = None,
                      idle_seconds: float = 60) -> None:
    """Background crawler: a pool of `concurrency` workers that each take the next job as soon
    as they finish one (a slow site no longer holds up the others), plus a housekeeping loop
    that schedules due recrawls every minute and recalculates percentiles hourly."""
    await asyncio.to_thread(store.init)

    def _reset() -> None:
        with store.connect() as conn:
            store.reset_stale_jobs(conn)

    await asyncio.to_thread(_reset)
    claim_lock = asyncio.Lock()   # one claim at a time: two workers never take the same job

    def stopped() -> bool:
        return bool(stop and stop.is_set())

    async def housekeeping() -> None:
        last_recalc = 0.0
        while not stopped():
            loop_time = asyncio.get_running_loop().time()
            recalc = loop_time - last_recalc > 3600
            try:
                await asyncio.to_thread(_housekeeping, recalc)
                if recalc:
                    last_recalc = loop_time
            except Exception as exc:  # pylint: disable=broad-exception-caught
                print(f"Benchmark housekeeping failed: {type(exc).__name__}")
            await asyncio.sleep(idle_seconds)

    async def crawl_worker() -> None:
        while not stopped():
            async with claim_lock:
                jobs = await asyncio.to_thread(_claim, 1)
            if not jobs:
                await asyncio.sleep(idle_seconds)
                continue
            await process_job(jobs[0])

    workers = [asyncio.create_task(crawl_worker()) for _ in range(concurrency or CONCURRENCY)]
    keeper = asyncio.create_task(housekeeping())
    try:
        await asyncio.gather(keeper, *workers)
    finally:
        for task in (keeper, *workers):
            task.cancel()


# --- seeding -------------------------------------------------------------------------------

async def domains_from_index(meili_index: Any, limit: int) -> list[dict[str, Any]]:
    """Unique domains from the search index (the sites already crawled for the search engine)."""
    found: dict[str, dict[str, Any]] = {}
    offset = 0
    while len(found) < limit:
        page = await meili_index.get_documents(offset=offset, limit=1000, fields=["url"])
        if not page.results:
            break
        for doc in page.results:
            host = (urlsplit(str(doc.get("url", ""))).hostname or "").lower()
            domain = rules.root_domain(host)
            if domain and domain not in found:
                found[domain] = {"domain": domain, "hostname": host, "priority": 50}
        offset += len(page.results)
    return list(found.values())[:limit]


# Infrastructure domains in the Tranco list (DNS, CDN, ad/tracking servers): not websites.
_INFRA = re.compile(r"awsdns|cloudfront|akamai|edgekey|edgesuite|azureedge|fastly|doubleclick|"
                    r"(^|[.-])(cdn|dns|ntp\d*|ns\d+|adservice|tracking|telemetry)[.-]")


def is_infrastructure(domain: str) -> bool:
    return bool(_INFRA.search(domain.lower()))


def parse_tranco(zip_bytes: bytes, limit: int, tlds: tuple[str, ...] = (".nl", ".be")) -> list[dict[str, Any]]:
    """Top-ranked domains with the given TLDs; higher rank = higher crawl priority.
    Infrastructure domains (DNS/CDN/tracking) are skipped."""
    out = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        with archive.open(archive.namelist()[0]) as handle:
            for raw in io.TextIOWrapper(handle, encoding="utf-8"):
                _rank, _, domain = raw.strip().partition(",")
                if domain.endswith(tlds) and not is_infrastructure(domain):
                    out.append({"domain": domain, "priority": max(1, 40 - len(out) // 250)})
                    if len(out) >= limit:
                        break
    return out


async def domains_from_tranco(limit: int) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=120, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(TRANCO_URL)
        response.raise_for_status()
        return await asyncio.to_thread(parse_tranco, response.content, limit)


