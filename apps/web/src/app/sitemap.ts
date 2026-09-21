import type { MetadataRoute } from "next";

import { publicOrigin } from "@/lib/public-origin";

// Without this Next renders the file once at build time and freezes the build
// machine's origin into the image; the domain is only known to the running
// container (see lib/public-origin.ts).
export const dynamic = "force-dynamic";

/**
 * Sitemap for the marketing site (a fixed list of pages under the deployment's
 * own origin). Generated-user sites get their own sitemap inside the rendered
 * project (the AI is instructed to emit one), served from
 * /p/<slug>/sitemap.xml by the api.
 */
export default function sitemap(): MetadataRoute.Sitemap {
  const origin = publicOrigin();
  const now = new Date();
  return [
    {
      url: `${origin}/`,
      lastModified: now,
      changeFrequency: "weekly",
      priority: 1.0,
    },
    ...[
      "max/product",
      "max/start",
      "max/guide",
      "pricing",
      "about",
      "security",
      "requisites",
      "contact",
      "changelog",
      "legal/privacy",
      "legal/offer",
      "legal/refunds",
      "legal/terms",
    ].map((path) => ({
      url: `${origin}/${path}`,
      lastModified: now,
      changeFrequency: "monthly" as const,
      priority: 0.6,
    })),
    {
      url: `${origin}/login`,
      lastModified: now,
      changeFrequency: "monthly",
      priority: 0.3,
    },
    {
      url: `${origin}/max/register`,
      lastModified: now,
      changeFrequency: "monthly",
      priority: 0.5,
    },
  ];
}
