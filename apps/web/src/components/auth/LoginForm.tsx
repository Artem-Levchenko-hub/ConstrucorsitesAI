"use client";

import Link from "next/link";
import { useActionState } from "react";
import { recoverAuthAction } from "@/lib/auth-action-recovery";
import { AuthPageRefresh } from "@/components/auth/AuthPageRefresh";
import { useTranslations } from "next-intl";
import { loginAction } from "@/app/(auth)/actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const recoverableAction = recoverAuthAction(loginAction);

export function LoginForm({ next }: { next?: string }) {
  const [state, formAction, pending] = useActionState(recoverableAction, {
    error: null,
  });
  const t = useTranslations("auth.form");

  return (
    <form action={formAction} className="space-y-4">
      {next && <input type="hidden" name="next" value={next} />}

      <div className="space-y-2">
        <Label htmlFor="email">{t("emailLabel")}</Label>
        <Input
          id="email"
          name="email"
          type="email"
          autoComplete="email"
          required
        />
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label htmlFor="password">{t("passwordLabel")}</Label>
          <Link href="/forgot-password" className="text-xs text-accent hover:underline">
            Забыли пароль?
          </Link>
        </div>
        <Input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
        />
      </div>

      {state.error && <p role="alert" className="text-xs text-danger">{state.error}</p>}

      {state.refreshRequired && <AuthPageRefresh />}

      <Button
        type="submit"
        variant="primary"
        size="lg"
        className="w-full rounded-[8px]"
        disabled={pending || state.refreshRequired}
      >
        {pending ? t("loginPending") : t("loginButton")}
      </Button>
    </form>
  );
}
