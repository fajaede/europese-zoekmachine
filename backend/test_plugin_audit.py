"""Tests for the Fajaede Cloud site audit endpoints (plugin, ticket auth, limits per plan)."""

import base64
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import plugin_cloud
from backend.benchmark import site_audit, store


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


async def fake_validate(url):
    return url


class PluginAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key = Ed25519PrivateKey.generate()
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.patches = [
            mock.patch.dict(os.environ, {"PLUGIN_TICKET_PUBLIC_KEY": base64.b64encode(raw).decode()}),
            mock.patch.object(store, "DB_PATH", os.path.join(self.tmp.name, "benchmark.db")),
            mock.patch.object(plugin_cloud, "validate_public_url", fake_validate),
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

    def finish(self, audit_id, result=None, error=None):
        site_audit._finish(audit_id, result if error is None else None, error)

    def test_pro_starts_an_audit_of_its_own_domain(self):
        response = self.client.post("/api/plugin/site-audit", headers=self.headers())
        self.assertEqual(response.status_code, 202)
        data = response.json()
        audit = data["audit"]
        self.assertEqual((audit["url"], audit["max_pages"], audit["status"]), ("https://jij.nl/", 25, "queued"))
        self.assertEqual((data["quota"]["used"], data["quota"]["remaining"], data["quota"]["window_days"]), (1, 1, 7))

    def test_one_audit_at_a_time(self):
        first = self.client.post("/api/plugin/site-audit", headers=self.headers()).json()["audit"]["id"]
        second = self.client.post("/api/plugin/site-audit", headers=self.headers()).json()["audit"]["id"]
        self.assertEqual(first, second)

    def test_limits_per_plan_and_failed_audits_do_not_count(self):
        h = self.headers()
        a = self.client.post("/api/plugin/site-audit", headers=h).json()["audit"]["id"]
        self.finish(a, error="De website weigert onze scanner")                 # failed: does not count
        b = self.client.post("/api/plugin/site-audit", headers=h).json()["audit"]["id"]
        self.finish(b, result={"score": 80})
        c = self.client.post("/api/plugin/site-audit", headers=h).json()["audit"]["id"]
        self.finish(c, result={"score": 81})
        blocked = self.client.post("/api/plugin/site-audit", headers=h)
        self.assertEqual(blocked.status_code, 429)
        self.assertIsNotNone(blocked.json()["detail"]["next_at"])
        premium = self.client.post("/api/plugin/site-audit", headers=self.headers(plan="premium", domain="groot.nl"))
        self.assertEqual(premium.json()["audit"]["max_pages"], 100)

    def test_quota_window_moves_on(self):
        with mock.patch.object(site_audit, "audits_since", return_value=["2026-11-15T10:00:00+00:00"] * 2):
            quota = plugin_cloud.audit_quota("jij.nl", "pro", now=datetime(2026, 11, 20, tzinfo=timezone.utc))
        self.assertEqual((quota["remaining"], quota["next_at"]), (0, "2026-11-22T10:00:00+00:00"))
        self.assertIsNone(plugin_cloud.audit_quota("jij.nl", "free"))

    def test_status_shows_the_latest_report_only_for_this_domain(self):
        audit_id = self.client.post("/api/plugin/site-audit", headers=self.headers()).json()["audit"]["id"]
        self.finish(audit_id, result={"score": 77, "issues": []})
        mine = self.client.get("/api/plugin/site-audit", headers=self.headers()).json()
        self.assertEqual((mine["audit"]["status"], mine["audit"]["result"]["score"]), ("done", 77))
        other = self.client.get("/api/plugin/site-audit", headers=self.headers(domain="ander.nl")).json()
        self.assertIsNone(other["audit"])

    def test_free_plan_is_locked(self):
        self.assertEqual(self.client.post("/api/plugin/site-audit", headers=self.headers(plan="free")).status_code, 403)
        data = self.client.get("/api/plugin/site-audit", headers=self.headers(plan="free")).json()
        self.assertTrue(data["locked"])
        self.assertIsNone(data["audit"])
        self.assertIsNone(data["quota"])

    def test_any_extension_can_be_audited(self):
        response = self.client.post("/api/plugin/site-audit", headers=self.headers(domain="jij.com"))
        self.assertEqual(response.json()["audit"]["url"], "https://jij.com/")

    def test_old_requests_fall_out_of_the_window(self):
        old = (datetime.now(timezone.utc) - timedelta(days=8)).replace(microsecond=0).isoformat()
        store.init()
        with store.connect() as conn:
            for i in range(2):
                conn.execute("INSERT INTO site_audits (id, url, max_pages, owner, status, requested_at) VALUES (?, ?, 25, ?, 'done', ?)",
                             (f"old{i}", "https://jij.nl/", "plugin:jij.nl", old))
        self.assertEqual(plugin_cloud.audit_quota("jij.nl", "pro")["remaining"], 2)


if __name__ == "__main__":
    unittest.main()
