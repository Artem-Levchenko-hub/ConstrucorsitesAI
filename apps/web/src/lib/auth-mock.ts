/**
 * Server-side session helpers backed by the api's JWT cookie (`omnia_session`).
 *
 * Keeps the original module name so existing imports (`@/lib/auth-mock`) stay
 * stable, but the implementation now talks to the real backend instead of the
 * dev-time mock.
 */

import { cookies } from "next/headers";

export const AUTH_COOKIE = "omnia_session";

export type SessionUser = { id: string; email: string };

function apiBaseUrl(): string {
  return (
    process.env.INTERNAL_API_URL ??
    process.env.NEXT_PUBLIC_API_URL ??
    "http://localhost:8000"
  );
}

/** Read the JWT cookie and resolve the user via /api/auth/me. */
export async function getSession(): Promise<SessionUser | null> {
  // Dev mock mode (NEXT_PUBLIC_USE_MOCKS !== "false"): there is no backend to
  // validate against, so hand back a demo user and let the workspace render
  // against the in-memory mock API. Prod builds with NEXT_PUBLIC_USE_MOCKS
  // ="false" (see deploy/full/docker-compose.yml), so this branch is dead there.
  if (process.env.NEXT_PUBLIC_USE_MOCKS !== "false") {
    return { id: "u-demo", email: "demo@omnia.ai" };
  }

  const c = await cookies();
  const token = c.get(AUTH_COOKIE)?.value;
  if (!token) return null;

  try {
    const r = await fetch(`${apiBaseUrl()}/api/auth/me`, {
      method: "GET",
      headers: { Cookie: `${AUTH_COOKIE}=${token}` },
      cache: "no-store",
    });
    if (!r.ok) return null;
    const user = (await r.json()) as { id: string; email: string };
    return { id: user.id, email: user.email };
  } catch {
    return null;
  }
}

/**
 * Legacy stub kept for backwards compatibility; the real cookie is set by the
 * api response in actions.ts (`callAuth`). Calling this is a no-op in the
 * production flow but harmless if some test fixture still does.
 */
export async function setSession(_user: SessionUser): Promise<void> {
  // intentionally empty
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
