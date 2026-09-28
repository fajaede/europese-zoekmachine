# Crawler Fixes - Europese Zoekmachine

**Datum:** 25 september 2026  
**Status:** ✅ Werkend

## Probleem
De crawler kon websites niet indexeren vanwege:
- 400 Bad Request errors bij het ophalen van pagina's
- robots.txt die alle requests blokkeerde (ook toegestane)

## Oplossingen

### 1. robots.txt Empty Check
Python's RobotFileParser gaf False terug ook als robots.txt leeg was.

**Fix:** Check mtime() == 0 en allow all in dat geval.

### 2. Minimale HTTP Headers
Te veel browser headers veroorzaakten 400 errors.

**Fix:** Gebruik alleen User-Agent, Accept, Accept-Language.

### 3. HTTP/1.1 Forceer
HTTP/2 veroorzaakte compatibiliteitsproblemen.

**Fix:** http2=False in httpx.AsyncClient

### 4. HEAD Request Fallback
Sommige servers accepteren geen HEAD requests.

**Fix:** Ga door met GET als HEAD faalt.

## Testen
```bash
# Crawler starten
curl -X POST http://localhost:18000/api/crawl \
  -H "Content-Type: application/json" \
  -d '{"url":"[https://www.fajaede.nl/](https://www.fajaede.nl/)"}'

# Logs volgen
docker compose logs -f backend | grep -iE 'fajaede|geindexeerd'
```

## Resultaat
- Crawler indexeert fajaede.nl succesvol
- 23+ documenten geindexeerd
- Frontend toont zoekresultaten
