with open('backend/main.py', 'r') as f:
    lines = f.readlines()

# Zoek de lifespan functie
lifespan_line = None
for i, line in enumerate(lines):
    if 'async def lifespan(fastapi_app: FastAPI):' in line:
        lifespan_line = i
        break

if lifespan_line:
    # Voeg SEO functie toe voor lifespan
    seo_func = [
        '\n',
        'def calculate_seo_score(html_content: str, url: str) -> int:\n',
        '    """Bereken SEO + geo relevantie score 0-100"""\n',
        '    score = 0\n',
        '    html_lower = html_content.lower()\n',
        '\n',
        '    # Title tag (20 punten)\n',
        '    if \'<title>\' in html_lower and \'</title>\' in html_lower:\n',
        '        score += 20\n',
        '\n',
        '    # Meta description (20 punten)\n',
        '    if \'meta name="description"\' in html_lower or "meta name=\'description\'" in html_lower:\n',
        '        score += 20\n',
        '\n',
        '    # H1 tag (10 punten)\n',
        '    if \'<h1\' in html_lower:\n',
        '        score += 10\n',
        '\n',
        '    # Afbeeldingen met alt (10 punten)\n',
        '    if \'<img\' in html_lower and \'alt=\' in html_lower:\n',
        '        score += 10\n',
        '\n',
        '    # Structured data (20 punten)\n',
        '    if \'schema.org\' in html_lower or \'application/ld+json\' in html_lower:\n',
        '        score += 20\n',
        '\n',
        '    # Mobile viewport (10 punten)\n',
        '    if \'viewport\' in html_lower:\n',
        '        score += 10\n',
        '\n',
        '    # Geo/lokale relevantie (10 punten)\n',
        '    geo_keywords = ["location", "address", "city", "country", "region",\n',
        '                    "coordinates", "map", "near", "distance", "local",\n',
        '                    "european", "eu", "brussels", "strasbourg"]\n',
        '    geo_count = sum(1 for kw in geo_keywords if kw in html_lower)\n',
        '    if geo_count >= 5: score += 10\n',
        '    elif geo_count >= 2: score += 7\n',
        '    elif geo_count >= 1: score += 5\n',
        '\n',
        '    return min(score, 100)\n',
        '\n',
        '\n',
    ]
    
    # Insert de SEO functie
    for i, line in enumerate(seo_func):
        lines.insert(lifespan_line + i, line)
    
    print(f"✅ SEO functie toegevoegd op regel {lifespan_line}")
else:
    print("❌ Kon lifespan functie niet vinden")

with open('backend/main.py', 'w') as f:
    f.writelines(lines)

print("✅ Bestand bijgewerkt!")
