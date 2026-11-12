#!/bin/bash
# Crawl a curated list of sites, one domain at a time (max 100 pages per site).
#
# Run on the Hetzner server from the project folder:
#   ./crawl_eu_sites.sh               # all sites below
#   ./crawl_eu_sites.sh https://x.eu  # only the given site(s)
#
# Uses ADMIN_API_KEY (from the environment or ./.env) so the public
# 3-per-hour crawl limit does not apply. Waits until each crawl has finished
# before starting the next domain.

set -u

API="${CRAWL_API:-http://localhost:18000}"
POLL_SECONDS=15
MAX_WAIT_SECONDS=3600

if [ -z "${ADMIN_API_KEY:-}" ] && [ -f .env ]; then
    ADMIN_API_KEY=$(grep -E '^ADMIN_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"\r')
fi
if [ -z "${ADMIN_API_KEY:-}" ]; then
    echo "❌ ADMIN_API_KEY ontbreekt (zet hem in .env of exporteer hem)." >&2
    exit 1
fi

SITES=(
    # 1. Privacy & digitale rechten
    "https://www.edpb.europa.eu"
    "https://www.gegevensbeschermingsautoriteit.be"
    "https://www.enisa.europa.eu"
    "https://digital-strategy.ec.europa.eu"
    "https://noyb.eu"
    "https://www.bitsoffreedom.nl"
    "https://www.edps.europa.eu"
    # 2. Burgerinformatie BE/NL/EU
    "https://www.belgium.be"
    "https://www.vlaanderen.be"
    "https://www.rijksoverheid.nl"
    "https://europa.eu/youreurope"
    # 3. Praktisch
    "https://www.meteo.be"
    "https://www.knmi.nl"
    "https://www.voedingscentrum.nl"
    "https://www.belastingdienst.nl"
    # 4. Eigen sites
    "https://www.fajaede.nl"
)
if [ "$#" -gt 0 ]; then
    SITES=("$@")
fi

crawl_status() {
    curl -s "$API/api/crawl/status" | sed -n 's/.*"status":"\([a-z]*\)".*/\1/p'
}

wait_until_idle() {
    local waited=0
    while [ "$(crawl_status)" = "actief" ]; do
        if [ "$waited" -ge "$MAX_WAIT_SECONDS" ]; then
            echo "   ⚠️  Crawl duurt langer dan $((MAX_WAIT_SECONDS / 60)) minuten; ga verder." >&2
            return 1
        fi
        sleep "$POLL_SECONDS"
        waited=$((waited + POLL_SECONDS))
    done
}

echo "🚀 Start crawl voor ${#SITES[@]} site(s) via $API"
echo ""

for site in "${SITES[@]}"; do
    wait_until_idle
    echo "📡 Crawling: $site"
    for attempt in 1 2 3; do
        response=$(curl -s -w $'\n%{http_code}' -X POST "$API/api/crawl" \
            -H "Content-Type: application/json" \
            -H "X-Admin-Key: $ADMIN_API_KEY" \
            -d "{\"url\":\"$site\"}")
        code=$(printf "%s" "$response" | tail -n1)
        body=$(printf "%s" "$response" | sed '$d')
        if [ "$code" = "409" ]; then
            # Er draait nog een crawl voor een ander domein: wachten en opnieuw.
            wait_until_idle
            continue
        fi
        break
    done
    echo "   HTTP $code: $body"
    # Geef de crawler even tijd om te starten voordat we de status bewaken.
    sleep 5
done

wait_until_idle
echo ""
echo "✅ Klaar. Status:"
curl -s "$API/api/crawl/status"
echo ""
