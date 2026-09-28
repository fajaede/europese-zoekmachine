with open('backend/main.py', 'r') as f:
    lines = f.readlines()

# Zoek de @app.get("/api/crawl/status") regel
crawl_status_line = None
for i, line in enumerate(lines):
    if '@app.get("/api/crawl/status")' in line:
        # Zoek het einde van deze functie (volgende @app. of einde)
        for j in range(i+1, len(lines)):
            if lines[j].strip().startswith('@app.') or lines[j].strip().startswith('async def') or lines[j].strip().startswith('def'):
                crawl_status_line = j
                break
        if crawl_status_line is None:
            crawl_status_line = i + 30  # ongeveer het einde van de functie
        break

if crawl_status_line:
    benchmark_code = [
        '\n',
        '@app.get("/api/benchmark/seo-geo")\n',
        'async def benchmark_seo_geo(url: str = None):\n',
        '    """Test SEO + geo scoring met echte URL of voorbeelden"""\n',
        '    \n',
        '    if url:\n',
        '        try:\n',
        '            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:\n',
        '                response = await client.get(url)\n',
        '                response.raise_for_status()\n',
        '                html = response.text\n',
        '                score = calculate_seo_score(html, url)\n',
        '                return {\n',
        '                    "url": url,\n',
        '                    "seo_geo_score": score,\n',
        '                    "max_score": 100,\n',
        '                    "html_length": len(html),\n',
        '                    "scoring_breakdown": {\n',
        '                        "title_tag": 20,\n',
        '                        "meta_description": 20,\n',
        '                        "h1_tag": 10,\n',
        '                        "images_with_alt": 10,\n',
        '                        "structured_data": 20,\n',
        '                        "mobile_viewport": 10,\n',
        '                        "geo_keywords": 10\n',
        '                    }\n',
        '                }\n',
        '        except Exception as e:\n',
        '            return {"error": str(e), "url": url}\n',
        '    else:\n',
        '        test_cases = [\n',
        '            {\n',
        '                "name": "EU institutionele pagina",\n',
        '                "html": """<html><title>European Commission - Brussels</title>\n',
        '                <meta name="description" content="Official EU site"><h1>Welcome</h1>\n',
        '                <img src="logo.png" alt="EU Logo"><div itemscope itemtype="https://schema.org/Organization">\n',
        '                <meta name="viewport" content="width=device-width">\n',
        '                <p>Location: Brussels, Belgium. Address: Rue de la Loi 200.</p></html>""",\n',
        '                "url": "https://commission.europa.eu"\n',
        '            },\n',
        '            {\n',
        '                "name": "Simpele pagina zonder SEO",\n',
        '                "html": "<html><body><p>Hello world</p></body></html>",\n',
        '                "url": "https://example.com"\n',
        '            }\n',
        '        ]\n',
        '        \n',
        '        results = []\n',
        '        for case in test_cases:\n',
        '            score = calculate_seo_score(case["html"], case["url"])\n',
        '            results.append({"name": case["name"], "seo_geo_score": score, "max_score": 100})\n',
        '        \n',
        '        return {\n',
        '            "benchmark": "SEO + Geo Scoring (examples)",\n',
        '            "results": results,\n',
        '            "note": "Voeg ?url=https://... toe om een live URL te scoren"\n',
        '        }\n',
        '\n',
    ]
    
    # Insert de benchmark code
    for i, line in enumerate(benchmark_code):
        lines.insert(crawl_status_line + i, line)
    
    print(f"✅ Benchmark endpoint toegevoegd op regel {crawl_status_line}")
else:
    print("❌ Kon crawl status endpoint niet vinden")

with open('backend/main.py', 'w') as f:
    f.writelines(lines)

print("✅ Bestand bijgewerkt!")
