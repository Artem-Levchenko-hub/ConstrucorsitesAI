import type { MetadataRoute } from "next";

const PUBLIC_ORIGIN =
  process.env.NEXT_PUBLIC_API_URL ?? "https://constructor.lead-generator.ru";

/**
 * Static sitemap for the marketing site. Generated-user sites get their own
 * sitemap inside the rendered project (the AI is instructed to emit one),
 * served from /p/<slug>/sitemap.xml by the api.
 */
export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
  return [
    {
      url: `${PUBLIC_ORIGIN}/`,
      lastModified: now,
      changeFrequency: "weekly",
      priority: 1.0,
    },
    {
      url: `${PUBLIC_ORIGIN}/login`,
      lastModified: now,
      changeFrequency: "monthly",
      priority: 0.3,
    },
    {
      url: `${PUBLIC_ORIGIN}/register`,
      lastModified: now,
      changeFrequency: "monthly",
      priority: 0.5,
    },
  ];
}
