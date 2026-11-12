"""Determine the real visitor IP behind Vercel and the local nginx proxy.

Traffic reaches the backend two ways:
- via the Vercel rewrite (www.fajaede.eu/api/...): the visitor IP is in
  x-vercel-forwarded-for, and frontend-next/proxy.ts adds X-Edge-Secret;
- directly (api.fajaede.eu): nginx sets X-Real-IP to $remote_addr, which a
  client cannot spoof because nginx overwrites it.

x-vercel-forwarded-for is only trusted together with the shared secret, so a
direct caller cannot pick an arbitrary IP to dodge per-IP rate limits.
"""
import hmac
import ipaddress
import logging
import os

from fastapi import Request

logger = logging.getLogger(__name__)
_warned = False


def _valid_ip(value: str) -> str | None:
    value = value.split(",", maxsplit=1)[0].strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def client_ip(request: Request) -> str:
    global _warned  # pylint: disable=global-statement
    secret = os.getenv("EDGE_PROXY_SECRET", "").strip()
    vercel_ip = _valid_ip(request.headers.get("x-vercel-forwarded-for", ""))
    if secret:
        provided = request.headers.get("x-edge-secret", "")
        if vercel_ip and hmac.compare_digest(provided.encode("utf-8"), secret.encode("utf-8")):
            return vercel_ip
    elif vercel_ip:
        # Not configured yet: keep per-visitor limits working, but the header is spoofable.
        if not _warned:
            logger.warning("EDGE_PROXY_SECRET is not set; trusting x-vercel-forwarded-for unverified.")
            _warned = True
        return vercel_ip
    real_ip = _valid_ip(request.headers.get("x-real-ip", ""))
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"
