import { NextResponse, type NextRequest } from "next/server";

const AUTH_COOKIE = "omnia_session_mock";

export function middleware(req: NextRequest) {
  const session = req.cookies.get(AUTH_COOKIE);
  const path = req.nextUrl.pathname;
  const isAuthRoute = path === "/login" || path === "/register";

  if (!session && !isAuthRoute && path.startsWith("/projects")) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("from", path);
    return NextResponse.redirect(url);
  }

  if (session && isAuthRoute) {
    const url = req.nextUrl.clone();
    url.pathname = "/projects";
    url.search = "";
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/projects/:path*", "/login", "/register"],
};
