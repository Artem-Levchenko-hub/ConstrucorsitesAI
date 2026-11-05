"use client";

import { useActionState, useState } from "react";
import { recoverAuthAction } from "@/lib/auth-action-recovery";
import { AuthPageRefresh } from "@/components/auth/AuthPageRefresh";
import { useTranslations } from "next-intl";
import Link from "next/link";

import { registerAction } from "@/app/(auth)/actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { newPasswordError, PASSWORD_HELP, PASSWORD_MIN_LENGTH } from "@/lib/password-policy";

const recoverableAction = recoverAuthAction(registerAction);

export function RegisterForm({
  next,
  source,
  referrerProjectId,
}: {
  next?: string;
  source?: string;
  referrerProjectId?: string;
}) {
  const [state, formAction, pending] = useActionState(recoverableAction, {
    error: null,
  });
  const t = useTranslations("auth.form");
  const [passwordError, setPasswordError] = useState<string | null>(null);

  return (
    <form action={formAction} onSubmit={event => {
      const error = newPasswordError(String(new FormData(event.currentTarget).get("password") ?? ""));
      setPasswordError(error);
      if (error) {
        event.preventDefault();
        event.currentTarget.querySelector<HTMLInputElement>("#password")?.focus();
      }
    }} className="space-y-4">
      {next && <input type="hidden" name="next" value={next} />}
      {/* V4.2b return-edge: carry viral-funnel provenance from the URL into the
          register action. The server action re-validates/sanitizes these. */}
      {source && <input type="hidden" name="source" value={source} />}
      {referrerProjectId && (
        <input type="hidden" name="referrer_project_id" value={referrerProjectId} />
      )}

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
        <Label htmlFor="password">{t("passwordLabel")}</Label>
        <Input
          id="password"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={PASSWORD_MIN_LENGTH}
          aria-describedby="password-help password-error"
          aria-invalid={Boolean(passwordError)}
          onChange={() => setPasswordError(null)}
        />
        <p id="password-help" className="text-xs leading-5 text-fg-tertiary">{PASSWORD_HELP}</p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="confirm">{t("confirmLabel")}</Label>
        <Input
          id="confirm"
          name="confirm"
          type="password"
          autoComplete="new-password"
          required
          minLength={PASSWORD_MIN_LENGTH}
          placeholder={t("confirmPlaceholder")}
        />
      </div>

      {(passwordError || state.error) && <p id="password-error" role="alert" className="text-xs text-danger">{passwordError || state.error}</p>}

      {state.refreshRequired && <AuthPageRefresh />}

      <Button
        type="submit"
        variant="primary"
        size="lg"
        className="w-full rounded-[8px]"
        disabled={pending || state.refreshRequired}
      >
        {pending ? t("registerPending") : t("registerButton")}
      </Button>

      <p className="text-center text-xs leading-5 text-fg-tertiary">
        {t("consent")} Ознакомьтесь с{" "}
        <Link className="text-accent-secondary hover:underline" href="/legal/privacy">политикой конфиденциальности</Link>
        {" "}и{" "}
        <Link className="text-accent-secondary hover:underline" href="/legal/personal-data">обработкой персональных данных</Link>.
      </p>
    </form>
  );
}
