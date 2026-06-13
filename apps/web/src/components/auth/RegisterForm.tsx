"use client";

import { useActionState } from "react";
import { registerAction } from "@/app/(auth)/actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function RegisterForm({
  next,
  source,
  referrerProjectId,
}: {
  next?: string;
  source?: string;
  referrerProjectId?: string;
}) {
  const [state, formAction, pending] = useActionState(registerAction, {
    error: null,
  });

  return (
    <form action={formAction} className="space-y-4">
      {next && <input type="hidden" name="next" value={next} />}
      {/* V4.2b return-edge: carry viral-funnel provenance from the URL into the
          register action. The server action re-validates/sanitizes these. */}
      {source && <input type="hidden" name="source" value={source} />}
      {referrerProjectId && (
        <input type="hidden" name="referrer_project_id" value={referrerProjectId} />
      )}

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
          autoComplete="new-password"
          required
          placeholder="не короче 8 символов, с цифрой"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="confirm">Подтверждение</Label>
        <Input
          id="confirm"
          name="confirm"
          type="password"
          autoComplete="new-password"
          required
          placeholder="повторите пароль"
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
        {pending ? "Регистрация…" : "Создать аккаунт"}
      </Button>

      <p className="text-xs text-fg-tertiary text-center">
        Регистрируясь, вы соглашаетесь с условиями использования.
      </p>
    </form>
  );
}
