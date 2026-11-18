"""Web Bot Auth: signed requests so Cloudflare (and others) can verify our bots.

Profile of HTTP Message Signatures (RFC 9421) as Cloudflare verifies it:
- Ed25519 keys; keyid = JWK thumbprint (RFC 7638/8037), base64url without padding.
- Each request carries Signature-Agent ("<directory host>"), Signature-Input and Signature,
  covering "@authority" and "signature-agent", tag "web-bot-auth", a short expiry and a nonce.
- The public keys are published at /.well-known/http-message-signatures-directory
  (JWKS, content type application/http-message-signatures-directory+json), and that
  response is itself signed with ("@authority";req), tag "http-message-signatures-directory".

Two identities, two keys (see https://psa.fajaede.nl/bot):
- "scan"    FajaedeScan, key WEB_BOT_AUTH_SCAN_KEY, directory on psa.fajaede.nl
- "crawler" FajaedeBot,  key WEB_BOT_AUTH_CRAWLER_KEY, directory on api.fajaede.eu (this server)
A key is a random 32-byte Ed25519 seed, base64url. Without a key nothing is signed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response

SIGNATURE_LIFETIME = 60  # seconds; short window against replay
DIRECTORY_PATH = "/.well-known/http-message-signatures-directory"
DIRECTORY_CONTENT_TYPE = "application/http-message-signatures-directory+json"
IDENTITIES = {
    "scan": ("WEB_BOT_AUTH_SCAN_KEY", "https://psa.fajaede.nl"),
    "crawler": ("WEB_BOT_AUTH_CRAWLER_KEY", "https://api.fajaede.eu"),
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _private_key(kind: str) -> Any:
    seed = os.getenv(IDENTITIES[kind][0], "").strip()
    if not seed:
        return None
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # pylint: disable=import-outside-toplevel
    return Ed25519PrivateKey.from_private_bytes(_b64url_decode(seed))


def public_jwk(private_key: Any) -> dict[str, str]:
    from cryptography.hazmat.primitives import serialization  # pylint: disable=import-outside-toplevel
    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"kty": "OKP", "crv": "Ed25519", "x": _b64url(raw)}


def thumbprint(jwk: dict[str, str]) -> str:
    """RFC 7638 thumbprint: SHA-256 of the required members in lexicographic order."""
    canonical = json.dumps({"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"]}, separators=(",", ":"))
    return _b64url(hashlib.sha256(canonical.encode("ascii")).digest())


def authority(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    default = {"https": 443, "http": 80}.get(parts.scheme)
    return f"{host}:{parts.port}" if parts.port and parts.port != default else host


def _params(components: str, keyid: str, tag: str, now: int) -> str:
    return (f'({components});created={now};keyid="{keyid}";alg="ed25519";expires={now + SIGNATURE_LIFETIME}'
            f';nonce="{_b64url(secrets.token_bytes(32))}";tag="{tag}"')


def sign_request(url: str, kind: str, now: int | None = None) -> dict[str, str]:
    """Headers that sign a request to `url` as `kind` ("scan" or "crawler"); {} without a key."""
    key = _private_key(kind)
    if key is None:
        return {}
    created = int(now if now is not None else time.time())
    agent = f'"{IDENTITIES[kind][1]}"'
    params = _params('"@authority" "signature-agent"', thumbprint(public_jwk(key)), "web-bot-auth", created)
    base = f'"@authority": {authority(url)}\n"signature-agent": {agent}\n"@signature-params": {params}'
    signature = base64.b64encode(key.sign(base.encode("ascii"))).decode("ascii")
    return {"Signature-Agent": agent, "Signature-Input": f"sig1={params}", "Signature": f"sig1=:{signature}:"}


def directory(kind: str, host: str, now: int | None = None) -> tuple[bytes, dict[str, str]] | None:
    """The signed key directory for `kind`, served on `host`; None without a key."""
    key = _private_key(kind)
    if key is None:
        return None
    jwk = public_jwk(key)
    created = int(now if now is not None else time.time())
    params = _params('"@authority";req', thumbprint(jwk), "http-message-signatures-directory", created)
    base = f'"@authority";req: {host.lower()}\n"@signature-params": {params}'
    signature = base64.b64encode(key.sign(base.encode("ascii"))).decode("ascii")
    body = json.dumps({"keys": [jwk]}).encode("utf-8")
    return body, {"Content-Type": DIRECTORY_CONTENT_TYPE, "Signature-Input": f"sig1={params}",
                  "Signature": f"sig1=:{signature}:", "Cache-Control": "public, max-age=3600"}


router = APIRouter(tags=["web-bot-auth"])


@router.get(DIRECTORY_PATH)
async def crawler_key_directory(request: Request) -> Response:
    """FajaedeBot's public key (this server crawls as FajaedeBot)."""
    result = directory("crawler", request.headers.get("host", "api.fajaede.eu"))
    if result is None:
        return Response(status_code=404)
    body, headers = result
    return Response(content=body, headers=headers)
