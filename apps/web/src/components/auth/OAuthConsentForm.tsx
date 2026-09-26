"use client";

import Link from "next/link";
import { useActionState } from "react";
import { ArrowRight } from "lucide-react";

import { oauthCompleteAction } from "@/app/(auth)/actions";
import { Button } from "@/components/ui/button";

/**
 * Экран подтверждения документов для нового аккаунта, который входит через
 * VK ID / Яндекс ID. Те же три обязательных согласия, что и в обычной
 * регистрации Yleum; аккаунт создаётся только после отправки формы.
 */
export function OAuthConsentForm({
  ticket,
  email,
  providerLabel,
  next,
  documentVersion,
}: {
  ticket: string;
  email: string;
  providerLabel: string;
  next: string;
  documentVersion: string;
}) {
  const [state, action, pending] = useActionState(oauthCompleteAction, {
    error: null,
  });

  return (
    <form action={action} className="space-y-5">
      <input type="hidden" name="ticket" value={ticket} />
      <input type="hidden" name="next" value={next} />
      <input type="hidden" name="document_version" value={documentVersion} />

      <div className="rounded-[10px] border border-border-default bg-surface-raised px-4 py-3 text-sm">
        <p className="text-fg-secondary">
          Вход через {providerLabel} как{" "}
          <span className="font-semibold text-fg-primary" data-oauth-email>
            {email}
          </span>
          . Пароль не нужен: провайдер уже подтвердил адрес.
        </p>
      </div>

      <div className="space-y-3 border-t border-border-default pt-5 text-sm">
        <label className="flex cursor-pointer items-start gap-3">
          <input
            name="terms_accepted"
            type="checkbox"
            required
            className="mt-1 size-4 accent-[#0062ee]"
          />
          <span className="text-fg-secondary">
            Принимаю{" "}
            <Link className="text-accent-secondary hover:underline" href="/legal/terms">
              условия использования
            </Link>
          </span>
        </label>
        <label className="flex cursor-pointer items-start gap-3">
          <input
            name="privacy_accepted"
            type="checkbox"
            required
            className="mt-1 size-4 accent-[#0062ee]"
          />
          <span className="text-fg-secondary">
            Ознакомлен с{" "}
            <Link className="text-accent-secondary hover:underline" href="/legal/privacy">
              политикой конфиденциальности
            </Link>
          </span>
        </label>
        <label className="flex cursor-pointer items-start gap-3">
          <input
            name="personal_data_accepted"
            type="checkbox"
            required
            className="mt-1 size-4 accent-[#0062ee]"
          />
          <span className="text-fg-secondary">
            Даю отдельное{" "}
            <Link
              className="text-accent-secondary hover:underline"
              href="/legal/personal-data"
            >
              согласие на обработку персональных данных
            </Link>
          </span>
        </label>
        <label className="flex cursor-pointer items-start gap-3">
          <input
            name="marketing_accepted"
            type="checkbox"
            className="mt-1 size-4 accent-[#0062ee]"
          />
          <span className="text-fg-tertiary">Получать новости продукта — необязательно</span>
        </label>
      </div>

      {state.error && (
        <p role="alert" className="rounded-[8px] bg-danger/10 px-4 py-3 text-sm text-danger-fg">
          {state.error}
        </p>
      )}

      <Button
        disabled={pending}
        className="h-12 w-full rounded-lg bg-accent text-base text-fg-on-accent hover:bg-accent-hover"
      >
        {pending ? "Создаём аккаунт…" : "Создать аккаунт"}
        {!pending && <ArrowRight className="ml-2 size-4" />}
      </Button>

      <p className="text-center text-xs leading-5 text-fg-tertiary">
        Мы храним только email и идентификатор у провайдера. Реквизиты, ФИО и
        телефон платформа не запрашивает.
      </p>
    </form>
  );
}
