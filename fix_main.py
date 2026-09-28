with open('backend/main.py', 'r') as f:
    content = f.read()

# Voeg SEO score functie toe na de imports (voor lifespan)
seo_func = '''
def calculate_seo_score(html_content: str, url: str) -> int:
    """Bereken SEO + geo relevantie score 0-100"""
    score = 0
    html_lower = html_content.lower()

    # Title tag (20 punten)
    if '<title>' in html_lower and '</title>' in html_lower:
        score += 20

    # Meta description (20 punten)
    if 'meta name="description"' in html_lower or "meta name='description'" in html_lower:
        score += 20

    # H1 tag (10 punten)
    if '<h1' in html_lower:
        score += 10

    # Afbeeldingen met alt (10 punten)
    if '<img' in html_lower and 'alt=' in html_lower:
        score += 10

    # Structured data (20 punten)
    if 'schema.org' in html_lower or 'application/ld+json' in html_lower:
        score += 20

    # Mobile viewport (10 punten)
    if 'viewport' in html_lower:
        score += 10

    # Geo/lokale relevantie (10 punten)
    geo_keywords = ["location", "address", "city", "country", "region",
                    "coordinates", "map", "near", "distance", "local",
                    "european", "eu", "brussels", "strasbourg"]
    geo_count = sum(1 for kw in geo_keywords if kw in html_lower)
    if geo_count >= 5: score += 10
    elif geo_count >= 2: score += 7
    elif geo_count >= 1: score += 5

    return min(score, 100)


'''

# Zoek waar we de SEO functie moeten invoegen (voor @asynccontextmanager)
insert_pos = content.find('@asynccontextmanager')
if insert_pos != -1:
    content = content[:insert_pos] + seo_func + content[insert_pos:]
    print("✅ SEO functie toegevoegd")
else:
    print("❌ Kon @asynccontextmanager niet vinden")

# Zoek waar we het benchmark endpoint moeten invoegen (na /api/crawl/status)
benchmark_func = '''
@app.get("/api/benchmark/seo-geo")
async def benchmark_seo_geo(url: str = None):
    """Test SEO + geo scoring met echte URL of voorbeelden"""
    
    if url:
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                html = response.text
                score = calculate_seo_score(html, url)
                return {
                    "url": url,
                    "seo_geo_score": score,
                    "max_score": 100,
                    "html_length": len(html),
                    "scoring_breakdown": {
                        "title_tag": 20,
                        "meta_description": 20,
                        "h1_tag": 10,
                        "images_with_alt": 10,
                        "structured_data": 20,
                        "mobile_viewport": 10,
                        "geo_keywords": 10
                    }
                }
        except Exception as e:
            return {"error": str(e), "url": url}
    else:
        test_cases = [
            {
                "name": "EU institutionele pagina",
                "html": """<html><title>European Commission - Brussels</title>
                <meta name="description" content="Official EU site"><h1>Welcome</h1>
                <img src="logo.png" alt="EU Logo"><div itemscope itemtype="https://schema.org/Organization">
                <meta name="viewport" content="width=device-width">
                <p>Location: Brussels, Belgium. Address: Rue de la Loi 200.</p></html>""",
                "url": "https://commission.europa.eu"
            },
            {
                "name": "Simpele pagina zonder SEO",
                "html": "<html><body><p>Hello world</p></body></html>",
                "url": "https://example.com"
            }
        ]
        
        results = []
        for case in test_cases:
            score = calculate_seo_score(case["html"], case["url"])
            results.append({"name": case["name"], "seo_geo_score": score, "max_score": 100})
        
        return {
            "benchmark": "SEO + Geo Scoring (examples)",
            "results": results,
            "note": "Voeg ?url=https://... toe om een live URL te scoren"
        }

'''

# Zoek het einde van de /api/crawl/status functie
crawl_status_pos = content.find('@app.get("/api/crawl/status")')
if crawl_status_pos != -1:
    # Zoek het einde van deze functie
    next_func = content.find('@app.get("/api/summarize")', crawl_status_pos)
    if next_func != -1:
        content = content[:next_func] + benchmark_func + "\n" + content[next_func:]
        print("✅ Benchmark endpoint toegevoegd")
    else:
        print("❌ Kon /api/summarize niet vinden")
else:
    print("❌ Kon /api/crawl/status niet vinden")

with open('backend/main.py', 'w') as f:
    f.write(content)

print("✅ Bestand gefixt!")
