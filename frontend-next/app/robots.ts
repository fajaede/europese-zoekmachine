import { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  // Gebruik de officiële domeinnaam uit de environment variables, met een fallback.
  const baseUrl = process.env.NEXT_PUBLIC_BASE_URL || "https://fajaede.eu";

  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: ["/api/", "/private/", "/dashboard"],
    },
    sitemap: `${baseUrl}/sitemap.xml`,
  };
}