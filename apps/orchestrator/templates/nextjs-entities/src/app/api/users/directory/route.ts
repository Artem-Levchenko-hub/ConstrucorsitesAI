/**
 * User directory — the list of REAL registered people any signed-in user may
 * pick from (a message recipient, an assignee, a class tutor, …). This is the
 * NON-admin counterpart to `/api/admin/users`: it returns only safe, pickable
 * fields (id, name, role) — never email / phone / password — so a teacher can
 * address a parent without exposing contacts. Auth required (you must be signed
 * in to see who else is in the app); password-less synthetic seed accounts are
 * hidden. Fixed template file — the AI never edits it.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

import { NextResponse } from "next/server";
import { isNotNull } from "drizzle-orm";

import { db } from "@/lib/db";
import { users } from "@/lib/db/schema";
import { getCurrentUser } from "@/lib/session";

export async function GET() {
  const me = await getCurrentUser();
  if (!me) return NextResponse.json({ error: "authentication required" }, { status: 401 });

  const rows = await db
    .select({ id: users.id, name: users.name, role: users.role })
    .from(users)
    // Only real, loginnable accounts — hide the password-less demo-owner seed.
    .where(isNotNull(users.passwordHash))
    .orderBy(users.name);

  // A user with no name still needs a human label so they're pickable.
  const data = rows.map((u) => ({
    id: u.id,
    name: u.name?.trim() || "Без имени",
    role: u.role,
  }));
  return NextResponse.json({ data });
}
