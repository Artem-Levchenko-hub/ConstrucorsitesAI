import { NextResponse, type NextRequest } from "next/server";

// Real api JWT cookie (api/auth sets this; middleware just checks presence —
// the layout calls /api/auth/me to validate the token).
const AUTH_COOKIE = "omnia_session";

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
  const isGeneralAuthRoute = path === "/login" || path === "/register";
  const isPublicMaxRoute =
    path === "/max/product" ||
    path === "/max/guide" ||
    path === "/max/start" ||
    path === "/max/register" ||
    path === "/max/verify-email";

  // Never redirect away from /login or /register based on cookie presence
  // alone. Middleware cannot validate the JWT, so a stale omnia_session used
  // to create an infinite loop:
  //   /login -> /projects -> app layout rejects JWT -> /login -> ...
  // Auth pages must remain reachable so the user can replace an expired
  // session by signing in again.

  const isProtectedRoute =
    path.startsWith("/projects") ||
    path.startsWith("/admin") ||
    path === "/max" ||
    path.startsWith("/max/");

  if (!session && !isGeneralAuthRoute && !isPublicMaxRoute && isProtectedRoute) {
    const url = req.nextUrl.clone();
    url.pathname = path.startsWith("/max") ? "/max/register" : "/login";
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
  matcher: ["/projects/:path*", "/admin/:path*", "/max/:path*", "/login", "/register"],
};
