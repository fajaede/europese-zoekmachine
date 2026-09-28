with open('backend/main.py', 'r') as f:
    lines = f.readlines()

# Zoek de regel met "return score" in calculate_seo_score
insert_line = None
for i, line in enumerate(lines):
    if i > 0 and i < 100 and 'return score' in line and 'def calculate_seo_score' in ''.join(lines[max(0,i-30):i]):
        insert_line = i + 1
        break

if insert_line:
    geo_func = [
        '\n',
        'def calculate_geo_score(html_content: str, url: str) -> int:\n',
        '    """Bereken geo/lokale relevantie score 0-100"""\n',
        '    score = 0\n',
        '    html_lower = html_content.lower()\n',
        '    geo_keywords = [\'location\', \'address\', \'city\', \'country\', \'region\',\n',
        '                    \'coordinates\', \'map\', \'near\', \'distance\', \'local\',\n',
        '                    \'european\', \'eu\', \'brussels\', \'strasbourg\']\n',
        '    geo_count = sum(1 for kw in geo_keywords if kw in html_lower)\n',
        '    if geo_count >= 10: score += 40\n',
        '    elif geo_count >= 5: score += 30\n',
        '    elif geo_count >= 2: score += 20\n',
        '    elif geo_count >= 1: score += 10\n',
        '    if \'"@type": "Place"\' in html_content: score += 20\n',
        '    if \'geo:\' in html_content: score += 15\n',
        '    if \'maps.google\' in html_lower: score += 10\n',
        '    return min(100, score)\n',
        '\n',
    ]
    for j, new_line in enumerate(geo_func):
        lines.insert(insert_line + j, new_line)
    with open('backend/main.py', 'w') as f:
        f.writelines(lines)
    print(f"✅ calculate_geo_score toegevoegd!")
else:
    print("❌ Kon calculate_seo_score niet vinden")
