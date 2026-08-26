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
    <main data-product-shell className="min-h-screen bg-[#121519] text-white">
      <header className="border-b border-[#2b2d32] bg-[#191b20]">
        <div className="mx-auto flex h-16 max-w-[1120px] items-center justify-between px-5 sm:px-8">
          <BrandMark />
          <Link href="/" className="text-xs text-[#9fa1b1] hover:text-white">На главную</Link>
        </div>
      </header>
      <article className="mx-auto max-w-[720px] px-5 py-16 sm:px-8 sm:py-24">
        <p className="omnia-kicker text-[#4f81f7]">11 / Legal</p>
        <h1 className="mt-4 text-[40px] font-semibold leading-[1.04] tracking-[-.045em] sm:text-[54px]">{title}</h1>
        <p className="mt-4 font-mono text-[10px] uppercase tracking-[.08em] text-[#828491]">Редакция от {updated}</p>
        <div className="legal-copy mt-12 space-y-9 border-t border-[#2b2d32] pt-10 text-[15px] leading-7 text-[#9fa1b1]">
          {children}
        </div>
        <div className="mt-14 border-t border-[#2b2d32] pt-7 text-sm text-[#828491]">
          Вопросы по документу:{" "}
          <a className="font-medium text-[#6a95fa]" href="mailto:support@lead-generator.ru">support@lead-generator.ru</a>
        </div>
      </article>
    </main>
  );
}

export function LegalSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <section><h2 className="mb-3 text-xl font-semibold text-white">{title}</h2>{children}</section>;
}
