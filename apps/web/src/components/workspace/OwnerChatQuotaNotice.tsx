import Link from "next/link";
import { FREE_CHAT_UPGRADE_MESSAGE } from "@/lib/owner-chat-quota";

export function OwnerChatQuotaNotice({ limited, remaining }: { limited: boolean; remaining: number | null }) {
  if (!limited) return null;
  return <div role="status" aria-live="polite" className="rounded-md border border-border-default p-3 text-xs leading-5 text-fg-secondary">
    <p>Free · Осталось сообщений: {remaining ?? "…"} из 1 на весь аккаунт.</p>
    <p>{remaining === 0 ? FREE_CHAT_UPGRADE_MESSAGE : "Соберите все пожелания в одном описании. Оно создаст ваше приложение; дальнейшие запросы доступны на Pro и Business."}</p>
    {remaining === 0 && <Link href="/billing/plan" className="font-medium text-accent underline">Выбрать тариф</Link>}
  </div>;
}
