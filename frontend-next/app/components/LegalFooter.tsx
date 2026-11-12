import Link from 'next/link';

export const COMPANY = {
  name: 'fajaedeAI+',
  street: 'Lodewijk de Koninckstraat 31',
  city: '2600 Berchem, Antwerpen',
  country: 'België',
  vat: 'BE1032407424',
  email: 'info@fajaede.nl',
};

export default function LegalFooter() {
  return (
    <footer className="border-t border-slate-200 bg-white/80 px-4 py-6 text-xs text-slate-500">
      <div className="mx-auto flex max-w-7xl flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="leading-relaxed">
          <span className="font-semibold text-slate-700">{COMPANY.name}</span> · {COMPANY.street},{' '}
          {COMPANY.city}, {COMPANY.country} · Ondernemings-/btw-nummer {COMPANY.vat} ·{' '}
          <a href={`mailto:${COMPANY.email}`} className="hover:text-orange-600">
            {COMPANY.email}
          </a>
        </p>
        <nav aria-label="Juridische informatie" className="flex flex-wrap gap-x-5 gap-y-2 font-semibold">
          <Link href="/builder" className="hover:text-orange-600">
            Website Builder
          </Link>
          <Link href="/algemene-voorwaarden" className="hover:text-orange-600">
            Algemene voorwaarden
          </Link>
          <Link href="/privacy-policy" className="hover:text-orange-600">
            Privacybeleid
          </Link>
          <Link href="/cookie-policy" className="hover:text-orange-600">
            Cookiebeleid
          </Link>
        </nav>
      </div>
    </footer>
  );
}
