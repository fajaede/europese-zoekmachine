with open('backend/main.py', 'r') as f:
    content = f.read()

# Vervang de foute len() call
old = '''    # Haal alle documenten
    all_docs = await meili_index.get_documents()
    total = len(all_docs)'''

new = '''    # Haal alle documenten
    docs_info = await meili_index.get_documents()
    all_docs = docs_info.results
    total = len(all_docs)'''

content = content.replace(old, new)

with open('backend/main.py', 'w') as f:
    f.write(content)

print("✅ Benchmark endpoint gefixt!")
