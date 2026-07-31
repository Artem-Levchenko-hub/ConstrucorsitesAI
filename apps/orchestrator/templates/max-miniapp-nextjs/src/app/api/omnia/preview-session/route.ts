import { createHmac, timingSafeEqual } from "node:crypto";
import { NextResponse } from "next/server";

import { db, schema } from "@/lib/db";
import { createMaxSession, MAX_SESSION_COOKIE, type MaxSessionUser } from "@/lib/max/session";

const BOOTSTRAP_TTL_SECONDS = 120;
const PREVIEW_SESSION_MAX_AGE_SECONDS = 15 * 60;
const PREVIEW_USER: MaxSessionUser = {
  id: "preview",
  firstName: "Preview",
  lastName: "MAX",
  username: "preview",
  languageCode: null,
  photoUrl: null,
};

function unavailable(): NextResponse {
  return new NextResponse(null, { status: 404 });
}

function bootstrapMessage(projectId: string, expires: string): string {
  return `omnia:max-preview-session:v1\n${projectId}\n${expires}`;
}

function validSignature(provided: string, expected: string): boolean {
  const left = Buffer.from(provided, "base64url");
  const right = Buffer.from(expected, "base64url");
  return left.length === right.length && timingSafeEqual(left, right);
}

export async function GET(request: Request) {
  if (process.env.NODE_ENV !== "development" || !process.env.OMNIA_PROJECT_ID) {
    return unavailable();
  }

  const secret = process.env.AUTH_SECRET;
  if (!secret) return unavailable();

  const url = new URL(request.url);
  const expires = url.searchParams.get("expires") || "";
  const providedSignature = url.searchParams.get("signature") || "";
  if (!/^[1-9]\d{0,11}$/.test(expires) || !providedSignature) return unavailable();

  const expiry = Number(expires);
  const now = Math.floor(Date.now() / 1000);
  if (!Number.isSafeInteger(expiry) || expiry < now || expiry > now + BOOTSTRAP_TTL_SECONDS) {
    return unavailable();
  }
  const expectedSignature = createHmac("sha256", secret)
    .update(bootstrapMessage(process.env.OMNIA_PROJECT_ID, expires), "utf8")
    .digest("base64url");
  if (!validSignature(providedSignature, expectedSignature)) return unavailable();

  try {
    await db
      .insert(schema.maxUsers)
      .values({
        maxUserId: PREVIEW_USER.id,
        firstName: PREVIEW_USER.firstName,
        lastName: PREVIEW_USER.lastName,
        username: PREVIEW_USER.username,
        languageCode: PREVIEW_USER.languageCode,
        photoUrl: PREVIEW_USER.photoUrl,
      })
      .onConflictDoUpdate({
        target: schema.maxUsers.maxUserId,
        set: {
          firstName: PREVIEW_USER.firstName,
          lastName: PREVIEW_USER.lastName,
          username: PREVIEW_USER.username,
          languageCode: PREVIEW_USER.languageCode,
          photoUrl: PREVIEW_USER.photoUrl,
          updatedAt: new Date(),
        },
      });
    const session = createMaxSession(PREVIEW_USER, {
      maxAge: PREVIEW_SESSION_MAX_AGE_SECONDS,
    });
    // Keep the redirect relative to the public preview origin. Next.js exposes
    // its container listen address in request.url behind nginx, which would
    // otherwise send the browser to https://0.0.0.0:3000/.
    const response = new NextResponse(null, {
      status: 307,
      headers: { Location: "/" },
    });
    response.headers.set("Cache-Control", "no-store");
    response.headers.set("Referrer-Policy", "no-referrer");
    response.cookies.set(MAX_SESSION_COOKIE, session.value, {
      httpOnly: true,
      secure: true,
      sameSite: "none",
      partitioned: true,
      path: "/",
      maxAge: session.maxAge,
    });
    return response;
  } catch {
    return unavailable();
  }
}
