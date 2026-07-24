"""API routes for the website builder functionality."""

import logging
from typing import Dict, Any, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field  # pylint: disable=no-name-in-module

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
    styleVariant: Literal["seo_ai_product", "minimal"] = Field(
        default="seo_ai_product",
        description="The visual style variant for the website."
    )
    language: str = Field(
        default="nl",
        description="The language for the website content (e.g., 'nl', 'en')."
    )

@router.post("/builder/generate")
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
