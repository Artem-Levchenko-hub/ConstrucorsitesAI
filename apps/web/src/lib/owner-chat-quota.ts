import type { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./api/client";
import type { User } from "./api/types";

export const OWNER_PROFILE_QUERY_KEY = ["owner-profile"] as const;
export const FREE_CHAT_UPGRADE_MESSAGE = "На Free доступно одно сообщение для создания приложения — на весь аккаунт. Для новых запросов выберите Pro или Business. Готовое приложение можно настроить и опубликовать.";

export function ownerChatQuota(user?: User) {
  const limited = user?.user_chat_messages_limit === 1;
  return { limited, remaining: limited ? user?.user_chat_messages_remaining ?? null : null,
    exhausted: limited && user?.user_chat_messages_remaining === 0 };
}

export function isFreeChatRefusal(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 402 && error.code === "entitlement_exceeded"
    && error.details?.entitlement === "free_chat_messages";
}

/** Called only after the server accepts a prompt, never on queued/optimistic sends. */
export function refreshOwnerAfterAcceptedPrompt(client: QueryClient) {
  client.setQueryData<User>(OWNER_PROFILE_QUERY_KEY, (user) => user?.user_chat_messages_limit === 1
    ? { ...user, user_chat_messages_remaining: 0 } : user);
  void client.invalidateQueries({ queryKey: OWNER_PROFILE_QUERY_KEY });
}
