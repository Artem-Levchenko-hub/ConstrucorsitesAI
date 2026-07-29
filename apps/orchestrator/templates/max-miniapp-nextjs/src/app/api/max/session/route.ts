import { eq } from "drizzle-orm";
import { NextResponse } from "next/server";

import { db, schema } from "@/lib/db";
import {
  createMaxSession,
  MAX_SESSION_COOKIE,
  type MaxSessionUser,
} from "@/lib/max/session";
import {
  type ValidatedMaxInitData,
  validateMaxInitData,
} from "@/lib/max/validate-init-data";

export async function POST(request: Request) {
  const token = process.env.MAX_BOT_TOKEN;
  if (!token) {
    return NextResponse.json({ error: "MAX integration is not configured" }, { status: 503 });
  }
  let initData = "";
  try {
    const body = (await request.json()) as { initData?: unknown };
    initData = typeof body.initData === "string" ? body.initData : "";
  } catch {
    return NextResponse.json({ error: "Invalid request" }, { status: 400 });
  }
  let launch: ValidatedMaxInitData;
  try {
    launch = validateMaxInitData(initData, token);
  } catch {
    return NextResponse.json({ error: "MAX authentication failed" }, { status: 401 });
  }
  const user: MaxSessionUser = {
    id: String(launch.user.id),
    firstName: launch.user.first_name,
    lastName: launch.user.last_name || null,
    username: launch.user.username || null,
    languageCode: launch.user.language_code || null,
    photoUrl: launch.user.photo_url || null,
  };
  try {
    const existing = await db.query.maxUsers.findFirst({
      where: eq(schema.maxUsers.maxUserId, user.id),
    });
    if (existing) {
      await db
        .update(schema.maxUsers)
        .set({
          firstName: user.firstName,
          lastName: user.lastName,
          username: user.username,
          languageCode: user.languageCode,
          photoUrl: user.photoUrl,
          updatedAt: new Date(),
        })
        .where(eq(schema.maxUsers.maxUserId, user.id));
    } else {
      await db.insert(schema.maxUsers).values({
        maxUserId: user.id,
        firstName: user.firstName,
        lastName: user.lastName,
        username: user.username,
        languageCode: user.languageCode,
        photoUrl: user.photoUrl,
      });
    }
    const session = createMaxSession(user);
    const response = NextResponse.json({ user, startParam: launch.startParam });
    response.cookies.set(MAX_SESSION_COOKIE, session.value, {
      httpOnly: true,
      secure: true,
      sameSite: "none",
      path: "/",
      maxAge: session.maxAge,
    });
    return response;
  } catch {
    return NextResponse.json({ error: "Temporary session failure" }, { status: 503 });
  }
}
