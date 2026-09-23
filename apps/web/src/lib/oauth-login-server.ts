import "server-only";

import {
  isOAuthProviderKey,
  normalizeOAuthProviders,
  type OAuthProviderKey,
  type OAuthProviderOption,
} from "@/lib/oauth-login";

/** Same order and empty-value rule as `lib/api/server.ts:apiBaseUrl`. */
function apiBaseUrl(): string {
  return (
    process.env.INTERNAL_API_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000"
  );
}

function mocksEnabled(): boolean {
  return process.env.NEXT_PUBLIC_USE_MOCKS !== "false";
}

/**
 * Настроенные провайдеры для кнопок на страницах входа и регистрации.
 * Любая проблема (api недоступен, мок-режим, мусор в ответе) = кнопок нет:
 * вход по email остаётся, страница не падает.
 */
export async function listOAuthProviders(): Promise<OAuthProviderOption[]> {
  if (mocksEnabled()) return [];
  try {
    const response = await fetch(`${apiBaseUrl()}/api/auth/oauth/providers`, {
      cache: "no-store",
    });
    if (!response.ok) return [];
    const body = (await response.json()) as { providers?: unknown };
    return normalizeOAuthProviders(body.providers);
  } catch {
    return [];
  }
}

export type OAuthPending = {
  provider: OAuthProviderKey;
  label: string;
  email: string;
  next: string;
  legal_document_version: string;
};

/**
 * Что показать на экране подтверждения документов: провайдер, email и версия
 * документов по одноразовому билету из callback-а. null — билет устарел или
 * уже использован.
 */
export async function getOAuthPending(ticket: string): Promise<OAuthPending | null> {
  if (!ticket || mocksEnabled()) return null;
  try {
    const response = await fetch(
      `${apiBaseUrl()}/api/auth/oauth/pending?ticket=${encodeURIComponent(ticket)}`,
      { cache: "no-store" },
    );
    if (!response.ok) return null;
    const body = (await response.json()) as Partial<OAuthPending>;
    if (
      !isOAuthProviderKey(body.provider) ||
      typeof body.label !== "string" ||
      typeof body.email !== "string" ||
      !body.email ||
      typeof body.legal_document_version !== "string"
    ) {
      return null;
    }
    return {
      provider: body.provider,
      label: body.label,
      email: body.email,
      next: typeof body.next === "string" && body.next.startsWith("/") ? body.next : "/max",
      legal_document_version: body.legal_document_version,
    };
  } catch {
    return null;
  }
}
