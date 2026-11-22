"""Tests for the Fajaede Cloud competitors endpoint (plugin, ticket auth, benchmark cohort)."""

import base64
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import plugin_cloud
from backend.benchmark import authority, store

# domain -> (psa, seo, geo, ai); all Dutch mkb sites, so they form one cohort
SITES = {
    "jij.nl": (60, 60, 40, 50),
    "boven1.nl": (70, 65, 45, 55),
    "boven2.nl": (90, 70, 50, 60),
    "leider.nl": (100, 95, 90, 95),
    "onder.nl": (50, 50, 30, 40),
    "laag.nl": (20, 30, 10, 20),
    "midden.nl": (65, 62, 42, 52),
}


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class PluginCompetitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key = Ed25519PrivateKey.generate()
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.patches = [
            mock.patch.dict(os.environ, {"PLUGIN_TICKET_PUBLIC_KEY": base64.b64encode(raw).decode()}),
            mock.patch.object(store, "DB_PATH", os.path.join(self.tmp.name, "benchmark.db")),
            mock.patch.object(authority, "DB_PATH", os.path.join(self.tmp.name, "authority.db")),
        ]
        for patch in self.patches:
            patch.start()
        plugin_cloud._hits.clear()
        store.init()
        stamp = store.now_iso()
        with store.connect() as conn:
            for domain, (psa, seo, geo, ai) in SITES.items():
                cur = conn.execute("INSERT INTO domains (root_domain, hostname, category, country, source, first_seen_at,"
                                   " next_crawl_at) VALUES (?, ?, 'mkb', 'NL', 'test', ?, ?)", (domain, domain, stamp, stamp))
                conn.execute("INSERT INTO scores (domain_id, psa_score, seo_score, geo_score, ai_visibility_score,"
                             " security_score, overall_score, pages_crawled, calculated_at) VALUES (?, ?, ?, ?, ?, 80, 0, 5, ?)",
                             (cur.lastrowid, psa, seo, geo, ai, stamp))
        app = FastAPI()
        app.include_router(plugin_cloud.router)
        self.client = TestClient(app)

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def get(self, **overrides):
        now = int(time.time())
        payload = {"v": 1, "domain": "jij.nl", "plan": "pro", "lic": 7, "iat": now, "exp": now + 3600, **overrides}
        body = json.dumps(payload, separators=(",", ":")).encode()
        header = "Fajaede-Ticket " + b64url(body) + "." + b64url(self.key.sign(body))
        return self.client.get("/api/plugin/competitors", headers={"Authorization": header})

    def test_pro_sees_the_sites_around_it(self):
        data = self.get().json()
        self.assertTrue(data["measured"])
        self.assertEqual(data["your_scores"], {"psa": 60, "seo": 60, "geo": 40, "ai": 50})
        self.assertEqual([e["domain"] for e in data["ahead"]], ["midden.nl", "boven1.nl", "boven2.nl"])
        self.assertEqual(data["leader"]["domain"], "leider.nl")
        self.assertEqual(data["behind"]["domain"], "onder.nl")
        self.assertEqual(data["group_size"], 6)
        self.assertIn("mkb", data["cohort_label"].lower())
        self.assertNotIn("distribution", data)
        strengths = {s["metric"] for s in data["leader"]["ahead_on"]}
        self.assertTrue({"psa", "seo"} <= strengths)

    def test_free_plan_sees_its_own_scores_only(self):
        data = self.get(plan="free").json()
        self.assertTrue(data["locked"] and data["measured"])
        self.assertEqual(data["your_scores"]["psa"], 60)
        self.assertNotIn("ahead", data)

    def test_unmeasured_site_is_queued(self):
        data = self.get(domain="nieuw.nl").json()
        self.assertFalse(data["measured"])
        with store.connect() as conn:
            row = conn.execute("SELECT source FROM domains WHERE root_domain='nieuw.nl'").fetchone()
        self.assertEqual(row["source"], "plugin")

    def test_other_extensions_are_not_covered(self):
        data = self.get(domain="jij.com").json()
        self.assertFalse(data["covered"])
        self.assertFalse(data["measured"])

    def test_needs_a_valid_ticket(self):
        self.assertEqual(self.client.get("/api/plugin/competitors").status_code, 401)


if __name__ == "__main__":
    unittest.main()
