/**
 * Cookie-backed mock auth for the demo path. Once apps/api is wired up,
 * this gets swapped for next-auth Credentials provider per
 * docs/01-api-contract.md auth flow. Until then, every login that meets
 * the validation rules succeeds and stores `omnia_session_mock`.
 */

import { cookies } from "next/headers";

export const AUTH_COOKIE = "omnia_session_mock";

export type SessionUser = { id: string; email: string };

export async function getSession(): Promise<SessionUser | null> {
  const c = await cookies();
  const raw = c.get(AUTH_COOKIE)?.value;
  if (!raw) return null;
  try {
    return JSON.parse(decodeURIComponent(raw)) as SessionUser;
  } catch {
    return null;
  }
}

export async function setSession(user: SessionUser): Promise<void> {
  const c = await cookies();
  c.set(AUTH_COOKIE, encodeURIComponent(JSON.stringify(user)), {
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 30,
  });
}

export async function clearSession(): Promise<void> {
  const c = await cookies();
  c.delete(AUTH_COOKIE);
}

export function validateCredentials(
  email: string,
  password: string,
): string | null {
  if (!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return "Введите корректный email";
  }
  if (password.length < 8) {
    return "Пароль не короче 8 символов";
  }
  if (!/\d/.test(password)) {
    return "Пароль должен содержать хотя бы одну цифру";
  }
  return null;
}
