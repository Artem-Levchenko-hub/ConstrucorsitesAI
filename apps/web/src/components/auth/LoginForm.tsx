"use client";

import { useActionState } from "react";
import { loginAction } from "@/app/(auth)/actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function LoginForm({ next }: { next?: string }) {
  const [state, formAction, pending] = useActionState(loginAction, {
    error: null,
  });

  return (
    <form action={formAction} className="space-y-4">
      {next && <input type="hidden" name="next" value={next} />}

      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input
          id="email"
          name="email"
          type="email"
          autoComplete="email"
          required
          placeholder="вы@почта.ru"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="password">Пароль</Label>
        <Input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          placeholder="не короче 8 символов, с цифрой"
        />
      </div>

      {state.error && <p className="text-xs text-danger">{state.error}</p>}

      <Button
        type="submit"
        variant="pill-primary"
        size="lg"
        className="w-full"
        disabled={pending}
      >
        {pending ? "Вход…" : "Войти"}
      </Button>
    </form>
  );
}
