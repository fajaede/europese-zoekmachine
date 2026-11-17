"""Benchmark crawler: crawls a few priority pages per domain and stores scores.

Domain scores (PSA, SEO, GEO, AI, security) come from the HOMEPAGE, because psa.fajaede.nl
also scores one URL: percentiles then compare like with like. The other pages are stored
for site-wide statistics (content, indexability, structure)."""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

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


def page_fields(url: str, status: int, html: str, audit: dict[str, Any]) -> dict[str, Any]:
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


def pick_pages(home_url: str, home_html: str, sitemap: list[rules.SitemapEntry], host: str,
               robots: RobotFileParser | None, limit: int) -> list[str]:
    """Highest-priority crawlable URLs on the same host, homepage excluded."""
    candidates: dict[str, float] = {}
    for entry in sitemap:
        candidates[entry.loc] = rules.url_priority(entry.loc, sitemap_priority=entry.priority, lastmod=entry.lastmod,
                                                   depth=max(0, urlsplit(entry.loc).path.strip("/").count("/")))
    soup = BeautifulSoup(home_html, "html.parser")
    for link in soup.find_all("a", href=True):
        url = urljoin(home_url, str(link["href"])).split("#", 1)[0]
        if url not in candidates:
            candidates[url] = rules.url_priority(url, depth=1)
    home_key = home_url.rstrip("/")
    allowed = [
        (score, url) for url, score in candidates.items()
        if (urlsplit(url).hostname or "").lower().removeprefix("www.") == host.removeprefix("www.")
        and url.rstrip("/") != home_key and rules.is_crawlable_url(url)
        and (robots is None or robots.can_fetch(USER_AGENT, url))
    ]
    allowed.sort(key=lambda item: -item[0])
    return [url for _, url in allowed[: max(0, limit - 1)]]


async def crawl_domain(domain_row: dict[str, Any]) -> dict[str, Any]:
    """Crawl one domain. Returns {category, language, scores, pages, error}."""
    domain = domain_row["root_domain"]
    host = domain_row["hostname"] or domain
    result: dict[str, Any] = {"category": domain_row["category"], "language": None, "scores": None, "pages": [], "error": None}

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
    parser: RobotFileParser | None = None
    if robots_tuple and robots_tuple[0] == 200:
        parser = RobotFileParser()
        parser.parse(robots_tuple[1].splitlines())
        if not parser.can_fetch(USER_AGENT, final_url):
            result["error"] = "blocked: robots.txt"
            return result
    llms_res = await fetch(f"{origin}/llms.txt", domain, max_bytes=200_000)
    sitemap, sitemap_status, sitemap_valid = await read_sitemaps(origin, domain, robots_tuple[1] if robots_tuple else "")

    audit_kwargs = {"robots_status": robots_tuple[0] if robots_tuple else None, "sitemap_status": sitemap_status,
                    "sitemap_valid": sitemap_valid, "robots_allowed": True}

    def analyse_home() -> tuple[str, dict[str, Any], dict[str, Any], list[str]]:
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
        urls = pick_pages(final_url, html, sitemap, urlsplit(final_url).hostname or host, parser,
                          rules.page_limit(category))
        return category, page_fields(final_url, status, html, home_audit), scores, urls

    category, home_page, scores, urls = await asyncio.to_thread(analyse_home)
    result.update(category=category, language=home_page["language"], scores=scores)
    result["pages"].append(home_page)

    hashes = {hashlib.sha256(html.encode("utf-8", "replace")).hexdigest()}
    for url in urls:
        await asyncio.sleep(PAGE_DELAY_SECONDS)
        page = await fetch(url, domain)
        if not page or "html" not in page[3].get("content-type", "").lower():
            continue
        digest = hashlib.sha256(page[1].encode("utf-8", "replace")).hexdigest()
        if digest in hashes:  # duplicate content
            continue
        hashes.add(digest)
        p_status, p_html, p_url = page[0], page[1], page[2]
        result["pages"].append(await asyncio.to_thread(
            lambda: page_fields(p_url, p_status, p_html, audit_seo_geo(p_html, p_url, response_status=p_status, **audit_kwargs))
        ))
    return result


def _store_result(job: dict[str, Any], outcome: dict[str, Any]) -> None:
    with store.connect() as conn:
        for page in outcome["pages"]:
            store.save_page(conn, job["id"], page)
        store.finish_job(conn, job["job_id"], job["id"], category=outcome["category"], language=outcome["language"],
                         scores=outcome["scores"], pages_crawled=len(outcome["pages"]), error=outcome["error"])


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


async def run_forever(stop: asyncio.Event | None = None) -> None:
    """Background loop: crawl due domains CONCURRENCY at a time; recalculate percentiles hourly."""
    await asyncio.to_thread(store.init)

    def _reset() -> None:
        with store.connect() as conn:
            store.reset_stale_jobs(conn)

    await asyncio.to_thread(_reset)
    last_recalc = 0.0
    while not (stop and stop.is_set()):
        loop_time = asyncio.get_running_loop().time()
        recalc = loop_time - last_recalc > 3600
        await asyncio.to_thread(_housekeeping, recalc)
        if recalc:
            last_recalc = loop_time
        jobs = await asyncio.to_thread(_claim, CONCURRENCY)
        if not jobs:
            await asyncio.sleep(60)
            continue

        async def run(job: dict[str, Any]) -> None:
            try:
                outcome = await asyncio.wait_for(crawl_domain(job), timeout=300)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                outcome = {"category": job["category"], "language": None, "scores": None, "pages": [],
                           "error": f"failed: {type(exc).__name__}"}
            await asyncio.to_thread(_store_result, job, outcome)

        await asyncio.gather(*(run(job) for job in jobs))


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


def parse_tranco(zip_bytes: bytes, limit: int, tlds: tuple[str, ...] = (".nl", ".be")) -> list[dict[str, Any]]:
    """Top-ranked domains with the given TLDs; higher rank = higher crawl priority."""
    out = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        with archive.open(archive.namelist()[0]) as handle:
            for raw in io.TextIOWrapper(handle, encoding="utf-8"):
                rank, _, domain = raw.strip().partition(",")
                if domain.endswith(tlds):
                    out.append({"domain": domain, "priority": max(1, 40 - len(out) // 250)})
                    if len(out) >= limit:
                        break
    return out


async def domains_from_tranco(limit: int) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=120, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(TRANCO_URL)
        response.raise_for_status()
        return await asyncio.to_thread(parse_tranco, response.content, limit)


