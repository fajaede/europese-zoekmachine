"""Tests for the site audit: report rules (pure), crawling a fake site, jobs and access."""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx

from backend.benchmark import site_audit, store, worker


def page(url, **kw):
    base = {"url": url, "requested_url": url, "status": 200, "response_ms": 300, "title": f"Titel van {url[-12:]} pagina",
            "meta_description": f"Een duidelijke omschrijving van {url} die lang genoeg is om in Google te tonen.",
            "h1_count": 1, "word_count": 600, "robots_directive": None, "canonical_url": url,
            "content_hash": url, "seo_score": 80, "failed_checks": [], "links": []}
    base.update(kw)
    return base


SITE = {"url": "https://x.nl/", "sitemap_valid": True, "robots_blocked": []}


def codes(report):
    return {i["code"]: i for i in report["issues"]}


class ReportTests(unittest.TestCase):
    def test_clean_site_has_no_issues(self):
        report = site_audit.build_report(SITE, [page("https://x.nl/"), page("https://x.nl/a")], {})
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["score"], 80)
        self.assertEqual(report["summary"]["pages_ok"], 2)

    def test_detects_site_wide_problems(self):
        pages = [
            page("https://x.nl/", links=["https://x.nl/weg", "https://x.nl/oud", "https://x.nl/extern-404"],
                 seo_score=100),
            page("https://x.nl/weg", status=404),
            page("https://x.nl/nieuw", requested_url="https://x.nl/oud"),                    # redirect
            page("https://x.nl/a", title="Zelfde titel hier", content_hash="h1"),
            page("https://x.nl/b", title="Zelfde titel hier", content_hash="h1", word_count=50),
            page("https://x.nl/c", meta_description="", h1_count=0, robots_directive="noindex,follow",
                 canonical_url="https://x.nl/a", failed_checks=["image_alt", "structured_data"], response_ms=3500),
            page("https://x.nl/d", h1_count=3, title="Kort"),
        ]
        report = site_audit.build_report({**SITE, "sitemap_valid": False, "robots_blocked": ["https://x.nl/geheim"]},
                                         pages, {"https://x.nl/extern-404": 404})
        found = codes(report)
        self.assertEqual(found["http_error"]["urls"], ["https://x.nl/weg"])
        self.assertEqual(set(found["broken_link"]["urls"]),
                         {"https://x.nl/ → https://x.nl/weg", "https://x.nl/ → https://x.nl/extern-404"})
        self.assertEqual(found["redirect_link"]["urls"], ["https://x.nl/ → https://x.nl/oud"])
        self.assertEqual(set(found["duplicate_title"]["urls"]), {"https://x.nl/a", "https://x.nl/b"})
        self.assertEqual(set(found["duplicate_content"]["urls"]), {"https://x.nl/a", "https://x.nl/b"})
        self.assertEqual(found["thin_content"]["urls"], ["https://x.nl/b"])
        for code in ("missing_description", "missing_h1", "noindex", "canonical_elsewhere", "slow_page",
                     "image_alt", "no_structured_data"):
            self.assertEqual(found[code]["urls"], ["https://x.nl/c"], code)
        self.assertEqual(found["multiple_h1"]["urls"], ["https://x.nl/d"])
        self.assertEqual(found["title_length"]["urls"], ["https://x.nl/d"])
        self.assertIn("sitemap_problem", found)
        self.assertEqual(found["robots_blocked"]["urls"], ["https://x.nl/geheim"])
        # critical issues first, noindex pages are not called "thin"
        self.assertEqual(report["issues"][0]["severity"], "kritiek")
        self.assertNotIn("https://x.nl/c", found["thin_content"]["urls"])
        # homepage counts double in the score: (100*2 + 80*5) / 7
        self.assertEqual(report["score"], 86)
        self.assertEqual(report["summary"]["issues"]["kritiek"], 3)

    def test_homepage_noindex_is_critical(self):
        found = codes(site_audit.build_report(SITE, [page("https://x.nl/", robots_directive="noindex")], {}))
        self.assertEqual(found["home_noindex"]["severity"], "kritiek")
        self.assertNotIn("noindex", found)

    def test_url_normalisation(self):
        # trailing slash and host case do not create false broken links or duplicates
        pages = [page("https://X.nl/", links=["https://x.nl/a/"]), page("https://x.nl/a")]
        self.assertNotIn("broken_link", codes(site_audit.build_report(SITE, pages, {})))


def fake_site(pages):
    async def fetch(url, domain, *, max_bytes=0, reasons=None, user_agent=None, **_):
        assert "FajaedeScan" in (user_agent or ""), "audits run as FajaedeScan"
        if url not in pages:
            return (404, "", url, httpx.Headers({"content-type": "text/html"}))
        status, body, ctype, final = (*pages[url], url)[:4] if len(pages[url]) == 3 else pages[url]
        return (status, body, final, httpx.Headers({"content-type": ctype}))
    return fetch


class CrawlTests(unittest.TestCase):
    def test_crawl_fake_site(self):
        words = "woord " * 400
        home = (f"<html lang='nl'><title>Home van de site</title><h1>Home</h1>{words}"
                '<a href="/diensten">d</a><a href="/oud">o</a><a href="/kapot">k</a><a href="/geheim">g</a></html>')
        pages = {
            "https://x.nl/": (200, home, "text/html"),
            "https://x.nl/robots.txt": (200, "User-agent: *\nDisallow: /geheim\n", "text/plain"),
            "https://x.nl/diensten": (200, f"<title>Diensten</title><h1>D</h1>{words}", "text/html"),
            "https://x.nl/oud": (200, f"<title>Nieuw</title><h1>N</h1>{words}", "text/html", "https://x.nl/nieuw"),
            "https://x.nl/kapot": (404, "niet gevonden", "text/html"),
        }
        with patch.object(worker, "fetch", fake_site(pages)), patch.object(worker, "PAGE_DELAY_SECONDS", 0):
            report = asyncio.run(site_audit.crawl_site("https://x.nl/", 10))
        urls = [p["url"] for p in report["pages"]]
        self.assertNotIn("https://x.nl/geheim", urls)                       # robots.txt respected
        self.assertIn("https://x.nl/nieuw", urls)
        found = codes(report)
        self.assertIn("https://x.nl/ → https://x.nl/kapot", found["broken_link"]["urls"])
        self.assertIn("https://x.nl/ → https://x.nl/oud", found["redirect_link"]["urls"])
        self.assertIn("sitemap_problem", found)                              # no sitemap.xml

    def test_blocked_homepage_raises_clear_error(self):
        with patch.object(worker, "fetch", fake_site({"https://x.nl/": (403, "", "text/html")})):
            with self.assertRaisesRegex(ValueError, "weigert onze scanner"):
                asyncio.run(site_audit.crawl_site("https://x.nl/", 5))


class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patcher = patch.object(store, "DB_PATH", os.path.join(self.tmp.name, "a.db"))
        self.patcher.start()
        store.init()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_lifecycle_and_owner_isolation(self):
        audit_id = site_audit.create_audit("https://x.nl/", 500, "dev:1")
        self.assertEqual(site_audit.get_audit(audit_id, "dev:1")["max_pages"], site_audit.MAX_PAGES_LIMIT)
        self.assertIsNone(site_audit.get_audit(audit_id, "dev:2"))          # someone else's audit
        self.assertIsNone(site_audit.get_audit(audit_id, "psa"))
        job = site_audit._claim_audit()
        self.assertEqual(job["id"], audit_id)
        self.assertIsNone(site_audit._claim_audit())
        site_audit._finish(audit_id, {"score": 77}, None)
        done = site_audit.get_audit(audit_id, "dev:1")
        self.assertEqual((done["status"], done["result"]["score"]), ("done", 77))

    def test_failed_audit_keeps_reason(self):
        audit_id = site_audit.create_audit("https://x.nl/", 5, "psa")
        job = site_audit._claim_audit()

        async def boom(url, max_pages):
            raise ValueError("De website weigert onze scanner (HTTP 403).")
        with patch.object(site_audit, "crawl_site", boom):
            asyncio.run(site_audit.run_audit(job))
        audit = site_audit.get_audit(audit_id)
        self.assertEqual((audit["status"], audit["error"]), ("failed", "De website weigert onze scanner (HTTP 403)."))


if __name__ == "__main__":
    unittest.main()
