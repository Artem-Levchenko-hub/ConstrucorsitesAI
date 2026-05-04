"use server";

import { redirect } from "next/navigation";
import {
  clearSession,
  setSession,
  validateCredentials,
  type SessionUser,
} from "@/lib/auth-mock";

const uuid = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);

type FormState = { error: string | null };

export async function loginAction(
  _prev: FormState,
  formData: FormData,
): Promise<FormState> {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const validationError = validateCredentials(email, password);
  if (validationError) return { error: validationError };

  const user: SessionUser = { id: uuid(), email };
  await setSession(user);
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

  const user: SessionUser = { id: uuid(), email };
  await setSession(user);
  redirect("/projects");
}

export async function logoutAction() {
  await clearSession();
  redirect("/");
}
