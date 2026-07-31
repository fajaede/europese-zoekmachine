"""API routes for the website builder functionality."""

import logging
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

# Use absolute imports from the 'api' root for better clarity and linter compatibility.
from api.routes.dependencies import get_website_generator
from api.routes.llm_client import LLMClientError
from api.routes.website_generator import WebsiteGeneratorService

router = APIRouter(tags=["builder"])
logger = logging.getLogger(__name__)

class BuilderRequest(BaseModel):
    """Request model for the website builder."""
    description: str = Field(
        ...,
        min_length=10,
        description="A description of the business or website.",
    )
    # Velden aangepast om overeen te komen met het frontend formulier
    site_type: str = Field(
        "general_business",
        description="The type of website (e.g., 'restaurant', 'law_firm').",
    )
    industry: str = Field(
        "", description="The industry or sector of the business."
    )
    style: str = Field(
        "modern",
        description="The desired design style (e.g., 'minimalist', 'luxury').",
    )

@router.post("/generate")
async def generate(
    request: BuilderRequest,
    service: WebsiteGeneratorService = Depends(get_website_generator),
) -> Dict[str, Any]:
    """
    Generates a website using the lightweight 'medium' generation process.
    """
    logger.info("Received website generation request for: %s", request.description[:50])
    try:
        # Use the more robust and memory-efficient medium website generator
        result = await service.generate_medium_website(request.model_dump())
        return result
    except LLMClientError as e:
        logger.error("LLM Client failed during website generation: %s", e, exc_info=True)
        raise HTTPException(status_code=503, detail=f"AI service failed: {e}") from e
    except Exception as e:
        logger.error("An unexpected error occurred in /builder/generate: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected server error occurred.") from e
