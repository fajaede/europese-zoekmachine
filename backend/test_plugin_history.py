"""Tests for the Fajaede Cloud history: authority per Common Crawl release, benchmark scores over time
and new links per month."""

import base64
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import plugin_cloud
from backend.benchmark import authority, backlinks, store
from backend.test_backlinks import EDGES, RANKS, VERTICES, as_bytes


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class PluginHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = os.path.join(self.tmp.name, "authority.db")
        authority.import_ranks([f"{r}\n" for r in RANKS], "cc-main-2026-aug-sep-oct", self.auth)
        backlinks.import_backlinks(as_bytes(VERTICES), as_bytes(f"{a}\t{b}" for a, b in EDGES),
                                   "cc-main-2026-aug-sep-oct", self.auth)
        self.key = Ed25519PrivateKey.generate()
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.patches = [
            mock.patch.dict(os.environ, {"PLUGIN_TICKET_PUBLIC_KEY": base64.b64encode(raw).decode()}),
            mock.patch.object(store, "DB_PATH", os.path.join(self.tmp.name, "benchmark.db")),
            mock.patch.object(authority, "DB_PATH", self.auth),
        ]
        for patch in self.patches:
            patch.start()
        plugin_cloud._hits.clear()
        app = FastAPI()
        app.include_router(plugin_cloud.router)
        self.client = TestClient(app)

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def headers(self, **overrides):
        now = int(time.time())
        payload = {"v": 1, "domain": "jij.nl", "plan": "pro", "lic": 7, "iat": now, "exp": now + 3600, **overrides}
        body = json.dumps(payload, separators=(",", ":")).encode()
        return {"Authorization": "Fajaede-Ticket " + b64url(body) + "." + b64url(self.key.sign(body))}

    def test_authority_card_records_one_point_per_release(self):
        self.client.get("/api/plugin/authority", headers=self.headers(plan="free"))   # also for free sites
        self.client.get("/api/plugin/authority", headers=self.headers())              # same release: no new point
        history = authority.history("jij.nl")
        self.assertEqual(len(history), 1)
        self.assertEqual((history[0]["release"], history[0]["referring_domains"]), ("cc-main-2026-aug-sep-oct", 8))
        authority.record_history("jij.nl", "cc-main-2026-nov-dec-jan", 21, 150000, 12, 3)
        data = self.client.get("/api/plugin/history", headers=self.headers()).json()
        self.assertEqual([p["release"] for p in data["authority"]], ["cc-main-2026-aug-sep-oct", "cc-main-2026-nov-dec-jan"])
        self.assertEqual(data["authority"][1]["score"], 21)

    def test_scores_over_time_and_new_links_per_month(self):
        store.init()
        with store.connect() as conn:
            cur = conn.execute("INSERT INTO domains (root_domain, hostname, country, source, first_seen_at, next_crawl_at)"
                               " VALUES ('jij.nl', 'jij.nl', 'NL', 'plugin', '2026-09-01', '2026-09-01')")
            for day, psa in (("2026-09-01", 40), ("2026-10-01", 50), ("2026-11-01", 70)):
                conn.execute("INSERT INTO scores (domain_id, psa_score, seo_score, geo_score, ai_visibility_score, pages_crawled,"
                             " calculated_at) VALUES (?, ?, 60, 40, 50, 5, ?)", (cur.lastrowid, psa, day + "T08:00:00+00:00"))
            store.save_links(conn, "stylez.nl", {"jij.nl": ("https://stylez.nl/", False)},
                             now=datetime(2026, 11, 21, tzinfo=timezone.utc))
            store.save_links(conn, "always.nl", {"jij.nl": ("https://always.nl/", False)},
                             now=datetime(2026, 10, 3, tzinfo=timezone.utc))
            store.save_links(conn, "groot.nl", {"jij.nl": ("https://groot.nl/", False)},    # in Common Crawl: not counted
                             now=datetime(2026, 11, 2, tzinfo=timezone.utc))
        with mock.patch.object(plugin_cloud, "datetime", wraps=datetime) as fake:
            fake.now.return_value = datetime(2026, 11, 22, tzinfo=timezone.utc)
            data = self.client.get("/api/plugin/history", headers=self.headers()).json()
        self.assertEqual([p["psa"] for p in data["scores"]], [40, 50, 70])
        self.assertEqual(data["scores"][-1]["date"], "2026-11-01")
        self.assertEqual(data["scores"][-1]["overall"], 60)                      # 50% psa 70 + 50% avg(60, 40, 50)
        months = {m["month"]: m["count"] for m in data["new_links"]}
        self.assertEqual(len(data["new_links"]), 12)
        self.assertEqual(data["new_links"][-1]["month"], "2026-11")
        self.assertEqual((months["2026-11"], months["2026-10"], months["2026-09"]), (1, 1, 0))

    def test_free_plan_is_locked_and_com_not_covered(self):
        free = self.client.get("/api/plugin/history", headers=self.headers(plan="free")).json()
        self.assertTrue(free["locked"])
        self.assertNotIn("authority", free)
        com = self.client.get("/api/plugin/history", headers=self.headers(domain="jij.com")).json()
        self.assertFalse(com["covered"])


if __name__ == "__main__":
    unittest.main()
