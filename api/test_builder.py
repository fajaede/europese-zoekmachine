"""Tests for builder credits and bounded OpenAI generation."""

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from urllib.parse import parse_qs, urlsplit

from api.routes import builder
from api.routes.website_generator import WebsiteGeneratorService


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "builder.db")
        self.db_patch = patch.object(builder, "DB_PATH", self.db_path)
        self.db_patch.start()
        builder.init_builder_storage()
        connection = builder._connect()
        connection.execute(
            "INSERT INTO builder_accounts (id, email, created_at) VALUES (?, ?, ?)",
            ("account-1", "builder@example.test", int(time.time())),
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_free_generation_is_once_and_failed_generation_is_refunded(self):
        usage_id, charge_type = builder._reserve_usage("account-1")
        self.assertEqual(charge_type, "free")

        with self.assertRaises(HTTPException) as error:
            builder._reserve_usage("account-1")
        self.assertEqual(error.exception.status_code, 402)

        builder._finish_usage(usage_id, refund=True)
        usage_id, charge_type = builder._reserve_usage("account-1")
        self.assertEqual(charge_type, "free")
        builder._finish_usage(usage_id, refund=False)

        with self.assertRaises(HTTPException) as error:
            builder._reserve_usage("account-1")
        self.assertEqual(error.exception.status_code, 402)

    def test_credit_generation_consumes_and_refunds_one_credit(self):
        connection = builder._connect()
        connection.execute(
            "UPDATE builder_accounts SET free_used = 1, credits = 10 WHERE id = ?",
            ("account-1",),
        )
        connection.commit()
        connection.close()

        usage_id, charge_type = builder._reserve_usage("account-1")
        self.assertEqual(charge_type, "credit")
        connection = builder._connect()
        self.assertEqual(
            connection.execute(
                "SELECT credits FROM builder_accounts WHERE id = ?",
                ("account-1",),
            ).fetchone()["credits"],
            9,
        )
        connection.close()

        builder._finish_usage(usage_id, refund=True)
        connection = builder._connect()
        self.assertEqual(
            connection.execute(
                "SELECT credits FROM builder_accounts WHERE id = ?",
                ("account-1",),
            ).fetchone()["credits"],
            10,
        )
        connection.close()

    def test_expired_reservations_are_refunded(self):
        connection = builder._connect()
        connection.execute(
            "UPDATE builder_accounts SET free_used = 1, credits = 9 WHERE id = ?",
            ("account-1",),
        )
        connection.execute(
            """
            INSERT INTO builder_usage (id, account_id, charge_type, status, created_at)
            VALUES ('stale-credit', 'account-1', 'credit', 'reserved', ?)
            """,
            (int(time.time()) - 3600,),
        )
        connection.commit()
        connection.close()

        builder.init_builder_storage()
        connection = builder._connect()
        account = connection.execute(
            "SELECT credits FROM builder_accounts WHERE id = ?",
            ("account-1",),
        ).fetchone()
        usage = connection.execute(
            "SELECT status FROM builder_usage WHERE id = 'stale-credit'"
        ).fetchone()
        connection.close()
        self.assertEqual(account["credits"], 10)
        self.assertEqual(usage["status"], "refunded")

    def test_mollie_requires_test_key_and_public_https_webhook(self):
        with patch.dict(
            os.environ,
            {
                "MOLLIE_MODE": "test",
                "MOLLIE_API_KEY": "live_not-a-test-key",
                "BUILDER_WEBHOOK_URL": "https://fajaede.eu/api/builder/payments/webhook",
            },
        ):
            self.assertIsNone(builder._mollie_config())
            os.environ["MOLLIE_API_KEY"] = "test_example"
            self.assertEqual(builder._mollie_config(), ("test", "test_example"))
            os.environ["BUILDER_WEBHOOK_URL"] = "http://localhost/webhook"
            self.assertIsNone(builder._mollie_config())

    def test_verified_email_creates_session_and_one_free_credit(self):
        app = FastAPI()
        app.include_router(builder.router, prefix="/api/builder")
        with (
            TestClient(app) as client,
            patch.object(builder, "send_email") as send_email_mock,
            patch.dict(os.environ, {"BUILDER_PUBLIC_URL": "https://fajaede.eu"}),
        ):
            response = client.post(
                "/api/builder/auth/request-verification",
                json={"email": "new-user@example.com"},
            )
            self.assertEqual(response.status_code, 200)
            email_body = send_email_mock.call_args.args[2]
            verification_url = next(
                line for line in email_body.splitlines() if "?verify=" in line
            )
            token = parse_qs(urlsplit(verification_url).query)["verify"][0]

            response = client.post(
                "/api/builder/auth/verify",
                json={"token": token},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("builder_session", response.cookies)

            account = client.get("/api/builder/account").json()
            self.assertTrue(account["authenticated"])
            self.assertEqual(account["email"], "new-user@example.com")
            self.assertTrue(account["free_available"])
            self.assertEqual(account["credits"], 0)

            repeated_verification = client.post(
                "/api/builder/auth/verify",
                json={"token": token},
            )
            self.assertEqual(repeated_verification.status_code, 400)

    def test_verification_mail_is_limited_per_email_regardless_of_ip(self):
        app = FastAPI()
        app.include_router(builder.router, prefix="/api/builder")
        with (
            TestClient(app) as client,
            patch.object(builder, "send_email") as send_email_mock,
            patch.dict(os.environ, {"BUILDER_PUBLIC_URL": "https://fajaede.eu"}),
        ):
            statuses = [
                client.post(
                    "/api/builder/auth/request-verification",
                    json={"email": "victim@example.com"},
                    headers={"x-forwarded-for": f"10.0.0.{index}"},
                ).status_code
                for index in range(5)
            ]
        self.assertEqual(statuses, [200, 200, 200, 429, 429])
        self.assertEqual(send_email_mock.call_count, 3)

    def test_captcha_is_required_when_turnstile_is_configured(self):
        app = FastAPI()
        app.include_router(builder.router, prefix="/api/builder")
        turnstile_calls = []

        async def fake_post(_client, url, data=None, **_kwargs):
            turnstile_calls.append(data)
            request = builder.httpx.Request("POST", url)
            success = data["response"] == "good-token"
            return builder.httpx.Response(200, json={"success": success}, request=request)

        with (
            TestClient(app) as client,
            patch.object(builder, "send_email") as send_email_mock,
            patch.object(builder.httpx.AsyncClient, "post", fake_post),
            patch.dict(
                os.environ,
                {
                    "BUILDER_PUBLIC_URL": "https://fajaede.eu",
                    "TURNSTILE_SECRET_KEY": "test-secret",
                },
            ),
        ):
            missing = client.post(
                "/api/builder/auth/request-verification",
                json={"email": "user@example.com"},
            )
            bad = client.post(
                "/api/builder/auth/request-verification",
                json={"email": "user@example.com", "captcha_token": "bad-token"},
            )
            good = client.post(
                "/api/builder/auth/request-verification",
                json={"email": "user@example.com", "captcha_token": "good-token"},
            )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(good.status_code, 200)
        self.assertEqual(len(turnstile_calls), 2)
        self.assertEqual(turnstile_calls[0]["secret"], "test-secret")
        self.assertEqual(send_email_mock.call_count, 1)

    def test_disposable_email_domains_are_blocked(self):
        from api.disposable_domains import is_disposable_email

        self.assertTrue(is_disposable_email("x@mailinator.com"))
        self.assertTrue(is_disposable_email("x@sub.yopmail.com"))
        self.assertFalse(is_disposable_email("x@gmail.com"))
        self.assertFalse(is_disposable_email("x@fajaede.nl"))
        with patch.dict(os.environ, {"BUILDER_BLOCKED_EMAIL_DOMAINS": "spam.example, @junk.test"}):
            self.assertTrue(is_disposable_email("x@junk.test"))
            self.assertTrue(is_disposable_email("x@spam.example"))

        app = FastAPI()
        app.include_router(builder.router, prefix="/api/builder")
        with (
            TestClient(app) as client,
            patch.object(builder, "send_email") as send_email_mock,
            patch.dict(os.environ, {"BUILDER_PUBLIC_URL": "https://fajaede.eu"}),
        ):
            response = client.post(
                "/api/builder/auth/request-verification",
                json={"email": "someone@yopmail.com"},
            )
        self.assertEqual(response.status_code, 400)
        send_email_mock.assert_not_called()

    def test_cross_site_requests_are_rejected_and_logout_all_ends_sessions(self):
        app = FastAPI()
        app.include_router(builder.router, prefix="/api/builder")
        connection = builder._connect()
        for token in ("phone-session", "laptop-session"):
            connection.execute(
                "INSERT INTO builder_sessions (token_hash, account_id, expires_at) VALUES (?, ?, ?)",
                (builder._hash(token), "account-1", int(time.time()) + 3600),
            )
        connection.commit()
        connection.close()

        with (
            TestClient(app) as client,
            patch.dict(os.environ, {"BUILDER_PUBLIC_URL": "https://fajaede.eu"}),
        ):
            client.cookies.set(builder.SESSION_COOKIE, "phone-session")
            foreign = client.post(
                "/api/builder/generate",
                json={"industry": "horeca", "style": "modern", "description": "Een gezellig restaurant."},
                headers={"origin": "https://evil.example"},
            )
            self.assertEqual(foreign.status_code, 403)

            logout_all = client.post(
                "/api/builder/auth/logout-all",
                headers={"origin": "https://www.fajaede.eu"},
            )
            self.assertEqual(logout_all.status_code, 200)

        connection = builder._connect()
        remaining = connection.execute(
            "SELECT COUNT(*) AS n FROM builder_sessions WHERE account_id = 'account-1'"
        ).fetchone()["n"]
        free_used = connection.execute(
            "SELECT free_used FROM builder_accounts WHERE id = 'account-1'"
        ).fetchone()["free_used"]
        connection.close()
        self.assertEqual(remaining, 0)
        self.assertEqual(free_used, 0)  # the rejected request did not consume anything

    def test_normalize_email_merges_common_variants(self):
        n = builder.normalize_email
        self.assertEqual(n("Naam+promo@Gmail.com"), "naam@gmail.com")
        self.assertEqual(n("n.a.am@googlemail.com"), "naam@gmail.com")
        self.assertEqual(n("jan+1@outlook.com"), "jan@outlook.com")
        # Dots only matter for Gmail; other providers keep them.
        self.assertEqual(n("j.an@outlook.com"), "j.an@outlook.com")

    def test_email_variant_cannot_get_a_second_free_generation(self):
        connection = builder._connect()
        connection.execute(
            "INSERT INTO builder_accounts (id, email, created_at) VALUES (?, ?, ?)",
            ("variant", "n.aam+extra@gmail.com", int(time.time())),
        )
        connection.execute(
            "INSERT INTO builder_accounts (id, email, created_at) VALUES (?, ?, ?)",
            ("original", "naam@gmail.com", int(time.time())),
        )
        connection.commit()
        connection.close()

        usage_id, charge_type = builder._reserve_usage("original", "198.51.100.1")
        self.assertEqual(charge_type, "free")

        with self.assertRaises(HTTPException) as error:
            builder._reserve_usage("variant", "198.51.100.2")
        self.assertEqual(error.exception.status_code, 402)

        # A failed (refunded) free generation releases the claim again.
        builder._finish_usage(usage_id, refund=True)
        _, charge_type = builder._reserve_usage("variant", "198.51.100.2")
        self.assertEqual(charge_type, "free")

    def test_free_generations_are_capped_per_ip(self):
        connection = builder._connect()
        for index in range(5):
            connection.execute(
                "INSERT INTO builder_accounts (id, email, created_at) VALUES (?, ?, ?)",
                (f"free-{index}", f"free-{index}@example.test", int(time.time())),
            )
        connection.commit()
        connection.close()

        for index in range(3):
            _, charge_type = builder._reserve_usage(f"free-{index}", "203.0.113.9")
            self.assertEqual(charge_type, "free")
        with self.assertRaises(HTTPException) as error:
            builder._reserve_usage("free-3", "203.0.113.9")
        self.assertEqual(error.exception.status_code, 429)
        _, charge_type = builder._reserve_usage("free-4", "198.51.100.7")
        self.assertEqual(charge_type, "free")

    def test_generated_html_is_stripped_of_active_content(self):
        from api.routes.website_generator import _strip_active_content

        cleaned = _strip_active_content(
            '<html><body onload="x()"><script>a</script>'
            '<a href="javascript:alert(1)">x</a><img src=x onerror=alert(1)>'
            '<iframe src="https://evil.test"></iframe></body></html>'
        )
        for needle in ("<script", "onload", "onerror", "javascript:", "<iframe"):
            self.assertNotIn(needle, cleaned.lower())

    def test_checkout_fails_closed_when_mollie_is_not_configured(self):
        app = FastAPI()
        app.include_router(builder.router, prefix="/api/builder")
        session_token = "test-session-token"
        connection = builder._connect()
        connection.execute(
            "INSERT INTO builder_sessions (token_hash, account_id, expires_at) VALUES (?, ?, ?)",
            (builder._hash(session_token), "account-1", int(time.time()) + 3600),
        )
        connection.commit()
        connection.close()

        with (
            TestClient(app) as client,
            patch.dict(
                os.environ,
                {
                    "MOLLIE_MODE": "test",
                    "MOLLIE_API_KEY": "",
                    "BUILDER_WEBHOOK_URL": "https://fajaede.eu/api/builder/payments/webhook",
                    "BUILDER_PUBLIC_URL": "https://fajaede.eu",
                },
            ),
        ):
            client.cookies.set(builder.SESSION_COOKIE, session_token)
            without_consent = client.post("/api/builder/checkout")
            partial_consent = client.post(
                "/api/builder/checkout",
                json={"accept_terms": True, "waive_withdrawal": False},
            )
            response = client.post(
                "/api/builder/checkout",
                json={"accept_terms": True, "waive_withdrawal": True},
            )
        self.assertEqual(without_consent.status_code, 400)
        self.assertEqual(partial_consent.status_code, 400)
        self.assertEqual(response.status_code, 503)
        self.assertIn("Mollie", response.json()["detail"])

    def test_paid_mollie_webhook_grants_credits_once(self):
        order_id = "order-1"
        connection = builder._connect()
        connection.execute(
            """
            INSERT INTO builder_payments
                (order_id, account_id, credits, amount, mode, status, created_at)
            VALUES (?, ?, 10, '5.00', 'test', 'open', ?)
            """,
            (order_id, "account-1", int(time.time())),
        )
        connection.commit()
        connection.close()

        payment_data = {
            "id": "tr_test123",
            "mode": "test",
            "status": "paid",
            "amount": {"currency": "EUR", "value": "5.00"},
            "metadata": {
                "builder_order": order_id,
                "account_id": "account-1",
                "credits": 10,
            },
        }

        class FakePaymentResponse:
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
                return FakePaymentResponse()

        class FakeRequest:
            async def body(self):
                return b"id=tr_test123"

        request = FakeRequest()
        with (
            patch.object(builder, "_mollie_config", return_value=("test", "test_key")),
            patch.object(builder.httpx, "AsyncClient", FakeMollieClient),
            patch.object(builder, "send_email") as send_email_mock,
        ):
            asyncio.run(builder.mollie_webhook(request))
            asyncio.run(builder.mollie_webhook(request))

        # Exactly one purchase confirmation, recording the withdrawal waiver.
        self.assertEqual(send_email_mock.call_count, 1)
        self.assertEqual(send_email_mock.call_args.args[1], "builder@example.test")
        self.assertIn("herroepingsrecht", send_email_mock.call_args.args[2])

        connection = builder._connect()
        credits = connection.execute(
            "SELECT credits FROM builder_accounts WHERE id = ?",
            ("account-1",),
        ).fetchone()["credits"]
        connection.close()
        self.assertEqual(credits, 10)

    def test_website_generation_returns_static_html_without_scripts(self):
        class FakeCompletions:
            async def create(self, **kwargs):
                self_test.assertEqual(kwargs["model"], "gpt-4o-2024-08-06")
                self_test.assertLessEqual(kwargs["max_tokens"], 16000)
                self_test.assertIn("images.unsplash.com", kwargs["messages"][1]["content"])
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content=json.dumps(
                                    {
                                        "html": "<html><body><script>alert(1)</script><h1>Hallo</h1></body></html>",
                                        "site_brief": {
                                            "target_audience": "Lokale klanten",
                                            "keywords": ["bakker"],
                                            "core_value": "Vers brood",
                                        },
                                    }
                                )
                            )
                        )
                    ]
                )

        self_test = self
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        )
        result = asyncio.run(
            WebsiteGeneratorService().generate_website(
                {
                    "site_type": "general_business",
                    "industry": "Bakker",
                    "style": "Modern",
                    "description": "Een lokale ambachtelijke bakkerij.",
                },
                fake_client,
            )
        )
        self.assertIn("<h1>Hallo</h1>", result["html"])
        self.assertNotIn("<script", result["html"])
        self.assertEqual(result["site_brief"]["keywords"], ["bakker"])

    def test_json_ld_is_kept_and_unknown_images_are_replaced(self):
        from api.routes.website_generator import (
            ALLOWED_PHOTO_IDS,
            _restrict_images,
            _strip_active_content,
        )

        html = (
            '<html><head><title>x</title>'
            '<script type="application/ld+json">{"@type": "Restaurant", "name": "A</b> & B"}</script>'
            '<script>alert(1)</script></head><body>'
            '<img src="https://images.unsplash.com/photo-123-abc?w=10">'
            '<img src="https://evil.test/tracker.gif">'
            '<img src="https://images.unsplash.com/photo-1517248135467-4c7edcad34c4?w=10">'
            '</body></html>'
        )
        cleaned = _strip_active_content(html)
        self.assertIn('<script type="application/ld+json">', cleaned)
        self.assertIn('"@type": "Restaurant"', cleaned)
        self.assertNotIn("alert(1)", cleaned)
        self.assertEqual(cleaned.count("</script>"), 1)
        self.assertIn(r"A<\/b> & B", cleaned)

        restricted = _restrict_images(cleaned, "food")
        self.assertNotIn("evil.test", restricted)
        self.assertNotIn("photo-123-abc", restricted)
        self.assertIn("1517248135467-4c7edcad34c4", restricted)
        self.assertIn("1517248135467-4c7edcad34c4", ALLOWED_PHOTO_IDS)

    def test_photo_category_follows_site_type_and_keywords(self):
        from api.routes.website_generator import _photo_category

        self.assertEqual(_photo_category({"site_type": "restaurant"}), "food")
        self.assertEqual(
            _photo_category({"site_type": "general_business", "industry": "horeca"}),
            "food",
        )
        self.assertEqual(
            _photo_category({"site_type": "general_business", "industry": "IT-advies"}),
            "business",
        )


if __name__ == "__main__":
    unittest.main()
