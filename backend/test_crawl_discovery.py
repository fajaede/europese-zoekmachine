"""Crawl fixes from the 2026-11 error check: a site that moved to another .nl/.be domain puts that domain in
the queue (once), and a 403 is retried after a day."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from backend.benchmark import rules, store, worker
from backend.seo_geo_audit import RedirectLeftDomain


def redirecting_fetch(target):
    async def fetch(url, domain, *, reasons=None, redirects=None, **_):
        reasons.append("redirect: other domain")
        if redirects is not None:
            redirects.append(target)
        return None
    return fetch


class RedirectDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "benchmark.db")
        store.init(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def crawl(self, target):
        row = {"root_domain": "sociaalwerkversterkt.nl", "hostname": "sociaalwerkversterkt.nl", "category": "onbekend"}
        with mock.patch.object(worker, "fetch", redirecting_fetch(target)):
            return asyncio.run(worker.crawl_domain(row))

    def test_moved_site_names_its_new_nl_be_domain(self):
        result = self.crawl("https://www.movisie.nl/home")
        self.assertEqual((result["error"], result["redirect_to"]), ("redirect: other domain", "movisie.nl"))
        self.assertNotIn("redirect_to", self.crawl("https://m.ufabet345.io/"))          # not .nl/.be: ignored
        self.assertNotIn("redirect_to", self.crawl("https://shop.sociaalwerkversterkt.nl/"))  # same site

    def test_new_domain_queued_once_within_the_daily_budget(self):
        with store.connect(self.path) as conn:
            self.assertTrue(worker.queue_redirect_target(conn, "movisie.nl"))
            self.assertFalse(worker.queue_redirect_target(conn, "movisie.nl"))            # already known: no second crawl
            self.assertFalse(worker.queue_redirect_target(conn, None))
            row = conn.execute("SELECT source, crawl_priority FROM domains WHERE root_domain='movisie.nl'").fetchone()
            self.assertEqual((row["source"], row["crawl_priority"]), ("redirect", 1))
            with mock.patch.object(store, "DISCOVERY_PER_DAY", 1):
                self.assertFalse(worker.queue_redirect_target(conn, "wildlands.nl"))      # budget used by movisie.nl

    def test_fetch_reports_the_redirect_target(self):
        async def leave(*_a, **_k):
            raise RedirectLeftDomain("https://www.christenunie.nl/")
        reasons, redirects = [], []
        with mock.patch.object(worker, "fetch_public_resource", leave):
            self.assertIsNone(asyncio.run(worker.fetch("https://christenunie-sgp.nl/", "christenunie-sgp.nl",
                                                       reasons=reasons, redirects=redirects)))
        self.assertEqual((reasons, redirects), (["redirect: other domain"], ["https://www.christenunie.nl/"]))


class BackoffTests(unittest.TestCase):
    def test_403_retried_after_a_day_robots_after_a_week(self):
        self.assertEqual([rules.backoff_days("blocked: HTTP 403", n) for n in range(5)], [1, 7, 30, 90, 90])
        self.assertEqual(rules.backoff_days("blocked: robots.txt", 0), 7)


if __name__ == "__main__":
    unittest.main()
