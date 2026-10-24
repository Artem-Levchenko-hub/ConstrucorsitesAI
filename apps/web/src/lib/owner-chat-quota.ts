import type { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./api/client";
import type { User } from "./api/types";

export const OWNER_PROFILE_QUERY_KEY = ["owner-profile"] as const;
export const FREE_CHAT_UPGRADE_MESSAGE = "Доступный лимит исчерпан. Для дальнейшей работы выберите тариф или пакет правок. Готовое приложение можно настроить и опубликовать.";

function available(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

export function ownerChatQuota(user?: User) {
  const initialValue = user?.generation_initials_available;
  const editValue = user?.generation_credit_units_available;
  const metered = available(initialValue) && available(editValue);
  const initials = metered ? initialValue : null;
  const edits = metered ? editValue : null;
  const limited = metered || user?.user_chat_messages_limit === 1;
  const legacyExhausted = limited && user?.user_chat_messages_remaining === 0;
  return { limited, remaining: limited ? user?.user_chat_messages_remaining ?? null : null,
    initials, edits,
    exhausted: metered ? initials === 0 && edits === 0 : legacyExhausted,
    initialExhausted: metered ? initials === 0 : legacyExhausted,
  };
}

export function isFreeChatRefusal(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 402 && error.code === "entitlement_exceeded"
    && error.details?.entitlement === "free_chat_messages";
}

/** Called only after the server accepts a prompt, never on queued/optimistic sends. */
export function refreshOwnerAfterAcceptedPrompt(client: QueryClient) {
  client.setQueryData<User>(OWNER_PROFILE_QUERY_KEY, (user) => !available(user?.generation_initials_available) && user?.user_chat_messages_limit === 1
    ? { ...user, user_chat_messages_remaining: 0 } : user);
  refreshOwnerAllowances(client);
}

/** Re-read spendable allowances after a confirmed payment or durable run verdict. */
export function refreshOwnerAllowances(client: QueryClient) {
  void client.invalidateQueries({ queryKey: OWNER_PROFILE_QUERY_KEY });
  void client.invalidateQueries({ queryKey: ["billing-credits"] });
}
