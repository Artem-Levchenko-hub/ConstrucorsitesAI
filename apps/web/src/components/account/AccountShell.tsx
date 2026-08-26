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
  all: { eyebrow: "10 / Account", title: "Аккаунт", lead: "Профиль, владелец бизнеса, безопасность и биллинг." },
  profile: { eyebrow: "10 / Account", title: "Профиль", lead: "Основные данные аккаунта, экспорт информации и управление удалением." },
  organization: { eyebrow: "10 / Account", title: "Организация", lead: "Владелец MAX-приложений и реквизиты, которые используются во всех проектах." },
  security: { eyebrow: "10 / Account", title: "Безопасность", lead: "Активные сессии, устройства и отзыв доступа." },
  billing: { eyebrow: "10 / Billing", title: "Баланс и пополнение", lead: "Пакеты использования и безопасная оплата на стороне ЮKassa." },
  transactions: { eyebrow: "10 / Billing", title: "Операции", lead: "История платежей, начислений и статусов." },
  plan: { eyebrow: "10 / Billing", title: "Управление тарифом", lead: "Режим эксплуатации приложения и доступные лимиты." },
  admin: { eyebrow: "Admin / Control", title: "Админ-центр", lead: "Аккаунты, роли, подтверждение организаций и журнал административных изменений." },
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
    <div data-product-shell className="flex h-dvh min-h-0 bg-[#121519] text-white">
      <aside data-graphite-shell className="hidden w-[220px] shrink-0 flex-col bg-[#121519] text-white md:flex">
        <div className="flex h-16 items-center border-b border-white/12 px-5"><BrandMark inverse href="/max" /></div>
        <nav className="flex-1 p-3">
          <Link href="/max" className="mb-5 flex h-9 items-center gap-3 rounded-[8px] px-3 text-xs text-white/55 hover:bg-white/[.06] hover:text-white"><LayoutGrid className="size-4" />MAX Studio</Link>
          <p className="omnia-kicker px-3 text-white/25">Настройки</p>
          <div className="mt-2 space-y-1">
            {navigation.map(([id, href, Icon, label]) => (
              <Link key={id} href={href} className={`flex h-9 items-center gap-3 rounded-[8px] px-3 text-xs ${active === id ? "bg-white/10 font-medium text-white" : "text-white/50 hover:bg-white/[.06] hover:text-white"}`}>
                <Icon className={`size-4 ${active === id ? "text-[#4f81f7]" : ""}`} />{label}
              </Link>
            ))}
            {isAdmin && (
              <Link href="/admin/max" className={`flex h-9 items-center gap-3 rounded-[8px] px-3 text-xs ${active === "admin" ? "bg-white/10 font-medium text-white" : "text-white/50 hover:bg-white/[.06] hover:text-white"}`}>
                <ScanSearch className={`size-4 ${active === "admin" ? "text-[#4f81f7]" : ""}`} />
                Админ-центр
              </Link>
            )}
          </div>
        </nav>
        <div className="border-t border-white/12 p-3">
          <p className="truncate px-3 text-xs font-medium">{email}</p>
          <form action={logoutAction}>
            <button className="mt-3 flex w-full items-center gap-3 rounded-[8px] px-3 py-2 text-xs text-white/45 hover:bg-white/[.06] hover:text-white"><LogOut className="size-3.5" />Выйти</button>
          </form>
        </div>
      </aside>
      <div className="min-w-0 flex-1 overflow-y-auto">
        <header className="flex h-16 items-center justify-between border-b border-[#2b2d32] bg-[#191b20] px-5 sm:px-8">
          <div className="md:hidden"><BrandMark href="/max" /></div>
          <p className="hidden text-xs text-[#828491] md:block">Настройки Omnia</p>
          <span className="truncate text-xs text-[#9fa1b1]">{email}</span>
        </header>
        <main className="px-5 py-8 sm:px-8 sm:py-10 lg:px-12">
          <div className="mx-auto max-w-[900px]">
            <header className="border-b border-[#2b2d32] pb-8">
              <p className="omnia-kicker text-[#4f81f7]">{page.eyebrow}</p>
              <h1 className="mt-3 text-[36px] font-semibold tracking-[-.045em] sm:text-[46px]">{page.title}</h1>
              <p className="mt-3 max-w-[680px] text-sm leading-6 text-[#9fa1b1]">{page.lead}</p>
            </header>
            <div className="mt-8">{children}</div>
          </div>
        </main>
      </div>
    </div>
  );
}
