"""Service for generating website content."""

from typing import Any, Dict


class WebsiteGeneratorService:
    """A service class to handle website generation logic."""

    async def generate_medium_website(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Generates a website using the medium process (stub)."""
        return {"status": "success", "message": "Website generated (stub).", "payload": payload}