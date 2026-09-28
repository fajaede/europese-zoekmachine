#!/usr/bin/env python3
"""Voegt SEO score functie toe aan main.py"""

import re

# Lees main.py
with open('backend/main.py', 'r') as f:
    content = f.read()

# SEO score functie
seo_function = '''

def calculate_seo_score(html_content: str, url: str) -> int:
    """Bereken SEO score 0-100"""
    score = 0
    html_lower = html_content.lower()
    
    # Title tag (15 punten)
    if '<title>' in html_lower and '</title>' in html_lower:
        score += 15
    
    # Meta description (15 punten)
    if 'meta name="description"' in html_lower or 'meta property="og:description"' in html_lower:
        score += 15
    
    # H1 heading (10 punten)
    if '<h1' in html_lower:
        score += 10
    
    # H2 heading (10 punten)
    if '<h2' in html_lower:
        score += 10
    
    # Interne links (10 punten)
    if 'href="/' in html_content or f'href="{url}' in html_content:
        score += 10
    
    # Externe links (10 punten)
    if 'href="http' in html_content:
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
    
    return min(score, 100)

'''

# Voeg toe na de imports (zoek naar eerste 'def' of 'class')
# Zoek de plek na imports
import_pattern = r"(^import .*$\n|^from .*$\n)*"
match = re.search(import_pattern, content, re.MULTILINE)

if match:
    insert_pos = match.end()
    content = content[:insert_pos] + seo_function + content[insert_pos:]
    
    # Schrijf terug
    with open('backend/main.py', 'w') as f:
        f.write(content)
    
    print("✅ SEO score functie toegevoegd!")
else:
    print("❌ Kon imports niet vinden")
