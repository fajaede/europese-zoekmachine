"""SEO generation routes for fajaedeAI+.

Provides an endpoint for generating optimized SEO metadata (titles, descriptions, slugs)
based on keywords using the local LLM.
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field

from api.llm_client import LLMClient

router = APIRouter(prefix="/api", tags=["SEO"])
llm_client = LLMClient()

class GenerateSEORequest(BaseModel):
    """Request model for SEO metadata generation."""
    keyword: str = Field(..., min_length=2, description="Het focus keyword voor de pagina.")
    language: str = Field("nl", description="De taal van de content.")
    country: str = Field("nl", description="Het doelland.")
    site_name: Optional[str] = Field(None, description="Naam van de website voor branding in de title tag.")
    page_type: Optional[str] = Field("service", description="Type pagina (bijv. blog, service, product).")
    tone: Optional[str] = Field("professional", description="De gewenste tone-of-voice.")

class GenerateSEOResponse(BaseModel):
    """Response model for generated SEO metadata."""
    title: str
    meta_description: str
    h1: str
    keywords: List[str]
    slug: str

@router.post("/generate-seo", response_model=GenerateSEOResponse)
async def generate_seo(payload: GenerateSEORequest) -> GenerateSEOResponse:
    """
    Genereert SEO-metadata op basis van een keyword middels de lokale LLM.
    """
    keyword = payload.keyword.strip()

    if not keyword:
        raise HTTPException(status_code=400, detail="Keyword is verplicht")

    system_prompt = (
        f"Je bent een senior SEO-expert en copywriter. Je schrijft in het {payload.language}.\n"
        "Genereer een pakkende, SEO-geoptimaliseerde titel (max 60 tekens), "
        "een meta-omschrijving (max 160 tekens), een H1 en 4 relevante keywords.\n"
        "Antwoord ALLEEN met een JSON-object dat exact aan het schema voldoet."
    )

    user_prompt = (
        f"Genereer SEO data voor het keyword: '{keyword}'.\n"
        f"Type pagina: {payload.page_type}\n"
        f"Tone of voice: {payload.tone}\n"
        f"Site naam: {payload.site_name or 'FajaedeSEO-AI'}\n"
    )

    try:
        schema = GenerateSEOResponse.model_json_schema()
        result = await llm_client.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            json_schema=schema,
        )
        return GenerateSEOResponse.model_validate(result)
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"SEO generatie mislukt via LLM: {str(e)}"
        ) from e