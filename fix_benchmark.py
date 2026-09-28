with open('backend/main.py', 'r') as f:
    content = f.read()

# Verwijder het incomplete benchmark endpoint
if '@app.get("/api/benchmark/{seo_score}/{geo_score}")' in content:
    # Zoek het einde van het endpoint
    start = content.find('@app.get("/api/benchmark/{seo_score}/{geo_score}")')
    # Zoek het volgende @app. of einde van file
    next_app = content.find('@app.', start + 10)
    if next_app == -1:
        next_app = len(content)
    content = content[:start] + content[next_app:]

# Voeg compleet benchmark endpoint toe
benchmark_endpoint = '''
@app.get("/api/benchmark/{seo_score}/{geo_score}")
async def get_benchmark(seo_score: int, geo_score: int, request: Request):
    """Bereken hoe een score verhoudt tot alle geïndexeerde websites."""
    meili_index = request.app.state.meili_index
    
    if not meili_index:
        raise HTTPException(status_code=503, detail="MeiliSearch niet beschikbaar")
    
    # Haal alle documenten
    all_docs = await meili_index.get_documents({"limit": 100000})
    total = len(all_docs)
    
    if total == 0:
        return {
            "seo_percentile": 0,
            "geo_percentile": 0,
            "avg_seo_score": 0,
            "avg_geo_score": 0,
            "total_websites": 0,
            "your_seo_score": seo_score,
            "your_geo_score": geo_score
        }
    
    # Tel hoeveel websites lagere scores hebben
    seo_better = sum(1 for doc in all_docs if doc.seo_score < seo_score)
    geo_better = sum(1 for doc in all_docs if doc.geo_score < geo_score)
    
    seo_percentile = (seo_better / total) * 100
    geo_percentile = (geo_better / total) * 100
    
    # Bereken gemiddelden
    avg_seo = sum(doc.seo_score for doc in all_docs) / total
    avg_geo = sum(doc.geo_score for doc in all_docs) / total
    
    return {
        "seo_percentile": round(seo_percentile, 1),
        "geo_percentile": round(geo_percentile, 1),
        "avg_seo_score": round(avg_seo, 1),
        "avg_geo_score": round(avg_geo, 1),
        "total_websites": total,
        "your_seo_score": seo_score,
        "your_geo_score": geo_score
    }
'''

# Voeg toe voor het laatste @app. block
last_app = content.rfind('@app.')
if last_app != -1:
    content = content[:last_app] + benchmark_endpoint + '\n' + content[last_app:]
else:
    content += benchmark_endpoint

with open('backend/main.py', 'w') as f:
    f.write(content)

print("✅ Benchmark endpoint toegevoegd!")
