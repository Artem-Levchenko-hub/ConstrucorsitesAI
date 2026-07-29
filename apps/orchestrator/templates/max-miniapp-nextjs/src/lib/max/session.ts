import { createHmac, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";

export const MAX_SESSION_COOKIE = "__Host-max_session";
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

export function createMaxSession(user: MaxSessionUser): {
  value: string;
  maxAge: number;
} {
  const encoded = Buffer.from(
    JSON.stringify({
      ...user,
      expiresAt: Math.floor(Date.now() / 1000) + SESSION_AGE_SECONDS,
    } satisfies SessionPayload),
  ).toString("base64url");
  return { value: `${encoded}.${signature(encoded)}`, maxAge: SESSION_AGE_SECONDS };
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
  return readMaxSession((await cookies()).get(MAX_SESSION_COOKIE)?.value);
}

export async function requireMaxUser(): Promise<MaxSessionUser> {
  const user = await getMaxUser();
  if (!user) throw new Error("MAX authentication required");
  return user;
}
