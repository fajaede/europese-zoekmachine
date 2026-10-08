import Link from "next/link";

export default function PrivacyPolicy() {
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
      <Link href="/" style={{ color: "#0f172a", textDecoration: "none", fontWeight: 500, display: 'inline-block', marginBottom: '24px' }}>
        &larr; Terug naar de zoekmachine
      </Link>
      <h1 style={{ fontSize: 32, margin: "0 0 12px", color: "#1e293b" }}>
        Privacybeleid voor fajaede.eu
      </h1>
      <p style={{ color: "#64748b", marginTop: 0 }}>
        <strong>Laatst bijgewerkt:</strong> 17 juni 2026
      </p>

      <h2 style={{ fontSize: 24, marginTop: 40, color: "#1e293b", borderBottom: '1px solid #e2e8f0', paddingBottom: '8px' }}>1. Wie zijn wij?</h2>
      <p>
        fajaede.eu (&quot;wij&quot;, &quot;ons&quot; of &quot;onze&quot;) is een dienst van fajaedeAI+. Wij
        respecteren uw privacy en zetten ons in om uw persoonlijke gegevens te
        beschermen.
      </p>
      <p style={{ background: '#f8fafc', padding: '16px', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
        <strong>Verwerkingsverantwoordelijke:</strong>
        <br />
        fajaedeAI+
        <br />
        Lodewijk de Koninckstraat 31
        <br />
        2600 Berchem, Antwerpen
        <br />
        België
        <br />
        BE1032407424
      </p>

      <h2 style={{ fontSize: 24, marginTop: 40, color: "#1e293b", borderBottom: '1px solid #e2e8f0', paddingBottom: '8px' }}>
        2. Welke gegevens verzamelen wij?
      </h2>
      <p>
        Wanneer u onze zoekmachine gebruikt, kunnen wij de volgende informatie
        verzamelen:
      </p>
      <ul>
        <li>
          <strong>Zoekopdrachten:</strong> De zoektermen die u invoert. Deze
          worden anoniem verwerkt om zoekresultaten en AI-samenvattingen te
          genereren.
        </li>
        <li>
          <strong>Technische gegevens:</strong> Wij kunnen standaard serverlogs
          bijhouden, die uw IP-adres, browsertype en het tijdstip van uw
          zoekopdracht kunnen bevatten. Deze gegevens worden gebruikt voor
          beveiliging en het opsporen van fouten.
        </li>
      </ul>

      <h2 style={{ fontSize: 24, marginTop: 40, color: "#1e293b", borderBottom: '1px solid #e2e8f0', paddingBottom: '8px' }}>
        3. Waarom verzamelen wij deze gegevens?
      </h2>
      <p>Wij gebruiken uw gegevens voor de volgende doeleinden:</p>
      <ul>
        <li>
          <strong>Het leveren van onze dienst:</strong> Om uw zoekopdrachten te
          verwerken en relevante resultaten en AI-samenvattingen te tonen.
        </li>
        <li>
          <strong>Beveiliging en onderhoud:</strong> Om onze systemen te
          beveiligen en de technische prestaties te garanderen.
        </li>
      </ul>

      <h2 style={{ fontSize: 24, marginTop: 40, color: "#1e293b", borderBottom: '1px solid #e2e8f0', paddingBottom: '8px' }}>
        4. Delen van gegevens met derden
      </h2>
      <p>
        Wanneer u de AI-samenvattingsfunctie gebruikt, verwerken wij uw vraag,
        relevante zoekresultaten uit onze zoekindex en, indien aanwezig, uw
        conversatiegeschiedenis om een antwoord te genereren. Deze gegevens
        worden eerst naar de OpenAI API verzonden. Als de OpenAI-aanroep mislukt,
        kan onze backend dezelfde prompt en conversatiegeschiedenis via een
        Ollama-model op onze infrastructuur verwerken. Voor de AI-ondersteunde
        SEO-functie kunnen maximaal 1.000 tekens van de door u aangeleverde
        content naar OpenAI worden verzonden om SEO-metadata te genereren.
        <br /><br />
        Uw invoer kan persoonsgegevens bevatten. Deel geen gevoelige
        persoonsgegevens in uw vraag of aangeleverde content, tenzij dit
        noodzakelijk is. Wij passen in deze functies momenteel geen automatische
        anonimisering toe voordat gegevens naar de AI-dienst worden verzonden.
        <br /><br />
        OpenAI vermeldt dat API-inputs en -outputs standaard niet worden gebruikt
        om modellen te trainen, tenzij de klant daar expliciet voor kiest.
        OpenAI kan API-inputs en -outputs voor bepaalde endpoints en functies
        standaard tot 30 dagen bewaren voor misbruikmonitoring, behoudens
        toepasselijke uitzonderingen. De instellingen en voorwaarden van ons
        OpenAI-account bepalen welke bewaarbeperkingen gelden.
        <br /><br />
        Als de OpenAI API-aanroep mislukt, kan onze backend de vraag,
        relevante zoekcontext en eventuele conversatiegeschiedenis als fallback
        verwerken met Ollama. Ollama draait lokaal op onze eigen server via
        localhost:11434 en gebruikt het lokaal geïnstalleerde model phi3:mini.
        Deze fallback-aanvraag wordt niet naar Ollama Cloud gestuurd.
        <br /><br />
        Wij gebruiken deze aanbieders om de door u gevraagde AI-functies te
        leveren en niet voor advertentiedoeleinden. Informatie over de
        verwerkingsdoeleinden, rechtsgrond, ontvangers, bewaartermijnen en
        eventuele internationale doorgiften moet overeenkomen met onze
        daadwerkelijke instellingen en afspraken met deze dienstverleners.
      </p>

      <h2 style={{ fontSize: 24, marginTop: 40, color: "#1e293b", borderBottom: '1px solid #e2e8f0', paddingBottom: '8px' }}>5. Uw rechten</h2>
      <p>
        Onder de AVG heeft u recht op inzage, rectificatie, verwijdering en
        beperking van de verwerking van uw persoonsgegevens. Omdat wij uw
        zoekopdrachten anoniem verwerken, is het uitoefenen van deze rechten op
        specifieke zoekdata niet direct mogelijk. Voor vragen over onze
        serverlogs kunt u contact met ons opnemen.
      </p>

      <h2 style={{ fontSize: 24, marginTop: 40, color: "#1e293b", borderBottom: '1px solid #e2e8f0', paddingBottom: '8px' }}>6. Contact</h2>
      <p>
        Als u vragen heeft over dit privacybeleid, kunt u contact met ons
        opnemen via de contactgegevens vermeld onder punt 1.
      </p>
    </main>
  );
}