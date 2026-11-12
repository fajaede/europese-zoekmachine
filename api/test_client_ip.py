"""Tests for trusting forwarded visitor IPs only with the edge secret."""

# pylint: disable=missing-function-docstring,missing-class-docstring

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.client_ip import client_ip


def fake_request(headers):
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host="127.0.0.1"))


class ClientIpTests(unittest.TestCase):

    def test_vercel_ip_trusted_only_with_matching_secret(self):
        with patch.dict(os.environ, {"EDGE_PROXY_SECRET": "s3cret"}):
            via_vercel = fake_request(
                {"x-vercel-forwarded-for": "198.51.100.1", "x-edge-secret": "s3cret", "x-real-ip": "76.76.21.1"}
            )
            spoofed = fake_request(
                {"x-vercel-forwarded-for": "198.51.100.1", "x-edge-secret": "wrong", "x-real-ip": "203.0.113.9"}
            )
            self.assertEqual(client_ip(via_vercel), "198.51.100.1")
            self.assertEqual(client_ip(spoofed), "203.0.113.9")

    def test_without_secret_falls_back_to_legacy_and_nginx_ip(self):
        with patch.dict(os.environ, {"EDGE_PROXY_SECRET": ""}):
            self.assertEqual(client_ip(fake_request({"x-vercel-forwarded-for": "198.51.100.1"})), "198.51.100.1")
            self.assertEqual(client_ip(fake_request({"x-real-ip": "203.0.113.9"})), "203.0.113.9")
            self.assertEqual(client_ip(fake_request({"x-real-ip": "not-an-ip"})), "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
