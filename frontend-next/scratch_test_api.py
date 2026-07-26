"""Een eenvoudig script om de /search API endpoint te testen.

Dit script voert een reeks vooraf gedefinieerde zoekopdrachten uit en drukt
statistieken af over de ontvangen resultaten, zoals het totale aantal,
en het aantal resultaten met afbeeldingen of video's.
"""

import asyncio
import json
import os
import httpx

API_BASE_URL = os.getenv("API_URL", "http://127.0.0.1:18000")
queries = ["europa", "nieuws", "geld", "fajaede.nl", "rotterdam"]

async def test_query(client, query):
    """Voert een enkele query uit en print de resultaten."""
    print(f"=== Query: {query} ===")
    try:
        params = {"q": query, "limit": 50}
        response = await client.get(f"{API_BASE_URL}/api/search", params=params, timeout=30.0)
        response.raise_for_status()  # Werpt een error op voor 4xx/5xx responses

        data = response.json()
        results = data.get("results", [])
        images = [res for res in results if res.get("image_url")]
        videos = [res for res in results if res.get("video_url")]

        print(f"Total results: {len(results)}")
        print(f"With image_url: {len(images)}")
        print(f"With video_url: {len(videos)}")

    except (httpx.RequestError, json.JSONDecodeError) as e:
        print(f"Request failed for query '{query}': {e}")

async def main():
    """Hoofdfunctie om alle queries asynchroon te testen."""
    async with httpx.AsyncClient() as client:
        for q in queries:
            await test_query(client, q)

if __name__ == "__main__":
    asyncio.run(main())
