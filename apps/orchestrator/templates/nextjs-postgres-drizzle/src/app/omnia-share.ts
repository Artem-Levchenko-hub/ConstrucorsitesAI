// Per-project share-card brand payload. The default below ships with the
// template so the app always compiles and the auth screen wears *some* brand;
// on every full build Omnia overwrites this file with the project's real name,
// niche and accent (services/share_meta.py). Consumed by:
//   • src/components/auth-shell.tsx — branded signin / signup chrome
export const share = {
  title: "Omnia project",
  tagline: "",
  accent: "#6366f1",
} as const;
