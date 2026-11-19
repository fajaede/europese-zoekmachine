# Benchmark-crawl

De benchmark vergelijkt een website met vergelijkbare sites:
"top 18% van de Nederlandse kleine bedrijven". psa.fajaede.nl vraagt die percentielen op via `/api/benchmark/compare`.

## Botnamen

Eén merk met twee namen, ingedeeld naar wat de bot doet (uitleg voor site-eigenaren: https://psa.fajaede.nl/bot):

| Naam | Wat | robots.txt |
|---|---|---|
| `FajaedeBot` | Crawlt zelfstandig: de zoekindex en deze benchmark | Wordt altijd gevolgd |
| `FajaedeScan` | Bekijkt één site op verzoek van een gebruiker: de SEO/GEO-audit (`/api/benchmark/seo-geo`) en de scans op psa.fajaede.nl | Geldt niet; het is een opvraging op verzoek, zoals in een browser |

Beide zijn in `backend/seo_geo_audit.py` vastgelegd (`CRAWLER_USER_AGENT`, `SCAN_USER_AGENT`). Cloudflare-regel voor eigen sites: `http.user_agent contains "Fajaede"`.

**Ondertekening (Web Bot Auth):** beide bots ondertekenen elk verzoek, zodat Cloudflare ze kan herkennen (`backend/web_bot_auth.py`). De sleutels staan in `.env`:
- `WEB_BOT_AUTH_SCAN_KEY` (FajaedeScan). Dezelfde waarde staat ook in Vercel bij psa.
- `WEB_BOT_AUTH_CRAWLER_KEY` (FajaedeBot). Alleen op deze server.

Elke sleutel is een willekeurige seed van 32 bytes, in base64url. De openbare sleutel van FajaedeBot staat op `https://api.fajaede.eu/.well-known/http-message-signatures-directory`, die van FajaedeScan op psa.fajaede.nl.

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

## Slim hercrawlen

**Ongewijzigde pagina's.** Van elke pagina wordt een vingerafdruk bewaard (`content_hash`). Die gaat over de zichtbare tekst, de titel, de description-, robots- en canonical-tags en de JSON-LD. Scripts, stijlen en commentaar tellen niet mee, dus wisselende CMS-codes geven geen valse wijziging. Is een pagina bij de volgende crawl gelijk, dan wordt alleen `fetched_at` bijgewerkt; de analyse wordt overgeslagen. De homepage wordt altijd opnieuw gemeten.

**Volgorde van de pagina's.** Bovenop de vaste regels (homepage, sitemap-prioriteit, contenttype, diepte, parameters):

| Signaal | Effect |
|---|---|
| In de sitemap toegevoegd of bijgewerkt (`lastmod`) sinds de vorige crawl | +15 |
| Gaf vorige keer 404/410 | −60 |
| Was vorige keer een doorverwijzing (301/302/307/308) | −20 |
| Was vorige keer een goede pagina (200, minstens 300 woorden) | +5 |
| Interne links vanaf al gecrawlde pagina's | +3 per pagina, maximaal +15 |
| De homepage is veranderd: belangrijke pagina's (diensten, producten, contact, prijzen) | +10 |

**Pauze na fouten.** De pauze loopt op met elke mislukte poging achter elkaar. Na een geslaagde crawl begint de telling opnieuw.

| Fout | Pauze per poging |
|---|---|
| HTTP 429 of 503 (te druk) | 1, 3, 7, 30 dagen |
| Andere blokkades (403, 401, robots.txt) | 7, 30, 90 dagen |
| Mislukt (dns, timeout, tls, ...) | 1, 7, 30, 90 dagen |
| Doorverwijzing naar een ander domein | 90 dagen |

## Beheer-endpoints

```bash
curl -H "X-Admin-Key: $ADMIN_API_KEY" https://api.fajaede.eu/api/benchmark/queue
curl -H "X-Admin-Key: $ADMIN_API_KEY" https://api.fajaede.eu/api/benchmark/index-stats
```

- **`/queue`:** jobs per status, de volgende 20 jobs, foutredenen van de laatste 24 uur, het aantal domeinen dat in het laatste uur is afgerond, en het aantal domeinen in pauze.
- **`/index-stats`:** domeinen per status en categorie, de versheid (gemeten in de laatste 7, 30 en 90 dagen), het aantal pagina's, het aandeel ongewijzigde pagina's, de cohortgroottes en de cohorten onder het minimum.

Bestaande databases krijgen de nieuwe kolommen automatisch bij de volgende start van de backend. Er gaat geen data verloren.

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

## Autoriteit uit Common Crawl (backlinks, gratis)

Common Crawl publiceert elke paar maanden een linkkaart van het web. Het rangbestand (~2,4 GB gzip,
~135 miljoen domeinen) wordt als stroom gelezen; alleen .nl- en .be-domeinen worden bewaard in
`backend/authority.db` (los van `benchmark.db`, dus de crawler merkt er niets van).

- **Autoriteit (0-100)**, logaritmisch zoals Moz Domain Authority / Ahrefs Domain Rating:
  `100 * (1 - ln(plek) / ln(aantal))`, met plek = rang op harmonische centraliteit tussen de .nl- (of
  .be-)sites **met links**. 100 = sterkste site, elke stap omhoog is moeilijker (plek 10 ~84, 1.000 ~52,
  10.000 ~36). Domeinen zonder links krijgen 0. Een percentiel bleek onbruikbaar: de meeste domeinen
  hebben een of twee zwakke links, dus elke echte bedrijfssite kwam boven 85. Na een wijziging van de
  schaal: `authority rescore` (geen download).
- `/api/benchmark/competitors` geeft `your_authority` en per concurrent `authority` (null zonder import).

```bash
docker exec fajaede-backend python -m backend.benchmark.authority import        # nieuwste uitgave, enkele minuten
docker exec fajaede-backend python -m backend.benchmark.authority status
docker exec fajaede-backend python -m backend.benchmark.authority seed 40000    # sterkste nieuwe domeinen naar de crawler
```

Een nieuwe import vervangt de vorige pas als hij volledig gelukt is. Herhaal hem bij een nieuwe
uitgave (zie https://commoncrawl.org/web-graphs).

### Verwijzende domeinen (backlinks)

Uit dezelfde uitgave: het knooppuntenbestand (~0,9 GB) en het linkbestand (~7,6 GB, ~1,8 miljard
regels), beide als stroom gelezen. Per .nl/.be-domein: het aantal verwijzende domeinen (alle
extensies), hoeveel daarvan .nl/.be, en de 5 sterkste verwijzende .nl/.be-domeinen. Duurt ongeveer
20-30 minuten; draai de autoriteit-import eerst (zelfde uitgave). Downloads hervatten automatisch
na een verbroken verbinding of "SlowDown" van Common Crawl.

```bash
docker exec -d fajaede-backend python -m backend.benchmark.backlinks import    # op de achtergrond
docker exec fajaede-backend python -m backend.benchmark.backlinks status       # voortgang
```

`GET /api/benchmark/backlinks?domain=` (X-Benchmark-Key) geeft autoriteit, verwijzende domeinen en
de sterkste verwijzers; `/competitors` geeft ook `referring_domains` per site.
