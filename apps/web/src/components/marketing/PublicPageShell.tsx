import type { LucideIcon } from "lucide-react";
import { ArrowLeft, ArrowRight } from "lucide-react";
import Link from "next/link";

import { BrandMark } from "./BrandMark";

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
    <div data-product-shell className="min-h-svh bg-[#121519] text-white">
      <header className="border-b border-[#2b2d32] bg-[#191b20]">
        <div className="mx-auto flex h-16 max-w-[1320px] items-center justify-between px-5 sm:px-8">
          <BrandMark />
          <div className="flex items-center gap-3">
            <Link href="/" className="hidden items-center gap-2 text-xs text-[#9fa1b1] hover:text-white sm:inline-flex"><ArrowLeft className="size-4" />На главную</Link>
            <Link href="/max/register" className="omnia-button omnia-button-primary min-h-9 px-4 text-xs">Создать приложение</Link>
          </div>
        </div>
      </header>
      <main>
        <section data-graphite-shell className="bg-[#121519] px-5 py-20 text-white sm:px-8 lg:py-24">
          <div className="mx-auto max-w-[960px] text-center">
            <span className="omnia-kicker text-[#4f81f7]">{eyebrow}</span>
            <h1 className="mt-6 text-[42px] font-semibold leading-[1.03] tracking-[-.05em] sm:text-[58px]">{title}</h1>
            <p className="mx-auto mt-6 max-w-[720px] text-[16px] leading-7 text-white/50">{lead}</p>
          </div>
        </section>
        <div className="mx-auto max-w-[1180px] px-5 py-16 sm:px-8 lg:py-20">{children}</div>
      </main>
      <footer className="border-t border-[#2b2d32] bg-[#191b20] px-5 py-10 sm:px-8">
        <div className="mx-auto flex max-w-[1180px] flex-col gap-5 text-xs text-[#828491] sm:flex-row sm:items-center sm:justify-between">
          <span>© 2026 Omnia</span>
          <div className="flex flex-wrap gap-5"><Link href="/requisites">Реквизиты</Link><Link href="/legal/offer">Оферта</Link><Link href="/legal/refunds">Оплата и возвраты</Link><Link href="/legal/privacy">Конфиденциальность</Link><Link href="/security">Безопасность</Link></div>
        </div>
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
            <span className="grid size-11 place-items-center rounded-[8px] bg-[#2b2d32] text-[#4f81f7]"><Icon className="size-5" /></span>
            <h2 className="mt-8 text-lg font-semibold">{title}</h2>
            <p className="mt-2 text-sm leading-6 text-[#9fa1b1]">{text}</p>
            {href && <span className="mt-6 inline-flex items-center gap-2 text-xs font-semibold text-[#6a95fa]">Подробнее <ArrowRight className="size-4" /></span>}
          </>
        );
        const className = "rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-7 transition-colors hover:border-[#828491]";
        return href ? <Link key={title} href={href} className={className}>{body}</Link> : <article key={title} className={className}>{body}</article>;
      })}
    </div>
  );
}
