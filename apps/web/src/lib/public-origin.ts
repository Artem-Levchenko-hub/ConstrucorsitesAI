/**
 * Public origin of THIS deployment (`https://host[:port]`, no trailing slash)
 * for the absolute URLs in page metadata, JSON-LD, sitemap.xml and robots.txt.
 *
 * The domain is a run-time fact, not a build-time one: the same web image must
 * serve any domain. Two rules follow.
 *
 * 1. The variable is the NON-public `PUBLIC_ORIGIN`. Next inlines every
 *    NEXT_PUBLIC_* that exists at build time into all bundles — server ones
 *    too — which would freeze the domain into the image.
 * 2. It is read at CALL time, and callers must themselves run per request
 *    (`generateMetadata` under the cookie-reading root layout, `force-dynamic`
 *    routes). A module-scope constant would capture the build machine's value.
 *
 * `NEXT_PUBLIC_API_URL` is what layout/sitemap/robots read before this helper
 * existed; it stays as a fallback so an older environment keeps its origin.
 *
 * Nothing here throws: an unset or malformed value degrades to a loopback
 * origin (wrong links in metadata, but a working site) instead of taking every
 * page down with `new URL("")`.
 *
 * Server-only: in a browser bundle `PUBLIC_ORIGIN` does not exist, so a client
 * import would silently yield the loopback origin — fail the build instead.
 */
import "server-only";

const FALLBACK_ORIGIN = "http://localhost:3000";

const reported = new Set<string>();

/** One log line per distinct problem, not one per request. */
function reportOnce(message: string): void {
  if (reported.has(message)) return;
  reported.add(message);
  console.warn(`[public-origin] ${message}`);
}

/** Origin of an absolute http(s) URL, or null when `value` is not one. */
function parseOrigin(value: string): string | null {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  // `origin` drops any path, query and trailing slash, so joins never double up.
  return url.origin;
}

export function publicOrigin(): string {
  const candidates = [
    ["PUBLIC_ORIGIN", process.env.PUBLIC_ORIGIN],
    ["NEXT_PUBLIC_API_URL", process.env.NEXT_PUBLIC_API_URL],
  ] as const;

  for (const [name, raw] of candidates) {
    const value = raw?.trim();
    if (!value) continue;
    const origin = parseOrigin(value);
    if (origin) return origin;
    reportOnce(`${name} is not an absolute http(s) URL — ignored`);
  }

  if (process.env.NODE_ENV === "production") {
    reportOnce(
      `PUBLIC_ORIGIN is not set — metadata, sitemap and robots use ${FALLBACK_ORIGIN}`,
    );
  }
  return FALLBACK_ORIGIN;
}
