/**
 * Register endpoint for the SDK's `auth.signUp()`. Creates an email + password
 * user (same insert the /signup server action does) and returns JSON the SDK
 * can read — NOT a redirect, so a client `fetch` can branch on the result. The
 * client then calls `auth.signIn()` to establish the session. Fixed template
 * file — the AI never edits it.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

import { eq } from "drizzle-orm";
import { NextResponse } from "next/server";

import { hashPassword } from "@/lib/auth";
import { db } from "@/lib/db";
import { users } from "@/lib/db/schema";

export async function POST(req: Request) {
  let body: { email?: string; password?: string; name?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Некорректный запрос" }, { status: 400 });
  }

  const email = String(body.email ?? "").trim().toLowerCase();
  const password = String(body.password ?? "");
  const name = String(body.name ?? "").trim() || null;

  if (!email || password.length < 8) {
    return NextResponse.json(
      { error: "Укажите email и пароль (минимум 8 символов)" },
      { status: 400 },
    );
  }

  // Pre-check keeps the common "email taken" path a clean 409. The unique
  // index on `email` is still the source of truth for a parallel-signup race.
  const existing = await db
    .select({ id: users.id })
    .from(users)
    .where(eq(users.email, email))
    .limit(1);
  if (existing[0]) {
    return NextResponse.json(
      { error: "Этот email уже зарегистрирован" },
      { status: 409 },
    );
  }

  try {
    await db.insert(users).values({
      email,
      name,
      passwordHash: await hashPassword(password),
    });
  } catch {
    // Most likely the unique constraint firing on a race — don't leak details.
    return NextResponse.json(
      { error: "Этот email уже зарегистрирован" },
      { status: 409 },
    );
  }

  return NextResponse.json({ data: { ok: true } });
}
