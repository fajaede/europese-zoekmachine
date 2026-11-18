"""Tests for the benchmark crawl (rules, signals, storage, crawler with fake sites)."""

import asyncio
import io
import os
import sqlite3
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

    def test_recrawl_signals_in_priority(self):
        p = lambda **kw: rules.url_priority("https://x.nl/pagina", now=NOW, **kw)  # noqa: E731
        base = p()
        self.assertEqual(p(is_new=True) - base, 15)
        self.assertEqual(p(previous_status=404) - base, -60)
        self.assertEqual(p(previous_status=301) - base, -20)
        self.assertEqual(p(previous_status=200, previous_words=800) - base, 5)
        self.assertEqual(p(previous_status=200, previous_words=50) - base, 0)       # thin page: no bonus
        self.assertEqual(p(inlinks=2) - base, 6)
        self.assertEqual(p(inlinks=50) - base, 15)                                   # capped
        key = lambda **kw: rules.url_priority("https://x.nl/diensten/", now=NOW, **kw)  # noqa: E731
        self.assertEqual(key(homepage_changed=True) - key(), 10)
        self.assertEqual(p(homepage_changed=True) - base, 0)                        # not a key page

    def test_backoff_days_table(self):
        self.assertEqual([rules.backoff_days("blocked: robots.txt", n) for n in range(4)], [7, 30, 90, 90])
        self.assertEqual(rules.backoff_days("blocked: HTTP 503", 0), 1)
        self.assertEqual(rules.backoff_days("iets anders", 0), 30)

    def test_robots_rfc9309(self):
        ua = "FajaedeAuditBot/1.0 (+https://api.fajaede.eu)"
        # zorgdoc.nl: only the homepage is allowed (urllib.robotparser wrongly blocked it)
        zorgdoc = rules.RobotsTxt("User-agent: *\nAllow: /$\nAllow: /favicon.ico$\nDisallow: /\n")
        self.assertTrue(zorgdoc.can_fetch(ua, "https://zorgdoc.nl/"))
        self.assertFalse(zorgdoc.can_fetch(ua, "https://zorgdoc.nl/artsen"))
        self.assertTrue(zorgdoc.can_fetch(ua, "https://zorgdoc.nl/favicon.ico"))
        # delpher.nl: everyone blocked, only whitelisted search bots allowed
        delpher = rules.RobotsTxt("# Block all\nUser-Agent: *\nDisallow: /\n\nUser-Agent: Googlebot\nAllow: /\n")
        self.assertFalse(delpher.can_fetch(ua, "https://delpher.nl/"))
        self.assertTrue(delpher.can_fetch("Googlebot/2.1", "https://delpher.nl/"))
        # longest match wins; Allow wins a tie; wildcards; query strings
        r = rules.RobotsTxt("User-agent: *\nDisallow: /shop\nAllow: /shop/info\nDisallow: /*?sort=\n"
                            "Disallow: /*.pdf$\nAllow: /p\nDisallow: /p\n")
        self.assertFalse(r.can_fetch(ua, "https://x.nl/shop/item"))
        self.assertTrue(r.can_fetch(ua, "https://x.nl/shop/info/a"))
        self.assertFalse(r.can_fetch(ua, "https://x.nl/lijst?sort=prijs"))
        self.assertFalse(r.can_fetch(ua, "https://x.nl/a/b.pdf"))
        self.assertTrue(r.can_fetch(ua, "https://x.nl/a/b.pdf?x=1"))   # '$' anchors the end
        self.assertTrue(r.can_fetch(ua, "https://x.nl/p"))              # tie: Allow wins
        # our own group overrides '*'; empty Disallow = allow all; no rules = allow
        own = rules.RobotsTxt("User-agent: *\nDisallow: /\n\nUser-agent: FajaedeAuditBot\nDisallow:\n")
        self.assertTrue(own.can_fetch(ua, "https://x.nl/alles"))
        self.assertTrue(rules.RobotsTxt("").can_fetch(ua, "https://x.nl/"))
        self.assertTrue(rules.RobotsTxt("<!DOCTYPE html><html>niet gevonden</html>").can_fetch(ua, "https://x.nl/"))
        # several user-agent lines share one group
        shared = rules.RobotsTxt("User-agent: GPTBot\nUser-agent: FajaedeAuditBot\nDisallow: /\n")
        self.assertFalse(shared.can_fetch(ua, "https://x.nl/"))
        self.assertTrue(shared.can_fetch("OtherBot", "https://x.nl/"))

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

    def test_spread(self):
        self.assertFalse(rules.has_spread([0] * 8 + [10, 20]))        # 80% the same: GEO-like
        self.assertTrue(rules.has_spread([0] * 7 + [10, 20, 30]))     # 70%: still allowed
        self.assertTrue(rules.has_spread(list(range(10))))
        self.assertFalse(rules.has_spread([]))

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

    def _fail_then(self, conn, domain, outcomes):
        """Run consecutive crawls with the given errors (None = success); return pause days per run."""
        store.add_domains(conn, [{"domain": domain}], "list")
        domain_id = conn.execute("SELECT id FROM domains WHERE root_domain=?", (domain,)).fetchone()[0]
        days = []
        for error in outcomes:
            store.schedule_due(conn, NOW + timedelta(days=400))
            job = conn.execute("SELECT j.id AS job_id, d.* FROM crawl_jobs j JOIN domains d ON d.id=j.domain_id"
                               " WHERE j.status='queued' AND d.id=? LIMIT 1", (domain_id,)).fetchone()
            conn.execute("UPDATE crawl_jobs SET status='running' WHERE id=?", (job["job_id"],))
            store.finish_job(conn, job["job_id"], job["id"], category="mkb", language=None,
                             scores=None if error else {"psa_score": 80}, pages_crawled=0 if error else 1,
                             error=error, now=NOW)
            row = conn.execute("SELECT next_crawl_at, fail_count, index_status FROM domains WHERE root_domain=?",
                               (domain,)).fetchone()
            days.append((datetime.fromisoformat(row["next_crawl_at"]) - NOW).days)
        return days, row

    def test_backoff_grows_and_resets(self):
        with store.connect(self.path) as conn:
            days, row = self._fail_then(conn, "b.nl", ["blocked: HTTP 403"] * 4)
            self.assertEqual(days, [7, 30, 90, 90])
            self.assertEqual((row["index_status"], row["fail_count"]), ("blocked", 4))
            days, _ = self._fail_then(conn, "r.nl", ["blocked: HTTP 429"] * 5)
            self.assertEqual(days, [1, 3, 7, 30, 30])
            days, _ = self._fail_then(conn, "d.nl", ["failed: dns"] * 4 + [None, "failed: timeout"])
            self.assertEqual(days, [1, 7, 30, 90, 30, 1])            # success resets the count
            days, row = self._fail_then(conn, "a.nl", ["redirect: other domain"])
            self.assertEqual((days, row["index_status"]), ([90], "redirect"))

    def test_migration_keeps_old_data(self):
        old = os.path.join(self.tmp.name, "old.db")
        conn = sqlite3.connect(old)
        conn.executescript("""
            CREATE TABLE domains (id INTEGER PRIMARY KEY, root_domain TEXT NOT NULL UNIQUE, hostname TEXT NOT NULL,
              category TEXT NOT NULL DEFAULT 'onbekend', country TEXT NOT NULL, language TEXT, source TEXT NOT NULL,
              first_seen_at TEXT NOT NULL, last_crawled_at TEXT, next_crawl_at TEXT NOT NULL,
              crawl_priority INTEGER NOT NULL DEFAULT 0, index_status TEXT NOT NULL DEFAULT 'new');
            CREATE TABLE pages (id INTEGER PRIMARY KEY, domain_id INTEGER NOT NULL, url TEXT NOT NULL,
              http_status INTEGER, title TEXT, meta_description TEXT, h1 TEXT, word_count INTEGER, language TEXT,
              canonical_url TEXT, robots_directive TEXT, schema_types TEXT, seo_score INTEGER, geo_score INTEGER,
              fetched_at TEXT NOT NULL, UNIQUE(domain_id, url));
            CREATE TABLE crawl_jobs (id INTEGER PRIMARY KEY, domain_id INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'queued', priority INTEGER NOT NULL DEFAULT 0,
              attempts INTEGER NOT NULL DEFAULT 0, scheduled_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
              error TEXT);
            INSERT INTO domains (root_domain, hostname, country, source, first_seen_at, next_crawl_at)
              VALUES ('oud.nl', 'oud.nl', 'NL', 'list', '2026-11-17', '2026-11-17');
            INSERT INTO pages (domain_id, url, fetched_at) VALUES (1, 'https://oud.nl/a', '2026-11-17');
        """)
        conn.commit()
        conn.close()
        store.init(old)
        store.init(old)                                                 # running twice is harmless
        with store.connect(old) as conn:
            row = conn.execute("SELECT root_domain, fail_count, homepage_hash FROM domains").fetchone()
            self.assertEqual(tuple(row), ("oud.nl", 0, None))
            self.assertEqual(conn.execute("SELECT url, content_hash FROM pages").fetchone()[0], "https://oud.nl/a")
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(crawl_jobs)")}
            self.assertTrue({"pages_crawled", "pages_unchanged"} <= cols)

    def test_queue_and_index_stats(self):
        with store.connect(self.path) as conn:
            self._seed(conn, 3, "mkb", "nl")
            self._fail_then(conn, "x.nl", ["blocked: HTTP 403"])
            queue = store.queue_overview(conn, NOW)
            self.assertEqual(queue["jobs_by_status"].get("done"), 3)
            self.assertEqual(queue["domains_in_backoff"], {"≤7 dagen": 1})
            stats = store.index_stats(conn)
            self.assertEqual(stats["by_status"]["ok"], 3)
            self.assertIn("NL:mkb", stats["cohorts_below_minimum"])

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
        with patch("backend.benchmark.rules.MIN_COHORT_SIZE", 30):
            known = compare_scores(rows, "mkb3.nl", {"psa": 90}, None)
            self.assertEqual(known["metrics"]["psa"]["cohort"], "NL:mkb")
            self.assertEqual(known["metrics"]["psa"]["group_size"], 40)
            # BE saas has only 10 sites: falls back to a broader cohort with enough data
            unknown = compare_scores(rows, "nieuw.be", {"psa": 90}, "saas")
            self.assertEqual(unknown["metrics"]["psa"]["cohort"], "*:*")
            self.assertEqual(unknown["metrics"]["psa"]["top_percent"], 100 - unknown["metrics"]["psa"]["percentile"])
            # Every site has SEO 50: no spread, so no SEO percentile (stored or compared).
            self.assertNotIn("seo", compare_scores(rows, "mkb3.nl", {"psa": 90, "seo": 70}, None)["metrics"])
        with store.connect(self.path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM benchmarks WHERE metric='seo_score'").fetchone()[0], 0)

    def test_redirect_gets_its_own_status(self):
        with store.connect(self.path) as conn:
            store.add_domains(conn, [{"domain": "c.nl"}], "list")
            job = store.claim_job(conn)
            store.finish_job(conn, job["job_id"], job["id"], category="onbekend", language=None, scores=None,
                             pages_crawled=0, error="redirect: other domain", now=NOW)
            self.assertEqual(conn.execute("SELECT index_status FROM domains").fetchone()[0], "redirect")


def fake_site(pages: dict[str, tuple[int, str, str]]):
    """Replaces worker.fetch: url -> (status, body, content-type)."""
    async def fetch(url, domain, *, max_bytes=0, reasons=None):
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

        async def none(url, domain, *, max_bytes=0, reasons=None):
            return None
        with patch.object(worker, "fetch", none):
            self.assertTrue(asyncio.run(worker.crawl_domain({"root_domain": "x.nl", "hostname": "x.nl", "category": "onbekend"}))["error"].startswith("failed"))

    def test_recrawl_skips_unchanged_pages(self):
        body = lambda title: f"<html lang='nl'><title>{title}</title><h1>{title}</h1>" + "woord " * 400  # noqa: E731
        home = body("Home") + "".join(f'<a href="/diensten/{i}">d</a>' for i in range(4))
        pages = {"https://x.nl/": (200, home, "text/html")}
        for i in range(4):
            pages[f"https://x.nl/diensten/{i}"] = (200, body(f"Dienst {i}"), "text/html")
        row = {"root_domain": "x.nl", "hostname": "x.nl", "category": "mkb"}
        with patch.object(worker, "fetch", fake_site(pages)), patch.object(worker, "PAGE_DELAY_SECONDS", 0):
            first = asyncio.run(worker.crawl_domain(row))
            self.assertEqual((len(first["pages"]), first["unchanged"]), (5, []))
            known = {p["url"]: {"content_hash": p["content_hash"], "http_status": 200, "word_count": p["word_count"]}
                     for p in first["pages"]}
            pages["https://x.nl/diensten/2"] = (200, body("Dienst 2 vernieuwd"), "text/html")
            second = asyncio.run(worker.crawl_domain({**row, "homepage_hash": first["homepage_hash"]}, known))
        self.assertEqual([p["url"] for p in second["pages"]], ["https://x.nl/", "https://x.nl/diensten/2"])
        self.assertEqual(len(second["unchanged"]), 3)
        self.assertEqual(second["homepage_hash"], first["homepage_hash"])

    def test_content_hash_ignores_cms_noise(self):
        page = lambda nonce, text="Welkom", desc="Over ons": (  # noqa: E731
            f'<html><head><meta name="description" content="{desc}"><script>var n="{nonce}"</script>'
            f'<style>.a{{}}</style></head><body><!-- {nonce} --><p class="x{nonce}">{text}</p></body></html>')
        self.assertEqual(worker.content_hash(page("abc")), worker.content_hash(page("xyz")))
        self.assertNotEqual(worker.content_hash(page("abc")), worker.content_hash(page("abc", text="Welkom terug")))
        self.assertNotEqual(worker.content_hash(page("abc")), worker.content_hash(page("abc", desc="Nieuw")))

    def test_frontier_prefers_much_linked_pages(self):
        home = '<a href="/a">a</a><a href="/b">b</a><a href="/c">c</a>'
        frontier = worker.build_frontier("https://x.nl/", home, [], "x.nl", None, {}, False)
        frontier.link_from("https://x.nl/a", {"https://x.nl/c"})
        frontier.link_from("https://x.nl/b", {"https://x.nl/c", "https://x.nl/nieuw"})
        self.assertEqual(frontier.pop(), "https://x.nl/c")             # 3 inlinks
        self.assertIn(frontier.pop(), {"https://x.nl/a", "https://x.nl/b"})
        order = [frontier.pop(), frontier.pop()]
        self.assertIn("https://x.nl/nieuw", order)                      # discovered on a subpage
        self.assertIsNone(frontier.pop())

    def test_frontier_changed_sitemap_url_and_old_404(self):
        now = datetime.now(timezone.utc)
        last_crawl = now - timedelta(days=10)
        sitemap = [rules.SitemapEntry("https://x.nl/oud", None, now - timedelta(days=40)),
                   rules.SitemapEntry("https://x.nl/ongezien", None, None),
                   rules.SitemapEntry("https://x.nl/bijgewerkt", None, now - timedelta(days=1)),
                   rules.SitemapEntry("https://x.nl/weg", None, None)]
        known = {"https://x.nl/oud": {"http_status": 200, "word_count": 100},
                 "https://x.nl/bijgewerkt": {"http_status": 200, "word_count": 100},
                 "https://x.nl/weg": {"http_status": 404, "word_count": 0}}
        frontier = worker.build_frontier("https://x.nl/", "", sitemap, "x.nl", None, known, False, last_crawl)
        self.assertTrue(frontier.signals["https://x.nl/bijgewerkt"]["is_new"])      # updated since last crawl
        self.assertFalse(frontier.signals["https://x.nl/ongezien"]["is_new"])       # no lastmod: no claim
        self.assertEqual([frontier.pop() for _ in range(4)],
                         ["https://x.nl/bijgewerkt", "https://x.nl/oud", "https://x.nl/ongezien", "https://x.nl/weg"])
        first_crawl = worker.build_frontier("https://x.nl/", "", sitemap, "x.nl", None, {}, False, None)
        self.assertFalse(any(s["is_new"] for s in first_crawl.signals.values()))    # first crawl: nothing is "new"

    def test_unreachable_reason_comes_from_https_attempt(self):
        async def failing(url, domain, *, max_bytes=0, reasons=None):
            reasons.append("redirect: other domain" if url.startswith("https") else "failed: timeout")
            return None
        with patch.object(worker, "fetch", failing):
            result = asyncio.run(worker.crawl_domain({"root_domain": "x.nl", "hostname": "x.nl", "category": "onbekend"}))
        self.assertEqual(result["error"], "redirect: other domain")

    def test_failure_reasons(self):
        request = httpx.Request("GET", "https://x.nl/")
        cases = [
            (ValueError("The redirect left the allowed crawl domain."), "redirect: other domain"),
            (ValueError("The website hostname could not be resolved."), "failed: dns"),
            (ValueError("Private and local addresses cannot be audited."), "failed: private address"),
            (ValueError("The website exceeded the redirect limit."), "failed: redirect loop"),
            (httpx.ReadTimeout("timed out", request=request), "failed: timeout"),
            (httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", request=request),
             "failed: tls certificate"),
            (httpx.ConnectError("All connection attempts failed", request=request), "failed: connection refused"),
        ]
        for exc, expected in cases:
            self.assertEqual(worker.failure_reason(exc), expected, str(exc))

    def test_robots_blocks_homepage(self):
        pages = {"https://x.nl/": (200, "<html></html>", "text/html"),
                 "https://x.nl/robots.txt": (200, "User-agent: *\nDisallow: /\n", "text/plain")}
        with patch.object(worker, "fetch", fake_site(pages)):
            result = asyncio.run(worker.crawl_domain({"root_domain": "x.nl", "hostname": "x.nl", "category": "onbekend"}))
        self.assertEqual(result["error"], "blocked: robots.txt")

    def test_parse_tranco_filters_tld(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("top-1m.csv", "1,google.com\n2,awsdns-eusc-37.nl\n3,nu.nl\n4,example.org\n"
                                           "5,cdn-x.nl\n6,vrt.be\n7,bol.com\n8,ad.nl\n")
        self.assertEqual([d["domain"] for d in worker.parse_tranco(buffer.getvalue(), 2)], ["nu.nl", "vrt.be"])

    def test_infrastructure_filter(self):
        for domain in ("awsdns-eusc-37.nl", "x.cloudfront.be", "ns1-dns.nl", "ntp1.nl", "cdn.bol.nl", "cdn-x.nl"):
            self.assertTrue(worker.is_infrastructure(domain), domain)
        for domain in ("nu.nl", "dnsbelgium.be", "fajaede.nl", "gemeente-utrecht.nl", "pakkettracking.nl",
                       "cdnow.nl"):
            self.assertFalse(worker.is_infrastructure(domain), domain)

    def test_pool_keeps_slots_busy(self):
        """A slow domain must not hold up the others (no batch waiting)."""
        tmp = tempfile.TemporaryDirectory()
        path = os.path.join(tmp.name, "pool.db")
        finished: list[str] = []

        async def fake_process(job):
            await asyncio.sleep(1.0 if job["root_domain"] == "traag.nl" else 0.05)
            finished.append(job["root_domain"])
            await asyncio.to_thread(worker._store_result, job, {"category": "mkb", "language": None,
                                                                "scores": {"psa_score": 80}, "pages": [], "error": None})

        async def main():
            stop = asyncio.Event()
            task = asyncio.create_task(worker.run_forever(stop, concurrency=2, idle_seconds=0.05))
            await asyncio.sleep(0.6)
            stop.set()
            await asyncio.wait_for(task, timeout=5)

        with patch.object(store, "DB_PATH", path), patch.object(worker, "process_job", fake_process):
            store.init()
            with store.connect() as conn:
                store.add_domains(conn, [{"domain": "traag.nl", "priority": 9}]
                                  + [{"domain": f"snel{i}.nl"} for i in range(6)], "list")
            asyncio.run(main())
        tmp.cleanup()
        # While the slow site was still busy, the other slot handled all six fast sites.
        self.assertEqual(sorted(finished[:6]), sorted(f"snel{i}.nl" for i in range(6)))


if __name__ == "__main__":
    unittest.main()
