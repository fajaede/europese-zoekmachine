with open('backend/main.py', 'r') as f:
    content = f.read()

# Voeg calculate_geo_score functie toe
geo_func = '''

def calculate_geo_score(html_content: str, url: str) -> int:
    """Bereken geo/lokale relevantie score 0-100"""
    score = 0
    html_lower = html_content.lower()
    
    # Check op locatie-gerelateerde keywords
    geo_keywords = [
        'location', 'address', 'city', 'country', 'region', 'state', 'province',
        'coordinates', 'latitude', 'longitude', 'map', 'near', 'nearby',
        'distance', 'directions', 'local', 'area', 'zone', 'district',
        'plaats', 'stad', 'land', 'regio', 'adres', 'locatie',
        'european', 'eu', 'brussels', 'strasbourg', 'luxembourg'
    ]
    
    geo_count = sum(1 for kw in geo_keywords if kw in html_lower)
    
    # Score gebaseerd op aantal geo keywords
    if geo_count >= 10:
        score += 40
    elif geo_count >= 5:
        score += 30
    elif geo_count >= 2:
        score += 20
    elif geo_count >= 1:
        score += 10
    
    # Check op gestructureerde geo data (JSON-LD)
    if '"@type": "Place"' in html_content or '"@type":"Place"' in html_content:
        score += 20
    if '"@type": "LocalBusiness"' in html_content or '"@type":"LocalBusiness"' in html_content:
        score += 20
    if 'geo:' in html_content or 'geo://' in html_content:
        score += 15
    
    # Check op adres patronen
    if re.search(r'\d{4,5}\s+[A-Z]{2}\s+\w+', html_content):
        score += 15
    
    # Check op map embeds
    if 'maps.google' in html_lower or 'google.com/maps' in html_lower:
        score += 10
    if 'openstreetmap' in html_lower:
        score += 10
    
    return min(100, score)
'''

# Voeg toe na calculate_seo_score functie
if 'def calculate_geo_score' not in content:
    # Zoek einde van calculate_seo_score
    seo_end = content.find('return score', content.find('def calculate_seo_score'))
    if seo_end != -1:
        insert_pos = content.find('\n', seo_end) + 1
        content = content[:insert_pos] + geo_func + content[insert_pos:]
        print("✅ calculate_geo_score toegevoegd!")
    else:
        print("❌ Kon calculate_seo_score niet vinden")
else:
    print("✅ calculate_geo_score bestaat al!")

with open('backend/main.py', 'w') as f:
    f.write(content)
