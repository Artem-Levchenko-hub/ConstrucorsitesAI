"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

type FormState = { error: string | null };

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const COOKIE_NAME = "omnia_session";

function validateCredentials(email: string, password: string): string | null {
  if (!email || !EMAIL_RE.test(email)) return "Введите корректный email";
  if (!password || password.length < 8) return "Пароль должен быть не короче 8 символов";
  if (!/\d/.test(password)) return "Пароль должен содержать хотя бы одну цифру";
  return null;
}

/**
 * Internal-network base URL for server-side calls (avoids the public nginx hop).
 * Falls back to the public URL if INTERNAL_API_URL is not set.
 */
function apiBaseUrl(): string {
  return (
    process.env.INTERNAL_API_URL ??
    process.env.NEXT_PUBLIC_API_URL ??
    "http://localhost:8000"
  );
}

/**
 * Call the api auth endpoint, forward the JWT cookie it sets to the browser via
 * Next.js cookies(). The api response's `Set-Cookie` is consumed by node-fetch
 * — we extract the token value from it and re-emit through the framework so the
 * browser actually receives it.
 */
async function callAuth(
  endpoint: "login" | "register",
  email: string,
  password: string,
): Promise<string | null> {
  const url = `${apiBaseUrl()}/api/auth/${endpoint}`;
  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
      cache: "no-store",
    });
  } catch (e) {
    return `Сервер временно недоступен (${(e as Error).message})`;
  }

  if (!response.ok) {
    let message = `Ошибка авторизации (HTTP ${response.status})`;
    try {
      const body = (await response.json()) as { error?: { message?: string; code?: string } };
      if (body?.error?.message) message = body.error.message;
      if (body?.error?.code === "conflict") message = "Email уже зарегистрирован";
      if (body?.error?.code === "unauthorized") message = "Неверный email или пароль";
    } catch {
      // ignore body parse failure
    }
    return message;
  }

  // Extract `omnia_session` from the api's Set-Cookie header(s) and re-emit it
  // through Next.js so the browser stores it.
  const setCookies = (response.headers as Headers & { getSetCookie?: () => string[] })
    .getSetCookie?.() ?? [];
  const sessionCookie = setCookies.find((c) => c.startsWith(`${COOKIE_NAME}=`));
  if (sessionCookie) {
    const [pair] = sessionCookie.split(";");
    const eq = pair.indexOf("=");
    if (eq > 0) {
      const value = pair.slice(eq + 1);
      const cookieStore = await cookies();
      cookieStore.set({
        name: COOKIE_NAME,
        value,
        httpOnly: true,
        secure: true,
        sameSite: "lax",
        path: "/",
        maxAge: 60 * 60 * 24 * 7,
      });
    }
  }

  return null;
}

export async function loginAction(
  _prev: FormState,
  formData: FormData,
): Promise<FormState> {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const validationError = validateCredentials(email, password);
  if (validationError) return { error: validationError };

  const error = await callAuth("login", email, password);
  if (error) return { error };
  redirect("/projects");
}

export async function registerAction(
  _prev: FormState,
  formData: FormData,
): Promise<FormState> {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const confirm = String(formData.get("confirm") ?? "");

  const validationError = validateCredentials(email, password);
  if (validationError) return { error: validationError };
  if (password !== confirm) return { error: "Пароли не совпадают" };

  const error = await callAuth("register", email, password);
  if (error) return { error };
  redirect("/projects");
}

export async function logoutAction() {
  const url = `${apiBaseUrl()}/api/auth/logout`;
  try {
    const cookieStore = await cookies();
    const session = cookieStore.get(COOKIE_NAME)?.value;
    if (session) {
      await fetch(url, {
        method: "POST",
        headers: { Cookie: `${COOKIE_NAME}=${session}` },
        cache: "no-store",
      }).catch(() => undefined);
    }
    cookieStore.delete(COOKIE_NAME);
  } catch {
    // best-effort
  }
  redirect("/");
}
