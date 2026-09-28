with open('backend/main.py', 'r') as f:
    content = f.read()

# Zoek en verwijder de foute geo_score functie
start = content.find('def calculate_geo_score')
if start != -1:
    end = content.find('"""Backend API', start)
    if end != -1:
        content = content[:start] + content[end:]

# Voeg een correcte geo_score functie toe
geo_func = '''def calculate_geo_score(html_content: str, url: str) -> int:
    """Bereken geo/lokale relevantie score 0-100"""
    score = 0
    html_lower = html_content.lower()
    geo_keywords = ["location", "address", "city", "country", "region",
                    "coordinates", "map", "near", "distance", "local",
                    "european", "eu", "brussels", "strasbourg"]
    geo_count = sum(1 for kw in geo_keywords if kw in html_lower)
    if geo_count >= 10: score += 40
    elif geo_count >= 5: score += 30
    elif geo_count >= 2: score += 20
    elif geo_count >= 1: score += 10
    if "Place" in html_content and "@" in html_content: score += 20
    if "geo:" in html_content: score += 15
    if "maps.google" in html_lower: score += 10
    return min(100, score)

'''

# Voeg toe na calculate_seo_score
seo_end = content.find('return min(score, 100)', content.find('def calculate_seo_score'))
if seo_end != -1:
    insert_pos = content.find('\n', seo_end) + 1
    content = content[:insert_pos] + geo_func + content[insert_pos:]
    print("✅ Geo score functie gefixt!")
else:
    print("❌ Kon calculate_seo_score niet vinden")

with open('backend/main.py', 'w') as f:
    f.write(content)
