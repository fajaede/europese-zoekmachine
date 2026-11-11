"""Security smoke tests for developer API key and rate-limit helpers."""

import importlib
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def main() -> None:
	with tempfile.TemporaryDirectory() as temp_dir:
		os.environ["DEVELOPER_KEYS_DB_PATH"] = str(Path(temp_dir) / "keys.db")
		os.environ["DEVELOPER_VERIFICATION_BASE_URL"] = "https://api.example.test"
		developer_api = importlib.import_module("api.developer_api")
		developer_api.init_db()

		key = developer_api.generate_api_key()
		assert key.startswith("sk_eu_")
		assert len(key) >= 32

		assert all(developer_api.consume_registration_limit("test-client") for _ in range(5))
		assert not developer_api.consume_registration_limit("test-client")

		app = FastAPI()
		app.include_router(developer_api.router, prefix="/api/developers")
		sent_links: list[str] = []
		with patch.object(developer_api, "_send_verification_email", side_effect=lambda _email, link: sent_links.append(link)):
			with TestClient(app) as client:
				register_response = client.post(
					"/api/developers/register",
					json={"email": "Test@example.com", "project_name": "Test"},
				)
				assert register_response.status_code == 202
				assert "api_key" not in register_response.json()
				assert len(sent_links) == 1

				verification = urlsplit(sent_links[0])
				token = parse_qs(verification.query)["token"][0]
				confirmation_page = client.get(f"{verification.path}?token={token}")
				assert confirmation_page.status_code == 200
				assert confirmation_page.headers["referrer-policy"] == "no-referrer"

				verified = client.post(verification.path, data={"token": token})
				assert verified.status_code == 200
				first_key = verified.json()["api_key"]

				duplicate = client.post(
					"/api/developers/register",
					json={"email": "TEST@example.com", "project_name": "Updated"},
				)
				assert duplicate.status_code == 202
				assert "api_key" not in duplicate.json()
				rotation_token = parse_qs(urlsplit(sent_links[-1]).query)["token"][0]
				rotated = client.post(verification.path, data={"token": rotation_token})
				assert rotated.status_code == 200
				second_key = rotated.json()["api_key"]
				assert second_key != first_key

				try:
					developer_api.validate_api_key(first_key)
				except HTTPException as error:
					assert error.status_code == 401
				else:
					raise AssertionError("Rotated developer key remained active")

				conn = developer_api.get_db()
				conn.execute("UPDATE api_keys SET daily_limit = 2 WHERE api_key = ?", (second_key,))
				conn.commit()
				conn.close()
				assert developer_api.consume_rate_limit(second_key, 2)
				assert developer_api.consume_rate_limit(second_key, 2)
				assert not developer_api.consume_rate_limit(second_key, 2)

		os.environ.pop("DEVELOPER_KEYS_DB_PATH", None)
		os.environ.pop("DEVELOPER_VERIFICATION_BASE_URL", None)

	print("Developer API key and rate-limit checks passed; no key was logged.")


if __name__ == "__main__":
	main()
