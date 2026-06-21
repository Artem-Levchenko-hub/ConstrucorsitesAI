/**
 * Set one account's role — admin-only. This is the assignment half of multi-role
 * RBAC: an operator (admin) promotes a customer to `teacher` / `manager` / etc.,
 * and the engine's readRoles/writeRoles gates then take effect for that user.
 * Self-demotion is blocked so the last operator can't lock themselves out of
 * role management. Fixed template file — the AI never edits it.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

import { NextRequest, NextResponse } from "next/server";
import { eq } from "drizzle-orm";

import { db } from "@/lib/db";
import { users } from "@/lib/db/schema";
import { getCurrentUser } from "@/lib/session";

// A role is a short identifier (the app's own vocabulary: teacher, student,
// parent, manager, admin…). Bounded + charset-limited so it stays a safe token.
const ROLE_RE = /^[a-z][a-z0-9_-]{0,31}$/i;

type Ctx = { params: Promise<{ id: string }> };

export async function PATCH(req: NextRequest, ctx: Ctx) {
  const me = await getCurrentUser();
  if (!me) return NextResponse.json({ error: "authentication required" }, { status: 401 });
  if (me.role !== "admin") return NextResponse.json({ error: "admin only" }, { status: 403 });

  const { id } = await ctx.params;
  const body = (await req.json().catch(() => ({}))) as { role?: unknown };
  const role = typeof body.role === "string" ? body.role.trim() : "";
  if (!ROLE_RE.test(role)) {
    return NextResponse.json({ error: "invalid role" }, { status: 400 });
  }
  // Don't let the current admin strip their OWN admin — that could orphan role
  // management with no operator left to fix it. Changing OTHER accounts is fine.
  if (id === me.id && role !== "admin") {
    return NextResponse.json(
      { error: "нельзя снять роль admin с самого себя" },
      { status: 400 },
    );
  }

  const [row] = await db
    .update(users)
    .set({ role })
    .where(eq(users.id, id))
    .returning({ id: users.id, email: users.email, name: users.name, role: users.role });
  if (!row) return NextResponse.json({ error: "not found" }, { status: 404 });

  return NextResponse.json({ data: row });
}
