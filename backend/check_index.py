from meilisearch import Client

meili = Client("http://meilisearch:7700", "Fajaede_Secure_Meili_Key_928374!")

stats = meili.index("documents").get_stats()
print(f"Totaal documenten: {stats.number_of_documents}")

results = meili.index("documents").search("", {"limit": 5})
print(f"\nVoorbeeld documenten: {len(results['hits'])}")

for hit in results['hits'][:3]:
    title = hit.get("title", "N/A")
    print(f"  - {title[:60]}")
