import type { MetadataRoute } from "next";

const PUBLIC_ORIGIN =
  process.env.NEXT_PUBLIC_API_URL ?? "https://constructor.lead-generator.ru";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        // Authenticated app routes — no value for crawlers, costs them budget.
        disallow: ["/projects", "/projects/", "/api/", "/_next/"],
      },
    ],
    sitemap: `${PUBLIC_ORIGIN}/sitemap.xml`,
    host: PUBLIC_ORIGIN,
  };
}
