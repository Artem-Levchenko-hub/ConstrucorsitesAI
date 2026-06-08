import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // The entity engine reads entities/*.json from disk at request time. `next
  // build` (standalone) only ships files it can trace, and JSON read via fs is
  // invisible to the tracer — without this the prod image has ZERO entities and
  // every /api/entities/* call 404s. Force them into the standalone bundle.
  // NOTE: the orchestrator's deploy overwrites this file with its own prod
  // config — the same include is mirrored there (builder.py _PROD_NEXT_CONFIG).
  outputFileTracingIncludes: {
    "/api/entities/**": ["./entities/**/*.json"],
  },
  // Allow the dev iframe (omnia workspace) to embed this project for live preview.
  // CSP frame-ancestors will be set by the orchestrator's reverse proxy.
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

export default nextConfig;
