"""Tests for Fajaede Cloud tickets and the plugin authority endpoint."""

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
from backend.benchmark import authority, backlinks, store
from backend.test_backlinks import EDGES, RANKS, VERTICES, as_bytes


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class TicketTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.env = mock.patch.dict(os.environ, {"PLUGIN_TICKET_PUBLIC_KEY": base64.b64encode(raw).decode()})
        self.env.start()
        plugin_cloud._hits.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "authority.db")
        authority.import_ranks([f"{r}\n" for r in RANKS], "cc-main-2026-aug-sep-oct", self.path)
        backlinks.import_backlinks(as_bytes(VERTICES), as_bytes(f"{a}\t{b}" for a, b in EDGES),
                                   "cc-main-2026-aug-sep-oct", self.path)
        self.db = mock.patch.object(authority, "DB_PATH", self.path)
        self.db.start()
        self.bench = mock.patch.object(store, "DB_PATH", os.path.join(self.tmp.name, "benchmark.db"))
        self.bench.start()
        app = FastAPI()
        app.include_router(plugin_cloud.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.bench.stop()
        self.db.stop()
        self.env.stop()
        self.tmp.cleanup()

    def ticket(self, key=None, **overrides):
        now = int(time.time())
        payload = {"v": 1, "domain": "jij.nl", "plan": "pro", "lic": 7, "iat": now, "exp": now + 12 * 3600}
        payload.update(overrides)
        body = json.dumps(payload, separators=(",", ":")).encode()
        return "Fajaede-Ticket " + b64url(body) + "." + b64url((key or self.key).sign(body))

    def get(self, header):
        return self.client.get("/api/plugin/authority", headers={"Authorization": header} if header else {})

    def test_pro_ticket_gets_authority_and_links(self):
        response = self.get(self.ticket())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual((data["domain"], data["plan"], data["locked"], data["covered"]), ("jij.nl", "pro", False, True))
        self.assertEqual(data["authority"]["of"], 4)
        self.assertEqual(data["links"]["referring_domains"], 8)
        self.assertEqual(data["links"]["top"][0]["domain"], "groot.nl")
        self.assertEqual(data["release"], "cc-main-2026-aug-sep-oct")

    def test_free_ticket_only_gets_the_score(self):
        data = self.get(self.ticket(plan="free")).json()
        self.assertTrue(data["locked"])
        self.assertIsNotNone(data["authority"])
        self.assertIsNone(data["links"])

    def test_invalid_tickets_are_refused(self):
        other = Ed25519PrivateKey.generate()
        now = int(time.time())
        cases = {
            "no header": None,
            "wrong scheme": "Bearer abc",
            "foreign key": self.ticket(key=other),
            "expired": self.ticket(iat=now - 13 * 3600, exp=now - 60),
            "too long": self.ticket(exp=now + 48 * 3600),
            "issued in the future": self.ticket(iat=now + 3600, exp=now + 7200),
            "unknown plan": self.ticket(plan="gold"),
            "not a domain": self.ticket(domain="localhost"),
            "garbage": "Fajaede-Ticket abc.def",
        }
        for label, header in cases.items():
            self.assertEqual(self.get(header).status_code, 401, label)
        # a valid signature over a changed payload: the "domain" swapped after signing
        parts = self.ticket().split(" ", 1)[1].split(".")
        payload = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
        payload["domain"] = "groot.nl"
        tampered = "Fajaede-Ticket " + b64url(json.dumps(payload).encode()) + "." + parts[1]
        self.assertEqual(self.get(tampered).status_code, 401, "payload changed after signing")

    def test_answers_only_about_the_ticket_domain(self):
        data = self.get(self.ticket(domain="sterk.nl")).json()
        self.assertEqual(data["domain"], "sterk.nl")
        self.assertEqual(data["links"]["referring_domains"], 1)

    def test_domain_outside_nl_be_is_not_covered(self):
        data = self.get(self.ticket(domain="example.com")).json()
        self.assertFalse(data["covered"])
        self.assertIsNone(data["authority"])
        self.assertIsNone(data["links"])

    def test_rate_limit_per_domain(self):
        with mock.patch.object(plugin_cloud, "RATE_LIMIT", 2):
            self.assertEqual(self.get(self.ticket()).status_code, 200)
            self.assertEqual(self.get(self.ticket()).status_code, 200)
            self.assertEqual(self.get(self.ticket()).status_code, 429)
            self.assertEqual(self.get(self.ticket(domain="sterk.nl")).status_code, 200)

    def test_not_configured_is_503(self):
        with mock.patch.dict(os.environ, {"PLUGIN_TICKET_PUBLIC_KEY": ""}):
            self.assertEqual(self.get(self.ticket()).status_code, 503)


if __name__ == "__main__":
    unittest.main()
