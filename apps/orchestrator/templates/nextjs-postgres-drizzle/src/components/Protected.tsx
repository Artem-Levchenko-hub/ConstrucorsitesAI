/**
 * `<Protected>` — server-side gate component. Renders children only when
 * the visitor is authed (optionally with a specific `role`). Otherwise
 * either renders nothing OR redirects to `/signin?next=...` based on
 * `behavior`.
 *
 * Why server-side: redirect happens BEFORE any HTML is sent, so there's
 * no flash of protected content. AI-generated client components that
 * need to react to auth state should `useSession()` from
 * `next-auth/react` instead.
 *
 * Usage in a server component:
 *
 *   <Protected>
 *     <DashboardWidget />
 *   </Protected>
 *
 *   <Protected role="admin" behavior="hide">
 *     <AdminPanelLink />
 *   </Protected>
 */

import { redirect } from "next/navigation";
import { getCurrentUser } from "@/lib/session";

export async function Protected({
  children,
  role,
  behavior = "redirect",
  next,
}: {
  children: React.ReactNode;
  role?: "admin" | "user";
  /** `redirect` → bounce to /signin when not authed (default).
   *  `hide` → render nothing. Use for optional UI like "Sign out" button. */
  behavior?: "redirect" | "hide";
  /** Where to come back to after sign-in. Defaults to the current path
   *  (set by the parent route). */
  next?: string;
}) {
  const user = await getCurrentUser();
  if (!user) {
    if (behavior === "hide") return null;
    const params = new URLSearchParams();
    if (next) params.set("next", next);
    redirect(`/signin${params.toString() ? `?${params.toString()}` : ""}`);
  }
  if (role && user.role !== role) {
    if (behavior === "hide") return null;
    redirect("/");
  }
  return <>{children}</>;
}
