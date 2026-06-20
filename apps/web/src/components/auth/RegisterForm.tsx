"use client";

import { useActionState } from "react";
import { useTranslations } from "next-intl";
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
  const t = useTranslations("auth.form");

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
        <Label htmlFor="email">{t("emailLabel")}</Label>
        <Input
          id="email"
          name="email"
          type="email"
          autoComplete="email"
          required
          placeholder={t("emailPlaceholder")}
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
          placeholder={t("passwordPlaceholder")}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="confirm">{t("confirmLabel")}</Label>
        <Input
          id="confirm"
          name="confirm"
          type="password"
          autoComplete="new-password"
          required
          placeholder={t("confirmPlaceholder")}
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
        {pending ? t("registerPending") : t("registerButton")}
      </Button>

      <p className="text-xs text-fg-tertiary text-center">{t("consent")}</p>
    </form>
  );
}
