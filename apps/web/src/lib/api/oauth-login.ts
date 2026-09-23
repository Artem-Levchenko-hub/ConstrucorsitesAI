import type { OAuthProviderKey } from "@/lib/oauth-login";

import { apiFetch } from "./client";

export type OAuthStart = { authorization_url: string };

/**
 * Начало входа через провайдера: api кладёт state и PKCE на сервер и отдаёт
 * ссылку, на которую браузер переходит сам. `next` — same-origin путь, куда
 * вернуть пользователя после входа (api проверяет его ещё раз).
 */
export function startOAuthLogin(
  provider: OAuthProviderKey,
  next?: string | null,
): Promise<OAuthStart> {
  const query = next ? `?next=${encodeURIComponent(next)}` : "";
  return apiFetch<OAuthStart>(`/api/auth/oauth/${provider}/start${query}`);
}
