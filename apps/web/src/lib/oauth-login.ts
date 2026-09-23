/**
 * Вход через VK ID и Яндекс ID — общие типы и тексты для кнопок и ошибок.
 *
 * Список провайдеров приходит с api (`GET /api/auth/oauth/providers`): кнопка
 * рисуется только для настроенных. Ошибки самого рукопожатия api возвращает
 * редиректом на `/login?oauth_error=<код>` — здесь их человеческие формулировки.
 */

export type OAuthProviderKey = "vk" | "yandex";

export type OAuthProviderOption = {
  provider: OAuthProviderKey;
  label: string;
};

export const OAUTH_PROVIDER_KEYS: readonly OAuthProviderKey[] = ["vk", "yandex"];

export function isOAuthProviderKey(value: unknown): value is OAuthProviderKey {
  return typeof value === "string" && (OAUTH_PROVIDER_KEYS as readonly string[]).includes(value);
}

/** Оставляет только корректные записи провайдеров из ответа api. */
export function normalizeOAuthProviders(value: unknown): OAuthProviderOption[] {
  if (!Array.isArray(value)) return [];
  const options: OAuthProviderOption[] = [];
  for (const item of value) {
    if (typeof item !== "object" || item === null) continue;
    const { provider, label } = item as { provider?: unknown; label?: unknown };
    if (!isOAuthProviderKey(provider) || typeof label !== "string" || !label.trim()) continue;
    if (options.some((option) => option.provider === provider)) continue;
    options.push({ provider, label: label.trim() });
  }
  return options;
}

export function oauthButtonLabel(option: OAuthProviderOption): string {
  return `Войти через ${option.label}`;
}

const OAUTH_ERROR_MESSAGES: Record<string, string> = {
  oauth_cancelled: "Вход через провайдера отменён. Попробуйте ещё раз или войдите по email.",
  oauth_state_invalid: "Ссылка для входа устарела. Нажмите кнопку провайдера ещё раз.",
  oauth_exchange_failed: "Провайдер не подтвердил вход. Попробуйте ещё раз чуть позже.",
  oauth_email_required:
    "Провайдер не передал email. Разрешите доступ к адресу почты или войдите по email.",
  oauth_provider_unavailable: "Вход через этого провайдера сейчас не настроен.",
  account_unavailable: "Аккаунт недоступен. Напишите в поддержку, если это ошибка.",
};

/** Текст для `?oauth_error=<код>`; неизвестный код — общая формулировка, пусто — null. */
export function oauthErrorMessage(code: string | null | undefined): string | null {
  if (!code) return null;
  return OAUTH_ERROR_MESSAGES[code] ?? "Не удалось войти через провайдера. Попробуйте ещё раз.";
}
