with open('backend/main.py', 'r') as f:
    content = f.read()

# Voeg calculate_seo_score toe aan het begin
seo_func = '''"""Backend API for the Europese Zoekmachine."""

def calculate_seo_score(html_content: str, url: str) -> int:
    """Bereken SEO relevantie score 0-100"""
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
    
    # Geo/lokale relevantie (20 punten)
    geo_keywords = ["location", "address", "city", "country", "region",
                    "coordinates", "map", "near", "distance", "local",
                    "european", "eu", "brussels", "strasbourg"]
    geo_count = sum(1 for kw in geo_keywords if kw in html_lower)
    if geo_count >= 5: score += 20
    elif geo_count >= 2: score += 15
    elif geo_count >= 1: score += 10
    
    return min(score, 100)

'''

# Vervang de begin code
content = seo_func + content

with open('backend/main.py', 'w') as f:
    f.write(content)

print("✅ Bestand gefixt!")
