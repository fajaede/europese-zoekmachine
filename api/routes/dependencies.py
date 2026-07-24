"""FastAPI dependencies for the application."""

from api.routes.website_generator import WebsiteGeneratorService


def get_website_generator() -> WebsiteGeneratorService:
    """Dependency function to get an instance of WebsiteGeneratorService."""
    # In a real application, this might be a singleton or a more complex factory.
    return WebsiteGeneratorService()