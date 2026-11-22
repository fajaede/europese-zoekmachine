import type { Metadata } from "next";
import BuilderClient from "./BuilderClient";

const title = "Website Builder met AI | fajaede.eu";
const description =
  "Laat in een paar minuten een complete website maken met AI: kies een stijl, beschrijf je bedrijf en ontvang je site. De eerste website is gratis.";

export const metadata: Metadata = {
  title,
  description,
  alternates: { canonical: "/builder" },
  openGraph: { title, description, url: "/builder", siteName: "fajaede.eu", type: "website", locale: "nl_NL",
    images: [{ url: "/images/searchfajaedeseo.jpg", width: 1200, height: 630 }] },
};

export default function Page() {
  return <BuilderClient />;
}
