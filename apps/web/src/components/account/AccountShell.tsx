import {
  Building2,
  CreditCard,
  LayoutGrid,
  LogOut,
  ScanSearch,
  Receipt,
  Shield,
  UserRound,
  WalletCards,
} from "lucide-react";
import Link from "next/link";

import { logoutAction } from "@/app/(auth)/actions";
import { BrandMark } from "@/components/marketing/BrandMark";
import type { AccountView } from "@/components/account/AccountControlCenter";
import { getMaxAdminAccessServer } from "@/lib/auth-mock";

const navigation = [
  ["profile", "/account", UserRound, "Профиль"],
  ["organization", "/account/organization", Building2, "Организация"],
  ["security", "/account/security", Shield, "Безопасность"],
  ["billing", "/billing", WalletCards, "Баланс"],
  ["transactions", "/billing/transactions", Receipt, "Операции"],
  ["plan", "/billing/plan", CreditCard, "Тариф"],
] as const;

const copy: Record<AccountView, { eyebrow: string; title: string; lead: string }> = {
  all: { eyebrow: "MAX Studio / Аккаунт", title: "Аккаунт", lead: "Профиль, владелец бизнеса, безопасность и биллинг." },
  profile: { eyebrow: "MAX Studio / Аккаунт", title: "Профиль", lead: "Основные данные аккаунта, экспорт информации и управление удалением." },
  organization: { eyebrow: "MAX Studio / Аккаунт", title: "Организация", lead: "Владелец MAX-приложений и реквизиты, которые используются во всех проектах." },
  security: { eyebrow: "MAX Studio / Аккаунт", title: "Безопасность", lead: "Активные сессии, устройства и отзыв доступа." },
  billing: { eyebrow: "MAX Studio / Биллинг", title: "Баланс и пополнение", lead: "Пакеты использования и безопасная оплата на стороне ЮKassa." },
  transactions: { eyebrow: "MAX Studio / Биллинг", title: "Операции", lead: "История платежей, начислений и статусов." },
  plan: { eyebrow: "MAX Studio / Биллинг", title: "Управление тарифом", lead: "Режим эксплуатации приложения и доступные лимиты." },
  admin: { eyebrow: "MAX Studio / Администрирование", title: "Админ-центр", lead: "Аккаунты, роли, подтверждение организаций и журнал административных изменений." },
};

export async function AccountShell({
  email,
  active,
  children,
}: {
  email: string;
  active: AccountView;
  children: React.ReactNode;
}) {
  const page = copy[active];
  const isAdmin = await getMaxAdminAccessServer();
  return (
    <div data-light-shell className="flex h-dvh min-h-0 bg-bg-base text-fg-primary">
      <aside className="hidden w-[220px] shrink-0 flex-col border-r border-border-default bg-surface-raised md:flex">
        <div className="flex h-16 items-center border-b border-border-default px-5"><BrandMark href="/max" /></div>
        <nav className="flex-1 p-3">
          <Link href="/max" className="mb-5 flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs text-fg-secondary transition-colors hover:bg-surface-base hover:text-fg-primary"><LayoutGrid className="size-4" />MAX Studio</Link>
          <p className="omnia-kicker px-3 text-fg-muted">Настройки</p>
          <div className="mt-2 space-y-1">
            {navigation.map(([id, href, Icon, label]) => (
              <Link key={id} href={href} className={`flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs transition-colors ${active === id ? "bg-surface-3 font-medium text-fg-primary" : "text-fg-secondary hover:bg-surface-base hover:text-fg-primary"}`}>
                <Icon className={`size-4 ${active === id ? "text-accent" : ""}`} />{label}
              </Link>
            ))}
            {isAdmin && (
              <Link href="/admin/max" className={`flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs transition-colors ${active === "admin" ? "bg-surface-3 font-medium text-fg-primary" : "text-fg-secondary hover:bg-surface-base hover:text-fg-primary"}`}>
                <ScanSearch className={`size-4 ${active === "admin" ? "text-accent" : ""}`} />
                Админ-центр
              </Link>
            )}
          </div>
        </nav>
        <div className="border-t border-border-default p-3">
          <p className="truncate px-3 text-xs font-medium">{email}</p>
          <form action={logoutAction}>
            <button className="mt-2 flex min-h-11 w-full items-center gap-3 rounded-[8px] px-3 py-2 text-xs text-fg-tertiary transition-colors hover:bg-surface-base hover:text-fg-primary"><LogOut className="size-3.5" />Выйти</button>
          </form>
        </div>
      </aside>
      <div className="min-w-0 flex-1 overflow-y-auto">
        <header className="flex h-16 items-center justify-between border-b border-border-default bg-surface-raised px-5 sm:px-8">
          <div className="md:hidden"><BrandMark href="/max" /></div>
          <p className="hidden text-xs text-fg-tertiary md:block">MAX Studio · Настройки</p>
          <span className="truncate text-xs text-fg-secondary">{email}</span>
        </header>
        <main className="px-5 py-8 sm:px-8 sm:py-10 lg:px-12">
          <div className="mx-auto max-w-[900px]">
            <header className="border-b border-border-default pb-8">
              <p className="omnia-kicker text-accent">{page.eyebrow}</p>
              <h1 className="mt-3 text-[36px] font-semibold tracking-[-.045em] sm:text-[46px]">{page.title}</h1>
              <p className="mt-3 max-w-[680px] text-sm leading-6 text-fg-secondary">{page.lead}</p>
            </header>
            <div className="mt-8">{children}</div>
          </div>
        </main>
      </div>
    </div>
  );
}
