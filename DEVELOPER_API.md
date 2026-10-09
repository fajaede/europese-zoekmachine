# 🚀 Developer API - Europese Zoekmachine

## Wat is het?

Een API waarmee developers zich kunnen registreren met hun email om een API key te krijgen, en vervolgens SEO metadata kunnen genereren voor hun content.

## Features

✅ **Email Registratie** - Developers registreren met email → krijgen API key  
✅ **API Key Authenticatie** - Beveiligde endpoints met X-API-Key header  
✅ **SEO Generatie** - Genereer SEO titles, descriptions en keywords met AI  
✅ **Rate Limiting** - 100 requests per dag per developer  
✅ **Usage Tracking** - Houd bij hoeveel requests elke developer maakt  
✅ **Developer Dashboard** - HTML pagina met documentatie en voorbeelden  

## Endpoints

### 1. Registreer een Developer
```bash
POST /api/developers/register
Content-Type: application/json

{
  "email": "jij@voorbeeld.nl",
  "project_name": "Mijn EU News Site"
}

Response:
{
  "api_key": "sk_eu_ryyCYDZJ7FqoGzaN67rRPG9IpsDKnCNa",
  "email": "jij@voorbeeld.nl",
  "project_name": "Mijn EU News Site",
  "daily_limit": 100,
  "message": "Succesvol geregistreerd!"
}
```

### 2. Genereer SEO Metadata
```bash
POST /api/developers/seo/generate
X-API-Key: sk_eu_jouw_key
Content-Type: application/json

{
  "content": "EU Parlement stemt over nieuwe klimaatwet...",
  "url": "[https://mijnsite.nl/eu-klimaat](https://mijnsite.nl/eu-klimaat)",
  "language": "nl"
}

Response:
{
  "title": "EU Klimaatwet 2026: Wat Betekent Dit?",
  "meta_description": "Het EU Parlement heeft gestemd over de nieuwe klimaatwet...",
  "keywords": ["EU", "klimaat", "wet", "parlement", "2026"],
  "score": 87
}
```

### 3. Check Usage Statistics
```bash
GET /api/developers/usage
X-API-Key: sk_eu_jouw_key

Response:
{
  "email": "jij@voorbeeld.nl",
  "project_name": "Mijn EU News Site",
  "daily_limit": 100,
  "requests_today": 5,
  "total_requests": 42,
  "created_at": "2026-09-28 18:00:00"
}
```

### 4. Developer Dashboard
```bash
GET /developers
```

Toont een mooie HTML pagina met documentatie, voorbeelden en quick start guide.

### 5. SEO- en lokale GEO-audit
```bash
GET /api/benchmark/seo-geo?url=https%3A%2F%2Fvoorbeeld.nl
```

De endpoint haalt de publieke HTML-pagina op en retourneert aparte scores van 0-100:
`seo_score` voor on-page SEO en `geo_score` voor lokale vindbaarheid. `seo_geo_score`
blijft beschikbaar als het gemiddelde van beide scores voor bestaande clients.

`scoring_breakdown.seo` en `scoring_breakdown.geo` bevatten per controle de status,
behaalde punten, maximum en uitleg. `issues` bevat de mislukte controles en
`coverage` vermeldt welk deel van de controles daadwerkelijk kon worden uitgevoerd.
Als `url` wordt weggelaten, retourneert de endpoint voorbeeldresultaten.

Dit is een on-page audit van één URL. De score controleert geen Google Business
Profile, externe bedrijfsvermeldingen, zoekresultaten of echte Core Web Vitals.
De endpoint weigert privé-/lokale IP-adressen, niet-standaard poorten en redirects
naar niet-publieke adressen.

## Installatie

De Developer API is al geïnstalleerd! Files:
- `api/developer_api.py` - De API code
- `backend/developer_dashboard.html` - Het dashboard
- `backend/developer_keys.db` - SQLite database met API keys

## Voorbeeld Code

### Python
```python
import requests

API_KEY = "sk_eu_jouw_key"

response = requests.post(
    "http://localhost:8000/api/developers/seo/generate",
    headers={"X-API-Key": API_KEY},
    json={
        "content": "EU Parlement stemt over nieuwe klimaatwet...",
        "url": "[https://mijnsite.nl/eu-klimaat](https://mijnsite.nl/eu-klimaat)",
        "language": "nl"
    }
)

seo = response.json()
print(f"Title: {seo['title']}")
print(f"Description: {seo['meta_description']}")
print(f"Score: {seo['score']}")
```

### JavaScript/Node.js
```javascript
const API_KEY = "sk_eu_jouw_key";

const response = await fetch("http://localhost:8000/api/developers/seo/generate", {
    method: "POST",
    headers: {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json"
    },
    body: JSON.stringify({
        content: "EU Parlement stemt over nieuwe klimaatwet...",
        url: "[https://mijnsite.nl/eu-klimaat](https://mijnsite.nl/eu-klimaat)",
        language: "nl"
    })
});

const seo = await response.json();
console.log("Title:", seo.title);
console.log("Description:", seo.meta_description);
```

### cURL
```bash
curl -X POST http://localhost:8000/api/developers/seo/generate \
  -H "X-API-Key: sk_eu_jouw_key" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "EU Parlement stemt over nieuwe klimaatwet...",
    "url": "[https://mijnsite.nl/eu-klimaat](https://mijnsite.nl/eu-klimaat)",
    "language": "nl"
  }'
```

## Database Schema

### api_keys tabel
- `id` - Auto increment ID
- `email` - Uniek email adres
- `api_key` - Unieke API key (sk_eu_...)
- `project_name` - Optionele project naam
- `created_at` - Registratie datum
- `last_used_at` - Laatste API gebruik
- `request_count` - Totaal aantal requests
- `daily_limit` - Dagelijks limiet (standaard 100)
- `is_active` - Of de key actief is

### rate_limits tabel
- `api_key` - API key
- `date` - Datum (YYYY-MM-DD)
- `request_count` - Aantal requests vandaag

## Next Steps

1. **Email Verificatie** - Stuur verificatie email bij registratie
2. **API Key Management** - Laat developers keys resetten/revoken
3. **Betaalde Tiers** - Hogere limieten voor betalende developers
4. **Analytics Dashboard** - Toon graphs van API usage
5. **Documentation Site** - Volledige API docs met Swagger

---

Gebouwd met ❤️ voor de Europese Zoekmachine
