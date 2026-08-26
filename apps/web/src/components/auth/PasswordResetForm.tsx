"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Check, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch } from "@/lib/api/client";

export function PasswordResetForm({ token }: { token: string }) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [pending, setPending] = useState(false);
  const [complete, setComplete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError("Пароли не совпадают");
      return;
    }
    setPending(true);
    setError(null);
    try {
      await apiFetch("/api/auth/password/reset", {
        method: "POST",
        json: { token, password },
      });
      setComplete(true);
    } catch (value) {
      setError(value instanceof Error ? value.message : "Не удалось изменить пароль");
    } finally {
      setPending(false);
    }
  }

  if (complete) {
    return (
      <div className="text-center">
        <Check className="mx-auto size-8 text-success" />
        <p className="mt-4 text-sm text-fg-secondary">
          Пароль изменён. Все прежние сессии завершены.
        </p>
        <Button asChild className="mt-5">
          <Link href="/login">Войти</Link>
        </Button>
      </div>
    );
  }

  if (!token) {
    return (
      <div className="text-center">
        <span className="mx-auto grid size-11 place-items-center rounded-[8px] bg-[#c63d35]/10 text-danger-fg">
          <AlertTriangle className="size-5" />
        </span>
        <p className="mt-4 text-sm font-medium">Ссылка недействительна</p>
        <p className="mt-2 text-xs leading-5 text-[#9fa1b1]">
          Запросите новую ссылку восстановления — старая могла истечь или уже использоваться.
        </p>
        <Button asChild className="mt-5">
          <Link href="/forgot-password">Получить новую ссылку</Link>
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="reset-password">Новый пароль</Label>
        <Input
          id="reset-password"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          minLength={8}
          required
          autoComplete="new-password"
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="reset-confirm">Повторите пароль</Label>
        <Input
          id="reset-confirm"
          type="password"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
          minLength={8}
          required
          autoComplete="new-password"
        />
      </div>
      {error && <p className="text-sm text-danger">{error}</p>}
      <Button className="w-full" disabled={pending}>
        {pending && <Loader2 className="mr-2 size-4 animate-spin" />}
        Сохранить пароль
      </Button>
    </form>
  );
}
