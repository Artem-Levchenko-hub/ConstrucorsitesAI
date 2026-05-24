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

/** Same safety rules as middleware.ts:safeNext — only same-origin paths. */
function safeNext(raw: FormDataEntryValue | null): string | null {
  if (typeof raw !== "string" || !raw) return null;
  if (!raw.startsWith("/")) return null;
  if (raw.startsWith("//")) return null;
  if (raw.includes("\\")) return null;
  return raw;
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

  // Extract `omnia_session=...` from Set-Cookie. Try the spec method first;
  // fall back to the raw header (the spec method is only Node 19.7+ and some
  // proxied fetches strip it). Always log what we see so a misfire is visible
  // in `docker logs omnia-prod-web`.
  const headersAny = response.headers as Headers & { getSetCookie?: () => string[] };
  const fromMethod = typeof headersAny.getSetCookie === "function"
    ? headersAny.getSetCookie()
    : [];
  const fromRaw = response.headers.get("set-cookie");

  // raw header may join multiple cookies with `, ` — splitting naively is unsafe,
  // but for our case we only need the FIRST `omnia_session=...; ...` segment.
  const candidates: string[] = [...fromMethod];
  if (fromRaw) candidates.push(fromRaw);

  let token: string | null = null;
  for (const raw of candidates) {
    const idx = raw.indexOf(`${COOKIE_NAME}=`);
    if (idx === -1) continue;
    const after = raw.slice(idx + COOKIE_NAME.length + 1);
    const end = after.search(/[;,\s]/);
    token = end === -1 ? after : after.slice(0, end);
    break;
  }

  console.log("[auth] api %s OK; set-cookie methods=%d raw=%s tokenFound=%s",
    endpoint, fromMethod.length, fromRaw ? "yes" : "no", token ? "yes" : "no");

  if (!token) {
    return "Не удалось установить сессию (cookie не получен от api)";
  }

  const cookieStore = await cookies();
  cookieStore.set({
    name: COOKIE_NAME,
    value: token,
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 7,
    // Set to ".omniadevelop.ru" in prod env so the session cookie is visible
    // on landing.* (marketing) and app.* (constructor). In dev, leave the env
    // unset — browsers reject explicit ".localhost" and the request host is used.
    domain: process.env.NEXT_PUBLIC_COOKIE_DOMAIN || undefined,
  });
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
  redirect(safeNext(formData.get("next")) ?? "/projects");
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
  redirect(safeNext(formData.get("next")) ?? "/projects");
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
    // Must clear with the same domain we set it under, otherwise the
    // parent-domain cookie survives and /api/auth/me would still succeed.
    cookieStore.set({
      name: COOKIE_NAME,
      value: "",
      path: "/",
      maxAge: 0,
      domain: process.env.NEXT_PUBLIC_COOKIE_DOMAIN || undefined,
    });
  } catch {
    // best-effort
  }
  redirect("/");
}
