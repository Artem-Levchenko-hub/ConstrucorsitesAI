/**
 * Edge middleware — the DETERMINISTIC auth floor for the cabinet.
 *
 * Auth.js runs the `jwt` session strategy (see src/lib/auth.ts), so a signed-in
 * user always carries the session cookie. We probe for it here and bounce anyone
 * without it to `/signin` BEFORE a protected page renders — so a logged-out
 * visitor (or a bot) never sees the app shell, no matter what the generated
 * layout/page does on the client. This is the guaranteed floor: it does not
 * depend on the AI remembering to guard the layout. Pages should STILL call
 * `requireUser()` for the real identity check; this gate just makes the
 * logged-OUT case impossible to reach.
 *
 * Cookie-probe only (no JWT verify in the edge): the Drizzle adapter isn't
 * edge-compatible, and a presence check is enough to gate the unauthenticated
 * case cheaply. The page's `requireUser()` and the owner-scoped entity API
 * enforce the actual identity/ownership.
 *
 * Scope (matcher): only the binding cabinet conventions — `/dashboard/*` (the
 * mandated APP_HOME) and `/admin/*`. The public landing `/`, `/signin`,
 * `/signup`, `/p/*`, `/api/*` (self-guarded) and Next internals stay open, so a
 * public storefront at `/` is never over-protected.
 *
 * Fixed template file — the AI never edits it.
 */
import { NextResponse, type NextRequest } from "next/server";

// Auth.js session cookie. Covers v5 (`authjs.*`) and v4 (`next-auth.*`) names,
// each with the `__Secure-` prefix browsers add on https (prod preview + deploy)
// and the bare form on http (local) — so the gate fires in every environment.
const SESSION_COOKIES = [
  "authjs.session-token",
  "__Secure-authjs.session-token",
  "next-auth.session-token",
  "__Secure-next-auth.session-token",
];

export function middleware(req: NextRequest): NextResponse {
  const signedIn = SESSION_COOKIES.some((name) => req.cookies.has(name));
  if (signedIn) return NextResponse.next();

  const url = req.nextUrl.clone();
  const next = url.pathname + (url.search || "");
  url.pathname = "/signin";
  url.search = "";
  url.searchParams.set("next", next);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/dashboard/:path*", "/admin/:path*"],
};
