/**
 * POST /signout — destroys the session and redirects to `/`.
 *
 * Used from server actions / `<SignOutButton>`. The corresponding GET
 * doesn't exist on purpose: a CSRF-protected POST is the only way to
 * sign someone out — GET would let an `<img src="/signout">` log users
 * out via image preload attacks.
 */

import { NextResponse, type NextRequest } from "next/server";
import { signOut } from "@/lib/auth";

export async function POST(req: NextRequest) {
  await signOut({ redirect: false });
  return NextResponse.redirect(new URL("/", req.url));
}
