#!/usr/bin/env python3
"""Voegt seo_score en geo_score toe aan document creatie"""

with open('backend/main.py', 'r') as f:
    content = f.read()

# Vind en vervang de eerste document creatie (rond regel 406)
old_doc1 = '''                "id": hashlib.sha256(url.encode()).hexdigest(),
                "url": url,
                "title": title,
                "content": content[:5000],
                "structured_data": structured_data'''

new_doc1 = '''                "id": hashlib.sha256(url.encode()).hexdigest(),
                "url": url,
                "title": title,
                "content": content[:5000],
                "structured_data": structured_data,
                "seo_score": calculate_seo_score(html, url),
                "geo_score": 0  # Placeholder, later implementeren'''

content = content.replace(old_doc1, new_doc1)

# Vind en vervang de tweede document creatie (rond regel 556)
old_doc2 = '''                    "id": hashlib.sha256(url.encode()).hexdigest(),
                    "url": url,
                    "title": title,
                    "content": content[:5000],
                    "structured_data": structured_data'''

new_doc2 = '''                    "id": hashlib.sha256(url.encode()).hexdigest(),
                    "url": url,
                    "title": title,
                    "content": content[:5000],
                    "structured_data": structured_data,
                    "seo_score": calculate_seo_score(html, url),
                    "geo_score": 0  # Placeholder'''

content = content.replace(old_doc2, new_doc2)

# Schrijf terug
with open('backend/main.py', 'w') as f:
    f.write(content)

print("✅ SEO scores toegevoegd aan documents!")
