import type { MetadataRoute } from "next";

import { publicOrigin } from "@/lib/public-origin";

// Rendered per request, not once at build time: the domain is only known to the
// running container (see lib/public-origin.ts and sitemap.ts).
export const dynamic = "force-dynamic";

export default function robots(): MetadataRoute.Robots {
  const origin = publicOrigin();
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        // Authenticated app routes — no value for crawlers, costs them budget.
        disallow: ["/account", "/billing", "/admin", "/api/", "/_next/"],
      },
    ],
    sitemap: `${origin}/sitemap.xml`,
    host: origin,
  };
}
