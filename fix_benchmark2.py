with open('backend/main.py', 'r') as f:
    content = f.read()

# Vervang de foute get_documents call
old = '''    # Haal alle documenten
    all_docs = await meili_index.get_documents({"limit": 100000})
    total = len(all_docs)'''

new = '''    # Haal alle documenten
    all_docs = await meili_index.get_documents()
    total = len(all_docs)'''

content = content.replace(old, new)

with open('backend/main.py', 'w') as f:
    f.write(content)

print("✅ Benchmark endpoint gefixt!")
