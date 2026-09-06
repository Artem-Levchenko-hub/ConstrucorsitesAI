import Link from "next/link";

import { BrandMark } from "@/components/marketing/BrandMark";
import "@/components/max/max-studio.css";
import "@/components/marketing/max-public.css";

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
    <main data-max-studio className="max-public min-h-screen">
      <header className="max-public-header">
        <div className="mx-auto flex h-16 max-w-[1120px] items-center justify-between px-5 sm:px-8">
          <BrandMark />
          <Link href="/" className="max-public-link">На главную</Link>
        </div>
      </header>
      <article className="mx-auto max-w-[720px] px-5 py-16 sm:px-8 sm:py-24">
        <p className="max-public-kicker">Документы</p>
        <h1 className="mt-4 text-[40px] font-semibold leading-[1.04] tracking-[-.045em] sm:text-[54px]">{title}</h1>
        <p className="mt-4 font-mono text-[10px] uppercase tracking-[.08em] text-fg-tertiary">Редакция от {updated}</p>
        <div className="legal-copy mt-12 space-y-9 border-t border-border-default pt-10 text-[15px] leading-7 text-fg-secondary">
          {children}
        </div>
        <div className="mt-14 border-t border-border-default pt-7 text-sm text-fg-tertiary">
          Вопросы по документу:{" "}
          <a className="font-medium text-accent-secondary" href="mailto:support@lead-generator.ru">support@lead-generator.ru</a>
        </div>
      </article>
    </main>
  );
}

export function LegalSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <section><h2 className="mb-3 text-xl font-semibold text-fg-primary">{title}</h2>{children}</section>;
}
