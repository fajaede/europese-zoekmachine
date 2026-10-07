
---

## 🔧 Quick Start - Copy-Paste Script

### Voorbeeld: Integratie in Je Website

Kopieer dit script en plak het in de `<head>` van je HTML:

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
          API_KEY: 'PLAK_HIER_JOUW_API_KEY', // Vervang met jouw API key van [https://api.fajaede.eu/developers](https://api.fajaede.eu/developers)
          API_URL: '[https://api.fajaede.eu/api/developers/seo/generate](https://api.fajaede.eu/api/developers/seo/generate)',
          
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
                'X-API-Key': SEO_CONFIG.API_KEY,
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

1. **Ga naar** https://api.fajaede.eu/developers
2. **Registreer** met je email
3. **Kopieer** je API key
4. **Vervang** `PLAK_HIER_JOUW_API_KEY` in het script
5. **Vul** je content in bij `pages:`
6. **Plak** het script in je HTML `<head>`
7. **Upload** naar je server
8. **Klaar!** 🎉

### Voorbeeld Content:

```javascript
pages: {
  '/': "Bakkerij Jan - Verse broden, taarten en gebak in Amsterdam sinds 1985. Bezoek onze winkel in de Kalverstraat of bestel online.",
  '/assortiment': "Ons assortiment: vers brood, luxe taarten, gebak, sandwiches en meer. Alle producten dagelijks vers bereid door onze bakkers.",
  '/contact': "Bezoek Bakkerij Jan, Kalverstraat 123 Amsterdam. Tel: 020-1234567. Open: Ma-Za 7:00-18:00, Zo 8:00-17:00."
}
```


---

## 🔒 Beheer-endpoints

`POST /api/crawl/stop` en `POST /api/crawl/reset` vereisen een admin key. Zet in `.env`:

```
ADMIN_API_KEY=<lange willekeurige string>
```

en stuur die mee als header `X-Admin-Key`. Zonder `ADMIN_API_KEY` zijn deze endpoints uitgeschakeld.
