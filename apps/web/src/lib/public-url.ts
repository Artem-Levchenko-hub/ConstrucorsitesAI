/**
 * Canonical public URL of a project — the `/p/<slug>` share link the preview,
 * the "Поделиться" dialog, and the remix-source attribution all point at.
 *
 * Single source of truth so the three callers can't drift on origin handling
 * (in prod `NEXT_PUBLIC_API_URL = https://constructor.lead-generator.ru`; on the
 * client it falls back to the current origin; SSR with neither set degrades to
 * the legacy `<slug>.omnia.ai` shape rather than a relative URL).
 */
export function buildPublicUrl(slug: string): string {
  const apiOrigin =
    process.env.NEXT_PUBLIC_API_URL ??
    (typeof window !== "undefined" ? window.location.origin : "");
  return apiOrigin
    ? `${apiOrigin.replace(/\/$/, "")}/p/${slug}`
    : `https://${slug}.omnia.ai`;
}
