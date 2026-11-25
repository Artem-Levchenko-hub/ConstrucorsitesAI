"use client";

import { useActionState } from "react";

import { PasswordField } from "@/components/auth/PasswordField";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, apiFetch } from "@/lib/api/client";
import { MONITORING_DASHBOARD_PATH, MONITORING_SESSION_API } from "@/lib/monitoring-access";

async function signIn(_previous: string | null, fields: FormData): Promise<string | null> {
  try {
    await apiFetch<void>(MONITORING_SESSION_API, {
      method: "POST",
      json: { username: fields.get("username"), password: fields.get("password") },
      timeoutMs: 10000,
    });
  } catch (error) {
    if (error instanceof ApiError) {
      if (error.status === 401 || error.status === 422) return "Проверьте логин и пароль.";
      if (error.status === 429) return "Слишком много попыток. Попробуйте позже.";
    }
    return "Не удалось войти. Попробуйте ещё раз.";
  }
  window.location.assign(MONITORING_DASHBOARD_PATH);
  return null;
}

export function MonitoringLoginForm() {
  const [error, action, pending] = useActionState(signIn, null);

  return (
    <form action={action} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="monitoring-username">Логин</Label>
        <Input id="monitoring-username" name="username" autoComplete="username"
          autoCapitalize="none" spellCheck={false} maxLength={64} required />
      </div>
      <div className="space-y-2">
        <Label htmlFor="monitoring-password">Пароль</Label>
        <PasswordField id="monitoring-password" name="password" autoComplete="current-password"
          showLabel="Показать пароль" hideLabel="Скрыть пароль" maxLength={256} required />
      </div>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      <Button type="submit" variant="primary" size="lg" className="w-full rounded-[8px]"
        disabled={pending}>
        {pending ? "Входим…" : "Войти"}
      </Button>
    </form>
  );
}
