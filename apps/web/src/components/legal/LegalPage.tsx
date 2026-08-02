import Link from "next/link";

import { BrandMark } from "@/components/marketing/BrandMark";

export function LegalPage({
  title,
  updated = "30 июля 2026",
  children,
}: {
  title: string;
  updated?: string;
  children: React.ReactNode;
}) {
  return (
    <main data-light-shell className="min-h-screen bg-[#f5f3ee] text-[#171716]">
      <header className="border-b border-[#d8d4cb] bg-[#fcfbf7]">
        <div className="mx-auto flex h-16 max-w-[1120px] items-center justify-between px-5 sm:px-8">
          <BrandMark />
          <Link href="/" className="text-xs text-[#6d6962] hover:text-[#171716]">На главную</Link>
        </div>
      </header>
      <article className="mx-auto max-w-[720px] px-5 py-16 sm:px-8 sm:py-24">
        <p className="omnia-kicker text-accent">11 / Legal</p>
        <h1 className="mt-4 text-[40px] font-semibold leading-[1.04] tracking-[-.045em] sm:text-[54px]">{title}</h1>
        <p className="mt-4 font-mono text-[10px] uppercase tracking-[.08em] text-[#8d887f]">Редакция от {updated}</p>
        <div className="legal-copy mt-12 space-y-9 border-t border-[#d8d4cb] pt-10 text-[15px] leading-7 text-[#6d6962]">
          {children}
        </div>
        <div className="mt-14 border-t border-[#d8d4cb] pt-7 text-sm text-[#8d887f]">
          Вопросы по документу:{" "}
          <a className="font-medium text-accent" href="mailto:support@lead-generator.ru">support@lead-generator.ru</a>
        </div>
      </article>
    </main>
  );
}

export function LegalSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <section><h2 className="mb-3 text-xl font-semibold text-[#171716]">{title}</h2>{children}</section>;
}
