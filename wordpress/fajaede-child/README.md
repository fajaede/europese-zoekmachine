# Fajaede – child theme voor fajaede.nl

Zakelijk child-thema op basis van **Twenty Twenty-Four** (block theme, Full Site Editing).

## Plan

### 1. Doel
Een professionele bedrijfssite voor fajaede.nl met:
- duidelijke homepage (hero → diensten → CTA),
- contactformulier beveiligd met **reCAPTCHA v3** (onzichtbaar, geen puzzels),
- een **downloadpagina die met één shortcode** per pagina te personaliseren is,
- updates van TT4 blijven werken: we overschrijven alleen wat nodig is.

### 2. Sitestructuur (voorstel)
| Pagina | Template | Inhoud |
|---|---|---|
| Home | `front-page` (TT4) | patterns `fajaede/hero`, `fajaede/services`, `fajaede/cta` |
| Diensten | standaard `page` | per dienst een sectie |
| Over ons | standaard `page` | team, missie, Europese hosting |
| Download | **Downloadpagina** | `[fajaede_download]` / `[fajaede_downloads]` |
| Contact | **Contactpagina** | tekst links, `[fajaede_contact]` rechts |
| Privacy / Cookies / Voorwaarden | standaard `page` | juridisch, gelinkt in footer |

### 3. Bestanden
```
fajaede-child/
├── style.css                 theme-header (Template: twentytwentyfour)
├── theme.json                kleuren, knoppen, links, custom templates
├── functions.php             laadt inc/* en CSS
├── inc/
│   ├── settings.php          Instellingen > Fajaede (sleutels, drempel, ontvanger)
│   ├── recaptcha.php         script laden + server-side verificatie
│   ├── contact-form.php      [fajaede_contact] + verwerking via admin-post.php
│   └── download-shortcode.php [fajaede_download], [fajaede_downloads], teller
├── assets/css/components.css formulier- en downloadstijlen
├── assets/js/forms.js        haalt reCAPTCHA-token op bij verzenden
├── parts/header.html, footer.html
├── templates/page-download.html, page-contact.html
└── patterns/hero, services, cta, download-page
```

### 4. Huisstijl
`theme.json` overschrijft het TT4-palet **met dezelfde slugs** (`base`, `contrast`, `accent`…),
zodat alle TT4-patterns automatisch de nieuwe kleuren krijgen. Huidige waarden zijn een
zakelijk blauw voorstel — **vervang ze door de exacte merkkleuren van fajaede.nl** (logo/huidige site).
Lettertype: Inter (zit al in TT4), ook voor koppen voor een strakkere bedrijfslook.

### 5. reCAPTCHA v3
- Sleutels aanmaken op https://www.google.com/recaptcha/admin (type **v3**, domeinen `fajaede.nl`, `www.fajaede.nl`).
- Bij voorkeur in `wp-config.php`:
  ```php
  define( 'FAJAEDE_RECAPTCHA_SITE_KEY', '...' );
  define( 'FAJAEDE_RECAPTCHA_SECRET_KEY', '...' );
  ```
  of via **Instellingen → Fajaede**.
- Script wordt alleen geladen op pagina's met een formulier.
- Server controleert: `success`, `action === "contact"`, `hostname`, en `score ≥ drempel` (standaard 0.5,
  aan te passen in instellingen of via filter `fajaede_recaptcha_threshold`).
- Extra lagen: WP-nonce, honeypot-veld, verplichte privacy-checkbox.
- Badge is verborgen; de verplichte Google-tekst staat onder het formulier.
- Zonder sleutels: formulier werkt in dev/staging, wordt geweigerd in `production`.
- **AVG**: reCAPTCHA vermelden in de privacyverklaring/cookiebeleid.

### 6. Downloadpagina (shortcode)
Maak een pagina, kies template **Downloadpagina**, voeg pattern *Downloadpagina (startpunt)* in
en vervang de `file`-ID's (zichtbaar in de URL van het bestand in de mediabibliotheek).

```text
[fajaede_download file="123" layout="hero" title="Fajaede SEO-rapport"
    subtitle="Alles over je zichtbaarheid" version="2.1" image="456"
    button="Download PDF" note="Gratis, geen account nodig" count="yes"]
Korte beschrijving, mag **HTML** bevatten.
[/fajaede_download]

[fajaede_downloads columns="3"]
  [fajaede_download file="124" title="Handleiding"]…[/fajaede_download]
  [fajaede_download file="125" title="Brochure"]…[/fajaede_download]
[/fajaede_downloads]
```

| Attribuut | Betekenis |
|---|---|
| `file` | Media-ID (aanbevolen) of URL |
| `title`, `subtitle` | Teksten; titel valt terug op bestandsnaam |
| `version`, `date` | Getoond in metaregel; datum valt terug op uploaddatum |
| `button`, `note` | Knoptekst en kleine tekst eronder |
| `image` | Media-ID of URL voor cover |
| `layout` | `card` (standaard) of `hero` |
| `meta` | `no` verbergt type/grootte/versie/datum |
| `count` | `yes` toont aantal downloads |

Bij een media-ID loopt de link via `/?fajaede_download=ID`: teller +1 (bots uitgesloten),
daarna redirect naar het bestand. Wordt het bestand vervangen, dan blijft de link gelijk.
Aantallen staan als kolom **Downloads** in de mediabibliotheek. Hook `fajaede_download` voor analytics.

### 7. Installatie
1. Zorg dat **Twenty Twenty-Four** geïnstalleerd is.
2. Zip de map `fajaede-child` → Weergave → Thema's → Uploaden → Activeren.
3. Instellingen → Fajaede: reCAPTCHA-sleutels + ontvanger.
4. Weergave → Editor: logo, menu en footer (adres/KvK/BTW-placeholders) invullen.
5. Pagina's Contact en Download aanmaken met de bijbehorende templates.
6. SMTP-plugin (bv. WP Mail SMTP) zodat `wp_mail` betrouwbaar aankomt.

### 8. Volgende fasen (optioneel)
- Downloads achter het formulier (“gated”): eerst e-mail, dan link.
- Hook `fajaede_contact_submitted` koppelen aan CRM/nieuwsbrief.
- Rate limiting per IP op het formulier.
- Structured data (`Organization`, `LocalBusiness`) in `wp_head`.
- Vertalingen (`languages/`) als de site ook Engelstalig wordt.

## Open punten
- De live site (fajaede.nl) was vanuit de bouwomgeving niet bereikbaar; kleuren, logo, menu-items
  en teksten in de patterns zijn daarom **placeholders** en moeten aan de huidige site worden aangepast.
