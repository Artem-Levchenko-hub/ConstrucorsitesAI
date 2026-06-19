// Per-project share-card brand payload. The default below ships with the
// template so the app always compiles and unfurls *something* branded; on every
// full build Omnia overwrites this file with the project's real name, niche and
// accent (services/share_meta.py). Consumed by:
//   • src/app/layout.tsx       — generateMetadata() → <title> + Open Graph
//   • src/app/opengraph-image.tsx — the 1200×630 social card image
export const share = {
  title: "Omnia project",
  tagline: "",
  accent: "#6366f1",
} as const;
