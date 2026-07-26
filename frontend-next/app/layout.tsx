import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || "https://fajaede-search-frontend.vercel.app";

export const metadata: Metadata = {
  title: "fajaedeseo AI",
  description: "fajaedeAI is een Europese zoekmachine die uw privacy respecteert en relevante resultaten levert.",
  metadataBase: new URL(siteUrl),
  alternates: {
    canonical: siteUrl,
  },
  icons: {
    icon: "/icon-48x48.png",
  },
  openGraph: {
    title: "fajaedeseo AI",
    description: "fajaedeAI is een Europese zoekmachine die uw privacy respecteert en relevante resultaten levert.",
    siteName: "fajaede1",
    type: "website",
    url: siteUrl,
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
    title: "fajaedeseo AI-services voor SEO ",
    description: "fajaedeAI is een Europese zoekmachine die uw privacy respecteert en relevante resultaten levert.",
    site: "@fajaedesr",
    creator: "@fajaedesr",
  },
  other: {
    "fb:app_id": "567594986640691",
    "geo.placename": "Belgie",
    "geo.region": "Brussel",
    "geo.position": "50.8386;4.3761",
    "ICBM": "50.8386, 4.3761",
    "Fajaede": "Martinn Margaritha",
    "twitter:label1": "Written by",
    "twitter:data1": "Martinn Margaritha",
    "twitter:label2": "Est. reading time",
    "twitter:data2": "",
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
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <head>
        {/* Start FajaedeSEO AI+ */}
        <meta itemProp="description" content="fajaedeAI is een Europese zoekmachine die uw privacy respecteert en relevante resultaten levert." />
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
                  "@type": "WebSite",
                  "name": "fajaedeseo AI",
                  "url": siteUrl
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
            })
          }}
        />
      </head>
      <body className="min-h-full flex flex-col">
        {children}
      </body>
    </html>
  );
}
