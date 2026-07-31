import { createHmac, timingSafeEqual } from "node:crypto";
import { cookies, headers } from "next/headers";

import { validateMaxInitData } from "@/lib/max/validate-init-data";

export const MAX_SESSION_COOKIE = "__Host-max_session";
const MAX_INIT_DATA_HEADER = "x-omnia-max-init-data";
const SESSION_AGE_SECONDS = 24 * 60 * 60;

export type MaxSessionUser = {
  id: string;
  firstName: string;
  lastName: string | null;
  username: string | null;
  languageCode: string | null;
  photoUrl: string | null;
};

type SessionPayload = MaxSessionUser & { expiresAt: number };

function secret(): string {
  const value =
    process.env.MAX_SESSION_SECRET ||
    process.env.AUTH_SECRET ||
    process.env.MAX_WEBHOOK_SECRET;
  if (!value) throw new Error("MAX session secret is not configured");
  return value;
}

function signature(value: string): string {
  return createHmac("sha256", secret()).update(value).digest("base64url");
}

export function createMaxSession(
  user: MaxSessionUser,
  options: { maxAge?: number } = {},
): {
  value: string;
  maxAge: number;
} {
  const maxAge = options.maxAge ?? SESSION_AGE_SECONDS;
  const encoded = Buffer.from(
    JSON.stringify({
      ...user,
      expiresAt: Math.floor(Date.now() / 1000) + maxAge,
    } satisfies SessionPayload),
  ).toString("base64url");
  return { value: `${encoded}.${signature(encoded)}`, maxAge };
}

export function readMaxSession(value: string | undefined): MaxSessionUser | null {
  if (!value) return null;
  const [encoded, provided, ...rest] = value.split(".");
  if (!encoded || !provided || rest.length) return null;
  const expected = signature(encoded);
  const left = Buffer.from(provided);
  const right = Buffer.from(expected);
  if (left.length !== right.length || !timingSafeEqual(left, right)) return null;
  try {
    const payload = JSON.parse(
      Buffer.from(encoded, "base64url").toString("utf8"),
    ) as SessionPayload;
    if (!payload.id || payload.expiresAt < Math.floor(Date.now() / 1000)) return null;
    const { expiresAt: _, ...user } = payload;
    return user;
  } catch {
    return null;
  }
}

export async function getMaxUser(): Promise<MaxSessionUser | null> {
  const cookieUser = readMaxSession(
    (await cookies()).get(MAX_SESSION_COOKIE)?.value,
  );
  if (cookieUser) return cookieUser;

  const token = process.env.MAX_BOT_TOKEN;
  const initData = (await headers()).get(MAX_INIT_DATA_HEADER);
  if (!token || !initData) return null;
  try {
    const launch = validateMaxInitData(initData, token);
    return {
      id: launch.user.id,
      firstName: launch.user.first_name,
      lastName: launch.user.last_name || null,
      username: launch.user.username || null,
      languageCode: launch.user.language_code || null,
      photoUrl: launch.user.photo_url || null,
    };
  } catch {
    return null;
  }
}

export async function requireMaxUser(): Promise<MaxSessionUser> {
  const user = await getMaxUser();
  if (!user) throw new Error("MAX authentication required");
  return user;
}
