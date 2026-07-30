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
    <div className="min-h-svh bg-[#080a10] text-white">
      <header className="h-20 border-b border-[#111626]">
        <div className="mx-auto flex h-full max-w-[1440px] items-center justify-between px-5 sm:px-8 lg:px-16">
          <BrandMark inverse label="MaxStudio" />
          <div className="flex items-center gap-3">
            <Link href="/" className="hidden items-center gap-2 text-[13px] text-[#94a3b8] hover:text-white sm:inline-flex">
              <ArrowLeft className="h-4 w-4" />
              На главную
            </Link>
            <Link href="/max/register" className="rounded-lg bg-gradient-to-r from-[#3b82f6] to-[#8b5cf6] px-5 py-2.5 text-[13px] font-semibold">
              Создать приложение
            </Link>
          </div>
        </div>
      </header>
      <main>
        <section className="border-b border-[#111626] px-5 py-20 sm:px-8 lg:px-16 lg:py-24">
          <div className="mx-auto max-w-[960px] text-center">
            <span className="rounded-full border border-[#1d4f91] bg-[#0d1729] px-3 py-1.5 text-[11px] font-semibold uppercase text-[#3b82f6]">
              {eyebrow}
            </span>
            <h1 className="font-display mt-6 text-[42px] font-bold leading-[1.05] tracking-[-0.04em] sm:text-[56px]">{title}</h1>
            <p className="mx-auto mt-6 max-w-[720px] text-[17px] leading-7 text-[#94a3b8]">{lead}</p>
          </div>
        </section>
        <div className="mx-auto max-w-[1180px] px-5 py-16 sm:px-8 lg:px-16 lg:py-20">{children}</div>
      </main>
      <footer className="border-t border-[#111626] px-5 py-12 sm:px-8 lg:px-16">
        <div className="mx-auto flex max-w-[1312px] flex-col gap-5 text-[12px] text-[#60708d] sm:flex-row sm:items-center sm:justify-between">
          <span>© 2026 MaxStudio by Omnia</span>
          <div className="flex flex-wrap gap-5">
            <Link href="/legal/privacy">Конфиденциальность</Link>
            <Link href="/legal/terms">Условия</Link>
            <Link href="/security">Безопасность</Link>
          </div>
        </div>
      </footer>
    </div>
  );
}

export function InfoGrid({
  items,
}: {
  items: Array<{
    Icon: LucideIcon;
    title: string;
    text: string;
    href?: string;
  }>;
}) {
  return (
    <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
      {items.map(({ Icon, title, text, href }) => {
        const content = (
          <>
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-[#211b4a] text-[#8b5cf6]">
              <Icon className="h-5 w-5" />
            </span>
            <h2 className="mt-5 text-[18px] font-semibold">{title}</h2>
            <p className="mt-2 text-[14px] leading-6 text-[#94a3b8]">{text}</p>
            {href && (
              <span className="mt-6 inline-flex items-center gap-2 text-[13px] font-semibold text-[#7ba7ff]">
                Подробнее
                <ArrowRight className="h-4 w-4" />
              </span>
            )}
          </>
        );
        return href ? (
          <Link key={title} href={href} className="rounded-2xl border border-[#202946] bg-[#13172a] p-7 transition-colors hover:border-[#3b82f6]/60">
            {content}
          </Link>
        ) : (
          <article key={title} className="rounded-2xl border border-[#202946] bg-[#13172a] p-7">
            {content}
          </article>
        );
      })}
    </div>
  );
}
