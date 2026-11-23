"""Links from every extension in the own link index, without counting a site Common Crawl already counts
and without crawling non-.nl/.be sites for the benchmark."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from backend.benchmark import authority, backlinks, ccdomains, store, worker
from backend.test_backlinks import EDGES, RANKS, VERTICES, as_bytes
from backend.test_benchmark import fake_site


class BloomTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "cc.bloom")

    def tearDown(self):
        ccdomains._cache.clear()
        self.tmp.cleanup()

    def test_build_from_vertices_and_lookup(self):
        lines = as_bytes(["0\tcom.example\t3", "1\tuk.co.shop\t1", "2\tde.firma\t1", "3\tnl.groot\t2"])
        info = ccdomains.build_from_vertices(lines, "rel", self.path, capacity=1000)
        self.assertEqual((info["count"], info["release"]), (4, "rel"))
        self.assertTrue(ccdomains.known("example.com", self.path))
        self.assertTrue(ccdomains.known("www.blog.example.com", self.path))     # subdomain of a known site
        self.assertTrue(ccdomains.known("shop.co.uk", self.path))
        self.assertFalse(ccdomains.known("nieuw-bureau.com", self.path))
        self.assertIsNone(ccdomains.known("example.com", os.path.join(self.tmp.name, "missing.bloom")))

    def test_false_positive_rate_is_about_one_percent(self):
        builder = ccdomains.Builder(capacity=20000)
        for i in range(20000):
            builder.add(f"site{i}.com")
        builder.save(self.path, "rel")
        rate = sum(bool(ccdomains.known(f"other{i}.org", self.path)) for i in range(5000)) / 5000
        self.assertLess(rate, 0.02)
        self.assertTrue(all(ccdomains.known(f"site{i}.com", self.path) for i in range(0, 20000, 997)))  # no false negatives

    def test_backlinks_import_fills_the_filter_from_the_same_stream(self):
        auth = os.path.join(self.tmp.name, "authority.db")
        authority.import_ranks([f"{r}\n" for r in RANKS], "rel", auth)
        builder = ccdomains.Builder(capacity=1000)
        backlinks.import_backlinks(as_bytes(VERTICES), as_bytes(f"{a}\t{b}" for a, b in EDGES), "rel", auth, builder)
        builder.save(self.path, "rel")
        self.assertTrue(ccdomains.known("google.com", self.path) and ccdomains.known("wiki.org", self.path))
        self.assertEqual(builder.count, len(VERTICES))

    def test_registered_domain_keeps_second_level_registrations(self):
        self.assertEqual(worker.registered_domain("www.blog.Shop.nl"), "shop.nl")
        self.assertEqual(worker.registered_domain("www.shop.co.uk"), "shop.co.uk")
        self.assertEqual(worker.registered_domain("partner.example.com"), "example.com")


class LinkIndexAllExtensionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = os.path.join(self.tmp.name, "authority.db")
        authority.import_ranks([f"{r}\n" for r in RANKS], "rel", self.auth)
        self.bloom = os.path.join(self.tmp.name, "cc.bloom")
        ccdomains.build_from_vertices(as_bytes(["0\tcom.bekend\t1", "1\tnl.groot\t3"]), "rel", self.bloom, capacity=1000)
        self.patches = [mock.patch.object(store, "DB_PATH", os.path.join(self.tmp.name, "benchmark.db")),
                        mock.patch.object(authority, "DB_PATH", self.auth),
                        mock.patch.object(ccdomains, "PATH", self.bloom)]
        for patch in self.patches:
            patch.start()
        store.init()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        ccdomains._cache.clear()
        self.tmp.cleanup()

    def test_counted_by_common_crawl_uses_ranks_and_filter(self):
        found = ccdomains.counted_by_common_crawl(["groot.nl", "stylez.nl", "bekend.com", "nieuw.com"])
        self.assertEqual(found, {"groot.nl", "bekend.com"})
        with mock.patch.object(ccdomains, "PATH", os.path.join(self.tmp.name, "none.bloom")):
            self.assertEqual(ccdomains.counted_by_common_crawl(["nieuw.com", "stylez.nl"]), {"nieuw.com"})  # no filter: safe side

    def test_crawler_stores_links_from_unknown_com_sites_only(self):
        links = {"jij.nl": ("https://x/", False)}
        with store.connect() as conn:
            self.assertEqual(worker.store_links(conn, "bekend.com", links), 0)
            self.assertEqual(worker.store_links(conn, "www.nieuw.com", links), 1)
            sources = [r[0] for r in conn.execute("SELECT source_domain FROM links")]
        self.assertEqual(sources, ["nieuw.com"])

    def test_reported_com_link_is_added_but_never_queued_for_the_benchmark(self):
        from backend import plugin_cloud
        pages = {"https://partner.de/kunden": (200, '<a href="https://www.jij.nl/">Jij</a>', "text/html"),
                 "https://bekend.com/links": (200, '<a href="https://jij.nl/">Jij</a>', "text/html")}
        with mock.patch.object(worker, "fetch", fake_site(pages)):
            added = asyncio.run(plugin_cloud.check_reported_link("https://partner.de/kunden", "jij.nl"))
            known = asyncio.run(plugin_cloud.check_reported_link("https://bekend.com/links", "jij.nl"))
        self.assertEqual((added["status"], added["source"]), ("added", "partner.de"))
        self.assertEqual(known["status"], "in_common_crawl")
        with store.connect() as conn:
            queued = conn.execute("SELECT COUNT(*) FROM domains WHERE root_domain='partner.de'").fetchone()[0]
        self.assertEqual(queued, 0)
        report = plugin_cloud.own_links("jij.nl")
        self.assertEqual([t["domain"] for t in report["top"]], ["partner.de"])


if __name__ == "__main__":
    unittest.main()
