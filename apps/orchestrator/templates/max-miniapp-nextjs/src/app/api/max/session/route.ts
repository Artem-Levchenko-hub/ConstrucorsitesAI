import { NextResponse } from "next/server";

import { db, schema } from "@/lib/db";
import {
  createMaxSession,
  getMaxUser,
  MAX_SESSION_COOKIE,
  type MaxSessionUser,
} from "@/lib/max/session";
import {
  MaxInitDataError,
  type ValidatedMaxInitData,
  validateMaxInitData,
} from "@/lib/max/validate-init-data";

/** Resume only an authenticated MAX identity; never renew or write a user on GET. */
export async function GET() {
  const headers = { "Cache-Control": "no-store" };
  try {
    const user = await getMaxUser();
    if (!user || typeof user.id !== "string" || !/^[1-9][0-9]{0,19}$/.test(user.id)) {
      return NextResponse.json(
        { error: "MAX authentication required" }, { status: 401, headers },
      );
    }
    return NextResponse.json({ user }, { headers });
  } catch {
    return NextResponse.json(
      { error: "Temporary session failure" }, { status: 503, headers },
    );
  }
}

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
  } catch (error) {
    const code = error instanceof MaxInitDataError ? error.code : "malformed";
    console.warn("[max-auth] rejected launch data", {
      code,
      length: initData.length,
    });
    return NextResponse.json(
      {
        error: "Не удалось подтвердить запуск приложения из MAX",
        code: `max_auth_${code}`,
      },
      { status: 401 },
    );
  }
  const user: MaxSessionUser = { id: launch.user.id };
  try {
    // Materialise the FK parent only. The profile MAX sends with the launch is
    // never stored; `first_name` stays empty because the starter schema still
    // declares the column NOT NULL for apps created before this rule.
    await db
      .insert(schema.maxUsers)
      .values({ maxUserId: user.id, firstName: "" })
      .onConflictDoNothing({ target: schema.maxUsers.maxUserId });
    const session = createMaxSession(user);
    const response = NextResponse.json({ user, startParam: launch.startParam });
    response.cookies.set(MAX_SESSION_COOKIE, session.value, {
      httpOnly: true,
      secure: true,
      // The mini app and its APIs share one origin. Lax keeps that first-party
      // session compatible with iOS MAX WebViews that reject SameSite=None
      // cookies, while still preventing cross-site subrequests from using it.
      sameSite: "lax",
      path: "/",
      maxAge: session.maxAge,
    });
    return response;
  } catch (error) {
    console.error("[max-auth] session persistence failed", {
      name: error instanceof Error ? error.name : "unknown",
    });
    return NextResponse.json({ error: "Temporary session failure" }, { status: 503 });
  }
}
