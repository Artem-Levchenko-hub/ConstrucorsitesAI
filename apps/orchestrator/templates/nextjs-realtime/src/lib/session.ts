/**
 * Server-side session helpers — what generated server components / route
 * handlers call instead of touching Auth.js directly. FIXED template file.
 */

import { redirect } from "next/navigation";

import { auth } from "@/lib/auth";

/** Authenticated users land on the chat home by default. */
export const APP_HOME = "/chat";

export interface CurrentUser {
  id: string;
  email: string;
  name?: string | null;
  image?: string | null;
  role: string;
}

/** The signed-in user, or null. Safe in any server component. Never throws. */
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

/** Redirect to /signin when not authed. Use at the top of a protected page. */
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
  if (opts?.role && user.role !== opts.role) redirect("/");
  return user;
}
