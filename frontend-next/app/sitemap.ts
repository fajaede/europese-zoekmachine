import type { MetadataRoute } from "next";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  // Gebruik de officiële domeinnaam uit de environment variables, met een fallback.
  const baseUrl = process.env.NEXT_PUBLIC_BASE_URL || "https://fajaede.eu";
  // Static routes always included
  const staticRoutes: MetadataRoute.Sitemap = [
    {
      url: baseUrl,
      lastModified: "2024-05-20", // Gebruik een statische datum voor de laatste grote wijziging
      changeFrequency: "daily",
      priority: 1.0,
    },
    {
      url: `${baseUrl}/privacy-policy`,
      lastModified: "2024-05-20",
      changeFrequency: "monthly",
      priority: 0.8,
    },
    {
      url: `${baseUrl}/cookie-policy`,
      lastModified: "2024-05-20",
      changeFrequency: "monthly",
      priority: 0.8,
    },
  ];

  // Als de IS_DOCKER_BUILD vlag is ingesteld tijdens de build, slaan we de dynamische API-request over.
  if (process.env.IS_DOCKER_BUILD === "true") {
    console.log("IS_DOCKER_BUILD is true – skipping dynamic sitemap generation during build.");
    return staticRoutes;
  }

  // Gebruik de API URL voor sitemap generatie, met fallback voor lokaal.
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:18000";

  let dynamicUrls: MetadataRoute.Sitemap = [];
  try {
    // Roep de API aan om de sitemap data te krijgen. Dit is veiliger en
    // consistenter dan direct met Meilisearch te praten vanuit de frontend build.
    // We gebruiken de /api/search endpoint als een proxy hiervoor met een lege query.
    const searchParams = new URLSearchParams({
      q: "",
      limit: "1000",
    });
    const res = await fetch(`${apiUrl}/api/search?${searchParams.toString()}`, {
      method: "GET", // Search endpoint is GET
      headers: {
        "X-Sitemap-Request": "true",
      },
      next: { revalidate: 3600 }, // Cache dit resultaat voor 1 uur (Vercel)
    });

    if (res.ok) {
      const data = await res.json();
      // De API retourneert 'results', niet 'hits'
      dynamicUrls = data.results.map((hit: { url: string; published_at?: string }) => {
        let lastMod = new Date();
        if (hit.published_at) {
          const parsed = new Date(hit.published_at);
          if (!isNaN(parsed.getTime())) {
            lastMod = parsed;
          }
        }
        return {
          url: hit.url,
          lastModified: lastMod,
          changeFrequency: "weekly",
          priority: 0.6,
        };
      });
    } else {
      console.error("Kon dynamische sitemap niet laden via API:", await res.text());
    }
  } catch (error) {
    console.error("Fout bij het aanroepen van de API in sitemap.ts:", error);
  }

  return [...staticRoutes, ...dynamicUrls];
}
