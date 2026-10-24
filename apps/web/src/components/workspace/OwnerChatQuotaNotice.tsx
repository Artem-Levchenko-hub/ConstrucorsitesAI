import Link from "next/link";
import { FREE_CHAT_UPGRADE_MESSAGE } from "@/lib/owner-chat-quota";

export function OwnerChatQuotaNotice({ limited, remaining, initials, edits, initialOnly = false }: {
  limited: boolean; remaining: number | null; initials?: number | null; edits?: number | null; initialOnly?: boolean;
}) {
  if (!limited) return null;
  if (typeof initials === "number" && typeof edits === "number") {
    const exhausted = initials === 0 && (initialOnly || edits === 0);
    return <div role="status" aria-live="polite" className="rounded-md border border-border-default p-3 text-xs leading-5 text-fg-secondary">
      <p>Генераций: {initials} · Правок: {edits}</p>
      {exhausted && <>
        <p>{initialOnly ? "Для нового приложения нужна доступная генерация и свободное место по тарифу. Пакет только для правок не добавляет генераций." : FREE_CHAT_UPGRADE_MESSAGE}</p>
        <Link href={initialOnly ? "/billing/plan" : "/billing"} className="font-medium text-accent underline">{initialOnly ? "Выбрать тариф" : "Пополнить лимит"}</Link>
      </>}
    </div>;
  }
  return <div role="status" aria-live="polite" className="rounded-md border border-border-default p-3 text-xs leading-5 text-fg-secondary">
    <p>Free · Осталось сообщений: {remaining ?? "…"} из 1 на весь аккаунт.</p>
    <p>{remaining === 0 ? FREE_CHAT_UPGRADE_MESSAGE : "Соберите все пожелания в одном описании. Оно создаст ваше приложение; дальнейшие запросы доступны на Pro и Business."}</p>
    {remaining === 0 && <Link href="/billing/plan" className="font-medium text-accent underline">Выбрать тариф</Link>}
  </div>;
}
