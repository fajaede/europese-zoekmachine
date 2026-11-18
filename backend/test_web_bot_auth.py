"""Tests for Web Bot Auth signing (format as Cloudflare verifies it)."""

import asyncio
import base64
import json
import os
import unittest
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from backend import seo_geo_audit, web_bot_auth as w

SEED = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")   # fixed test key, never used live


def verify(jwk, signature_header, base):
    raw = base64.b64decode(signature_header.split("=", 1)[1].strip(":"))
    Ed25519PublicKey.from_public_bytes(w._b64url_decode(jwk["x"])).verify(raw, base.encode())


class WebBotAuthTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"WEB_BOT_AUTH_SCAN_KEY": SEED, "WEB_BOT_AUTH_CRAWLER_KEY": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_thumbprint_rfc8037_example(self):
        # RFC 8037 Appendix A.3
        jwk = {"kty": "OKP", "crv": "Ed25519", "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"}
        self.assertEqual(w.thumbprint(jwk), "kPrK_qmxVWaYVA9wwBF6Iuo3vVzz7TxHCTwXBygrS4k")

    def test_authority(self):
        self.assertEqual(w.authority("https://WWW.Example.nl/pad?x=1"), "www.example.nl")
        self.assertEqual(w.authority("https://example.nl:443/"), "example.nl")
        self.assertEqual(w.authority("https://example.nl:8443/"), "example.nl:8443")

    def test_request_signature(self):
        h = w.sign_request("https://Shop.nl/winkel", "scan", now=1_700_000_000)
        self.assertEqual(h["Signature-Agent"], '"https://psa.fajaede.nl"')        # quoted, single value
        params = h["Signature-Input"].removeprefix("sig1=")
        self.assertTrue(params.startswith('("@authority" "signature-agent");created=1700000000;keyid="'))
        self.assertIn(';alg="ed25519";expires=1700000060;nonce="', params)
        self.assertTrue(params.endswith(';tag="web-bot-auth"'))
        self.assertTrue(h["Signature"].startswith("sig1=:") and h["Signature"].endswith(":"))
        jwk = w.public_jwk(w._private_key("scan"))
        self.assertIn(f'keyid="{w.thumbprint(jwk)}"', params)
        verify(jwk, h["Signature"],
               f'"@authority": shop.nl\n"signature-agent": "https://psa.fajaede.nl"\n"@signature-params": {params}')
        # no unsupported parameters
        for bad in (";req", ";sf", ";bs", ";key=", "@query-params", "@status"):
            self.assertNotIn(bad, params)

    def test_without_key_nothing_is_signed(self):
        self.assertEqual(w.sign_request("https://x.nl/", "crawler"), {})
        self.assertIsNone(w.directory("crawler", "api.fajaede.eu"))

    def test_directory_is_signed_jwks(self):
        body, headers = w.directory("scan", "PSA.fajaede.nl", now=1_700_000_000)
        jwks = json.loads(body)
        self.assertEqual(list(jwks["keys"][0]), ["kty", "crv", "x"])             # never "d"
        self.assertEqual(headers["Content-Type"], "application/http-message-signatures-directory+json")
        params = headers["Signature-Input"].removeprefix("sig1=")
        self.assertTrue(params.startswith('("@authority";req);created=1700000000;'))
        self.assertTrue(params.endswith(';tag="http-message-signatures-directory"'))
        verify(jwks["keys"][0], headers["Signature"], f'"@authority";req: psa.fajaede.nl\n"@signature-params": {params}')

    def test_fetch_signs_every_hop_with_the_right_identity(self):
        seen = []

        def handler(request):
            seen.append((request.url.host, request.headers.get("signature-agent"), request.headers.get("signature-input")))
            if request.url.host == "a.nl":
                return httpx.Response(301, headers={"location": "https://www.a.nl/"})
            return httpx.Response(200, text="ok", headers={"content-type": "text/html"})

        real_client = httpx.AsyncClient

        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        async def same(url):
            return url

        with patch.dict(os.environ, {"WEB_BOT_AUTH_CRAWLER_KEY": SEED}), \
                patch.object(seo_geo_audit.httpx, "AsyncClient", client), \
                patch.object(seo_geo_audit, "validate_public_url", same):
            asyncio.run(seo_geo_audit.fetch_public_resource("https://a.nl/"))                       # scan (default UA)
            asyncio.run(seo_geo_audit.fetch_public_resource(
                "https://b.nl/", headers={"User-Agent": seo_geo_audit.CRAWLER_USER_AGENT}))           # crawler
        self.assertEqual([s[0] for s in seen], ["a.nl", "www.a.nl", "b.nl"])
        self.assertEqual([s[1] for s in seen[:2]], ['"https://psa.fajaede.nl"'] * 2)                  # each hop signed
        self.assertEqual(seen[2][1], '"https://api.fajaede.eu"')
        self.assertTrue(all(s[2] for s in seen))


if __name__ == "__main__":
    unittest.main()
