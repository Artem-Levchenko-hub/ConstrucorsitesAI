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
import "@/components/max/max-studio.css";
import "./account.css";

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
    <div data-max-studio className="account-shell">
      <header className="account-topbar">
        <BrandMark href="/max" />
        <Link href="/max" className="account-back">
          <LayoutGrid className="size-4" />К приложениям</Link>
        <div className="account-user">
          <span>{email}</span>
          <form action={logoutAction}>
            <button aria-label="Выйти из аккаунта">
              <LogOut className="size-4" />
            </button>
          </form>
        </div>
      </header>
      <div className="account-layout">
        <aside className="account-sidebar">
          <p>Аккаунт</p>
          <nav aria-label="Разделы аккаунта">{navigation.map(([id, href, Icon, label]) => <Link key={id} href={href} aria-current={active === id ? "page" : undefined}>
            <Icon className="size-4" />{label}</Link>)}</nav>
          {isAdmin && <Link className="account-admin" href="/admin/max">
            <ScanSearch className="size-4" />Админ-центр</Link>}</aside>
        <main
          data-product-shell={active === "admin" ? "" : undefined}
          className="account-main"
        >
          <header className="account-heading">
            <h1>{page.title}</h1>
            <p>{page.lead}</p>
          </header>
          {children}</main>
      </div>
    </div>
  );
}
