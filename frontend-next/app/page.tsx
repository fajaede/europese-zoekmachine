import type { Metadata } from "next";
import HomeClient from "./HomeClient";

const title = "fajaede.eu | Europese zoekmachine zonder tracking";
const description =
  "Europese zoekmachine zonder tracking of profilering, plus een websitebuilder met AI. Zoek veilig en neutraal in een eigen Europese index.";

export const metadata: Metadata = {
  alternates: { canonical: "/" },
  openGraph: { title, description, url: "/", siteName: "fajaede.eu", type: "website", locale: "nl_NL",
    images: [{ url: "/images/searchfajaedeseo.jpg", width: 1200, height: 630 }] },
};

export default function Page() {
  return <HomeClient />;
}
