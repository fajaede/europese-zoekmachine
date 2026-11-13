"""Website builder generation, verified-email sessions, and Mollie credits."""

import asyncio
import hashlib
import logging
import os
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from api.client_ip import client_ip as shared_client_ip
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field

from api.disposable_domains import is_disposable_email
from api.email_utils import send_email
from api.routes.website_generator import WebsiteGeneratorService

router = APIRouter(tags=["builder"])
logger = logging.getLogger(__name__)

DB_PATH = os.getenv(
    "BUILDER_DB_PATH",
    os.getenv(
        "DEVELOPER_KEYS_DB_PATH",
        str(Path(__file__).resolve().parents[2] / "backend" / "developer_keys.db"),
    ),
)
SESSION_COOKIE = "builder_session"
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
VERIFICATION_TTL_SECONDS = 30 * 60
CREDIT_PACK_SIZE = 10
CREDIT_PACK_PRICE = "5.00"
MOLLIE_API_BASE = "https://api.mollie.com/v2"


TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TERMS_VERSION = "2026-11-12"


class CheckoutRequest(BaseModel):
    accept_terms: bool = False
    waive_withdrawal: bool = False


class VerificationRequest(BaseModel):
    email: EmailStr
    captcha_token: str = Field(default="", max_length=2048)


class VerificationConfirmation(BaseModel):
    token: str = Field(min_length=1, max_length=128)


class WebsiteRequest(BaseModel):
    site_type: Literal[
        "general_business",
        "law_firm",
        "restaurant",
        "webshop",
        "portfolio",
        "medical",
    ] = "general_business"
    industry: str = Field(min_length=2, max_length=100)
    style: str = Field(min_length=2, max_length=100)
    description: str = Field(min_length=10, max_length=1500)


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def init_builder_storage() -> None:
    """Create persistent builder tables and recover abandoned reservations."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    connection = _connect()
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS builder_accounts (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                free_used INTEGER NOT NULL DEFAULT 0,
                credits INTEGER NOT NULL DEFAULT 0 CHECK (credits >= 0),
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS builder_verifications (
                email TEXT PRIMARY KEY COLLATE NOCASE,
                token_hash TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS builder_sessions (
                token_hash TEXT PRIMARY KEY,
                account_id TEXT NOT NULL REFERENCES builder_accounts(id),
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS builder_payments (
                order_id TEXT PRIMARY KEY,
                mollie_id TEXT UNIQUE,
                account_id TEXT NOT NULL REFERENCES builder_accounts(id),
                credits INTEGER NOT NULL CHECK (credits = 10),
                amount TEXT NOT NULL CHECK (amount = '5.00'),
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                granted_at INTEGER,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS builder_usage (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL REFERENCES builder_accounts(id),
                charge_type TEXT NOT NULL CHECK (charge_type IN ('free', 'credit')),
                status TEXT NOT NULL CHECK (status IN ('reserved', 'completed', 'refunded')),
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS builder_rate_limits (
                scope_hash TEXT NOT NULL,
                window_start INTEGER NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (scope_hash, window_start)
            );
            CREATE TABLE IF NOT EXISTS builder_free_claims (
                email_key TEXT PRIMARY KEY,
                account_id TEXT NOT NULL REFERENCES builder_accounts(id),
                claimed_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_builder_sessions_account
                ON builder_sessions(account_id);
            CREATE INDEX IF NOT EXISTS idx_builder_payments_account
                ON builder_payments(account_id, created_at);
            """
        )
        payment_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(builder_payments)")
        }
        for column, definition in (("terms_version", "TEXT"), ("consent_at", "INTEGER")):
            if column not in payment_columns:
                connection.execute(f"ALTER TABLE builder_payments ADD COLUMN {column} {definition}")
        now = int(time.time())
        stale_before = now - 15 * 60
        connection.execute("BEGIN IMMEDIATE")
        # Backfill free claims for accounts that used their free request before claims existed.
        for row in connection.execute(
            "SELECT id, email, created_at FROM builder_accounts WHERE free_used = 1"
        ).fetchall():
            connection.execute(
                "INSERT OR IGNORE INTO builder_free_claims (email_key, account_id, claimed_at) VALUES (?, ?, ?)",
                (normalize_email(row["email"]), row["id"], row["created_at"]),
            )
        # Housekeeping: drop expired sessions/verifications and old rate-limit buckets.
        connection.execute("DELETE FROM builder_sessions WHERE expires_at < ?", (now,))
        connection.execute("DELETE FROM builder_verifications WHERE expires_at < ?", (now,))
        connection.execute(
            "DELETE FROM builder_rate_limits WHERE window_start < ?",
            (now - 2 * 24 * 60 * 60,),
        )
        stale_usage = connection.execute(
            """
            SELECT id, account_id, charge_type FROM builder_usage
            WHERE status = 'reserved' AND created_at < ?
            """,
            (stale_before,),
        ).fetchall()
        for usage in stale_usage:
            if usage["charge_type"] == "free":
                _release_free(connection, usage["account_id"])
            else:
                connection.execute(
                    "UPDATE builder_accounts SET credits = credits + 1 WHERE id = ?",
                    (usage["account_id"],),
                )
            connection.execute(
                "UPDATE builder_usage SET status = 'refunded' WHERE id = ?",
                (usage["id"],),
            )
        connection.commit()
    finally:
        connection.close()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


def normalize_email(email: str) -> str:
    """Canonical form used only to count the free request and per-address limits.

    Strips "+tag" sub-addressing for every domain and dots for Gmail, so
    naam+1@gmail.com and n.aam@gmail.com count as naam@gmail.com. Accounts and
    mail delivery keep using the exact address the user verified.
    """
    local, _, domain = email.strip().lower().rpartition("@")
    if not local:
        return email.strip().lower()
    local = local.split("+", 1)[0] or local
    if domain in GMAIL_DOMAINS:
        domain = "gmail.com"
        local = local.replace(".", "") or local
    return f"{local}@{domain}"


def _free_available(connection: sqlite3.Connection, account_id: str, email: str, free_used: int) -> bool:
    if free_used:
        return False
    claim = connection.execute(
        "SELECT account_id FROM builder_free_claims WHERE email_key = ?",
        (normalize_email(email),),
    ).fetchone()
    return claim is None or claim["account_id"] == account_id


def _release_free(connection: sqlite3.Connection, account_id: str) -> None:
    connection.execute("UPDATE builder_accounts SET free_used = 0 WHERE id = ?", (account_id,))
    connection.execute("DELETE FROM builder_free_claims WHERE account_id = ?", (account_id,))


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


# (scope, window seconds, max requests) - tunable via environment variables.
VERIFY_LIMITS = (
    ("verify-ip", 15 * 60, _env_int("BUILDER_VERIFY_PER_IP_15M", 5)),
    ("verify-email", 60 * 60, _env_int("BUILDER_VERIFY_PER_EMAIL_HOUR", 3)),
    ("verify-global", 60 * 60, _env_int("BUILDER_VERIFY_GLOBAL_HOUR", 100)),
)
GENERATE_LIMITS = (
    ("generate-account", 60 * 60, _env_int("BUILDER_GENERATE_PER_ACCOUNT_HOUR", 10)),
    ("generate-ip", 60 * 60, _env_int("BUILDER_GENERATE_PER_IP_HOUR", 15)),
)
FREE_LIMITS = (
    ("free-ip", 24 * 60 * 60, _env_int("BUILDER_FREE_PER_IP_DAY", 3)),
    ("free-global", 24 * 60 * 60, _env_int("BUILDER_FREE_GLOBAL_DAY", 200)),
)


def _client_ip(request: Request) -> str:
    """Real visitor IP; see api/client_ip.py for which headers are trusted."""
    return shared_client_ip(request)


async def _verify_captcha(token: str, client_ip: str) -> None:
    """Validate a Cloudflare Turnstile token when TURNSTILE_SECRET_KEY is set."""
    secret = os.getenv("TURNSTILE_SECRET_KEY", "").strip()
    if not secret:
        logger.warning("TURNSTILE_SECRET_KEY is not set; builder captcha is disabled.")
        return
    if not token:
        raise HTTPException(status_code=400, detail="Bevestig eerst dat je geen robot bent.")
    data = {"secret": secret, "response": token}
    if client_ip and client_ip != "unknown":
        data["remoteip"] = client_ip
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(TURNSTILE_VERIFY_URL, data=data)
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError):
        logger.exception("Could not verify Turnstile token")
        raise HTTPException(
            status_code=503,
            detail="De captcha kon niet worden gecontroleerd. Probeer het opnieuw.",
        ) from None
    if result.get("success") is not True:
        raise HTTPException(
            status_code=400,
            detail="Captcha ongeldig of verlopen. Probeer het opnieuw.",
        )


def _consume_rate_limits(
    connection: sqlite3.Connection,
    checks: list[tuple[str, str, int, int]],
) -> bool:
    """Increment every (scope, key, window, limit) bucket; False if any is exceeded.

    Must run inside a transaction; the caller rolls back when False is returned.
    """
    now = int(time.time())
    # Keep hashed IP/e-mail counters at most two days (as stated in the privacy policy).
    connection.execute(
        "DELETE FROM builder_rate_limits WHERE window_start < ?",
        (now - 2 * 24 * 60 * 60,),
    )
    allowed = True
    for scope, key, window, limit in checks:
        scope_hash = _hash(f"{scope}:{key}")
        bucket = now - now % window  # window start as a unix timestamp
        connection.execute(
            """
            INSERT INTO builder_rate_limits (scope_hash, window_start, request_count)
            VALUES (?, ?, 1)
            ON CONFLICT(scope_hash, window_start)
            DO UPDATE SET request_count = request_count + 1
            """,
            (scope_hash, bucket),
        )
        count = connection.execute(
            "SELECT request_count FROM builder_rate_limits WHERE scope_hash = ? AND window_start = ?",
            (scope_hash, bucket),
        ).fetchone()["request_count"]
        if count > limit:
            allowed = False
    return allowed


def _get_session_account(request: Request) -> sqlite3.Row:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or len(token) > 128:
        raise HTTPException(status_code=401, detail="Bevestig eerst je e-mailadres.")

    connection = _connect()
    try:
        account = connection.execute(
            """
            SELECT a.id, a.email, a.free_used, a.credits
            FROM builder_sessions s
            JOIN builder_accounts a ON a.id = s.account_id
            WHERE s.token_hash = ? AND s.expires_at > ?
            """,
            (_hash(token), int(time.time())),
        ).fetchone()
        if not account:
            raise HTTPException(status_code=401, detail="Sessie verlopen; bevestig je e-mailadres opnieuw.")
        return account
    finally:
        connection.close()


def _require_same_origin(request: Request) -> None:
    """Reject browser requests that come from another site (CSRF defence in depth).

    Browsers send an Origin header on POST requests; requests without one (server
    to server, tests) are left to the SameSite cookie protection.
    """
    origin = request.headers.get("origin", "").strip()
    public_url = os.getenv("BUILDER_PUBLIC_URL", "").strip().rstrip("/")
    if not origin or not public_url:
        return
    allowed = set()
    parsed = urlsplit(public_url)
    if parsed.hostname:
        host = parsed.hostname.lower()
        bare = host[4:] if host.startswith("www.") else host
        for candidate in (bare, f"www.{bare}"):
            allowed.add(f"{parsed.scheme}://{candidate}")
    for extra in os.getenv("BUILDER_ALLOWED_ORIGINS", "").split(","):
        if extra.strip():
            allowed.add(extra.strip().rstrip("/").lower())
    if origin.lower().rstrip("/") not in allowed:
        raise HTTPException(status_code=403, detail="Verzoek van een onbekende website geweigerd.")


def _public_url() -> str:
    value = os.getenv("BUILDER_PUBLIC_URL", "").strip().rstrip("/")
    if not value:
        raise HTTPException(status_code=503, detail="BUILDER_PUBLIC_URL is niet geconfigureerd.")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"})
    ):
        raise HTTPException(status_code=503, detail="De builder-URL is niet veilig geconfigureerd.")
    return value


def _mollie_config() -> tuple[str, str] | None:
    mode = os.getenv("MOLLIE_MODE", "test").strip().lower()
    api_key = os.getenv("MOLLIE_API_KEY", "").strip()
    if mode not in {"test", "live"}:
        return None
    required_prefix = "test_" if mode == "test" else "live_"
    if not api_key.startswith(required_prefix):
        return None
    webhook_url = os.getenv("BUILDER_WEBHOOK_URL", "").strip()
    parsed = urlsplit(webhook_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return None
    return mode, api_key


def _checkout_status() -> tuple[bool, str]:
    if not _mollie_config():
        return False, "Mollie is nog niet ingesteld voor de gekozen test- of livemodus."
    try:
        _public_url()
    except HTTPException:
        return False, "De openbare builder-URL is ongeldig ingesteld."
    return True, ""


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    secure = forwarded_proto.split(",", maxsplit=1)[0].strip().lower() == "https"
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/api/builder",
    )


def _reserve_usage(account_id: str, client_ip: str = "unknown") -> tuple[str, str]:
    connection = _connect()
    usage_id = str(uuid.uuid4())
    try:
        connection.execute("BEGIN IMMEDIATE")
        account = connection.execute(
            "SELECT email, free_used, credits FROM builder_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if not account:
            raise HTTPException(status_code=401, detail="Builder-account niet gevonden.")
        free_available = _free_available(
            connection, account_id, account["email"], account["free_used"]
        )

        keys = {
            "generate-account": account_id,
            "generate-ip": client_ip,
            "free-ip": client_ip,
            "free-global": "all",
        }
        limits = list(GENERATE_LIMITS)
        if free_available:
            limits += list(FREE_LIMITS)
        if not _consume_rate_limits(
            connection,
            [(scope, keys[scope], window, limit) for scope, window, limit in limits],
        ):
            raise HTTPException(
                status_code=429,
                detail="Te veel aanvragen op dit moment. Probeer het later opnieuw.",
            )

        if free_available:
            charge_type = "free"
            connection.execute(
                "UPDATE builder_accounts SET free_used = 1 WHERE id = ?",
                (account_id,),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO builder_free_claims (email_key, account_id, claimed_at)
                VALUES (?, ?, ?)
                """,
                (normalize_email(account["email"]), account_id, int(time.time())),
            )
        elif account["credits"] > 0:
            charge_type = "credit"
            connection.execute(
                "UPDATE builder_accounts SET credits = credits - 1 WHERE id = ?",
                (account_id,),
            )
        else:
            raise HTTPException(
                status_code=402,
                detail=(
                    "De gratis aanvraag is al gebruikt voor dit e-mailadres (of een variant "
                    "ervan). Koop een pakket van 10 credits voor €5,00."
                ),
            )

        connection.execute(
            """
            INSERT INTO builder_usage (id, account_id, charge_type, status, created_at)
            VALUES (?, ?, ?, 'reserved', ?)
            """,
            (usage_id, account_id, charge_type, int(time.time())),
        )
        connection.commit()
        return usage_id, charge_type
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _finish_usage(usage_id: str, refund: bool) -> None:
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        usage = connection.execute(
            "SELECT account_id, charge_type FROM builder_usage WHERE id = ? AND status = 'reserved'",
            (usage_id,),
        ).fetchone()
        if not usage:
            connection.commit()
            return
        if refund:
            if usage["charge_type"] == "free":
                _release_free(connection, usage["account_id"])
            else:
                connection.execute(
                    "UPDATE builder_accounts SET credits = credits + 1 WHERE id = ?",
                    (usage["account_id"],),
                )
            status = "refunded"
        else:
            status = "completed"
        connection.execute(
            "UPDATE builder_usage SET status = ? WHERE id = ?",
            (status, usage_id),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        logger.exception("Could not finalize builder usage reservation %s", usage_id)
        raise
    finally:
        connection.close()


@router.post("/auth/request-verification")
async def request_verification(payload: VerificationRequest, request: Request) -> dict[str, str]:
    _require_same_origin(request)
    public_url = _public_url()
    email = str(payload.email).strip().lower()
    if is_disposable_email(email):
        raise HTTPException(
            status_code=400,
            detail="Tijdelijke of wegwerp-e-mailadressen worden niet geaccepteerd. Gebruik je vaste e-mailadres.",
        )
    client_ip = _client_ip(request)
    await _verify_captcha(payload.captcha_token, client_ip)
    keys = {"verify-ip": client_ip, "verify-email": normalize_email(email), "verify-global": "all"}
    now = int(time.time())
    token = secrets.token_urlsafe(32)
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        allowed = _consume_rate_limits(
            connection,
            [(scope, keys[scope], window, limit) for scope, window, limit in VERIFY_LIMITS],
        )
        if not allowed:
            connection.rollback()
            raise HTTPException(status_code=429, detail="Te veel verificatieverzoeken. Probeer het later opnieuw.")
        connection.execute(
            """
            INSERT INTO builder_verifications (email, token_hash, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET token_hash = excluded.token_hash,
                expires_at = excluded.expires_at
            """,
            (email, _hash(token), now + VERIFICATION_TTL_SECONDS),
        )
        connection.commit()
    finally:
        connection.close()

    verification_url = f"{public_url}/builder?{urlencode({'verify': token})}"
    try:
        await asyncio.to_thread(
            send_email,
            "Bevestig je e-mailadres voor fajaede Builder",
            email,
            "Open deze beveiligde link om je e-mailadres te bevestigen en je gratis "
            f"websiteaanvraag te activeren:\n\n{verification_url}\n\n"
            "De link is 30 minuten geldig. Heb je dit niet aangevraagd, negeer dit bericht.",
        )
    except (OSError, RuntimeError, ValueError):
        connection = _connect()
        try:
            connection.execute(
                "DELETE FROM builder_verifications WHERE email = ? AND token_hash = ?",
                (email, _hash(token)),
            )
            connection.commit()
        finally:
            connection.close()
        logger.exception("Could not send builder verification email")
        raise HTTPException(status_code=503, detail="De verificatiemail kon niet worden verzonden.") from None

    return {"message": "Controleer je e-mail voor de verificatielink. De link is 30 minuten geldig."}


@router.post("/auth/verify")
async def verify_email(
    payload: VerificationConfirmation,
    request: Request,
    response: Response,
) -> dict[str, str]:
    _require_same_origin(request)
    token = payload.token
    if len(token) > 128:
        raise HTTPException(status_code=400, detail="Verificatielink is ongeldig of verlopen.")

    now = int(time.time())
    session_token = secrets.token_urlsafe(32)
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        pending = connection.execute(
            "SELECT email, expires_at FROM builder_verifications WHERE token_hash = ?",
            (_hash(token),),
        ).fetchone()
        if not pending or pending["expires_at"] < now:
            raise HTTPException(status_code=400, detail="Verificatielink is ongeldig of verlopen.")

        account = connection.execute(
            "SELECT id FROM builder_accounts WHERE email = ?",
            (pending["email"],),
        ).fetchone()
        if account:
            account_id = account["id"]
        else:
            account_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO builder_accounts (id, email, created_at) VALUES (?, ?, ?)",
                (account_id, pending["email"], now),
            )
        connection.execute(
            "INSERT INTO builder_sessions (token_hash, account_id, expires_at) VALUES (?, ?, ?)",
            (_hash(session_token), account_id, now + SESSION_TTL_SECONDS),
        )
        connection.execute(
            "DELETE FROM builder_verifications WHERE email = ?",
            (pending["email"],),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    _set_session_cookie(response, request, session_token)
    return {"message": "E-mailadres bevestigd. Je gratis aanvraag is beschikbaar."}


@router.get("/account")
async def get_account(request: Request) -> dict:
    checkout_available, checkout_message = _checkout_status()
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return {
            "authenticated": False,
            "checkout_available": checkout_available,
            "checkout_message": checkout_message,
            "free_available": False,
            "credits": 0,
        }
    try:
        account = _get_session_account(request)
    except HTTPException:
        return {
            "authenticated": False,
            "checkout_available": checkout_available,
            "checkout_message": checkout_message,
            "free_available": False,
            "credits": 0,
        }
    connection = _connect()
    try:
        free_available = _free_available(
            connection, account["id"], account["email"], account["free_used"]
        )
    finally:
        connection.close()
    return {
        "authenticated": True,
        "email": account["email"],
        "free_available": free_available,
        "credits": account["credits"],
        "checkout_available": checkout_available,
        "checkout_message": checkout_message,
    }


@router.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict[str, str]:
    _require_same_origin(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    if token:
        connection = _connect()
        try:
            connection.execute("DELETE FROM builder_sessions WHERE token_hash = ?", (_hash(token),))
            connection.commit()
        finally:
            connection.close()
    response.delete_cookie(SESSION_COOKIE, path="/api/builder")
    return {"message": "Je bent afgemeld."}


@router.post("/auth/logout-all")
async def logout_all(request: Request, response: Response) -> dict[str, str]:
    """End every session of this account, e.g. after a lost or shared device."""
    _require_same_origin(request)
    account = _get_session_account(request)
    connection = _connect()
    try:
        connection.execute("DELETE FROM builder_sessions WHERE account_id = ?", (account["id"],))
        connection.commit()
    finally:
        connection.close()
    response.delete_cookie(SESSION_COOKIE, path="/api/builder")
    return {"message": "Je bent op alle apparaten afgemeld."}


@router.post("/checkout")
async def create_checkout(request: Request, payload: CheckoutRequest | None = None) -> dict[str, str]:
    _require_same_origin(request)
    account = _get_session_account(request)
    if not payload or not payload.accept_terms or not payload.waive_withdrawal:
        raise HTTPException(
            status_code=400,
            detail=(
                "Ga akkoord met de algemene voorwaarden en met directe levering "
                "(verlies van herroepingsrecht) om credits te kopen."
            ),
        )
    config = _mollie_config()
    if not config:
        raise HTTPException(
            status_code=503,
            detail="Mollie is nog niet veilig ingesteld. Configureer een API-key die past bij MOLLIE_MODE en een openbare HTTPS-webhook.",
        )
    mode, api_key = config
    public_url = _public_url()
    webhook_url = os.getenv("BUILDER_WEBHOOK_URL", "").strip()
    order_id = str(uuid.uuid4())
    now = int(time.time())

    connection = _connect()
    try:
        connection.execute(
            """
            INSERT INTO builder_payments
                (order_id, account_id, credits, amount, mode, status, created_at,
                 terms_version, consent_at)
            VALUES (?, ?, ?, ?, ?, 'creating', ?, ?, ?)
            """,
            (
                order_id,
                account["id"],
                CREDIT_PACK_SIZE,
                CREDIT_PACK_PRICE,
                mode,
                now,
                TERMS_VERSION,
                now,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    payload = {
        "amount": {"currency": "EUR", "value": CREDIT_PACK_PRICE},
        "description": "fajaede Builder - 10 credits",
        "redirectUrl": f"{public_url}/builder?payment=return",
        "webhookUrl": webhook_url,
        "metadata": {
            "builder_order": order_id,
            "account_id": account["id"],
            "credits": CREDIT_PACK_SIZE,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            mollie_response = await client.post(
                f"{MOLLIE_API_BASE}/payments",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            mollie_response.raise_for_status()
            payment = mollie_response.json()
        checkout_url = payment.get("_links", {}).get("checkout", {}).get("href")
        mollie_id = payment.get("id")
        if not checkout_url or not mollie_id or payment.get("mode") != mode:
            raise RuntimeError("Mollie returned an incomplete or unexpected payment.")
        connection = _connect()
        try:
            connection.execute(
                """
                UPDATE builder_payments SET mollie_id = ?, status = ?
                WHERE order_id = ? AND mode = ? AND status = 'creating'
                """,
                (mollie_id, payment.get("status", "open"), order_id, mode),
            )
            connection.commit()
        finally:
            connection.close()
        return {"checkout_url": checkout_url}
    except (httpx.HTTPError, ValueError, RuntimeError):
        connection = _connect()
        try:
            connection.execute("DELETE FROM builder_payments WHERE order_id = ?", (order_id,))
            connection.commit()
        finally:
            connection.close()
        logger.exception("Could not create Mollie checkout for builder order %s", order_id)
        raise HTTPException(status_code=502, detail="Mollie kon de betaling niet starten.") from None


@router.post("/payments/webhook")
async def mollie_webhook(request: Request) -> dict[str, str]:
    config = _mollie_config()
    if not config:
        raise HTTPException(status_code=503, detail="Mollie-webhook is niet geconfigureerd.")
    mode, api_key = config

    body = (await request.body()).decode("utf-8", errors="replace")
    payment_id = parse_qs(body).get("id", [""])[0]
    if not re.fullmatch(r"tr_[A-Za-z0-9]+", payment_id):
        raise HTTPException(status_code=400, detail="Ongeldige Mollie-melding.")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            payment_response = await client.get(
                f"{MOLLIE_API_BASE}/payments/{payment_id}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            payment_response.raise_for_status()
            payment = payment_response.json()
    except httpx.HTTPError:
        logger.exception("Could not verify Mollie payment %s", payment_id)
        raise HTTPException(status_code=502, detail="Mollie-betaling kon niet worden geverifieerd.") from None

    metadata = payment.get("metadata") or {}
    order_id = metadata.get("builder_order")
    if (
        payment.get("id") != payment_id
        or payment.get("mode") != mode
        or payment.get("amount") != {"currency": "EUR", "value": CREDIT_PACK_PRICE}
        or metadata.get("credits") != CREDIT_PACK_SIZE
        or not isinstance(order_id, str)
    ):
        raise HTTPException(status_code=400, detail="Betalingsgegevens komen niet overeen.")

    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        order = connection.execute(
            "SELECT account_id, credits, amount, mode, granted_at FROM builder_payments WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if (
            not order
            or order["account_id"] != metadata.get("account_id")
            or order["credits"] != CREDIT_PACK_SIZE
            or order["amount"] != CREDIT_PACK_PRICE
            or order["mode"] != mode
        ):
            raise HTTPException(status_code=404, detail="Builder-bestelling niet gevonden.")

        status = payment.get("status", "unknown")
        granted_at = order["granted_at"]
        confirmation_email = None
        if status == "paid" and not granted_at:
            connection.execute(
                "UPDATE builder_accounts SET credits = credits + ? WHERE id = ?",
                (CREDIT_PACK_SIZE, order["account_id"]),
            )
            granted_at = int(time.time())
            buyer = connection.execute(
                "SELECT email FROM builder_accounts WHERE id = ?",
                (order["account_id"],),
            ).fetchone()
            confirmation_email = buyer["email"] if buyer else None
        connection.execute(
            """
            UPDATE builder_payments SET mollie_id = ?, status = ?, granted_at = ?
            WHERE order_id = ?
            """,
            (payment_id, status, granted_at, order_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    if confirmation_email:
        await _send_purchase_confirmation(confirmation_email, payment_id, order_id)
    return {"status": "ok"}


async def _send_purchase_confirmation(email: str, payment_id: str, order_id: str) -> None:
    """Confirm the purchase and the withdrawal waiver on a durable medium (best effort)."""
    try:
        public_url = _public_url()
    except HTTPException:
        public_url = "https://www.fajaede.eu"
    body = (
        "Bedankt voor je aankoop bij fajaede Builder.\n\n"
        f"Product: {CREDIT_PACK_SIZE} credits voor fajaede Builder\n"
        f"Bedrag: EUR {CREDIT_PACK_PRICE.replace('.', ',')} (inclusief btw)\n"
        f"Bestelnummer: {order_id}\n"
        f"Mollie-betaling: {payment_id}\n\n"
        "De credits zijn direct aan je account toegevoegd.\n\n"
        "Je hebt vóór de betaling uitdrukkelijk ingestemd met directe levering van deze "
        "digitale inhoud en erkend dat je daardoor je herroepingsrecht verliest "
        "(art. VI.53, 13° Wetboek van economisch recht).\n\n"
        f"Algemene voorwaarden (versie {TERMS_VERSION}): {public_url}/algemene-voorwaarden\n\n"
        "fajaede - Lodewijk de Koninckstraat 31, 2600 Berchem, België - BE1032407424\n"
    )
    try:
        await asyncio.to_thread(
            send_email,
            "Bevestiging van je aankoop: 10 credits fajaede Builder",
            email,
            body,
        )
    except (OSError, RuntimeError, ValueError):
        # Credits are already granted; never fail the webhook over the email.
        logger.exception("Could not send purchase confirmation for order %s", order_id)


@router.post("/generate")
async def generate(payload: WebsiteRequest, request: Request) -> dict:
    _require_same_origin(request)
    account = _get_session_account(request)
    usage_id, _ = _reserve_usage(account["id"], _client_ip(request))
    try:
        client = getattr(request.app.state, "openai_client", None)
        if client is None:
            raise HTTPException(status_code=503, detail="OpenAI is niet geconfigureerd.")
        result = await WebsiteGeneratorService().generate_website(
            payload.model_dump(),
            client,
        )
        _finish_usage(usage_id, refund=False)
        return result
    except HTTPException:
        _finish_usage(usage_id, refund=True)
        raise
    except Exception as error:
        _finish_usage(usage_id, refund=True)
        logger.exception("Website generation failed")
        raise HTTPException(
            status_code=502,
            detail="De website kon niet worden gegenereerd. Je aanvraag is niet in rekening gebracht.",
        ) from error
