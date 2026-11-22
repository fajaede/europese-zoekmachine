import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import LegalFooter from "./components/LegalFooter";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || "https://www.fajaede.eu";

const description = "Europese zoekmachine zonder tracking of profilering, plus een websitebuilder met AI. Zoek veilig en neutraal in een eigen Europese index.";

export const metadata: Metadata = {
  title: "fajaede.eu | Europese zoekmachine zonder tracking",
  description,
  metadataBase: new URL(siteUrl),
  icons: {
    icon: "/icon-48x48.png",
  },
  openGraph: {
    siteName: "fajaede.eu",
    type: "website",
    locale: "nl_NL",
    images: [
      {
        url: "/images/searchfajaedeseo.jpg",
        secureUrl: "/images/searchfajaedeseo.jpg",
        width: 1200,
        height: 630,
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "fajaede.eu | Europese zoekmachine zonder tracking",
    description,
    site: "@fajaedesr",
    creator: "@fajaedesr",
  },
  other: {
    "fb:app_id": "567594986640691",
    "geo.placename": "Berchem, Antwerpen",
    "geo.region": "BE-VAN",
    "geo.position": "51.1906;4.4304",
    "ICBM": "51.1906, 4.4304",
    "Fajaede": "Martinn Margaritha",
    "twitter:label1": "Written by",
    "twitter:data1": "Martinn Margaritha",
  },
  robots: {
    index: true,
    follow: true,
    "max-image-preview": "large",
    "max-snippet": -1,
    "max-video-preview": -1,
    googleBot: {
      index: true,
      follow: true,
      "max-video-preview": -1,
      "max-image-preview": "large",
      "max-snippet": -1,
    },
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="nl"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <head>
        {/* Start FajaedeSEO AI+ */}
        <meta itemProp="description" content={description} />
        <meta itemProp="image" content="/images/eu.jpg" />
        <meta property="fb:pages" content="fajaede1" />
        <meta property="fb:admins" content="fajaede1" />
        <meta property="article:publisher" content="fajaede1" />
        <script
          type="application/ld+json"
          className="fajaede-schema-graph fajaede-schema-graph--main"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              "@context": "https://schema.org",
              "@graph": [
                {
                  "@type": "Organization",
                  "@id": "https://www.fajaede.nl/#organization",
                  "name": "fajaede",
                  "url": "https://www.fajaede.nl/",
                  "email": "info@fajaede.nl",
                  "address": { "@type": "PostalAddress", "addressLocality": "Berchem", "addressRegion": "Antwerpen", "postalCode": "2600", "addressCountry": "BE" },
                  "sameAs": ["https://www.fajaede.eu/", "https://psa.fajaede.nl/", "https://github.com/fajaede"]
                },
                {
                  "@type": "WebSite",
                  "@id": `${siteUrl}/#website`,
                  "name": "fajaede.eu",
                  "url": siteUrl,
                  "inLanguage": "nl",
                  "description": description,
                  "publisher": { "@id": "https://www.fajaede.nl/#organization" }
                },
                {
                  "@type": "FAQPage",
                  "mainEntity": [
                    {
                      "@type": "Question",
                      "name": "Wat is fajaedeAi?",
                      "acceptedAnswer": {
                        "@type": "Answer",
                        "text": "FajaedeAI is een onafhankelijke Europese zoekmachine. Volledig privacy-gericht en speciaal ontwikkeld voor EU-burgers om veilig, neutraal en zonder profilering of tracking het web te doorzoeken."
                      }
                    },
                    {
                      "@type": "Question",
                      "name": "Wat is fajaedeseo?",
                      "acceptedAnswer": {
                        "@type": "Answer",
                        "text": "FajaedeSEO is een slimme WordPress (WP) plugin voor geavanceerde SEO automation. Het helpt website-eigenaren hun processen te automatiseren en hun organische vindbaarheid efficiënt te optimaliseren."
                      }
                    }
                  ]
                }
              ]
            }).replace(/</g, "\\u003c")
          }}
        />
      </head>
      <body className="min-h-full flex flex-col">
        <div className="flex-1">{children}</div>
        <LegalFooter />
      </body>
    </html>
  );
}
