import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // The MAX editor renders this dev server inside a narrow phone iframe.
  // Next's floating Dev Tools portal can cover that iframe and intercept every
  // tap even when it is collapsed to the small "Issues" badge. Runtime and
  // compile errors still use the normal error overlay when indicators are off.
  devIndicators: false,
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
