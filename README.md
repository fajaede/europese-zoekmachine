
---

## Veilige API-integratie

### Voorbeeld: Integratie in Je Website

Plaats een developer-API-key nooit in HTML, browser-JavaScript, `localStorage` of een publieke repository. Bezoekers kunnen die key uit de browser halen en jouw dagelijkse API-limiet verbruiken.

De browser mag alleen jouw eigen same-origin backend-route aanroepen. Die route bewaart `EUROPEAN_SEARCH_API_KEY` op de server en stuurt het verzoek door naar de Developer API. De key wordt nooit naar de browser teruggestuurd. Zie `DEVELOPER_API.md` voor server-side voorbeelden.

```html
<!DOCTYPE html>
<html lang="nl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Jouw Website</title>
    
    <!-- === SEO AUTO-INJECT SCRIPT === -->
    <script>
      (function() {
        // === CONFIGURATIE ===
        const SEO_CONFIG = {
          API_URL: '/api/seo/generate', // Same-origin backend proxy; implement this route on your server.
          
          // VUL HIER JOUW PAGINA CONTENT IN
          pages: {
            '/': "Jouw homepage content - beschrijf je bedrijf, diensten en locatie",
            '/over-ons': "Over ons pagina content",
            '/diensten': "Diensten pagina content",
            '/contact': "Contact pagina content"
          }
        };
        
        // === AUTO DETECT EN INJECT ===
        async function injectSEO() {
          const path = window.location.pathname;
          const content = SEO_CONFIG.pages[path];
          
          if (!content || content.length < 50) {
            console.log('[SEO] Geen content gevonden voor', path);
            return;
          }
          
          console.log('[SEO] Genereren voor:', path);
          
          try {
            const response = await fetch(SEO_CONFIG.API_URL, {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json'
              },
              body: JSON.stringify({
                content: content,
                url: window.location.href,
                language: 'nl'
              })
            });
            
            const data = await response.json();
            
            // Update title
            document.title = data.title;
            
            // Update/maak description
            let descTag = document.querySelector('meta[name="description"]');
            if (!descTag) {
              descTag = document.createElement('meta');
              descTag.name = 'description';
              document.head.appendChild(descTag);
            }
            descTag.content = data.meta_description;
            
            // Update/maak keywords
            let keywordsTag = document.querySelector('meta[name="keywords"]');
            if (!keywordsTag) {
              keywordsTag = document.createElement('meta');
              keywordsTag.name = 'keywords';
              document.head.appendChild(keywordsTag);
            }
            keywordsTag.content = data.keywords.join(', ');
            
            console.log('[SEO] ✅ Klaar! Score:', data.score);
            console.log('[SEO] Title:', data.title);
            console.log('[SEO] Keywords:', data.keywords.join(', '));
            
          } catch (error) {
            console.error('[SEO] ❌ Fout:', error);
          }
        }
        
        // Start wanneer pagina geladen is
        if (document.readyState === 'loading') {
          document.addEventListener('DOMContentLoaded', injectSEO);
        } else {
          injectSEO();
        }
      })();
    </script>
    <!-- === EINDE SEO AUTO-INJECT === -->
    
</head>
<body>
    <!-- Je content -->
</body>
</html>
```

### Stappenplan:

1. Registreer voor een API-key via de Developer API.
2. Bewaar de key als `EUROPEAN_SEARCH_API_KEY` in je serveromgeving.
3. Implementeer `/api/seo/generate` als server-side proxy die de key toevoegt.
4. Laat browsercode uitsluitend jouw eigen same-origin route aanroepen.
5. Commit nooit `.env`-bestanden of sleutels.

## Server-side environment configuration

Configure these values in the relevant server/container environment, never in browser code or public `NEXT_PUBLIC_*` variables:

- `SITEMAP_API_KEY`: same random secret in the Python backend and `frontend-next`; required for the large sitemap-only search request.
- `MEILI_MASTER_KEY`: Meilisearch server key; do not use a public/default key.
- `VALUESERP_API_KEY`: replacement key for the ValueSERP scraper. The old source-embedded token was removed and must be revoked at ValueSERP.
- `DEVELOPER_VERIFICATION_BASE_URL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` (or legacy `SMTP_PASS`), `SMTP_FROM`: required for email verification; see `DEVELOPER_API.md`.
- `ADMIN_API_KEY`: required for crawler stop/reset operations.

Use a unique, high-entropy secret for `SITEMAP_API_KEY` and `ADMIN_API_KEY`, and rotate either if it has ever been committed or shared.

### Website Builder payments

The Builder includes one free generation per verified email address. Additional generations use one credit each; a pack of 10 credits costs €5.00 including VAT (€0.50 per generation). Configure these server-side values before enabling checkout:

- `OPENAI_API_KEY`: valid server-side OpenAI API key.
- `MOLLIE_MODE=test` and `MOLLIE_API_KEY=test_...`: test-mode payment credentials. The key prefix must match the selected mode.
- `BUILDER_PUBLIC_URL`: public Builder URL used for email verification and checkout return links.
- `BUILDER_WEBHOOK_URL`: publicly reachable HTTPS URL ending in `/api/builder/payments/webhook`.

Verify test payments and credit fulfillment before switching to `MOLLIE_MODE=live` with a Mollie live API key. Never expose API keys in frontend or `NEXT_PUBLIC_*` variables. At the current package price, Mollie fees, VAT, OpenAI usage and other operating costs reduce revenue; enforce and monitor model token limits before launch.
### Voorbeeld Content:

```javascript
pages: {
  '/': "Bakkerij Jan - Verse broden, taarten en gebak in Amsterdam sinds 1985. Bezoek onze winkel in de Kalverstraat of bestel online.",
  '/assortiment': "Ons assortiment: vers brood, luxe taarten, gebak, sandwiches en meer. Alle producten dagelijks vers bereid door onze bakkers.",
  '/contact': "Bezoek Bakkerij Jan, Kalverstraat 123 Amsterdam. Tel: 020-1234567. Open: Ma-Za 7:00-18:00, Zo 8:00-17:00."
}
```
