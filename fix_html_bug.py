with open('backend/main.py', 'r') as f:
    content = f.read()

# Vervang de parameter naam in de functie definitie
content = content.replace(
    'def calculate_seo_score(html: str, url: str) -> int:',
    'def calculate_seo_score(html_content: str, url: str) -> int:'
)

# Vervang alle referenties naar html binnen de functie (zolang het niet html.escape of html.unescape is)
# Eerst alle html.lower() vervangen
content = content.replace('html.lower()', 'html_content.lower()')
content = content.replace('html.count(', 'html_content.count(')

with open('backend/main.py', 'w') as f:
    f.write(content)

print("✅ Fix toegepast!")
