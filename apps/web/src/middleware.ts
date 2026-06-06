import { NextResponse, type NextRequest } from "next/server";

// Real api JWT cookie (api/auth sets this; middleware just checks presence —
// the layout calls /api/auth/me to validate the token).
const AUTH_COOKIE = "omnia_session";

/**
 * Allow only same-origin relative redirects (`/projects`, `/account?x=1`).
 * Rejects protocol-relative (`//evil`), absolute (`https://...`) and
 * back-tracking (`/../`) variants so a hostile landing CTA can't bounce
 * the user off to a phishing host after a fresh sign-in.
 */
function safeNext(raw: string | null): string | null {
  if (!raw) return null;
  if (!raw.startsWith("/")) return null;
  if (raw.startsWith("//")) return null;
  if (raw.includes("\\")) return null;
  return raw;
}

export function middleware(req: NextRequest) {
  // Dev mock mode (NEXT_PUBLIC_USE_MOCKS !== "false"): no backend exists to set
  // the auth cookie, so let every route through — the layout's getSession()
  // returns a demo user in this mode. Prod builds with NEXT_PUBLIC_USE_MOCKS
  // ="false", so this guard is inert there.
  if (process.env.NEXT_PUBLIC_USE_MOCKS !== "false") {
    return NextResponse.next();
  }

  const session = req.cookies.get(AUTH_COOKIE);
  const path = req.nextUrl.pathname;
  const isAuthRoute = path === "/login" || path === "/register";

  // Already signed-in user hitting /login or /register → honour ?next= so
  // the landing-page CTA "Войти" lands directly on whatever path it asked
  // for (default: /projects).
  if (session && isAuthRoute) {
    const next = safeNext(req.nextUrl.searchParams.get("next"));
    const url = req.nextUrl.clone();
    url.pathname = next ?? "/projects";
    url.search = "";
    return NextResponse.redirect(url);
  }

  if (!session && !isAuthRoute && path.startsWith("/projects")) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    // Preserve where the user actually wanted to land — including any
    // query string, since /projects?filter=… should round-trip too.
    const target = `${path}${req.nextUrl.search}`;
    url.search = "";
    url.searchParams.set("next", target);
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/projects/:path*", "/login", "/register"],
};
