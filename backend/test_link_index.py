"""Tests for the own link index: external links from crawled pages, storage without Common Crawl duplicates,
discovery, and the plugin endpoints (authority report + "missing a link?")."""

import asyncio
import base64
import json
import os
import tempfile
import time
import unittest
from datetime import timedelta
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import plugin_cloud
from backend.benchmark import authority, backlinks, store, worker
from backend.test_backlinks import EDGES, RANKS, VERTICES, as_bytes
from backend.test_benchmark import NOW, fake_site


class ExternalLinkTests(unittest.TestCase):
    def test_only_other_nl_be_sites_at_domain_level(self):
        html = ('<a href="https://www.fajaede.nl/plugin">x</a> <a href="https://blog.ander.be/a#b">y</a>'
                '<a href="/intern">z</a> <a href="https://www.mijnsite.nl/contact">eigen</a>'
                '<a href="https://example.com">com</a> <a href="mailto:a@b.nl">mail</a>'
                '<a href="https://cdn.jsdelivr.nl/x">cdn</a> <a class="btn" href=\'//stylez.nl\'>proto</a>')
        links = worker.external_links(html, "https://mijnsite.nl/", "mijnsite.nl")
        self.assertEqual(links, {"fajaede.nl": False, "ander.be": False, "stylez.nl": False})

    def test_nofollow_only_when_every_link_is_nofollow(self):
        html = ('<a rel="nofollow" href="https://a.nl">1</a> <a href="https://a.nl/x">2</a>'
                '<a href="https://b.nl" rel="sponsored noopener">3</a>')
        self.assertEqual(worker.external_links(html, "https://s.nl/", "s.nl"), {"a.nl": False, "b.nl": True})

    def test_link_farms_are_ignored(self):
        html = "".join(f'<a href="https://site{i}.nl">x</a>' for i in range(worker.MAX_EXTERNAL_LINKS + 1))
        self.assertEqual(worker.external_links(html, "https://s.nl/", "s.nl"), {})

    def test_crawl_collects_links_from_all_pages(self):
        pages = {
            "https://stylez.nl/": (200, '<html lang="nl"><title>S</title><a href="/over">over</a>'
                                        '<a rel="nofollow" href="https://fajaede.nl">F</a></html>', "text/html"),
            "https://stylez.nl/over": (200, '<title>Over</title><a href="https://www.fajaede.nl/x">F</a>'
                                            '<a href="https://psa.ander.be">B</a>', "text/html"),
        }
        row = {"root_domain": "stylez.nl", "hostname": "stylez.nl", "category": "mkb"}
        with mock.patch.object(worker, "fetch", fake_site(pages)), mock.patch.object(worker, "PAGE_DELAY_SECONDS", 0):
            result = asyncio.run(worker.crawl_domain(row))
        self.assertEqual(result["links"], {"fajaede.nl": ("https://stylez.nl/", False),
                                           "ander.be": ("https://stylez.nl/over", False)})


class Databases(unittest.TestCase):
    """Temporary benchmark.db plus an authority.db with the small graph of test_backlinks."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bench = os.path.join(self.tmp.name, "benchmark.db")
        self.auth = os.path.join(self.tmp.name, "authority.db")
        authority.import_ranks([f"{r}\n" for r in RANKS], "cc-main-2026-aug-sep-oct", self.auth)
        backlinks.import_backlinks(as_bytes(VERTICES), as_bytes(f"{a}\t{b}" for a, b in EDGES),
                                   "cc-main-2026-aug-sep-oct", self.auth)
        store.init(self.bench)
        self.patches = [mock.patch.object(store, "DB_PATH", self.bench), mock.patch.object(authority, "DB_PATH", self.auth)]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()


class StoreLinkTests(Databases):
    def test_sources_known_to_common_crawl_are_not_stored(self):
        links = {"jij.nl": ("https://groot.nl/partners", False)}
        with store.connect() as conn:
            self.assertEqual(worker.store_links(conn, "www.groot.nl", links), 0)      # groot.nl is in the CC graph
            self.assertEqual(worker.store_links(conn, "stylez.nl", links), 1)          # unknown to CC: stored
            rows = conn.execute("SELECT source_domain, target_domain FROM links").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("stylez.nl", "jij.nl")])

    def test_unknown_targets_are_queued_with_a_daily_cap(self):
        links = {"jij.nl": ("u", False), "nieuw1.nl": ("u", False), "nieuw2.be": ("u", False), "nieuw3.nl": ("u", False)}
        with mock.patch.object(store, "DISCOVERY_PER_DAY", 2), store.connect() as conn:
            worker.store_links(conn, "stylez.nl", links)
            queued = [r[0] for r in conn.execute("SELECT root_domain FROM domains WHERE source='link' ORDER BY 1")]
        self.assertEqual(queued, ["nieuw1.nl", "nieuw2.be"])       # jij.nl is known to CC: not queued

    def test_first_seen_stays_last_seen_moves_and_old_links_expire(self):
        with store.connect() as conn:
            store.save_links(conn, "a.nl", {"jij.nl": ("https://a.nl/", True)}, now=NOW - timedelta(days=200))
            store.save_links(conn, "b.nl", {"jij.nl": ("https://b.nl/", True)}, now=NOW - timedelta(days=10))
            store.save_links(conn, "b.nl", {"jij.nl": ("https://b.nl/x", False)}, now=NOW)
            rows = store.own_backlinks(conn, "jij.nl", now=NOW)
        self.assertEqual([r["source_domain"] for r in rows], ["b.nl"])           # a.nl not seen for 200 days
        self.assertEqual((rows[0]["first_seen_at"][:10], rows[0]["sample_url"], rows[0]["nofollow"]),
                         ((NOW - timedelta(days=10)).date().isoformat(), "https://b.nl/x", 0))


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class PluginLinkTests(Databases):
    def setUp(self):
        super().setUp()
        self.key = Ed25519PrivateKey.generate()
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.patches.append(mock.patch.dict(os.environ, {"PLUGIN_TICKET_PUBLIC_KEY": base64.b64encode(raw).decode()}))
        self.patches[-1].start()
        plugin_cloud._hits.clear()
        plugin_cloud._reports.clear()
        app = FastAPI()
        app.include_router(plugin_cloud.router)
        self.client = TestClient(app)

    def ticket(self, **overrides):
        now = int(time.time())
        payload = {"v": 1, "domain": "jij.nl", "plan": "pro", "lic": 7, "iat": now, "exp": now + 3600, **overrides}
        body = json.dumps(payload, separators=(",", ":")).encode()
        return {"Authorization": "Fajaede-Ticket " + b64url(body) + "." + b64url(self.key.sign(body))}

    def report(self, url, **ticket):
        return self.client.post("/api/plugin/report-link", json={"url": url}, headers=self.ticket(**ticket))

    def test_report_merges_without_counting_twice(self):
        with store.connect() as conn:
            store.save_links(conn, "stylez.nl", {"jij.nl": ("https://stylez.nl/", False)})
            store.save_links(conn, "sterk.nl", {"jij.nl": ("https://sterk.nl/", False)})   # in CC since then
        data = self.client.get("/api/plugin/authority", headers=self.ticket()).json()
        self.assertEqual(data["links"]["referring_domains"], 8)                              # Common Crawl
        self.assertEqual(data["fajaede_links"]["referring_domains"], 1)                      # only stylez.nl
        self.assertEqual(data["fajaede_links"]["top"][0]["domain"], "stylez.nl")
        free = self.client.get("/api/plugin/authority", headers=self.ticket(plan="free")).json()
        self.assertNotIn("fajaede_links", free)

    def test_plugin_site_is_queued_for_a_crawl(self):
        self.client.get("/api/plugin/authority", headers=self.ticket(domain="klant.nl"))
        with store.connect() as conn:
            row = conn.execute("SELECT source FROM domains WHERE root_domain='klant.nl'").fetchone()
        self.assertEqual(row["source"], "plugin")

    def test_reported_link_is_checked_and_stored(self):
        pages = {"https://www.stylez.nl/partners": (200, '<a href="https://jij.nl/">Jij</a>', "text/html"),
                 "https://www.stylez.nl/robots.txt": (200, "User-agent: *\nAllow: /", "text/plain"),
                 "https://alwaysb.nl/": (200, '<a href="https://ander.nl/">nee</a>', "text/html"),
                 "https://dicht.nl/robots.txt": (200, "User-agent: FajaedeScan\nDisallow: /", "text/plain")}
        with mock.patch.object(worker, "fetch", fake_site(pages)):
            added = self.report("https://www.stylez.nl/partners").json()
            missing = self.report("https://alwaysb.nl/").json()
            blocked = self.report("https://dicht.nl/").json()
            gone = self.report("https://weg.nl/pagina").json()
        self.assertEqual(added, {"status": "added", "source": "stylez.nl", "nofollow": False})
        self.assertEqual((missing["status"], blocked["status"], gone["status"]), ("not_found", "blocked", "unreachable"))
        self.assertEqual(self.report("https://groot.nl/").json()["status"], "in_common_crawl")   # no fetch needed
        self.assertEqual(self.report("https://www.jij.nl/").json()["status"], "own_site")
        self.assertEqual(self.report("ftp://stylez.nl/x").json()["status"], "invalid")
        with store.connect() as conn:
            rows = conn.execute("SELECT source_domain, verified FROM links").fetchall()
            queued = conn.execute("SELECT source FROM domains WHERE root_domain='stylez.nl'").fetchone()
        self.assertEqual([tuple(r) for r in rows], [("stylez.nl", 1)])
        self.assertEqual(queued["source"], "report")

    def test_report_needs_paid_plan_nl_be_and_has_a_daily_limit(self):
        self.assertEqual(self.report("https://a.nl/", plan="free").status_code, 403)
        self.assertEqual(self.report("https://a.nl/", domain="jij.com").status_code, 400)
        with mock.patch.object(plugin_cloud, "REPORTS_PER_DAY", 1):
            self.assertEqual(self.report("https://groot.nl/").status_code, 200)
            self.assertEqual(self.report("https://groot.nl/").status_code, 429)

    def test_private_addresses_are_refused_by_the_safe_fetch(self):
        async def fail(*_args, **_kwargs):
            raise ValueError("private address")
        with mock.patch("backend.benchmark.worker.fetch_public_resource", fail):
            self.assertEqual(self.report("http://127.0.0.1.nip.nl/admin").json()["status"], "unreachable")


if __name__ == "__main__":
    unittest.main()
