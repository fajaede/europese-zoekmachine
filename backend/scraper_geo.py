import asyncio
import httpx
from pathlib import Path
import yaml

# ===== CONFIGURATIE =====
VALUESERP_API_KEY = "7FFD3319614B409BA697DB65D878F97B"
VALUESERP_API_URL = "https://api.valueserp.com/search"
MEILI_URL = "http://meilisearch:7700"
MEILI_KEY = ""
MAX_QUERIES_PER_COUNTRY = 2
# ========================

async def google_search(query: str, country_code: str) -> list[dict]:
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
        dc_path = Path("docker-compose.yml")
        if dc_path.exists():
            content = dc_path.read_text()
            if "MEILI_MASTER_KEY=" in content:
                meili_key = content.split("MEILI_MASTER_KEY=")[1].split("\n")[0].strip('"')
        if not meili_key:
            meili_key = "masterKey"
    
    config_path = Path("/app/config/geo_queries.yaml")
    config = yaml.safe_load(config_path.read_text())
    
    meili = Client(MEILI_URL, meili_key)
    
    for i, country in enumerate(config["countries"]):
        await process_country(country, meili, i)
        await asyncio.sleep(3)
    
    print("\n✓ Klaar!")

if __name__ == "__main__":
    asyncio.run(main())
