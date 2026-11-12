"""Tests for Developer API Pro/Premium passes and the Mollie webhook."""

# Tests deliberately exercise private helpers and use small inline fakes.
# pylint: disable=protected-access,missing-function-docstring,missing-class-docstring
# pylint: disable=too-few-public-methods,consider-using-with

import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import developer_api

DAY = 86400


class DeveloperPlanTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(developer_api, "DB_PATH", str(Path(self.temp_dir.name) / "keys.db"))
        self.db_patch.start()
        developer_api.init_db()
        conn = developer_api.get_db()
        conn.execute(
            "INSERT INTO api_keys (email, api_key, project_name) VALUES (?, ?, ?)",
            ("dev@example.test", "sk_eu_testkey", "Test"),
        )
        conn.commit()
        conn.close()
        app = FastAPI()
        app.include_router(developer_api.router, prefix="/api/developers")
        self.client = TestClient(app)

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _key_row(self):
        conn = developer_api.get_db()
        row = conn.execute("SELECT * FROM api_keys WHERE api_key = ?", ("sk_eu_testkey",)).fetchone()
        conn.close()
        return row

    def _set_plan(self, plan, expires_at):
        conn = developer_api.get_db()
        conn.execute("UPDATE api_keys SET plan = ?, plan_expires_at = ?", (plan, expires_at))
        conn.commit()
        conn.close()

    def test_free_limit_until_pass_and_fallback_after_expiry(self):
        self.assertEqual(developer_api.effective_daily_limit(self._key_row()), 100)
        self._set_plan("premium", int(time.time()) + DAY)
        self.assertEqual(developer_api.effective_daily_limit(self._key_row()), 10000)
        self._set_plan("premium", int(time.time()) - 1)
        self.assertEqual(developer_api.active_plan(self._key_row()), ("free", 0))
        self.assertEqual(developer_api.effective_daily_limit(self._key_row()), 100)

    def test_same_plan_stacks_and_upgrade_converts_pro_rata(self):
        now = 1_000_000
        self.assertEqual(developer_api.extend_plan("free", 0, "pro", 90, now), now + 90 * DAY)
        self.assertEqual(
            developer_api.extend_plan("pro", now + 10 * DAY, "pro", 30, now), now + 40 * DAY
        )
        # 29 leftover Pro days (EUR 9) are worth 9 Premium days (EUR 29).
        upgraded = developer_api.extend_plan("pro", now + 29 * DAY, "premium", 30, now)
        self.assertEqual(upgraded, now + 30 * DAY + 9 * DAY)

    def test_checkout_requires_consent_and_blocks_downgrade(self):
        headers = {"X-API-Key": "sk_eu_testkey"}
        no_consent = self.client.post(
            "/api/developers/checkout", headers=headers, json={"plan": "pro", "days": 30}
        )
        self.assertEqual(no_consent.status_code, 400)

        bad_days = self.client.post(
            "/api/developers/checkout",
            headers=headers,
            json={"plan": "pro", "days": 60, "accept_terms": True, "immediate_start": True},
        )
        self.assertEqual(bad_days.status_code, 400)

        self._set_plan("premium", int(time.time()) + 10 * DAY)
        downgrade = self.client.post(
            "/api/developers/checkout",
            headers=headers,
            json={"plan": "pro", "days": 30, "accept_terms": True, "immediate_start": True},
        )
        self.assertEqual(downgrade.status_code, 409)

        with patch.dict(os.environ, {"MOLLIE_API_KEY": "", "MOLLIE_MODE": "test"}):
            unconfigured = self.client.post(
                "/api/developers/checkout",
                headers=headers,
                json={"plan": "premium", "days": 90, "accept_terms": True, "immediate_start": True},
            )
        self.assertEqual(unconfigured.status_code, 503)

        conn = developer_api.get_db()
        orders = conn.execute("SELECT COUNT(*) FROM developer_payments").fetchone()[0]
        conn.close()
        self.assertEqual(orders, 0)

    def test_paid_webhook_activates_pass_once(self):
        conn = developer_api.get_db()
        key_id = conn.execute("SELECT id FROM api_keys").fetchone()[0]
        conn.execute(
            "INSERT INTO developer_payments (order_id, key_id, plan, days, amount, mode, status, created_at) "
            "VALUES ('order-1', ?, 'pro', 90, '24.00', 'test', 'open', ?)",
            (key_id, int(time.time())),
        )
        conn.commit()
        conn.close()

        payment_data = {
            "id": "tr_dev123",
            "mode": "test",
            "status": "paid",
            "amount": {"currency": "EUR", "value": "24.00"},
            "metadata": {"developer_order": "order-1", "plan": "pro", "days": 90},
        }

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return payment_data

        class FakeMollieClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, *_args, **_kwargs):
                return FakeResponse()

        class FakeRequest:
            async def body(self):
                return b"id=tr_dev123"

        config = ("test", "test_key", "https://api.example.test/api/developers/payments/webhook", "https://api.example.test")
        with (
            patch.object(developer_api, "_mollie_config", return_value=config),
            patch.object(developer_api.httpx, "AsyncClient", FakeMollieClient),
            patch.object(developer_api, "send_email") as send_email_mock,
        ):
            asyncio.run(developer_api.developer_mollie_webhook(FakeRequest()))
            asyncio.run(developer_api.developer_mollie_webhook(FakeRequest()))

        self.assertEqual(send_email_mock.call_count, 1)
        self.assertIn("Pro-pas, 90 dagen", send_email_mock.call_args.args[2])
        plan, expires_at = developer_api.active_plan(self._key_row())
        self.assertEqual(plan, "pro")
        self.assertAlmostEqual(expires_at, time.time() + 90 * DAY, delta=60)

        # A wrong amount from Mollie never activates anything.
        payment_data["amount"] = {"currency": "EUR", "value": "0.01"}
        with (
            patch.object(developer_api, "_mollie_config", return_value=config),
            patch.object(developer_api.httpx, "AsyncClient", FakeMollieClient),
        ):
            with self.assertRaises(developer_api.HTTPException):
                asyncio.run(developer_api.developer_mollie_webhook(FakeRequest()))

    def test_registration_mail_is_limited_per_email_across_ips(self):
        sent = []
        with (
            patch.dict(os.environ, {"DEVELOPER_VERIFICATION_BASE_URL": "https://api.example.test"}),
            patch.object(developer_api, "_send_verification_email", lambda *args: sent.append(args)),
        ):
            codes = [
                self.client.post(
                    "/api/developers/register",
                    headers={"X-Real-IP": f"198.51.100.{i}"},
                    json={"email": "victim@example.com"},
                ).status_code
                for i in range(5)
            ]
        self.assertEqual(codes, [202, 202, 202, 429, 429])
        self.assertEqual(len(sent), 3)

    def test_registration_uses_forwarded_ip_not_proxy_address(self):
        with (
            patch.dict(os.environ, {"DEVELOPER_VERIFICATION_BASE_URL": "https://api.example.test"}),
            patch.object(developer_api, "_send_verification_email", lambda *args: None),
        ):
            for i in range(6):
                response = self.client.post(
                    "/api/developers/register",
                    headers={"X-Real-IP": "203.0.113.7"},
                    json={"email": f"user{i}@example.com"},
                )
            other_visitor = self.client.post(
                "/api/developers/register",
                headers={"X-Real-IP": "203.0.113.8"},
                json={"email": "someone@example.com"},
            )
        self.assertEqual(response.status_code, 429)  # 6th from the same visitor
        self.assertEqual(other_visitor.status_code, 202)  # others are not blocked

    def test_captcha_required_when_site_key_is_configured(self):
        with patch.dict(os.environ, {"TURNSTILE_SITE_KEY": "site", "TURNSTILE_SECRET_KEY": "secret"}):
            response = self.client.post("/api/developers/register", json={"email": "a@example.com"})
        self.assertEqual(response.status_code, 400)

    def test_free_tier_has_a_global_daily_cap(self):
        with patch.object(developer_api, "FREE_GLOBAL_DAY", 1), patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            first = self.client.post(
                "/api/developers/seo/generate",
                headers={"X-API-Key": "sk_eu_testkey"},
                json={"content": "Bakkerij in Antwerpen"},
            )
            second = self.client.post(
                "/api/developers/seo/generate",
                headers={"X-API-Key": "sk_eu_testkey"},
                json={"content": "Bakkerij in Antwerpen"},
            )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 503)

    def test_output_is_plain_text(self):
        self.assertEqual(
            developer_api.clean_text('<script>alert(1)</script>Titel\x00 <b>vet</b>  "x"', 60),
            'alert(1)Titel vet "x"',
        )
        self.assertEqual(developer_api.clean_text("a <img src=x onerror=1", 60), "a img src=x onerror=1")
        self.assertEqual(len(developer_api.clean_text("x" * 500, 60)), 60)

    def test_usage_reports_plan_and_rotation_keeps_it(self):
        expires = int(time.time()) + 30 * DAY
        self._set_plan("pro", expires)
        usage = self.client.get("/api/developers/usage", headers={"X-API-Key": "sk_eu_testkey"}).json()
        self.assertEqual((usage["plan"], usage["daily_limit"], usage["plan_expires_at"]), ("pro", 2000, expires))

        token = developer_api.create_verification("dev@example.test", "Test")
        new_key, _, _ = developer_api.verify_registration_token(token)
        conn = developer_api.get_db()
        row = conn.execute("SELECT * FROM api_keys WHERE api_key = ?", (new_key,)).fetchone()
        conn.close()
        self.assertEqual(developer_api.active_plan(row), ("pro", expires))


if __name__ == "__main__":
    unittest.main()
