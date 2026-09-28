"""Developer API met email registratie en SEO generatie."""
import sqlite3
import secrets
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, EmailStr
import os

router = APIRouter()

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "backend", "developer_keys.db")

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
    conn.commit()
    conn.close()
    print("Developer database geinitieerd")

class RegisterRequest(BaseModel):
    email: EmailStr
    project_name: Optional[str] = None

class RegisterResponse(BaseModel):
    api_key: str
    email: str
    project_name: Optional[str]
    daily_limit: int
    message: str

class SEOGenerateRequest(BaseModel):
    content: str
    url: Optional[str] = None
    language: str = "nl"

class SEOGenerateResponse(BaseModel):
    title: str
    meta_description: str
    keywords: list[str]
    score: int

class UsageResponse(BaseModel):
    email: str
    project_name: Optional[str]
    daily_limit: int
    requests_today: int
    total_requests: int
    created_at: str

def generate_api_key() -> str:
    return f"sk_eu_{secrets.token_urlsafe(24)}"

def check_rate_limit(api_key: str, daily_limit: int) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    today = datetime.now().date().isoformat()
    cursor.execute("SELECT request_count FROM rate_limits WHERE api_key = ? AND date = ?", (api_key, today))
    row = cursor.fetchone()
    conn.close()
    return row is None or row[0] < daily_limit

def increment_usage(api_key: str):
    conn = get_db()
    cursor = conn.cursor()
    today = datetime.now().date().isoformat()
    cursor.execute("INSERT INTO rate_limits (api_key, date, request_count) VALUES (?, ?, 1) ON CONFLICT(api_key, date) DO UPDATE SET request_count = request_count + 1", (api_key, today))
    cursor.execute("UPDATE api_keys SET request_count = request_count + 1, last_used_at = CURRENT_TIMESTAMP WHERE api_key = ?", (api_key,))
    conn.commit()
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

@router.post("/register", response_model=RegisterResponse)
async def register_developer(request: RegisterRequest):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT api_key FROM api_keys WHERE email = ?", (request.email,))
    existing = cursor.fetchone()
    if existing:
        conn.close()
        return RegisterResponse(api_key=existing[0], email=request.email, project_name=None, daily_limit=100, message="Email al geregistreerd.")
    api_key = generate_api_key()
    cursor.execute("INSERT INTO api_keys (email, api_key, project_name) VALUES (?, ?, ?)", (request.email, api_key, request.project_name))
    conn.commit()
    conn.close()
    return RegisterResponse(api_key=api_key, email=request.email, project_name=request.project_name, daily_limit=100, message="Succesvol geregistreerd!")

@router.post("/seo/generate", response_model=SEOGenerateResponse)
async def generate_seo(request: SEOGenerateRequest, x_api_key: str = Header(..., alias="X-API-Key")):
    user = validate_api_key(x_api_key)
    if not check_rate_limit(x_api_key, user['daily_limit']):
        raise HTTPException(status_code=429, detail=f"Daily limit bereikt ({user['daily_limit']} requests)")
    increment_usage(x_api_key)
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        prompt = f"Genereer SEO metadata (taal: {request.language}). Content: {request.content[:1000]}. Return JSON: {{title, meta_description, keywords, score}}"
        response = await client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "system", "content": "SEO expert. Return ALLEEN JSON."}, {"role": "user", "content": prompt}], response_format={"type": "json_object"})
        import json
        seo_data = json.loads(response.choices[0].message.content)
        return SEOGenerateResponse(title=seo_data.get("title", "")[:60], meta_description=seo_data.get("meta_description", "")[:160], keywords=seo_data.get("keywords", []), score=seo_data.get("score", 0))
    except Exception as e:
        print(f"SEO fout: {e}")
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
