#!/usr/bin/env python3

with open('backend/main.py', 'r') as f:
    lines = f.readlines()

# Regel 562 (0-based: 561)
line_idx = 561
if line_idx < len(lines) and '"structured_data": {}' in lines[line_idx]:
    line = lines[line_idx]
    indent = len(line) - len(line.lstrip())
    
    # Voeg toe NA regel 562
    lines.insert(line_idx + 1, ' ' * indent + '"seo_score": calculate_seo_score(html, url),\n')
    lines.insert(line_idx + 2, ' ' * indent + '"geo_score": 0,\n')
    
    with open('backend/main.py', 'w') as f:
        f.writelines(lines)
    
    print("✅ Regel 562 gefixt!")
else:
    print("❌ Regel niet gevonden")
