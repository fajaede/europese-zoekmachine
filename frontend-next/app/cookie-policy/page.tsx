import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Cookiebeleid | fajaede.eu",
  description: "Welke cookies en opslag fajaede.eu gebruikt: alleen strikt noodzakelijke, geen tracking.",
  alternates: { canonical: "/cookie-policy" },
};

const h2Style = {
  fontSize: 24,
  marginTop: 40,
  color: "#1e293b",
  borderBottom: "1px solid #e2e8f0",
  paddingBottom: 8,
} as const;

const cellStyle = {
  border: "1px solid #e2e8f0",
  padding: "10px 12px",
  verticalAlign: "top",
  textAlign: "left",
} as const;

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h2 style={h2Style}>{title}</h2>
      {children}
    </section>
  );
}

export default function CookiePolicy() {
  return (
    <main
      style={{
        maxWidth: 960,
        margin: "0 auto",
        padding: "48px 20px",
        fontFamily: "system-ui, sans-serif",
        lineHeight: 1.7,
        color: "#334155",
      }}
    >
      <Link
        href="/"
        style={{ color: "#0f172a", textDecoration: "none", fontWeight: 500, display: "inline-block", marginBottom: 24 }}
      >
        &larr; Terug naar de zoekmachine
      </Link>
      <h1 style={{ fontSize: 32, margin: "0 0 12px", color: "#1e293b" }}>Cookiebeleid voor fajaede.eu</h1>
      <p style={{ color: "#64748b", marginTop: 0 }}>
        <strong>Laatst bijgewerkt:</strong> 12 november 2026
      </p>

      <Section title="1. Kort samengevat">
        <p>
          fajaede.eu gebruikt <strong>geen tracking-, analytische of advertentiecookies</strong> en bouwt geen profielen
          van bezoekers op. Gewoon zoeken plaatst niets op uw apparaat. Alleen als u de Website Builder gebruikt,
          plaatsen wij één cookie die strikt noodzakelijk is om u ingelogd te houden.
        </p>
        <p>
          Strikt noodzakelijke cookies mogen volgens de Europese en Belgische regels zonder toestemming worden geplaatst.
          Daarom tonen wij geen cookiebanner: er valt niets te weigeren dat niet nodig is voor de dienst die u zelf
          gebruikt.
        </p>
      </Section>

      <Section title="2. Welke cookies en opslag gebruiken wij?">
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
            <thead style={{ background: "#f8fafc" }}>
              <tr>
                <th style={cellStyle}>Naam</th>
                <th style={cellStyle}>Wanneer</th>
                <th style={cellStyle}>Doel</th>
                <th style={cellStyle}>Bewaartermijn</th>
                <th style={cellStyle}>Soort</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td style={cellStyle}>
                  <code>builder_session</code>
                </td>
                <td style={cellStyle}>Alleen nadat u uw e-mailadres voor de Website Builder bevestigt</td>
                <td style={cellStyle}>
                  U ingelogd houden zodat u uw gratis aanvraag en gekochte credits kunt gebruiken. Bevat alleen een
                  willekeurige code, geen persoonsgegevens. Niet leesbaar voor scripts (HttpOnly) en alleen verstuurd
                  naar de Builder.
                </td>
                <td style={cellStyle}>30 dagen, of tot u zich afmeldt</td>
                <td style={cellStyle}>Strikt noodzakelijk, eigen cookie</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p>
          Wij gebruiken geen <code>localStorage</code>, <code>sessionStorage</code> of vergelijkbare opslag. Uw zoekopdracht,
          gekozen tabblad en het gesprek met de AI staan alleen in het werkgeheugen van de pagina en verdwijnen zodra u
          het tabblad sluit of vernieuwt.
        </p>
      </Section>

      <Section title="3. Diensten van derden">
        <ul>
          <li>
            <strong>Cloudflare Turnstile (beveiliging):</strong> alleen op het e-mailformulier van de Website Builder
            controleren wij met Cloudflare Turnstile of u geen bot is. Cloudflare verwerkt daarvoor technische
            browsergegevens en uw IP-adres, uitsluitend om misbruik te voorkomen. Turnstile wordt niet gebruikt voor
            advertenties of tracking. Zie het{" "}
            <a href="https://www.cloudflare.com/privacypolicy/" target="_blank" rel="noopener noreferrer">
              privacybeleid van Cloudflare
            </a>
            .
          </li>
          <li>
            <strong>Voorbeeldfoto&apos;s (Unsplash):</strong> websites die u met de Builder genereert, tonen foto&apos;s die
            rechtstreeks van images.unsplash.com worden geladen. Daarbij ziet Unsplash uw IP-adres, zoals bij elke
            afbeelding op internet. Er worden geen cookies geplaatst.
          </li>
          <li>
            <strong>Delen op sociale media:</strong> de deelknoppen zijn gewone links. Er wordt pas iets naar het sociale
            netwerk gestuurd als u er zelf op klikt.
          </li>
        </ul>
      </Section>

      <Section title="4. Cookies beheren">
        <p>
          U kunt cookies altijd via de instellingen van uw browser bekijken en verwijderen. Verwijdert of blokkeert u de
          cookie <code>builder_session</code>, dan werkt het zoeken gewoon, maar moet u voor de Website Builder opnieuw uw
          e-mailadres bevestigen. Uw tegoed blijft bewaard. Met &quot;Overal afmelden&quot; in de Builder beëindigt u
          bovendien alle sessies op al uw apparaten.
        </p>
      </Section>

      <Section title="5. Vragen?">
        <p>
          Lees ook ons{" "}
          <Link href="/privacy-policy" style={{ color: "#c2410c", textDecoration: "underline" }}>
            privacybeleid
          </Link>{" "}
          of neem contact met ons op via de gegevens die daarin staan.
        </p>
      </Section>
    </main>
  );
}
