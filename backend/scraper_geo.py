import asyncio
import os
import re
import httpx
import yaml
from backend.seo_geo_audit import validate_public_url

# ===== CONFIGURATIE =====
VALUESERP_API_KEY = os.getenv("VALUESERP_API_KEY", "")
VALUESERP_API_URL = "https://api.valueserp.com/search"
MEILI_URL = os.getenv("MEILI_HOST", "http://meilisearch:7700")
MEILI_KEY = os.getenv("MEILI_MASTER_KEY", "")
MAX_QUERIES_PER_COUNTRY = 2
# ========================

async def google_search(query: str, country_code: str) -> list[dict]:
    if not VALUESERP_API_KEY:
        raise RuntimeError("VALUESERP_API_KEY is not configured.")
    if not query or len(query) > 200:
        raise ValueError("Search query is empty or too long.")
    if not re.fullmatch(r"[a-zA-Z]{2}", country_code):
        raise ValueError("Invalid country code.")

    async with httpx.AsyncClient() as client:
        params = {
            "q": query,
            "gl": country_code,
            "num": 5,
            "api_key": VALUESERP_API_KEY,
        }
        resp = await client.get(VALUESERP_API_URL, params=params, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
        
        results = []
        for r in data.get("organic_results", [])[:5]:
            results.append({
                "url": r.get("url"),
                "title": r.get("title"),
                "snippet": r.get("snippet", ""),
            })
        return results

async def process_country(country: dict, meili_client, query_index: int):
    print(f"Processing {country['code']} ({country['language']})...")
    
    queries_to_use = country["queries"][:MAX_QUERIES_PER_COUNTRY]
    
    for query in queries_to_use:
        print(f"  Query: {query}")
        
        try:
            results = await google_search(query, country["code"])
        except Exception as e:
            print(f"    Search failed: {e}")
            continue
        
        for i, result in enumerate(results):
            url = result.get("url")
            title = result.get("title", "")
            snippet = result.get("snippet", "")
            
            if not url:
                continue

            try:
                url = await validate_public_url(url)
            except (TypeError, ValueError):
                print("    Skipped result with a non-public or invalid URL.")
                continue
            
            try:
                await meili_client.index("documents").add_documents([{
                    "url": url,
                    "title": f"{title} ({query})",
                    "text": f"{title}\n{snippet}",
                    "country": country["code"],
                    "language": country["language"],
                    "query": query,
                    "category": "geo_snippet",
                    "rank": i + 1,
                }])
                print(f"    ✓ Indexed: {url[:50]}...")
            except Exception as e:
                print(f"    ✗ Failed {url[:40]}: {e}")
        
        await asyncio.sleep(2)

async def main():
    from meilisearch import Client
    
    meili_key = MEILI_KEY
    if not meili_key:
        raise RuntimeError("MEILI_MASTER_KEY is not configured.")
    
    config_path = Path("/app/config/geo_queries.yaml")
    config = yaml.safe_load(config_path.read_text())
    
    meili = Client(MEILI_URL, meili_key)
    
    for i, country in enumerate(config["countries"]):
        await process_country(country, meili, i)
        await asyncio.sleep(3)
    
    print("\n✓ Klaar!")

if __name__ == "__main__":
    asyncio.run(main())
