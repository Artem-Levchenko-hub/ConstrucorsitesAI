"use client";

import Link from "next/link";
import { startTransition, useActionState, useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { recoverAuthAction } from "@/lib/auth-action-recovery";
import { AuthPageRefresh } from "@/components/auth/AuthPageRefresh";
import { ArrowRight, Building2, Check, ShieldCheck } from "lucide-react";

import { maxRegisterAction } from "@/app/(auth)/actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RegistrationPhoneConfirmation } from "./PhoneVerification";

const recoverableAction = recoverAuthAction(maxRegisterAction);

export function MaxRegisterForm({ next = "/max", children, existingSession = false }: { next?: string; children?: ReactNode; existingSession?: boolean }) {
  const router = useRouter();
  const [fields, setFields] = useState({ email: "", phone: "", password: "", confirm: "" });
  const [consents, setConsents] = useState<Record<string, boolean>>({});
  const update = (name: keyof typeof fields, value: string) => setFields(previous => ({ ...previous, [name]: value }));
  const [state, action, pending] = useActionState(recoverableAction, {
    error: null,
  });

  useEffect(() => {
    // cookies().set re-renders this page while a successful signup is still
    // resolving. Keep that action's phone instead of treating it as a new visit.
    if (existingSession && !pending && !state.registered) {
      router.replace(`/max/onboarding?next=${encodeURIComponent(next)}`);
    }
  }, [existingSession, next, pending, router, state.registered]);

  if (state.registered) return <RegistrationPhoneConfirmation {...state.registered} />;
  if (existingSession) return <p role="status">Открываем аккаунт…</p>;

  return (<>
    <form onSubmit={event => {
      event.preventDefault();
      if (pending) return;
      const data = new FormData(event.currentTarget);
      // Dispatch explicitly: React's form action resets native inputs even
      // when the server returns a validation error. Preserve the user's work.
      startTransition(() => action(data));
    }} className="space-y-5">
      <input type="hidden" name="next" value={next} />
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2 sm:col-span-2">
          <Label htmlFor="max-email">Рабочий email</Label>
          <Input
            id="max-email"
            name="email"
            type="email"
            autoComplete="email"
            required
            value={fields.email}
            onChange={event => update("email", event.target.value)}
            className="h-12 border-border-default bg-surface"
          />
        </div>
        <div className="space-y-2 sm:col-span-2">
          <Label htmlFor="max-phone">Телефон с кодом страны</Label>
          <Input id="max-phone" name="phone" type="tel" inputMode="tel" autoComplete="tel" required value={fields.phone}
            onChange={event => update("phone", event.target.value)} className="h-12 border-border-default bg-surface" />
          <p className="text-xs leading-5 text-fg-tertiary">Подтверждение по SMS нужно один раз. Затем — вход по email и паролю.</p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="max-password">Пароль</Label>
          <Input
            id="max-password"
            name="password"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            value={fields.password}
            onChange={event => update("password", event.target.value)}
            className="h-12 border-border-default bg-surface"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="max-confirm">Повторите пароль</Label>
          <Input
            id="max-confirm"
            name="confirm"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            value={fields.confirm}
            onChange={event => update("confirm", event.target.value)}
            className="h-12 border-border-default bg-surface"
          />
        </div>
      </div>

      <div className="space-y-3 border-t border-border-default pt-5 text-sm">
        {[
          ["terms_accepted", "/legal/terms", "Принимаю", "условия использования"],
          ["privacy_accepted", "/legal/privacy", "Ознакомлен с", "политикой конфиденциальности"],
          ["personal_data_accepted", "/legal/personal-data", "Даю отдельное", "согласие на обработку персональных данных"],
        ].map(([name, href, prefix, label]) => (
          <label key={name} className="flex cursor-pointer items-start gap-3">
            <input name={name} type="checkbox" required checked={Boolean(consents[name])}
              onChange={event => setConsents(previous => ({ ...previous, [name]: event.target.checked }))}
              className="mt-1 size-4 accent-[#0062ee]" />
            <span className="text-fg-secondary">{prefix}{" "}<Link className="text-accent-secondary hover:underline" href={href}>{label}</Link></span>
          </label>
        ))}
        <label className="flex cursor-pointer items-start gap-3">
          <input
            name="marketing_accepted"
            type="checkbox"
            checked={Boolean(consents.marketing_accepted)}
            onChange={event => setConsents(previous => ({ ...previous, marketing_accepted: event.target.checked }))}
            className="mt-1 size-4 accent-[#0062ee]"
          />
          <span className="text-fg-tertiary">
            Получать новости продукта — необязательно
          </span>
        </label>
      </div>

      {state.error && (
        <p role="alert" className="rounded-[8px] bg-danger/10 px-4 py-3 text-sm text-danger-fg">
          {state.error}
        </p>
      )}

      {state.refreshRequired && <AuthPageRefresh />}

      <Button
        disabled={pending || state.refreshRequired}
        className="h-12 w-full rounded-lg bg-accent text-base text-fg-on-accent hover:bg-accent-hover"
      >
        {pending ? "Создаём аккаунт…" : "Продолжить"}
        {!pending && <ArrowRight className="ml-2 size-4" />}
      </Button>

      <div className="grid gap-2 text-xs text-fg-tertiary sm:grid-cols-3">
        {[
          [Building2, "Email — для входа и восстановления"],
          [ShieldCheck, "Секреты не передаются агенту"],
          [Check, "MAX и платежи подключаются позже"],
        ].map(([Icon, text]) => {
          const ItemIcon = Icon as typeof Building2;
          return (
            <div key={String(text)} className="flex items-center gap-2">
              <ItemIcon className="size-3.5 text-accent" />
              {String(text)}
            </div>
          );
        })}
      </div>
    </form>
    {children}
  </>);
}
