#!/usr/bin/env python3

with open('backend/main.py', 'r') as f:
    lines = f.readlines()

# Regel 410 en 560 aanpassen (0-based: 409 en 559)
for i in [409, 559]:
    if i < len(lines):
        line = lines[i]
        indent = len(line) - len(line.lstrip())
        
        # Voeg seo_score toe NA structured_data regel
        if '"structured_data"' in line:
            # Insert na deze regel
            lines.insert(i + 1, ' ' * indent + '"seo_score": calculate_seo_score(html, url),\n')
            lines.insert(i + 2, ' ' * indent + '"geo_score": 0,\n')

# Schrijf terug
with open('backend/main.py', 'w') as f:
    f.writelines(lines)

print("✅ SEO scores toegevoegd op regel 410 en 560!")
