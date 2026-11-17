"""Tests for the benchmark crawl (rules, signals, storage, crawler with fake sites)."""

import asyncio
import io
import os
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx

from backend.benchmark import rules, signals, store, worker
from backend.benchmark.routes import cohort_label, compare_scores

NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


class RulesTests(unittest.TestCase):
    def test_page_limits_per_category(self):
        self.assertEqual([rules.page_limit(c) for c in ("mkb", "zakelijk", "saas", "ecommerce", "nieuws", "publiek")],
                         [5, 10, 15, 20, 20, 10])
        self.assertEqual(rules.page_limit("bestaat-niet"), 5)

    def test_recrawl_intervals(self):
        self.assertEqual(rules.recrawl_days("nieuws"), 7)
        self.assertEqual(rules.recrawl_days("mkb"), 30)
        self.assertEqual(rules.recrawl_days("klein"), 90)

    def test_root_domain_and_country(self):
        self.assertEqual(rules.root_domain("https://WWW.Shop.nl/x?y=1"), "shop.nl")
        self.assertEqual(rules.root_domain("bakker.be"), "bakker.be")
        self.assertEqual([rules.country_of(d) for d in ("a.nl", "b.be", "c.com")], ["NL", "BE", "INT"])

    def test_priority_order(self):
        p = lambda url, **kw: rules.url_priority(url, now=NOW, **kw)  # noqa: E731
        self.assertEqual(p("https://x.nl/"), 1000)
        self.assertGreater(p("https://x.nl/diensten/"), p("https://x.nl/random/"))
        self.assertGreater(p("https://x.nl/blog/a", lastmod=NOW - timedelta(days=3)), p("https://x.nl/blog/a", lastmod=NOW - timedelta(days=900)))
        self.assertLess(p("https://x.nl/shop?sort=price"), p("https://x.nl/shop"))
        self.assertLess(p("https://x.nl/tag/seo/"), p("https://x.nl/seo/"))
        self.assertGreater(p("https://x.nl/a", sitemap_priority=1.0), p("https://x.nl/a", sitemap_priority=0.1))

    def test_crawlable_urls(self):
        self.assertTrue(rules.is_crawlable_url("https://x.nl/over-ons"))
        self.assertFalse(rules.is_crawlable_url("https://x.nl/brochure.pdf"))
        self.assertFalse(rules.is_crawlable_url("https://x.nl/winkelwagen"))
        self.assertFalse(rules.is_crawlable_url("mailto:info@x.nl"))

    def test_parse_sitemap_urlset_and_index(self):
        urlset = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://x.nl/a</loc><priority>0.8</priority><lastmod>2026-11-01</lastmod></url>
          <url><loc>https://x.nl/b</loc><priority>oops</priority></url></urlset>"""
        entries, children = rules.parse_sitemap(urlset)
        self.assertEqual([e.loc for e in entries], ["https://x.nl/a", "https://x.nl/b"])
        self.assertEqual(entries[0].priority, 0.8)
        self.assertEqual(entries[0].lastmod.year, 2026)
        self.assertIsNone(entries[1].priority)
        self.assertEqual(children, [])
        index = '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://x.nl/s1.xml</loc></sitemap></sitemapindex>'
        self.assertEqual(rules.parse_sitemap(index), ([], ["https://x.nl/s1.xml"]))
        self.assertEqual(rules.parse_sitemap("geen xml"), ([], []))
        self.assertEqual(rules.sitemaps_from_robots("User-agent: *\nSitemap: https://x.nl/sm.xml\n"), ["https://x.nl/sm.xml"])

    def test_classify(self):
        self.assertEqual(rules.classify("gemeente-utrecht.nl", "", set(), 500), "publiek")
        self.assertEqual(rules.classify("winkel.nl", "<button>In winkelwagen</button>", set(), 500), "ecommerce")
        self.assertEqual(rules.classify("x.nl", "", {"newsarticle"}, 500), "nieuws")
        self.assertEqual(rules.classify("tool.io", "Pricing for our SaaS platform", set(), 500), "saas")
        self.assertEqual(rules.classify("klein.nl", "", set(), 8), "klein")
        self.assertEqual(rules.classify("kapper.nl", "", {"localbusiness"}, 30), "mkb")
        self.assertEqual(rules.classify("bureau.nl", "", set(), 400), "zakelijk")
        # Publishing pace: hundreds of fresh sitemap pages = news; an active company blog is not.
        self.assertEqual(rules.classify("nu.nl", "", set(), 2000, recent_sitemap_pages=453), "nieuws")
        self.assertEqual(rules.classify("bureau.nl", "", set(), 72, recent_sitemap_pages=22), "zakelijk")

    def test_percentile_and_rank(self):
        group = sorted([10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
        self.assertEqual(rules.percentile(55, group), 50)
        self.assertEqual(rules.percentile(50, group), 45)       # tie counts half
        self.assertEqual(rules.rank(100, group), 1)
        self.assertEqual(rules.rank(5, group), 11)
        self.assertIsNone(rules.percentile(50, [1, 2, 3]))      # group too small

    def test_cohort_labels(self):
        self.assertEqual(rules.cohorts("NL", "mkb"), ["NL:mkb", "*:mkb", "NL:*", "*:*"])
        self.assertEqual(cohort_label("NL:mkb"), "Nederlandse kleine bedrijven / mkb")
        self.assertEqual(cohort_label("*:*"), "websites")
        self.assertEqual(cohort_label("BE:*"), "Belgische websites")


class SignalsTests(unittest.TestCase):
    https = "https://x.nl/"

    def test_psa_matches_psa_app(self):
        clean = '<a href="/privacy">Privacy</a><script src="cookiebot.js"></script>'
        levels = signals.psa_levels(clean, self.https, self.https, {"Strict-Transport-Security": "1", "X-Frame-Options": "DENY"})
        self.assertEqual(levels, {"privacy": "P3", "security": "S3", "age": "A1"})
        self.assertEqual(signals.psa_score(levels), 100)
        risky = '<meta name="generator" content="WordPress 6.0"><script>gtag("x")</script> casino betting'
        levels = signals.psa_levels(risky, self.https, self.https, {})
        self.assertEqual(levels, {"privacy": "P1", "security": "S1", "age": "A3"})
        self.assertEqual(signals.psa_score(levels), 40)
        self.assertEqual(signals.psa_levels("<a href='/privacy'>p</a>", "http://x.nl/", "http://x.nl/", {})["security"], "S1")

    def test_security_score(self):
        self.assertEqual(signals.security_score("http://x.nl/", {"Strict-Transport-Security": "1"}), 0)
        self.assertEqual(signals.security_score(self.https, {}), 40)
        self.assertEqual(signals.security_score(self.https, {h: "1" for h in signals.SECURITY_HEADERS}), 100)

    def test_ai_checks_and_score(self):
        html = """<h2>Wat</h2><h2>Hoe</h2><meta name="author" content="A">
        <script type="application/ld+json">{"@type":"Organization","name":"X"}</script>"""
        checks = signals.ai_checks(html, (200, "User-agent: GPTBot\nDisallow: /\n"), (404, ""))
        self.assertEqual(checks, {"ai-crawlers": "warning", "llms-txt": "warning", "structured-data": "pass",
                                  "answer-structure": "pass", "source-transparency": "pass"})
        self.assertEqual(signals.ai_score(checks), 60)
        self.assertEqual(signals.ai_checks("", None, None)["ai-crawlers"], "unknown")
        self.assertIsNone(signals.ai_score({"a": "unknown"}))

    def test_blocked_ai_crawlers_like_psa(self):
        robots = "User-agent: *\nDisallow: /\n\nUser-agent: GPTBot\nAllow: /\n"
        blocked = signals.blocked_ai_crawlers(robots)
        self.assertNotIn("GPTBot", blocked)
        self.assertIn("ClaudeBot", blocked)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "b.db")
        store.init(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_claim_finish_and_recrawl(self):
        with store.connect(self.path) as conn:
            added = store.add_domains(conn, [{"domain": "https://www.a.nl"}, {"domain": "a.nl"}, {"domain": "nope"}], "list")
            self.assertEqual(added, 1)
            job = store.claim_job(conn)
            self.assertEqual(job["root_domain"], "a.nl")
            self.assertIsNone(store.claim_job(conn))
            store.finish_job(conn, job["job_id"], job["id"], category="nieuws", language="nl",
                             scores={"psa_score": 80, "seo_score": 60, "geo_score": 40, "ai_visibility_score": 50,
                                     "security_score": 70}, pages_crawled=3, now=NOW)
            row = conn.execute("SELECT * FROM domains").fetchone()
            self.assertEqual((row["category"], row["index_status"]), ("nieuws", "ok"))
            self.assertEqual(row["next_crawl_at"][:10], "2026-11-25")      # news: weekly
            score = conn.execute("SELECT * FROM scores").fetchone()
            self.assertEqual(score["overall_score"], 66)                  # (80*40 + 50*40 + 70*20) / 100
            self.assertEqual(store.schedule_due(conn, NOW + timedelta(days=3)), 0)
            self.assertEqual(store.schedule_due(conn, NOW + timedelta(days=8)), 1)
            self.assertEqual(store.schedule_due(conn, NOW + timedelta(days=8)), 0)  # no duplicate job

    def test_failed_and_blocked_domains_wait_longer(self):
        with store.connect(self.path) as conn:
            store.add_domains(conn, [{"domain": "b.nl"}], "list")
            job = store.claim_job(conn)
            store.finish_job(conn, job["job_id"], job["id"], category="onbekend", language=None, scores=None,
                             pages_crawled=0, error="blocked: HTTP 403", now=NOW)
            row = conn.execute("SELECT index_status, next_crawl_at FROM domains").fetchone()
            self.assertEqual(row["index_status"], "blocked")
            self.assertEqual(row["next_crawl_at"][:10], "2027-02-16")

    def _seed(self, conn, n, category="mkb", tld="nl"):
        for i in range(n):
            store.add_domains(conn, [{"domain": f"{category}{i}.{tld}"}], "list")
            job = store.claim_job(conn)
            store.finish_job(conn, job["job_id"], job["id"], category=category, language="nl",
                             scores={"psa_score": i * 100 // max(1, n - 1), "seo_score": 50}, pages_crawled=1)

    def test_recalculate_and_compare(self):
        with store.connect(self.path) as conn:
            self._seed(conn, 40, "mkb", "nl")
            self._seed(conn, 10, "saas", "be")
            written = store.recalculate_benchmarks(conn)
            self.assertGreater(written, 0)
            top = conn.execute("SELECT * FROM benchmarks WHERE cohort='NL:mkb' AND metric='psa_score'"
                               " ORDER BY percentile DESC LIMIT 1").fetchone()
            self.assertEqual((top["rank_in_cohort"], top["comparison_group_size"]), (1, 40))
            rows = [dict(r) for r in store.latest_scores(conn)]
        with patch("backend.benchmark.routes.MIN_COHORT_SIZE", 30):
            known = compare_scores(rows, "mkb3.nl", {"psa": 90}, None)
            self.assertEqual(known["metrics"]["psa"]["cohort"], "NL:mkb")
            self.assertEqual(known["metrics"]["psa"]["group_size"], 40)
            # BE saas has only 10 sites: falls back to a broader cohort with enough data
            unknown = compare_scores(rows, "nieuw.be", {"psa": 90}, "saas")
            self.assertEqual(unknown["metrics"]["psa"]["cohort"], "*:*")
            self.assertEqual(unknown["metrics"]["psa"]["top_percent"], 100 - unknown["metrics"]["psa"]["percentile"])


def fake_site(pages: dict[str, tuple[int, str, str]]):
    """Replaces worker.fetch: url -> (status, body, content-type)."""
    async def fetch(url, domain, *, max_bytes=0):
        if url not in pages:
            return (404, "", url, httpx.Headers({"content-type": "text/html"}))
        status, body, ctype = pages[url]
        headers = {"content-type": ctype, "strict-transport-security": "max-age=1", "x-frame-options": "DENY"}
        return (status, body, url, httpx.Headers(headers))
    return fetch


class CrawlerTests(unittest.TestCase):
    def test_crawl_respects_limit_robots_and_duplicates(self):
        body = lambda title: f"<html lang='nl'><title>{title}</title><h1>{title}</h1><h2>a</h2><h2>b</h2>"  # noqa: E731
        home = body("Home") + "".join(f'<a href="/diensten/{i}">d</a>' for i in range(10)) + '<a href="/privacy">Privacy</a>'
        sitemap = ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                   + "".join(f"<url><loc>https://x.nl/blog/{i}</loc></url>" for i in range(5)) + "</urlset>")
        pages = {
            "https://x.nl/": (200, home, "text/html"),
            "https://x.nl/robots.txt": (200, "User-agent: *\nDisallow: /diensten/3\nSitemap: https://x.nl/sm.xml", "text/plain"),
            "https://x.nl/sm.xml": (200, sitemap, "application/xml"),
            "https://x.nl/llms.txt": (200, "# X", "text/plain"),
            "https://x.nl/diensten/0": (200, body("Same"), "text/html"),
            "https://x.nl/diensten/1": (200, body("Same"), "text/html"),   # duplicate content
        }
        for i in range(2, 10):
            pages[f"https://x.nl/diensten/{i}"] = (200, body(f"Dienst {i}"), "text/html")
        row = {"root_domain": "x.nl", "hostname": "x.nl", "category": "mkb"}
        with patch.object(worker, "fetch", fake_site(pages)), patch.object(worker, "PAGE_DELAY_SECONDS", 0):
            result = asyncio.run(worker.crawl_domain(row))
        self.assertIsNone(result["error"])
        urls = [p["url"] for p in result["pages"]]
        self.assertEqual(urls[0], "https://x.nl/")
        self.assertLessEqual(len(urls), 5)                               # mkb: 5 pages
        self.assertNotIn("https://x.nl/diensten/3", urls)                # robots.txt disallow
        self.assertEqual(sum(1 for u in urls if u.endswith(("/0", "/1"))), 1)  # duplicate skipped
        self.assertEqual(result["scores"]["psa_score"], 100)
        self.assertEqual(result["language"], "nl")
        self.assertEqual(result["pages"][0]["title"], "Home")

    def test_blocked_and_unreachable(self):
        with patch.object(worker, "fetch", fake_site({"https://x.nl/": (403, "", "text/html")})):
            self.assertEqual(asyncio.run(worker.crawl_domain({"root_domain": "x.nl", "hostname": "x.nl", "category": "onbekend"}))["error"], "blocked: HTTP 403")

        async def none(url, domain, *, max_bytes=0):
            return None
        with patch.object(worker, "fetch", none):
            self.assertTrue(asyncio.run(worker.crawl_domain({"root_domain": "x.nl", "hostname": "x.nl", "category": "onbekend"}))["error"].startswith("failed"))

    def test_robots_blocks_homepage(self):
        pages = {"https://x.nl/": (200, "<html></html>", "text/html"),
                 "https://x.nl/robots.txt": (200, "User-agent: *\nDisallow: /\n", "text/plain")}
        with patch.object(worker, "fetch", fake_site(pages)):
            result = asyncio.run(worker.crawl_domain({"root_domain": "x.nl", "hostname": "x.nl", "category": "onbekend"}))
        self.assertEqual(result["error"], "blocked: robots.txt")

    def test_parse_tranco_filters_tld(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("top-1m.csv", "1,google.com\n2,nu.nl\n3,example.org\n4,vrt.be\n5,bol.com\n6,ad.nl\n")
        self.assertEqual([d["domain"] for d in worker.parse_tranco(buffer.getvalue(), 2)], ["nu.nl", "vrt.be"])


if __name__ == "__main__":
    unittest.main()
