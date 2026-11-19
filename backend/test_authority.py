"""Tests for domain authority from the Common Crawl ranks file."""

import os
import tempfile
import unittest

from backend.benchmark import authority

HEADER = authority.HEADER + "\n"


def line(pos, value, rev, pr_pos=None, hosts=1):
    return f"{pos}\t{value}\t{pr_pos or pos}\t0.001\t{rev}\t{hosts}\n"


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "authority.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_line_keeps_only_nl_and_be(self):
        record = authority.parse_line(line(7, 2.5e7, "nl.nu", hosts=12))
        self.assertEqual(record["domain"], "nu.nl")
        self.assertEqual((record["tld"], record["hc_pos"], record["n_hosts"]), ("nl", 7, 12))
        self.assertEqual(authority.parse_line(line(8, 1.0, "be.vrt.www"))["domain"], "www.vrt.be")
        self.assertIsNone(authority.parse_line(line(1, 3e7, "com.google")))
        self.assertIsNone(authority.parse_line(line(1, 3e7, "nl")))            # TLD alone
        self.assertIsNone(authority.parse_line(HEADER))
        self.assertIsNone(authority.parse_line("garbage"))

    def test_import_scores_per_country_with_ties(self):
        lines = [HEADER, line(1, 9.0, "com.google"), line(2, 8.0, "nl.groot"), line(3, 7.0, "be.sterk"),
                 line(4, 6.0, "nl.middel"), line(5, 5.0, "nl.klein-a"), line(6, 5.0, "nl.klein-b"),
                 line(7, 4.0, "be.zwak")]
        summary = authority.import_ranks(lines, "test-release", self.path)
        self.assertEqual(summary["domains"], {"nl": 4, "be": 2})
        found = authority.lookup(["groot.nl", "www.middel.nl", "klein-a.nl", "klein-b.nl", "sterk.be", "zwak.be",
                                  "onbekend.nl"], self.path)
        self.assertEqual(found["groot.nl"]["score"], 75)       # 3 of 4 .nl domains below it
        self.assertEqual(found["middel.nl"]["score"], 50)
        self.assertEqual(found["klein-a.nl"]["score"], 0)      # tie: nobody strictly below
        self.assertEqual(found["klein-a.nl"]["rank"], found["klein-b.nl"]["rank"])
        self.assertEqual((found["sterk.be"]["score"], found["zwak.be"]["score"]), (50, 0))
        self.assertEqual(found["groot.nl"]["of"], 4)
        self.assertNotIn("onbekend.nl", found)

    def test_reimport_replaces_and_records_meta(self):
        authority.import_ranks([HEADER, line(1, 2.0, "nl.oud")], "r1", self.path)
        authority.import_ranks([HEADER, line(1, 2.0, "nl.nieuw")], "r2", self.path)
        self.assertEqual(set(authority.lookup(["oud.nl", "nieuw.nl"], self.path)), {"nieuw.nl"})
        with authority.connect(self.path) as conn:
            info = authority.meta(conn)
        self.assertEqual(info["last_import"]["release"], "r2")
        self.assertFalse(info["import_running"])

    def test_bad_header_or_empty_download_fails_without_swapping(self):
        authority.import_ranks([HEADER, line(1, 2.0, "nl.blijft")], "r1", self.path)
        with self.assertRaises(ValueError):
            authority.import_ranks(["#other\tformat\n", line(1, 2.0, "nl.x")], "r2", self.path)
        with self.assertRaises(ValueError):
            authority.import_ranks([HEADER, line(1, 2.0, "com.x")], "r3", self.path)
        self.assertIn("blijft.nl", authority.lookup(["blijft.nl"], self.path))

    def test_top_domains_skips_known_and_lookup_without_db(self):
        authority.import_ranks([HEADER, line(1, 9.0, "nl.a"), line(2, 8.0, "be.b"), line(3, 7.0, "nl.c")],
                               "r", self.path)
        top = authority.top_domains(2, {"a.nl"}, self.path)
        self.assertEqual([t["domain"] for t in top], ["b.be", "c.nl"])
        self.assertEqual(authority.lookup(["a.nl"], os.path.join(self.tmp.name, "missing.db")), {})

    def test_competitors_response_gets_authority(self):
        from unittest import mock
        from backend.benchmark.routes import add_authority
        authority.import_ranks([HEADER, line(1, 9.0, "nl.leider"), line(2, 8.0, "nl.jij"), line(3, 7.0, "nl.ander")],
                               "r", self.path)
        result = {"domain": "jij.nl", "ahead": [{"domain": "leider.nl"}], "leader": None,
                  "behind": {"domain": "onbekend.nl"}}
        with mock.patch.object(authority, "DB_PATH", self.path):
            add_authority(result)
        self.assertEqual(result["your_authority"], 33)
        self.assertEqual(result["ahead"][0]["authority"], 67)
        self.assertIsNone(result["behind"]["authority"])


if __name__ == "__main__":
    unittest.main()
