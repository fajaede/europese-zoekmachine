"""Tests for referring domains from the Common Crawl domain graph."""

import os
import tempfile
import unittest

from backend.benchmark import authority, backlinks

# vertices sorted by reversed domain, as in the real file: be.* and nl.* are contiguous id ranges
VERTICES = ["0\tbe.alpha\t1", "1\tbe.beta\t1", "2\tcom.google\t9", "3\tcom.voorbeeld\t1",
            "4\tnl.groot\t3", "5\tnl.jij\t1", "6\tnl.klein\t1", "7\tnl.sterk\t2", "8\torg.wiki\t4"]
RANKS = [authority.HEADER, "1\t9\t1\t0.1\tnl.groot\t3", "2\t8\t2\t0.1\tnl.sterk\t2",
         "3\t7\t3\t0.1\tbe.alpha\t1", "4\t6\t4\t0.1\tnl.klein\t1", "5\t5\t5\t0.1\tnl.jij\t1",
         "6\t4\t6\t0.1\tbe.beta\t1"]
EDGES = [
    (2, 5), (3, 5), (8, 5),                       # .com/.org -> jij.nl (counted, not named)
    (4, 5), (7, 5), (6, 5), (0, 5), (1, 5),       # 5 .nl/.be sources -> jij.nl
    (4, 7), (5, 0),                               # groot -> sterk, jij -> alpha.be
    (5, 2),                                       # jij.nl -> google.com (target not .nl/.be: ignored)
]


def as_bytes(rows):
    return [f"{row}\n".encode() for row in rows]


class BacklinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "authority.db")
        authority.import_ranks([f"{r}\n" for r in RANKS], "rel", self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def run_import(self, edges=EDGES, top_k=None):
        edge_lines = as_bytes(f"{a}\t{b}" for a, b in edges)
        return backlinks.import_backlinks(as_bytes(VERTICES), edge_lines, "rel", self.path)

    def test_counts_all_referring_domains_and_local_ones(self):
        summary = self.run_import()
        self.assertEqual(summary["domains_with_links"], 3)               # jij.nl, sterk.nl, alpha.be
        found = backlinks.lookup("www.jij.nl", self.path)
        self.assertEqual(found["referring_domains"], 8)
        self.assertEqual(found["referring_local"], 5)
        self.assertEqual(backlinks.lookup("sterk.nl", self.path)["referring_domains"], 1)
        self.assertEqual(backlinks.lookup("alpha.be", self.path)["top"][0]["domain"], "jij.nl")
        self.assertIsNone(backlinks.lookup("google.com", self.path))
        self.assertIsNone(backlinks.lookup("klein.nl", self.path))      # nobody links to it
        self.assertEqual(backlinks.counts(["jij.nl", "sterk.nl", "x.nl"], self.path), {"jij.nl": 8, "sterk.nl": 1})

    def test_top_sources_are_the_strongest_local_domains(self):
        self.run_import()
        top = backlinks.lookup("jij.nl", self.path)["top"]
        self.assertEqual(len(top), backlinks.TOP_K)
        self.assertEqual([t["domain"] for t in top], ["groot.nl", "sterk.nl", "alpha.be", "klein.nl", "beta.be"])
        scores = [t["authority"] for t in top]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_keeps_only_strongest_when_more_than_top_k(self):
        original = backlinks.TOP_K
        backlinks.TOP_K = 2
        try:
            self.run_import()
        finally:
            backlinks.TOP_K = original
        self.assertEqual([t["domain"] for t in backlinks.lookup("jij.nl", self.path)["top"]], ["groot.nl", "sterk.nl"])

    def test_requires_matching_authority_import_and_keeps_old_data_on_failure(self):
        self.run_import()
        with self.assertRaises(ValueError):
            backlinks.import_backlinks(as_bytes(VERTICES), as_bytes(["1\t2"]), "other-release", self.path)
        with self.assertRaises(ValueError):                              # no links into .nl/.be at all
            backlinks.import_backlinks(as_bytes(VERTICES), as_bytes(["4\t2"]), "rel", self.path)
        self.assertEqual(backlinks.lookup("jij.nl", self.path)["referring_domains"], 8)

    def test_non_contiguous_vertices_are_rejected(self):
        broken = ["0\tnl.a\t1", "1\tcom.b\t1", "2\tnl.c\t1"]
        with self.assertRaises(ValueError):
            backlinks.import_backlinks(as_bytes(broken), as_bytes(["0\t2"]), "rel", self.path)

    def test_ranges_index(self):
        ranges = backlinks.Ranges([(10, 12), (0, 1)])
        self.assertEqual([ranges.index(i) for i in (0, 1, 10, 12, 5, 13)], [0, 1, 2, 4, -1, -1])
        self.assertEqual(ranges.size, 5)

    def test_backlinks_endpoint_and_competitor_counts(self):
        from unittest import mock
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.benchmark import routes
        self.run_import()
        app = FastAPI()
        app.include_router(routes.router)
        with mock.patch.object(authority, "DB_PATH", self.path), mock.patch.dict(os.environ, {"BENCHMARK_API_KEY": "k"}):
            client = TestClient(app)
            self.assertEqual(client.get("/api/benchmark/backlinks?domain=jij.nl").status_code, 401)
            data = client.get("/api/benchmark/backlinks?domain=https://www.jij.nl/x", headers={"X-Benchmark-Key": "k"}).json()
            unknown = client.get("/api/benchmark/backlinks?domain=niemand.nl", headers={"X-Benchmark-Key": "k"}).json()
            result = {"domain": "jij.nl", "ahead": [{"domain": "sterk.nl"}], "leader": None, "behind": None}
            routes.add_authority(result)
        self.assertEqual((data["domain"], data["referring_domains"], data["referring_local"]), ("jij.nl", 8, 5))
        self.assertEqual(data["top_referring"][0]["domain"], "groot.nl")
        self.assertEqual(data["release"], "rel")
        self.assertEqual((unknown["authority"], unknown["referring_domains"]), (None, 0))
        self.assertEqual((result["your_referring_domains"], result["ahead"][0]["referring_domains"]), (8, 1))


if __name__ == "__main__":
    unittest.main()
