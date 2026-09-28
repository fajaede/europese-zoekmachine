with open('backend/main.py', 'r') as f:
    content = f.read()

# Vervang alle redis:6379 naar localhost:6379
content = content.replace('redis://redis:6379', 'redis://localhost:6379')

with open('backend/main.py', 'w') as f:
    f.write(content)

print("✅ Redis URLs gefixt!")
