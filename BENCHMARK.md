# Benchmark-crawl

De benchmark vergelijkt een website met vergelijkbare sites:
"top 18% van de Nederlandse kleine bedrijven". psa.fajaede.nl vraagt die percentielen op via `/api/benchmark/compare`.

## Hoe het werkt

- Per domein crawlt de worker een paar pagina's met de hoogste prioriteit. De volgorde komt uit de sitemap en de links op de homepage. Eerst de homepage, dan diensten, producten, contact en blog. Parameter-URL's, archieven, login en winkelwagen krijgen strafpunten. Dubbele pagina's worden overgeslagen, en robots.txt wordt altijd gevolgd.
- **De domeinscores komen van de homepage.** Het gaat om PSA, SEO, GEO, AI-zichtbaarheid en security. psa.fajaede.nl scant ook één URL, dus de vergelijking is eerlijk. De overige pagina's worden bewaard voor statistieken over de hele site.
- De berekening van PSA en AI is een kopie van die in psa (`app/api/scan/route.ts`, `lib/generative-visibility.ts`). **Verander je die regels in psa, pas dan ook `backend/benchmark/signals.py` aan.**
- Er wordt geen ruwe HTML bewaard. Elke meting heeft een tijdstip (`calculated_at`). Scores ouder dan 90 dagen tellen niet mee.

| Categorie | Pagina's | Opnieuw na |
|---|---|---|
| Kleine bedrijven / MKB (`mkb`) | 5 | 30 dagen |
| Zakelijke dienstverlening (`zakelijk`) | 10 | 30 dagen |
| SaaS / tech (`saas`) | 15 | 14 dagen |
| E-commerce (`ecommerce`) | 20 | 7 dagen |
| Nieuws / media (`nieuws`) | 20 | 7 dagen |
| Overheid / onderwijs / zorg (`publiek`) | 10 | 30 dagen |
| Kleine statische site (`klein`) | 5 | 90 dagen |
| Geblokkeerd of onbereikbaar | – | 90 dagen |

De categorie wordt na de eerste crawl automatisch bepaald (`backend/benchmark/rules.py`). Je kunt hem ook zelf meegeven bij het toevoegen.

Cohorten zijn land × categorie: `NL:mkb`, `*:mkb`, `NL:*` en `*:*`. Een percentiel wordt alleen getoond bij minstens 30 sites (`BENCHMARK_MIN_COHORT`). Anders valt de vergelijking terug op een bredere groep.

**Spreiding is vereist.** Heeft meer dan 70% van een groep dezelfde score, dan wordt voor die meting geen percentiel getoond. Een voorbeeld is GEO 0 bij sites zonder lokale bedrijfsgegevens.

**Status per domein** (`index_status`):

| Status | Betekenis |
|---|---|
| `ok` | Gemeten. |
| `blocked` | De site weigert bots: HTTP 401, 403, 429 of 503, of een blokkade in robots.txt. |
| `redirect` | De site stuurt door naar een ander domein. Dat volgt de crawler bewust niet. |
| `failed` | Het domein kon niet gemeten worden. De reden staat in `crawl_jobs.error`: `dns`, `timeout`, `tls certificate`, `connection refused`, enzovoort. |

## Aanzetten op de server

Zet in `.env`:

```env
BENCHMARK_WORKER=1              # start de crawler mee met de backend
BENCHMARK_CONCURRENCY=4         # domeinen tegelijk
BENCHMARK_PAGE_DELAY=1.0        # seconden tussen pagina's van hetzelfde domein
BENCHMARK_API_KEY=<lange willekeurige sleutel>   # dezelfde sleutel komt in Vercel (psa)
```

Herstart daarna de backend: `docker compose up -d --build backend`.

De database staat in `backend/benchmark.db` (SQLite). Die staat in .gitignore en blijft bewaard via de volume-mount.

## Domeinen toevoegen (proef: 10.000)

```bash
# 1. domeinen uit de eigen zoekindex
curl -X POST -H "X-Admin-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"source":"index","limit":10000}' https://api.fajaede.eu/api/benchmark/domains

# 2. aanvullen met .nl/.be uit de Tranco-toplijst
curl -X POST -H "X-Admin-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"source":"tranco","limit":8000}' https://api.fajaede.eu/api/benchmark/domains

# eigen lijst
curl -X POST -H "X-Admin-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"source":"list","domains":[{"domain":"voorbeeld.nl","category":"mkb"}]}' \
  https://api.fajaede.eu/api/benchmark/domains
```

Bestaande domeinen worden overgeslagen.

## Volgen

```bash
curl -H "X-Admin-Key: $ADMIN_API_KEY" https://api.fajaede.eu/api/benchmark/status
curl -X POST -H "X-Admin-Key: $ADMIN_API_KEY" https://api.fajaede.eu/api/benchmark/recalculate
```

De percentielen worden elk uur automatisch herberekend.

**Snelheid:** ongeveer 3 tot 17 seconden per domein, afhankelijk van het aantal pagina's. Met 4 domeinen tegelijk duurt de proef van 10.000 domeinen ongeveer 4 tot 8 uur.

## Vergelijken (voor psa)

```bash
curl -H "X-Benchmark-Key: $BENCHMARK_API_KEY" \
  "https://api.fajaede.eu/api/benchmark/compare?domain=voorbeeld.nl&psa=82&seo=67&geo=40&ai=50"
```

Het antwoord geeft per score `percentile`, `top_percent`, `rank`, `group_size`, `cohort` en `cohort_label` (bijvoorbeeld "Nederlandse kleine bedrijven / mkb"), plus `data_as_of`.
