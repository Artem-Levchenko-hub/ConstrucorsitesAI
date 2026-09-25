"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { startOAuthLogin } from "@/lib/api/oauth-login";
import {
  oauthButtonLabel,
  type OAuthProviderKey,
  type OAuthProviderOption,
} from "@/lib/oauth-login";

/**
 * Буквенная метка провайдера. Фирменные цвета намеренно не используем: тема
 * продукта запрещает оранжевые оттенки в UI (theme-contract), а VK — фирменный
 * синий, который в палитре есть.
 */
function ProviderMark({ provider }: { provider: OAuthProviderKey }) {
  const look =
    provider === "vk"
      ? { className: "bg-[#0077FF] text-white", text: "VK" }
      : { className: "bg-fg-primary text-bg-base", text: "Я" };
  return (
    <span
      aria-hidden
      className={`grid size-5 shrink-0 place-items-center rounded-[5px] text-[10px] font-bold leading-none ${look.className}`}
    >
      {look.text}
    </span>
  );
}

/**
 * Кнопки «Войти через VK ID / Яндекс ID». Рисует ровно те провайдеры, что
 * настроены на api (список приходит с сервера страницы); без настроенных
 * провайдеров не рендерит ничего, и страница входа выглядит как раньше.
 */
export function OAuthButtons({
  providers,
  next,
  navigate,
}: {
  providers: OAuthProviderOption[];
  next?: string | null;
  /** Точка подмены в тестах; по умолчанию — переход браузера на провайдера. */
  navigate?: (url: string) => void;
}) {
  const [pending, setPending] = useState<OAuthProviderKey | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (providers.length === 0) return null;

  async function begin(provider: OAuthProviderKey) {
    setPending(provider);
    setError(null);
    try {
      const { authorization_url } = await startOAuthLogin(provider, next);
      (navigate ?? ((url: string) => window.location.assign(url)))(authorization_url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось начать вход");
      setPending(null);
    }
  }

  return (
    <div className="space-y-3" data-oauth-buttons>
      <div className="flex items-center gap-3 text-[11px] uppercase tracking-[.08em] text-fg-tertiary">
        <span className="h-px flex-1 bg-border-default" />
        или
        <span className="h-px flex-1 bg-border-default" />
      </div>
      {providers.map((option) => (
        <Button
          key={option.provider}
          type="button"
          variant="outline"
          size="lg"
          className="w-full rounded-[8px]"
          disabled={pending !== null}
          data-oauth-provider={option.provider}
          onClick={() => begin(option.provider)}
        >
          {pending === option.provider ? (
            <Loader2 className="animate-spin" />
          ) : (
            <ProviderMark provider={option.provider} />
          )}
          {pending === option.provider ? "Переходим к провайдеру…" : oauthButtonLabel(option)}
        </Button>
      ))}
      {error && (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}
      {/* Обещание о данных оставлено: это факт, который человек иначе не проверит.
          Но сказано одной строкой, а не абзацем. */}
      <p className="text-center text-[11px] leading-4 text-fg-tertiary">
        От провайдера берём только email
      </p>
    </div>
  );
}
