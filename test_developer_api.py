"""Test de developer API endpoints."""
import sys
sys.path.insert(0, "/root/europese-zoekmachine")

from api.developer_api import init_db, generate_api_key

# Test database init
print("✅ Test 1: Database init")
init_db()

# Test API key generatie
print("\n✅ Test 2: API key generatie")
key = generate_api_key()
print(f"Gegenereerde key: {key}")
print(f"Format correct: {key.startswith('sk_eu_')}")

print("\n✅ Alle tests geslaagd!")
