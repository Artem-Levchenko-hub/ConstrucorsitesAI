/**
 * Server-side session helpers — what AI-generated server components and
 * server actions should call instead of touching Auth.js directly.
 *
 * Why two helpers and not just `auth()`:
 * - `getCurrentUser()` returns `User | null` — cheap, never throws, fine
 *   for conditional rendering ("show 'Sign in' link if not authed").
 * - `requireUser()` redirects to `/signin?next=...` when not authed —
 *   the right primitive for protected pages where unauthenticated visits
 *   are an error, not a state.
 *
 * Generated pages should prefer these over re-implementing the check.
 */

import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";

export interface CurrentUser {
  id: string;
  email: string;
  name?: string | null;
  image?: string | null;
  role: string;
}

/** Returns the signed-in user, or null. Safe to call in any server
 *  component / server action. Never throws. */
export async function getCurrentUser(): Promise<CurrentUser | null> {
  const session = await auth();
  if (!session?.user?.id || !session.user.email) return null;
  return {
    id: session.user.id,
    email: session.user.email,
    name: session.user.name,
    image: session.user.image,
    role: session.user.role,
  };
}

/** Throw-via-redirect when no session. Use at the top of any protected
 *  page or server action. `next` is the path to come back to after sign-in.
 *
 *  Optional `role` enforces RBAC — currently just exact-match against
 *  `users.role`. A 403 forbidden page would be nicer than redirect-to-home
 *  for the wrong-role case, but for an MVP redirect is fine.
 */
export async function requireUser(opts?: {
  role?: "admin" | "user";
  next?: string;
}): Promise<CurrentUser> {
  const user = await getCurrentUser();
  if (!user) {
    const params = new URLSearchParams();
    if (opts?.next) params.set("next", opts.next);
    redirect(`/signin${params.toString() ? `?${params.toString()}` : ""}`);
  }
  if (opts?.role && user.role !== opts.role) {
    redirect("/");
  }
  return user;
}
