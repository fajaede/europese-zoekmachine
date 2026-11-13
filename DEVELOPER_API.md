# 🚀 Developer API - Europese Zoekmachine

## Wat is het?

Een API waarmee developers zich kunnen registreren met hun email om een API key te krijgen, en vervolgens SEO metadata kunnen genereren voor hun content.

## Features

✅ **Email Registratie** - Developers registreren met email → krijgen API key  
✅ **API Key Authenticatie** - Beveiligde endpoints met X-API-Key header  
✅ **SEO Generatie** - Genereer SEO titles, descriptions en keywords met AI  
✅ **Rate Limiting** - Free 100, Pro 2.000, Premium 10.000 requests per dag  
✅ **Pro/Premium-passen** - Prepaid via Mollie (30 of 90 dagen, geen automatische verlenging)  
✅ **Usage Tracking** - Houd bij hoeveel requests elke developer maakt  
✅ **Developer Dashboard** - HTML pagina met documentatie en voorbeelden  

## Serverconfiguratie voor e-mailverificatie

Stel deze variabelen uitsluitend in op de backendserver; commit geen `.env`-bestand:

```env
DEVELOPER_VERIFICATION_BASE_URL=https://api.fajaede.eu
SMTP_HOST=<smtp-host>
SMTP_PORT=587
SMTP_USER=<smtp-user>
SMTP_PASS=<smtp-password>
SMTP_FROM=<verified-sender-address>
```

Productie vereist HTTPS voor `DEVELOPER_VERIFICATION_BASE_URL`. Lokale ontwikkeling mag `http://localhost:18000` gebruiken. Zonder mailconfiguratie wordt geen developer-key aangemaakt.

## Beveiliging

- Een API-key wordt pas na mailboxverificatie eenmalig getoond. Bij een bestaand e-mailadres roteert bevestiging de oude key.
- Registratie is begrensd per client-IP; API-gebruik heeft een atomische daglimiet.
- Behandel de API-key als een wachtwoord. Bewaar hem uitsluitend server-side in een environment variable.
- Zet nooit een API-key in browser-JavaScript, een publieke repository, screenshots of logs.
- Registratie-antwoorden zijn generiek om account-enumeratie te beperken; verificatietokens verlopen na 30 minuten en zijn eenmalig.
- Een eerder in broncode gepleegde ValueSERP-key-publicatie vereist intrekken en rouleren in het ValueSERP-dashboard.

## Endpoints

### 1. Registreer een Developer
```bash
POST /api/developers/register
Content-Type: application/json

{
  "email": "jij@voorbeeld.nl",
  "project_name": "Mijn EU News Site"
}

Response (`202 Accepted`; dezelfde tekst voor nieuwe en bestaande accounts):
{
  "message": "Als dit adres kan worden geregistreerd, ontvang je een verificatiemail."
}
```

Open de mail-link en bevestig het adres. Sla daarna de API-key uit het eenmalige verificatieantwoord op in een server-side environment variable.

De verificatieflow gebruikt `GET /api/developers/verify?token=...` om een bevestigingspagina te tonen; deze GET wijzigt geen account. De bevestigingsknop doet een `POST /api/developers/verify` met de token. De token verloopt na 30 minuten.

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
import os

API_KEY = os.environ["EUROPEAN_SEARCH_API_KEY"]

response = requests.post(
    "https://api.fajaede.eu/api/developers/seo/generate",
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
const API_KEY = process.env.EUROPEAN_SEARCH_API_KEY;

const response = await fetch("https://api.fajaede.eu/api/developers/seo/generate", {
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
curl -X POST https://api.fajaede.eu/api/developers/seo/generate \
  -H "X-API-Key: sk_eu_jouw_key" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "EU Parlement stemt over nieuwe klimaatwet...",
    "url": "[https://mijnsite.nl/eu-klimaat](https://mijnsite.nl/eu-klimaat)",
    "language": "nl"
  }'
```

### 6. Pakketten en betalen
```bash
GET  /api/developers/plans              # prijzen en limieten
POST /api/developers/checkout           # X-API-Key + {"plan": "pro"|"premium", "days": 30|90, "accept_terms": true, "immediate_start": true}
POST /api/developers/payments/webhook   # alleen voor Mollie
```

| Pakket  | Requests/dag | 30 dagen | 90 dagen |
|---------|--------------|----------|----------|
| Free    | 100          | gratis   | gratis   |
| Pro     | 2.000        | €9       | €24      |
| Premium | 10.000       | €29      | €79      |

Passen worden niet automatisch verlengd. Dezelfde pas opnieuw kopen telt dagen op; een upgrade Pro → Premium zet resterende Pro-dagen naar prijsverhouding om. Downgraden kan pas na afloop. Een key-rotatie behoudt de pas. Daglimieten zijn instelbaar met `DEVELOPER_PRO_DAILY_LIMIT` en `DEVELOPER_PREMIUM_DAILY_LIMIT`.

Gebruikt dezelfde `MOLLIE_API_KEY` en `MOLLIE_MODE` als de builder. De webhook is standaard `${DEVELOPER_VERIFICATION_BASE_URL}/api/developers/payments/webhook` (overschrijfbaar met `DEVELOPER_WEBHOOK_URL`); hij wordt per betaling meegestuurd, dus in het Mollie-dashboard hoeft niets ingesteld te worden.

### Bedrijven en btw
Checkout accepteert optioneel `business`, `company_name`, `vat_number` en `company_address`. Het btw-nummer wordt via VIES gecontroleerd (bij storing: 503, geen betaling). Belgische bedrijven betalen de prijs incl. 21% btw; andere EU-bedrijven met geldig nummer de prijs excl. btw (btw verlegd). Na betaling krijgt `INVOICE_NOTIFY_EMAIL` (standaard `SMTP_FROM`) een mail met alle factuurgegevens; de factuur zelf (Peppol) maak je in je boekhoudpakket.

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

1. **API Key Management** - Voeg een authenticated revoke endpoint toe
2. **Analytics Dashboard** - Toon graphs van API usage
3. **Documentation Site** - Volledige API docs met Swagger

---

Gebouwd met ❤️ voor de Europese Zoekmachine
