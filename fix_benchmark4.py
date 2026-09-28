with open('backend/main.py', 'r') as f:
    content = f.read()

# Vervang .seo_score en .geo_score door ['seo_score'] en ['geo_score']
content = content.replace('doc.seo_score', "doc['seo_score']")
content = content.replace('doc.geo_score', "doc['geo_score']")

with open('backend/main.py', 'w') as f:
    f.write(content)

print("✅ Benchmark endpoint gefixt!")
