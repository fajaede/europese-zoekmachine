with open('backend/main.py', 'r') as f:
    content = f.read()

# Oude code
old_code = '''    # Controleer of de 'is_running' vlag al in Redis staat.
    if await request.app.state.redis_client.get("crawler:is_running"):
        return {
            "message": "Een crawl-taak is al actief. Wacht tot deze is voltooid."
        }

    # Creëer en start de crawler.
    crawler = Crawler(
        request.app.state.meili_index, request.app.state.redis_client
    )
    background_tasks.add_task(crawler.run, crawl_request.url)
    return {"message": f"Crawl-taak voor {crawl_request.url} is gestart."}'''

# Nieuwe code met queue support
new_code = '''    # Voeg URL toe aan de wachtrij
    await request.app.state.redis_client.lpush("crawler:queue", crawl_request.url)
    queue_size = await request.app.state.redis_client.llen("crawler:queue")
    
    # Start crawler als er nog geen actief is
    if not await request.app.state.redis_client.get("crawler:is_running"):
        crawler = Crawler(
            request.app.state.meili_index, request.app.state.redis_client
        )
        background_tasks.add_task(crawler.run, crawl_request.url)
        return {
            "message": f"Crawl-taak voor {crawl_request.url} is gestart.",
            "queue_size": queue_size,
            "status": "started"
        }
    else:
        return {
            "message": f"URL toegevoegd aan wachtrij (positie {queue_size}). Crawl is al bezig.",
            "queue_size": queue_size,
            "status": "queued"
        }'''

content = content.replace(old_code, new_code)

with open('backend/main.py', 'w') as f:
    f.write(content)

print("✅ Crawl endpoint aangepast met queue support!")
