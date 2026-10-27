"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";
import { confirmAccountEmailChange } from "@/lib/api/account-credentials";

export function EmailChangeConfirmation() {
  const token = useRef("");
  const initialized = useRef(false);
  const active = useRef(false);
  const submitting = useRef(false);
  const [state, setState] = useState<"loading" | "ready" | "pending" | "completed" | "invalid">("loading");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    active.current = true;
    if (!initialized.current) {
      initialized.current = true;
      token.current = new URLSearchParams(window.location.hash.slice(1)).get("token") ?? "";
      // Keep the capability in this mounted component only, outside URLs, storage and query caches.
      window.history.replaceState(window.history.state, "", window.location.pathname);
      setState(token.current.length >= 32 && token.current.length <= 512 ? "ready" : "invalid");
    }
    return () => { active.current = false; };
  }, []);

  async function confirm() {
    if (!token.current || submitting.current || state !== "ready") return;
    submitting.current = true; setBusy(true); setError(null);
    try {
      const result = await confirmAccountEmailChange(token.current);
      if (!active.current) return;
      token.current = "";
      setState(result.status);
      // This page may be opened while another account is signed in. Never log it out.
    } catch (value) {
      if (!active.current) return;
      setError(value instanceof Error ? value.message : "Не удалось подтвердить адрес. Попробуйте ещё раз.");
      if (value instanceof ApiError && [400, 404, 409, 410, 422].includes(value.status)) {
        token.current = ""; setState("invalid");
      }
    } finally {
      submitting.current = false;
      if (active.current) setBusy(false);
    }
  }

  return <div className="space-y-4 text-sm text-fg-secondary">
    {state === "loading" && <p role="status">Проверяем ссылку…</p>}
    {state === "ready" && <>
      <p>Подтвердите этот email, если вы запросили его смену в Yleum. Для смены логина нужно подтвердить оба адреса — старый и новый.</p>
      <Button className="w-full" disabled={busy} onClick={() => void confirm()}>{busy ? "Подтверждаем…" : "Подтвердить email"}</Button>
    </>}
    {state === "pending" && <p role="status">Этот адрес подтверждён. Теперь подтвердите второй адрес по ссылке в другом письме. Пока email для входа остаётся прежним.</p>}
    {state === "completed" && <>
      <p role="status">Email для входа изменён. Прежние сессии этого аккаунта завершены. Войдите с новым email и вашим паролем.</p>
      <Button asChild className="w-full"><Link href="/login">Перейти ко входу</Link></Button>
    </>}
    {state === "invalid" && <>
      <p>Ссылка недействительна. Она могла истечь, уже использоваться или быть заменена новым запросом.</p>
      <Link className="underline" href="/account">Запросить подтверждение в профиле</Link>
    </>}
    {error && <p role="alert" className="text-danger">{error}</p>}
  </div>;
}
