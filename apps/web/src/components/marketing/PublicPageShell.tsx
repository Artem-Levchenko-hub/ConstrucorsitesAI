import type { LucideIcon } from "lucide-react";
import { ArrowLeft, ArrowRight } from "lucide-react";
import Link from "next/link";

import { BrandMark } from "./BrandMark";
import "@/components/max/max-studio.css";
import "./max-public.css";

export function PublicPageShell({
  eyebrow,
  title,
  lead,
  children,
}: {
  eyebrow: string;
  title: string;
  lead: string;
  children: React.ReactNode;
}) {
  return (
    <div data-max-studio className="max-public">
      <header className="max-public-header">
        <div className="max-public-header__inner">
          <div className="max-public-header__brand"><BrandMark /><span>MAX Studio</span></div>
          <div className="max-public-header__actions">
            <Link href="/" className="max-public-link hidden items-center gap-2 sm:inline-flex"><ArrowLeft className="size-4" />На главную</Link>
            <Link href="/max/register" className="max-public-button max-public-button--primary">Создать приложение</Link>
          </div>
        </div>
      </header>
      <main>
        <section className="max-public-hero">
          <div className="max-public-wrap">
            <span className="max-public-kicker">{eyebrow}</span>
            <h1>{title}</h1>
            <p>{lead}</p>
          </div>
        </section>
        <div className="max-public-content">{children}</div>
      </main>
      <footer className="max-public-footer">
        <div><span>© 2026 Omnia · MAX Studio</span><nav><Link href="/requisites">Реквизиты</Link><Link href="/legal/offer">Оферта</Link><Link href="/legal/refunds">Оплата и возвраты</Link><Link href="/legal/privacy">Конфиденциальность</Link><Link href="/security">Безопасность</Link></nav></div>
      </footer>
    </div>
  );
}

export function InfoGrid({
  items,
}: {
  items: Array<{ Icon: LucideIcon; title: string; text: string; href?: string }>;
}) {
  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
      {items.map(({ Icon, title, text, href }) => {
        const body = (
          <>
            <span className="grid size-11 place-items-center rounded-[8px] bg-surface-3 text-accent"><Icon className="size-5" /></span>
            <h2 className="mt-8 text-lg font-semibold">{title}</h2>
            <p className="mt-2 text-sm leading-6 text-fg-secondary">{text}</p>
            {href && <span className="mt-6 inline-flex items-center gap-2 text-xs font-semibold text-accent-secondary">Подробнее <ArrowRight className="size-4" /></span>}
          </>
        );
        const className = "rounded-[12px] border border-border-default bg-surface p-7 transition-colors hover:border-border-strong";
        return href ? <Link key={title} href={href} className={className}>{body}</Link> : <article key={title} className={className}>{body}</article>;
      })}
    </div>
  );
}
