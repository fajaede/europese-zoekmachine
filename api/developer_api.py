"""Developer API met email registratie en SEO generatie."""
import asyncio
import html
import sqlite3
import secrets
import hashlib
import time
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qs, urlsplit
from fastapi import APIRouter, HTTPException, Header, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr, Field
from api.email_utils import send_email
import os

router = APIRouter()

DB_PATH = os.getenv(
    "DEVELOPER_KEYS_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "backend", "developer_keys.db"),
)

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS api_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        api_key TEXT UNIQUE NOT NULL,
        project_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_used_at TIMESTAMP,
        request_count INTEGER DEFAULT 0,
        daily_limit INTEGER DEFAULT 100,
        is_active BOOLEAN DEFAULT 1
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS rate_limits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        api_key TEXT NOT NULL,
        date DATE NOT NULL,
        request_count INTEGER DEFAULT 0,
        UNIQUE(api_key, date)
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS registration_limits (
        ip_hash TEXT NOT NULL,
        window_start INTEGER NOT NULL,
        request_count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (ip_hash, window_start)
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS pending_registrations (
        email TEXT PRIMARY KEY,
        token_hash TEXT UNIQUE NOT NULL,
        project_name TEXT,
        intent TEXT NOT NULL CHECK(intent IN ('create', 'rotate')),
        expires_at INTEGER NOT NULL
    )""")
    conn.commit()
    conn.close()
    print("Developer database geinitieerd")

class RegisterRequest(BaseModel):
    email: EmailStr
    project_name: Optional[str] = Field(default=None, max_length=100)

class RegisterResponse(BaseModel):
    api_key: str
    email: str
    project_name: Optional[str]
    daily_limit: int
    message: str

class RegistrationPendingResponse(BaseModel):
    message: str

def delete_pending_registration(email: str) -> None:
    conn = get_db()
    try:
        conn.execute("DELETE FROM pending_registrations WHERE email = ?", (email,))
        conn.commit()
    finally:
        conn.close()

class SEOGenerateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)
    url: Optional[str] = Field(default=None, max_length=2048)
    language: str = Field(default="nl", min_length=2, max_length=10, pattern=r"^[A-Za-z]{2}(?:-[A-Za-z]{2})?$")

class SEOGenerateResponse(BaseModel):
    title: str = Field(max_length=60)
    meta_description: str = Field(max_length=160)
    keywords: list[str] = Field(max_length=8)
    score: int = Field(ge=0, le=100)

class UsageResponse(BaseModel):
    email: str
    project_name: Optional[str]
    daily_limit: int
    requests_today: int
    total_requests: int
    created_at: str

def generate_api_key() -> str:
    return f"sk_eu_{secrets.token_urlsafe(24)}"


def consume_rate_limit(api_key: str, daily_limit: int) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    today = datetime.now().date().isoformat()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute(
            "INSERT OR IGNORE INTO rate_limits (api_key, date, request_count) VALUES (?, ?, 0)",
            (api_key, today),
        )
        cursor.execute(
            "UPDATE rate_limits SET request_count = request_count + 1 "
            "WHERE api_key = ? AND date = ? AND request_count < ?",
            (api_key, today, daily_limit),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return False
        cursor.execute(
            "UPDATE api_keys SET request_count = request_count + 1, last_used_at = CURRENT_TIMESTAMP WHERE api_key = ?",
            (api_key,),
        )
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def consume_registration_limit(client_host: str) -> bool:
    now = int(time.time())
    window_start = now // 3600
    ip_hash = hashlib.sha256(client_host.encode("utf-8")).hexdigest()
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute("DELETE FROM registration_limits WHERE window_start < ?", (window_start - 24,))
        cursor.execute(
            "INSERT INTO registration_limits (ip_hash, window_start, request_count) VALUES (?, ?, 1) "
            "ON CONFLICT(ip_hash, window_start) DO UPDATE SET request_count = request_count + 1",
            (ip_hash, window_start),
        )
        cursor.execute(
            "SELECT request_count FROM registration_limits WHERE ip_hash = ? AND window_start = ?",
            (ip_hash, window_start),
        )
        row = cursor.fetchone()
        conn.commit()
        return bool(row and row[0] <= 5)
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_verification(email: str, project_name: Optional[str]) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = int(time.time()) + 30 * 60
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute("SELECT 1 FROM api_keys WHERE lower(email) = ?", (email,))
        intent = "rotate" if cursor.fetchone() else "create"
        cursor.execute(
            "INSERT INTO pending_registrations (email, token_hash, project_name, intent, expires_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(email) DO UPDATE SET "
            "token_hash = excluded.token_hash, project_name = excluded.project_name, "
            "intent = excluded.intent, expires_at = excluded.expires_at",
            (email, token_hash, project_name, intent, expires_at),
        )
        conn.commit()
        return token
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def _verification_base_url() -> str:
    configured = os.getenv("DEVELOPER_VERIFICATION_BASE_URL", "").strip()
    if not configured:
        raise RuntimeError("DEVELOPER_VERIFICATION_BASE_URL is not configured.")
    parsed = urlsplit(configured)
    is_local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and is_local):
        raise RuntimeError("The developer verification URL must use HTTPS.")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("The developer verification URL is invalid.")
    return configured.rstrip("/")


def _send_verification_email(email: str, verification_url: str) -> None:
    send_email(
        "Bevestig je Developer API-registratie",
        email,
        "Bevestig je e-mailadres voor de Europese Zoekmachine Developer API door "
        f"deze link te openen en op bevestigen te klikken:\n\n{verification_url}\n\n"
        "De link verloopt na 30 minuten. Heb je dit niet aangevraagd, negeer dit bericht.",
    )


def verify_registration_token(token: str) -> tuple[str, str, Optional[str]] | None:
    if not token or len(token) > 128:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute(
            "SELECT email, intent, project_name, expires_at FROM pending_registrations WHERE token_hash = ?",
            (token_hash,),
        )
        pending = cursor.fetchone()
        if not pending or pending["expires_at"] < int(time.time()):
            cursor.execute("DELETE FROM pending_registrations WHERE token_hash = ?", (token_hash,))
            conn.commit()
            return None

        email = pending["email"]
        api_key = generate_api_key()
        cursor.execute("SELECT id FROM api_keys WHERE lower(email) = ?", (email,))
        existing = cursor.fetchone()
        if existing:
            cursor.execute(
                "UPDATE api_keys SET api_key = ?, project_name = ?, request_count = 0, last_used_at = NULL, is_active = 1 WHERE id = ?",
                (api_key, pending["project_name"], existing["id"]),
            )
        else:
            cursor.execute(
                "INSERT INTO api_keys (email, api_key, project_name) VALUES (?, ?, ?)",
                (email, api_key, pending["project_name"]),
            )
        cursor.execute("DELETE FROM pending_registrations WHERE email = ?", (email,))
        conn.commit()
        return api_key, email, pending["project_name"]
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def validate_api_key(api_key: str) -> dict:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM api_keys WHERE api_key = ? AND is_active = 1", (api_key,))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        raise HTTPException(status_code=401, detail="Ongeldige API key")
    return dict(row)

@router.post("/register", response_model=RegistrationPendingResponse, status_code=202)
async def register_developer(request: RegisterRequest, http_request: Request):
    client_host = http_request.client.host if http_request.client else "unknown"
    if not consume_registration_limit(client_host):
        raise HTTPException(status_code=429, detail="Te veel registraties vanaf dit netwerk. Probeer het later opnieuw.")

    normalized_email = str(request.email).strip().lower()
    try:
        token = create_verification(normalized_email, request.project_name)
        verification_url = f"{_verification_base_url()}/api/developers/verify?token={token}"
        await asyncio.to_thread(_send_verification_email, normalized_email, verification_url)
    except Exception as exc:
        delete_pending_registration(normalized_email)
        raise HTTPException(status_code=503, detail="Verificatiemail kon niet worden verzonden. Probeer het later opnieuw.") from exc

    return RegistrationPendingResponse(message="Als dit adres kan worden geregistreerd, ontvang je een verificatiemail.")


@router.get("/verify", response_class=HTMLResponse)
async def show_verification_form(request: Request):
    token = request.query_params.get("token", "")
    if not token or len(token) > 128:
        raise HTTPException(status_code=400, detail="Ongeldige of verlopen verificatielink.")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT expires_at FROM pending_registrations WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
    finally:
        conn.close()
    if not row or row["expires_at"] < int(time.time()):
        raise HTTPException(status_code=400, detail="Ongeldige of verlopen verificatielink.")

    escaped_token = html.escape(token, quote=True)
    page = (
        "<!doctype html><html lang='nl'><meta charset='utf-8'>"
        "<meta name='referrer' content='no-referrer'><title>E-mail bevestigen</title>"
        "<body><main><h1>E-mailadres bevestigen</h1>"
        "<p>Bevestig je adres om een nieuwe API-key te activeren of je bestaande key te roteren.</p>"
        "<form method='post' action='/api/developers/verify'>"
        f"<input type='hidden' name='token' value='{escaped_token}'>"
        "<button type='submit'>E-mailadres bevestigen</button></form></main></body></html>"
    )
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; form-action 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
        },
    )


@router.post("/verify", response_model=RegisterResponse)
async def verify_developer_email(request: Request):
    if not consume_registration_limit(request.client.host if request.client else "unknown"):
        raise HTTPException(status_code=429, detail="Te veel verificatiepogingen. Probeer het later opnieuw.")
    if "application/x-www-form-urlencoded" not in request.headers.get("content-type", "").lower():
        raise HTTPException(status_code=415, detail="Verwacht een URL-gecodeerd verificatieformulier.")
    body = await request.body()
    if len(body) > 2048:
        raise HTTPException(status_code=413, detail="Verificatieverzoek te groot.")
    try:
        token = parse_qs(body.decode("utf-8", errors="strict"), max_num_fields=4).get("token", [""])[0]
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Ongeldige verificatieaanvraag.") from None

    verified = verify_registration_token(token)
    if not verified:
        raise HTTPException(status_code=400, detail="Ongeldige of verlopen verificatielink.")
    api_key, email_address, project_name = verified
    return RegisterResponse(
        api_key=api_key,
        email=email_address,
        project_name=project_name,
        daily_limit=100,
        message="E-mailadres bevestigd. Bewaar deze API-key; hij wordt niet opnieuw getoond.",
    )

@router.post("/seo/generate", response_model=SEOGenerateResponse)
async def generate_seo(request: SEOGenerateRequest, x_api_key: str = Header(..., alias="X-API-Key")):
    user = validate_api_key(x_api_key)
    if not consume_rate_limit(x_api_key, user['daily_limit']):
        raise HTTPException(status_code=429, detail=f"Daily limit bereikt ({user['daily_limit']} requests)")
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        prompt = f"Genereer SEO metadata (taal: {request.language}). Content: {request.content[:1000]}. Return JSON: {{title, meta_description, keywords, score}}"
        response = await client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "system", "content": "SEO expert. Return ALLEEN JSON."}, {"role": "user", "content": prompt}], response_format={"type": "json_object"})
        import json
        seo_data = json.loads(response.choices[0].message.content)
        keywords_raw = seo_data.get("keywords", [])
        keywords_list = keywords_raw if isinstance(keywords_raw, list) else (keywords_raw.split(", ") if isinstance(keywords_raw, str) else [])
        return SEOGenerateResponse(title=seo_data.get("title", "")[:60], meta_description=seo_data.get("meta_description", "")[:160], keywords=keywords_list, score=seo_data.get("score", 0))
    except Exception as e:
        print(f"SEO generation failed: {type(e).__name__}")
        words = request.content.split()[:10]
        return SEOGenerateResponse(title=" ".join(words), meta_description=request.content[:160], keywords=words[:5], score=50)

@router.get("/usage", response_model=UsageResponse)
async def get_usage(x_api_key: str = Header(..., alias="X-API-Key")):
    user = validate_api_key(x_api_key)
    conn = get_db()
    cursor = conn.cursor()
    today = datetime.now().date().isoformat()
    cursor.execute("SELECT request_count FROM rate_limits WHERE api_key = ? AND date = ?", (x_api_key, today))
    row = cursor.fetchone()
    conn.close()
    return UsageResponse(email=user['email'], project_name=user['project_name'], daily_limit=user['daily_limit'], requests_today=row[0] if row else 0, total_requests=user['request_count'], created_at=user['created_at'])

init_db()
