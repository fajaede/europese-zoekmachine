import type { Metadata } from 'next';
import Link from 'next/link';
import type { ReactNode } from 'react';
import { COMPANY } from '../components/LegalFooter';

export const metadata: Metadata = {
  title: 'Algemene voorwaarden | fajaede.eu',
  description: 'Algemene voorwaarden en herroepingsbeleid voor fajaede.eu en fajaede Builder.',
  alternates: { canonical: '/algemene-voorwaarden' },
};

const h2Style = {
  fontSize: 22,
  marginTop: 36,
  color: '#1e293b',
  borderBottom: '1px solid #e2e8f0',
  paddingBottom: 8,
} as const;

function Section({ id, title, children }: { id?: string; title: string; children: ReactNode }) {
  return (
    <section id={id}>
      <h2 style={h2Style}>{title}</h2>
      {children}
    </section>
  );
}

export default function AlgemeneVoorwaarden() {
  return (
    <main
      style={{
        maxWidth: 960,
        margin: '0 auto',
        padding: '48px 20px',
        fontFamily: 'system-ui, sans-serif',
        lineHeight: 1.7,
        color: '#334155',
      }}
    >
      <Link
        href="/"
        style={{ color: '#0f172a', textDecoration: 'none', fontWeight: 500, display: 'inline-block', marginBottom: 24 }}
      >
        &larr; Terug naar de zoekmachine
      </Link>
      <h1 style={{ fontSize: 32, margin: '0 0 12px', color: '#1e293b' }}>Algemene voorwaarden</h1>
      <p style={{ color: '#64748b', marginTop: 0 }}>
        <strong>Versie:</strong> 12 november 2026 · Van toepassing op fajaede.eu, fajaede Builder en de Developer API
      </p>

      <Section title="1. Wie zijn wij?">
        <p style={{ background: '#f8fafc', padding: 16, borderRadius: 8, border: '1px solid #e2e8f0' }}>
          {COMPANY.name}
          <br />
          {COMPANY.street}
          <br />
          {COMPANY.city}
          <br />
          {COMPANY.country}
          <br />
          Ondernemings- en btw-nummer: {COMPANY.vat}
          <br />
          E-mail: <a href={`mailto:${COMPANY.email}`}>{COMPANY.email}</a>
        </p>
      </Section>

      <Section title="2. Toepassing">
        <p>
          Deze voorwaarden gelden voor elk gebruik van fajaede.eu, voor elke aankoop van credits voor fajaede
          Builder en voor het gebruik van de Developer API, inclusief Pro- en Premium-passen. Door een account te
          bevestigen of een aankoop te doen, aanvaardt u deze voorwaarden. Dwingende
          rechten die u als consument heeft, blijven altijd van toepassing.
        </p>
      </Section>

      <Section title="3. Onze diensten">
        <ul>
          <li>
            <strong>Zoekmachine:</strong> het zoeken op fajaede.eu is gratis.
          </li>
          <li>
            <strong>fajaede Builder:</strong> een online dienst die op basis van uw omschrijving met behulp van
            kunstmatige intelligentie (AI) een HTML-website genereert, die u kunt bekijken en kopiëren.
          </li>
          <li>
            <strong>Developer API:</strong> een programmeerinterface (api.fajaede.eu) die met AI SEO-metadata
            genereert. Toegang loopt via een persoonlijke API-key die u na e-mailbevestiging ontvangt.
          </li>
        </ul>
      </Section>

      <Section title="4. Account">
        <p>
          Voor de Builder bevestigt u uw e-mailadres via een verificatielink. U bent verantwoordelijk voor het
          gebruik van uw account. Per persoon is één gratis aanvraag bedoeld; het aanmaken van meerdere accounts om
          die beperking te omzeilen is niet toegestaan.
        </p>
      </Section>

      <Section title="5. Prijzen en betaling">
        <ul>
          <li>Na uw gratis aanvraag kost elke generatie 1 credit.</li>
          <li>
            Een creditpakket bevat 10 credits en kost <strong>€5,00 inclusief btw</strong> (€0,50 per generatie). Dit is
            een eenmalige betaling, geen abonnement.
          </li>
          <li>Betalingen verlopen via onze betaaldienstverlener Mollie. Wij ontvangen geen kaart- of bankgegevens.</li>
          <li>
            Mislukt een generatie door een technische fout aan onze kant, dan wordt er geen credit afgeschreven of wordt
            de credit automatisch teruggezet.
          </li>
          <li>Credits zijn persoonlijk, niet overdraagbaar en niet inwisselbaar voor geld.</li>
        </ul>
        <p>
          <strong>Developer API:</strong>
        </p>
        <ul>
          <li>Free: 100 requests per dag, gratis.</li>
          <li>
            Pro: 2.000 requests per dag, <strong>€9,00</strong> per 30 dagen of <strong>€24,00</strong> per 90 dagen.
          </li>
          <li>
            Premium: 10.000 requests per dag, <strong>€29,00</strong> per 30 dagen of <strong>€79,00</strong> per 90
            dagen.
          </li>
          <li>
            Alle prijzen zijn inclusief btw. Een pas is een eenmalige, vooraf betaalde periode en wordt nooit
            automatisch verlengd. Een nieuwe pas van hetzelfde pakket wordt opgeteld bij de resterende dagen. Bij een
            upgrade van Pro naar Premium worden de resterende Pro-dagen naar verhouding van de prijs omgezet in
            Premium-dagen.
          </li>
          <li>
            De daglimiet wordt elke dag opnieuw ingesteld; ongebruikte requests worden niet meegenomen. Na afloop
            valt uw API-key terug op Free.
          </li>
        </ul>
      </Section>

      <Section title="6. Levering">
        <p>
          Credits zijn digitale inhoud die niet op een materiële drager wordt geleverd. Ze worden direct na
          bevestiging van uw betaling door Mollie aan uw account toegevoegd. U ontvangt een bevestiging van uw
          aankoop per e-mail.
        </p>
      </Section>

      <Section id="herroeping" title="7. Herroepingsrecht">
        <p>
          Als consument heeft u in principe het recht om een overeenkomst op afstand binnen 14 dagen zonder opgave
          van redenen te herroepen.
        </p>
        <p>
          Voor digitale inhoud die niet op een materiële drager wordt geleverd, vervalt dit recht zodra de levering
          is begonnen, als u daar vooraf uitdrukkelijk mee heeft ingestemd en heeft erkend dat u daardoor uw
          herroepingsrecht verliest (artikel VI.53, 13° van het Wetboek van economisch recht). Bij het kopen van
          credits vragen wij u deze instemming en erkenning uitdrukkelijk te geven. Omdat de credits direct na
          betaling worden geleverd, kunt u een aankoop van credits daarna niet meer herroepen.
        </p>
        <p>
          Een Pro- of Premium-pas voor de Developer API is een dienst. Op uw uitdrukkelijk verzoek start de pas direct
          na betaling. Als consument kunt u de aankoop toch binnen 14 dagen herroepen door een e-mail te sturen naar{' '}
          <a href={`mailto:${COMPANY.email}`}>{COMPANY.email}</a>. U betaalt dan alleen een bedrag in verhouding tot
          de dagen die al verstreken zijn; de rest betalen wij binnen 14 dagen terug (artikel VI.51 van het Wetboek
          van economisch recht). Koopt u de pas als onderneming, dan geldt geen herroepingsrecht.
        </p>
        <p>
          Uw wettelijke rechten bij een gebrekkige dienst (zie artikel 9) blijven onverminderd van toepassing.
        </p>
      </Section>

      <Section title="8. Gegenereerde websites en gebruik">
        <ul>
          <li>
            Websites worden automatisch door AI gegenereerd. Controleer teksten, links, contactgegevens en wettelijke
            informatie altijd voordat u een website publiceert. U bent zelf verantwoordelijk voor wat u publiceert.
          </li>
          <li>
            U mag de gegenereerde HTML vrij gebruiken, aanpassen en publiceren. Wij garanderen niet dat de uitvoer
            uniek is of geen rechten van derden schendt.
          </li>
          <li>
            Het is niet toegestaan de Builder te gebruiken voor onwettige, misleidende of schadelijke inhoud, zoals
            phishing, oplichting of inbreuk op rechten van anderen, of de dienst geautomatiseerd of overmatig te
            belasten. Bij misbruik mogen wij een account blokkeren.
          </li>
          <li>Om misbruik te voorkomen gelden er limieten op het aantal aanvragen per periode.</li>
        </ul>
      </Section>

      <Section title="9. Klachten en conformiteit">
        <p>
          Werkt de dienst niet zoals beloofd of heeft u een klacht? Mail ons via{' '}
          <a href={`mailto:${COMPANY.email}`}>{COMPANY.email}</a>. Wij reageren binnen 14 dagen. Bij een gebrek dat
          aan ons te wijten is, zorgen wij voor herstel, het terugzetten van credits of een (gedeeltelijke)
          terugbetaling, overeenkomstig uw wettelijke rechten.
        </p>
        <p>
          Komen wij er samen niet uit, dan kunt u als consument terecht bij de Consumentenombudsdienst (
          <a href="https://consumentenombudsdienst.be" rel="noopener noreferrer" target="_blank">
            consumentenombudsdienst.be
          </a>
          ).
        </p>
      </Section>

      <Section title="10. Aansprakelijkheid">
        <p>
          Wij leveren de dienst met zorg, maar kunnen niet garanderen dat deze altijd foutloos of ononderbroken
          beschikbaar is. Behalve bij opzet of zware fout is onze aansprakelijkheid beperkt tot het bedrag dat u in
          de 12 maanden voorafgaand aan de schade aan ons heeft betaald. Deze beperking geldt niet voor zover de wet
          dat verbiedt.
        </p>
      </Section>

      <Section title="11. Privacy">
        <p>
          Hoe wij persoonsgegevens verwerken, leest u in ons <Link href="/privacy-policy">privacybeleid</Link>.
        </p>
      </Section>

      <Section title="12. Wijzigingen">
        <p>
          Wij kunnen deze voorwaarden wijzigen. Voor een aankoop gelden de voorwaarden die van kracht waren op het
          moment van die aankoop.
        </p>
      </Section>

      <Section title="13. Toepasselijk recht">
        <p>
          Op deze voorwaarden is Belgisch recht van toepassing. Geschillen worden voorgelegd aan de bevoegde
          rechtbanken van Antwerpen, tenzij de wet u als consument een andere rechter of de dwingende bescherming
          van het recht van uw woonland toekent.
        </p>
      </Section>
    </main>
  );
}
