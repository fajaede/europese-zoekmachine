"""LLM Client related definitions."""

import json
import os
from typing import Any, Dict

import openai


class LLMClientError(RuntimeError):
    """Custom exception for LLM client-related errors."""


class LLMClient:
    """A client for interacting with a local or remote LLM."""

    def __init__(self):
        # In een productie-omgeving zou je de base_url en api_key
        # uit environment variables halen.
        ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self.client = openai.AsyncOpenAI(
            base_url=f"{ollama_host}/v1",
            api_key="ollama",  # Vereist voor de OpenAI library, ook al is het lokaal.
        )

    async def generate_json(
        self, system_prompt: str, user_prompt: str, json_schema: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generates a JSON object from the LLM based on a schema."""
        # Voeg het JSON-schema toe aan de systeemprompt om de LLM te sturen.
        full_system_prompt = (
            f"{system_prompt}\n\n"
            "Here is the JSON schema to follow:\n"
            f"```json\n{json.dumps(json_schema, indent=2)}\n```"
        )
        try:
            response = await self.client.chat.completions.create(
                model="phi3:mini",
                messages=[
                    {"role": "system", "content": full_system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                # Vraag de API om een JSON-object te retourneren.
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        except (json.JSONDecodeError, openai.APIError) as e:
            raise LLMClientError(f"Failed to generate valid JSON from LLM: {e}") from e
