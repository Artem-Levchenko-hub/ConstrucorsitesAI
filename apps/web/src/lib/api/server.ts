/**
 * Server-side fetch helper for React Server Components.
 *
 * The browser-side `apiFetch` from ./client uses `credentials: "include"`
 * which is meaningless on the Node side — server fetches don't see the
 * user's browser cookies automatically. RSCs must read `omnia_session`
 * via `next/headers` and pass it explicitly.
 */

import { cookies } from "next/headers";

const COOKIE_NAME = "omnia_session";

function apiBaseUrl(): string {
  return (
    process.env.INTERNAL_API_URL ??
    process.env.NEXT_PUBLIC_API_URL ??
    "http://localhost:8000"
  );
}

/**
 * GET an api endpoint with the user's session cookie attached.
 * Returns parsed JSON on 2xx, null on any error or non-2xx — callers decide
 * whether that means notFound() or a degraded render.
 */
export async function serverApiFetch<T>(path: string): Promise<T | null> {
  const c = await cookies();
  const token = c.get(COOKIE_NAME)?.value;
  if (!token) return null;

  try {
    const r = await fetch(`${apiBaseUrl()}${path}`, {
      headers: { Cookie: `${COOKIE_NAME}=${token}` },
      cache: "no-store",
    });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}
