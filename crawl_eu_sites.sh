#!/bin/bash

EU_SITES=(
    "https://commission.europa.eu"
    "https://www.europarl.europa.eu"
    "https://www.consilium.europa.eu"
    "https://european-union.europa.eu"
    "https://www.eca.europa.eu"
    "https://curia.europa.eu"
    "https://www.ecb.europa.eu"
    "https://www.edps.europa.eu"
    "https://www.fajaede.nl"
)

echo "🚀 Start crawl voor EU sites..."
echo ""

for site in "${EU_SITES[@]}"; do
    echo "📡 Crawling: $site"
    response=$(curl -s -X POST "http://localhost:18000/api/crawl" \
        -H "Content-Type: application/json" \
        -d "{\"url\":\"$site\"}")
    
    echo "   Response: $response"
    echo ""
    
    # Wacht 2 seconden tussen requests
    sleep 2
done

echo "✅ Alle crawl requests verzonden!"
echo ""
echo "📊 Check status met:"
echo "   curl http://localhost:18000/api/crawl/status"
