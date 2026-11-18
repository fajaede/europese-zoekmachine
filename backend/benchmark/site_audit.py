"""Site audit on request: crawls up to `max_pages` pages of one site (as FajaedeScan, robots.txt
respected) and reports site-wide issues: errors and broken links, redirects, duplicate or
missing titles/descriptions, H1 problems, thin and duplicate content, noindex, canonicals,
sitemap problems, slow pages, alt texts, language/viewport and structured data.

`build_report` is pure (no network) and holds all the rules, so it is fully unit-testable.
Jobs live in the `site_audits` table and run in their own loop (max 2 at a time)."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit

from backend.benchmark import rules, store, worker
from backend.seo_geo_audit import SCAN_TOKEN, SCAN_USER_AGENT, audit_seo_geo

MAX_PAGES_LIMIT = 100
LINK_CHECK_LIMIT = 50          # extra checks of internal links that were not crawled
SLOW_MS = 2000
THIN_WORDS = 200
URLS_PER_ISSUE = 500        # all affected URLs (the report pages show the first 20)
CONCURRENT_AUDITS = 2
KEEP_DAYS = 90

# code -> (severity, title, explanation); impact order = order of this table within a severity
ISSUES: dict[str, tuple[str, str, str]] = {
    "http_error": ("kritiek", "Pagina's met een foutcode",
                   "Deze pagina's geven een HTTP-fout (4xx/5xx). Bezoekers en zoekmachines komen op een foutpagina."),
    "broken_link": ("kritiek", "Gebroken interne links",
                    "Pagina's linken naar adressen op je eigen site die een fout geven. Herstel of verwijder de links."),
    "home_noindex": ("kritiek", "Homepage niet indexeerbaar",
                     "De homepage heeft een noindex-instructie: zoekmachines nemen je belangrijkste pagina niet op."),
    "missing_title": ("belangrijk", "Ontbrekende paginatitel", "Elke pagina hoort een unieke <title> te hebben."),
    "duplicate_title": ("belangrijk", "Dubbele paginatitels",
                        "Meerdere pagina's hebben dezelfde titel. Zoekmachines weten dan niet welke pagina waarover gaat."),
    "missing_description": ("belangrijk", "Ontbrekende meta-omschrijving",
                            "Zonder omschrijving kiest Google zelf een tekst voor het zoekresultaat."),
    "duplicate_description": ("belangrijk", "Dubbele meta-omschrijvingen",
                              "Meerdere pagina's delen dezelfde omschrijving; maak ze per pagina specifiek."),
    "missing_h1": ("belangrijk", "Geen H1-kop", "Een duidelijke H1 vertelt bezoekers en zoekmachines waar de pagina over gaat."),
    "noindex": ("belangrijk", "Pagina's met noindex",
                "Deze pagina's worden niet in zoekmachines opgenomen. Controleer of dat de bedoeling is."),
    "canonical_elsewhere": ("belangrijk", "Canonical wijst naar een andere pagina",
                            "Deze pagina's zeggen dat een andere URL de 'echte' versie is; ze worden zelf vaak niet getoond."),
    "duplicate_content": ("belangrijk", "Dubbele inhoud",
                          "Pagina's met dezelfde inhoud concurreren met elkaar. Voeg ze samen of gebruik een canonical."),
    "thin_content": ("belangrijk", "Weinig inhoud",
                     f"Pagina's met minder dan {THIN_WORDS} woorden hebben weinig kans om goed te scoren."),
    "redirect_link": ("belangrijk", "Interne links naar doorverwijzingen",
                      "Interne links gaan via een redirect. Link direct naar het eindadres; dat is sneller."),
    "sitemap_problem": ("belangrijk", "Sitemap ontbreekt of is ongeldig",
                        "Een geldige sitemap.xml helpt zoekmachines alle pagina's te vinden."),
    "slow_page": ("belangrijk", "Trage pagina's", f"Deze pagina's reageerden trager dan {SLOW_MS / 1000:.0f} seconden."),
    "robots_blocked": ("tip", "Door robots.txt geblokkeerde sitemap-pagina's",
                       "Deze pagina's staan in je sitemap maar robots.txt verbiedt crawlen. Haal ze uit één van beide."),
    "title_length": ("tip", "Titel te kort of te lang", "Een titel van 10 tot 60 tekens wordt volledig getoond."),
    "description_length": ("tip", "Omschrijving te kort of te lang", "Een omschrijving van 70 tot 160 tekens werkt het best."),
    "multiple_h1": ("tip", "Meerdere H1-koppen", "Gebruik één H1 per pagina en H2/H3 voor de onderdelen."),
    "image_alt": ("tip", "Afbeeldingen zonder alt-tekst",
                  "Alt-teksten helpen blinde bezoekers en zoekmachines te begrijpen wat er op een afbeelding staat."),
    "mobile_language": ("tip", "Viewport of taal ontbreekt", "Zonder viewport-tag en lang-attribuut werkt de pagina slechter op mobiel en in zoekmachines."),
    "no_structured_data": ("tip", "Geen gestructureerde data",
                           "Schema.org-gegevens (JSON-LD) helpen zoekmachines en AI-assistenten je inhoud te begrijpen."),
}
SEVERITY_ORDER = {"kritiek": 0, "belangrijk": 1, "tip": 2}
_H1 = re.compile(r"<h1\b", re.I)


def _norm(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return f"{parts.scheme}://{(parts.hostname or '').lower()}{path}" + (f"?{parts.query}" if parts.query else "")


def build_report(site: dict[str, Any], pages: list[dict[str, Any]], link_status: dict[str, int | None]) -> dict[str, Any]:
    """site: {url, sitemap_status, sitemap_valid, sitemap_urls, robots_blocked}
    pages: [{url, requested_url, status, response_ms, error, title, meta_description, h1_count, word_count,
             robots_directive, canonical_url, content_hash, seo_score, failed_checks: [codes], links: [urls]}]
    link_status: status per extra-checked internal link (None = unreachable)."""
    found: dict[str, list[str]] = defaultdict(list)

    def add(code: str, url: str) -> None:
        if url not in found[code]:
            found[code].append(url)

    status_by_url: dict[str, int | None] = {}
    redirected: set[str] = set()
    for page in pages:
        status_by_url[_norm(page["url"])] = page.get("status")
        requested = page.get("requested_url") or page["url"]
        if _norm(requested) != _norm(page["url"]):
            redirected.add(_norm(requested))
    for url, status in link_status.items():
        status_by_url.setdefault(_norm(url), status)

    home = _norm(site["url"])
    ok_pages = [p for p in pages if (p.get("status") or 0) < 400 and p.get("status")]
    titles: dict[str, list[str]] = defaultdict(list)
    descriptions: dict[str, list[str]] = defaultdict(list)
    hashes: dict[str, list[str]] = defaultdict(list)

    for page in pages:
        url = page["url"]
        status = page.get("status")
        if status is None or status >= 400:
            add("http_error", url)
            continue
        noindex = "noindex" in (page.get("robots_directive") or "").lower()
        if noindex:
            add("home_noindex" if _norm(url) == home else "noindex", url)
        title = (page.get("title") or "").strip()
        if not title:
            add("missing_title", url)
        else:
            titles[title.lower()].append(url)
            if not 10 <= len(title) <= 60:
                add("title_length", url)
        description = (page.get("meta_description") or "").strip()
        if not description:
            add("missing_description", url)
        else:
            descriptions[description.lower()].append(url)
            if not 70 <= len(description) <= 160:
                add("description_length", url)
        h1 = page.get("h1_count") or 0
        if h1 == 0:
            add("missing_h1", url)
        elif h1 > 1:
            add("multiple_h1", url)
        if (page.get("word_count") or 0) < THIN_WORDS and not noindex:
            add("thin_content", url)
        if page.get("content_hash"):
            hashes[page["content_hash"]].append(url)
        canonical = page.get("canonical_url")
        if canonical and _norm(canonical) != _norm(url):
            add("canonical_elsewhere", url)
        if (page.get("response_ms") or 0) > SLOW_MS:
            add("slow_page", url)
        failed = set(page.get("failed_checks") or [])
        if "image_alt" in failed:
            add("image_alt", url)
        if "mobile_language" in failed:
            add("mobile_language", url)
        if "structured_data" in failed:
            add("no_structured_data", url)
        for link in page.get("links") or []:
            target = _norm(link)
            target_status = status_by_url.get(target, 200)
            if target_status is None or target_status >= 400:
                add("broken_link", f"{url} → {link}")
            elif target in redirected:
                add("redirect_link", f"{url} → {link}")

    for group, code in ((titles, "duplicate_title"), (descriptions, "duplicate_description"), (hashes, "duplicate_content")):
        for urls in group.values():
            if len(urls) > 1:
                for url in urls:
                    add(code, url)
    if not site.get("sitemap_valid"):
        add("sitemap_problem", site.get("sitemap_url") or f"{site['url'].rstrip('/')}/sitemap.xml")
    for url in site.get("robots_blocked") or []:
        add("robots_blocked", url)

    issues = []
    for code, urls in found.items():
        severity, title, explanation = ISSUES[code]
        issues.append({"code": code, "severity": severity, "title": title, "explanation": explanation,
                       "count": len(urls), "urls": urls[:URLS_PER_ISSUE]})
    order = list(ISSUES)
    issues.sort(key=lambda i: (SEVERITY_ORDER[i["severity"]], order.index(i["code"])))

    scored = [(p.get("seo_score"), 2 if _norm(p["url"]) == home else 1) for p in ok_pages if p.get("seo_score") is not None]
    weight = sum(w for _, w in scored)
    score = round(sum(s * w for s, w in scored) / weight) if weight else None
    page_issues: dict[str, list[str]] = defaultdict(list)
    for issue in issues:
        for entry in issue["urls"]:
            page_issues[entry.split(" → ", 1)[0]].append(issue["code"])
    times = [p["response_ms"] for p in ok_pages if p.get("response_ms")]
    return {
        "url": site["url"],
        "score": score,
        "summary": {
            "pages_crawled": len(pages),
            "pages_ok": len(ok_pages),
            "average_response_ms": round(sum(times) / len(times)) if times else None,
            "issues": {sev: sum(i["count"] for i in issues if i["severity"] == sev) for sev in SEVERITY_ORDER},
        },
        "issues": issues,
        "pages": [{"url": p["url"], "status": p.get("status"), "title": p.get("title"), "word_count": p.get("word_count"),
                   "seo_score": p.get("seo_score"), "response_ms": p.get("response_ms"),
                   "issues": sorted(set(page_issues.get(p["url"], [])))} for p in pages],
    }


# --- crawling -------------------------------------------------------------------------------

async def _fetch_timed(url: str, domain: str, **kwargs: Any) -> tuple[worker.FetchResult | None, int, list[str]]:
    reasons: list[str] = []
    started = time.perf_counter()
    result = await worker.fetch(url, domain, user_agent=SCAN_USER_AGENT, reasons=reasons, **kwargs)
    return result, round((time.perf_counter() - started) * 1000), reasons


def _page_record(requested: str, result: worker.FetchResult, response_ms: int, host: str) -> dict[str, Any]:
    status, html, final_url, headers = result
    record: dict[str, Any] = {"url": final_url, "requested_url": requested, "status": status, "response_ms": response_ms}
    if status >= 400 or "html" not in headers.get("content-type", "html").lower():
        return record
    audit = audit_seo_geo(html, final_url, response_status=status)
    fields = worker.page_fields(final_url, status, html, audit, worker.content_hash(html))
    record.update({
        "title": fields["title"], "meta_description": fields["meta_description"],
        "h1_count": len(_H1.findall(html)), "word_count": fields["word_count"],
        "robots_directive": fields["robots_directive"],
        "canonical_url": fields["canonical_url"] and urljoin(final_url, fields["canonical_url"]),
        "content_hash": fields["content_hash"], "seo_score": audit["seo_score"],
        "failed_checks": [c["code"] for c in audit["scoring_breakdown"]["seo"] if c["passed"] is False],
        "links": sorted(worker.internal_links(html, final_url, host)),
    })
    return record


async def crawl_site(url: str, max_pages: int) -> dict[str, Any]:
    """Crawl and analyse one site. Raises ValueError with a Dutch reason when the homepage fails."""
    max_pages = max(1, min(MAX_PAGES_LIMIT, max_pages))
    domain = rules.root_domain(url)
    home, home_ms, reasons = await _fetch_timed(url, domain)
    if not home:
        raise ValueError(f"De website kon niet worden opgehaald ({reasons[0] if reasons else 'onbekende fout'}).")
    status, html, final_url, _headers = home
    if status in {401, 403, 429, 503}:
        raise ValueError(f"De website weigert onze scanner (HTTP {status}). Zie psa.fajaede.nl/bot.")
    host = urlsplit(final_url).hostname or domain
    origin = f"{urlsplit(final_url).scheme}://{urlsplit(final_url).netloc}"

    robots_res, _, _ = await _fetch_timed(f"{origin}/robots.txt", domain, max_bytes=500_000)
    robots_text = robots_res[1] if robots_res and robots_res[0] == 200 else ""
    parser = rules.RobotsTxt(robots_text) if robots_text else None
    sitemap, sitemap_status, sitemap_valid = await worker.read_sitemaps(origin, domain, robots_text, user_agent=SCAN_USER_AGENT)

    pages = [await asyncio.to_thread(_page_record, url, home, home_ms, host)]
    frontier = await asyncio.to_thread(worker.build_frontier, final_url, html, sitemap, host, parser, {}, False,
                                       None, SCAN_TOKEN)
    seen = {_norm(final_url)}
    attempts = 0
    while len(pages) < max_pages and attempts < max_pages * 2:
        next_url = frontier.pop()
        if next_url is None:
            break
        attempts += 1
        await asyncio.sleep(worker.PAGE_DELAY_SECONDS)
        result, ms, reasons = await _fetch_timed(next_url, domain)
        if result is None:
            if reasons and reasons[0].startswith("redirect"):
                continue                      # left the site: not a page of this site
            pages.append({"url": next_url, "requested_url": next_url, "status": None, "error": reasons[0] if reasons else None})
            continue
        if _norm(result[2]) in seen:
            continue                          # redirect to a page we already have
        seen.add(_norm(result[2]))
        record = await asyncio.to_thread(_page_record, next_url, result, ms, host)
        pages.append(record)
        frontier.link_from(record["url"], set(record.get("links") or []))

    crawled = {_norm(p["url"]) for p in pages} | {_norm(p.get("requested_url") or p["url"]) for p in pages}
    unchecked = sorted({link for p in pages for link in p.get("links") or [] if _norm(link) not in crawled
                        and rules.is_crawlable_url(link)})[:LINK_CHECK_LIMIT]
    link_status: dict[str, int | None] = {}
    for link in unchecked:
        result, _, reasons = await _fetch_timed(link, domain, max_bytes=300_000)
        if result is not None:
            link_status[link] = result[0]
        elif reasons and reasons[0] in {"failed: page too large", "redirect: other domain"}:
            link_status[link] = 200           # reachable, just big or moved to another site
        else:
            link_status[link] = None
    robots_blocked = [e.loc for e in sitemap if parser and not parser.can_fetch(SCAN_TOKEN, e.loc)][:URLS_PER_ISSUE]
    site = {"url": final_url, "sitemap_status": sitemap_status, "sitemap_valid": bool(sitemap_valid),
            "robots_blocked": robots_blocked}
    return await asyncio.to_thread(build_report, site, pages, link_status)


# --- jobs -----------------------------------------------------------------------------------

def create_audit(url: str, max_pages: int, owner: str) -> str:
    audit_id = uuid.uuid4().hex
    with store.connect() as conn:
        conn.execute("INSERT INTO site_audits (id, url, max_pages, owner, requested_at) VALUES (?, ?, ?, ?, ?)",
                     (audit_id, url, max(1, min(MAX_PAGES_LIMIT, max_pages)), owner, store.now_iso()))
    return audit_id


def get_audit(audit_id: str, owner: str | None = None) -> dict[str, Any] | None:
    """The audit as API response; `owner` restricts access (None = any, for internal use)."""
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM site_audits WHERE id=?", (audit_id,)).fetchone()
    if row is None or (owner is not None and row["owner"] != owner):
        return None
    return {"id": row["id"], "url": row["url"], "max_pages": row["max_pages"], "status": row["status"],
            "requested_at": row["requested_at"], "finished_at": row["finished_at"], "error": row["error"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None}


def _claim_audit() -> dict[str, Any] | None:
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM site_audits WHERE status='queued' ORDER BY requested_at LIMIT 1").fetchone()
        if row is None:
            return None
        conn.execute("UPDATE site_audits SET status='running', started_at=? WHERE id=?", (store.now_iso(), row["id"]))
        return dict(row)


def _finish(audit_id: str, result: dict[str, Any] | None, error: str | None) -> None:
    with store.connect() as conn:
        conn.execute("UPDATE site_audits SET status=?, finished_at=?, result_json=?, error=? WHERE id=?",
                     ("done" if error is None else "failed", store.now_iso(),
                      json.dumps(result, ensure_ascii=False) if result is not None else None, error, audit_id))
        conn.execute("DELETE FROM site_audits WHERE requested_at < ?",
                     (store.now_iso(datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)),))


async def run_audit(job: dict[str, Any]) -> None:
    try:
        result = await asyncio.wait_for(crawl_site(job["url"], job["max_pages"]), timeout=15 * 60)
        await asyncio.to_thread(_finish, job["id"], result, None)
    except ValueError as exc:
        await asyncio.to_thread(_finish, job["id"], None, str(exc))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        await asyncio.to_thread(_finish, job["id"], None, f"De audit is mislukt ({type(exc).__name__}).")


async def run_audits_forever(stop: asyncio.Event | None = None, idle_seconds: float = 3) -> None:
    """Pool of CONCURRENT_AUDITS workers; audits that were running at a restart start again."""
    def _prepare() -> None:
        store.init()
        with store.connect() as conn:
            conn.execute("UPDATE site_audits SET status='queued' WHERE status='running'")

    await asyncio.to_thread(_prepare)
    lock = asyncio.Lock()

    async def audit_worker() -> None:
        while not (stop and stop.is_set()):
            async with lock:
                job = await asyncio.to_thread(_claim_audit)
            if job is None:
                await asyncio.sleep(idle_seconds)
                continue
            await run_audit(job)

    await asyncio.gather(*(audit_worker() for _ in range(CONCURRENT_AUDITS)))
