/**
 * Admin user-management — list every account so an operator can see who signed
 * up and assign roles (the [id] sibling route sets the role). Admin-only; the
 * fixed backend owns this so role assignment can't be self-served by a customer.
 * This is what makes multi-role apps (teacher/student/parent) actually work:
 * the first signup is admin and promotes the rest from here. Never returns
 * password_hash. Fixed template file — the AI never edits it.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

import { NextResponse } from "next/server";
import { desc } from "drizzle-orm";

import { db } from "@/lib/db";
import { users } from "@/lib/db/schema";
import { getCurrentUser } from "@/lib/session";

export async function GET() {
  const me = await getCurrentUser();
  if (!me) return NextResponse.json({ error: "authentication required" }, { status: 401 });
  if (me.role !== "admin") return NextResponse.json({ error: "admin only" }, { status: 403 });

  const rows = await db
    .select({
      id: users.id,
      email: users.email,
      name: users.name,
      role: users.role,
      created_at: users.createdAt,
    })
    .from(users)
    .orderBy(desc(users.createdAt));

  return NextResponse.json({ data: rows });
}
