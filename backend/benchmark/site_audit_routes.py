"""Internal site-audit endpoints for psa.fajaede.nl (X-Benchmark-Key = BENCHMARK_API_KEY).

  POST /api/site-audit        {"url": "https://voorbeeld.nl", "max_pages": 50}  -> 202 {"id": ...}
  GET  /api/site-audit/{id}   -> {"status": "queued"|"running"|"done"|"failed", "result": {...}}

The Developer API has its own endpoints (api/developer_api.py) with per-plan limits."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.benchmark import site_audit
from backend.benchmark.routes import _check_key
from backend.seo_geo_audit import validate_public_url

router = APIRouter(prefix="/api/site-audit", tags=["site-audit"])


class AuditRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    max_pages: int = Field(default=50, ge=1, le=site_audit.MAX_PAGES_LIMIT)


@router.post("", status_code=202)
async def start_audit(body: AuditRequest, request: Request) -> dict[str, Any]:
    _check_key(request, "BENCHMARK_API_KEY", "X-Benchmark-Key")
    try:
        url = await validate_public_url(body.url.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit_id = await asyncio.to_thread(site_audit.create_audit, url, body.max_pages, "psa")
    return {"id": audit_id, "status": "queued"}


@router.get("/{audit_id}")
async def audit_status(audit_id: str, request: Request) -> dict[str, Any]:
    _check_key(request, "BENCHMARK_API_KEY", "X-Benchmark-Key")
    audit = await asyncio.to_thread(site_audit.get_audit, audit_id, "psa")
    if audit is None:
        raise HTTPException(status_code=404, detail="Audit niet gevonden")
    return audit
