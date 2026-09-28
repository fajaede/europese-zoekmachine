with open('backend/main.py', 'r') as f:
    content = f.read()

# Zoek de regel die crawler:is_running niet verwijdert
old = 'await redis_client.delete("crawler:queue", "crawler:visited_urls", "crawler:content_hashes")'
new = 'await redis_client.delete("crawler:queue", "crawler:visited_urls", "crawler:content_hashes", "crawler:is_running")'

if old in content:
    content = content.replace(old, new)
    with open('backend/main.py', 'w') as f:
        f.write(content)
    print("✅ Fix toegevoegd!")
else:
    print("❌ Kon de regel niet vinden")
