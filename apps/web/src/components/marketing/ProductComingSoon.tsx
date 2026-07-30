import type { ComponentType } from "react";
import Link from "next/link";
import { ArrowLeft, ArrowRight, Clock3 } from "lucide-react";

import { BrandMark } from "./BrandMark";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";

export function ProductComingSoon({
  title,
  description,
  capabilities,
  Icon,
  locale,
}: {
  title: string;
  description: string;
  capabilities: string[];
  Icon: ComponentType<{ className?: string; strokeWidth?: number }>;
  locale: string;
}) {
  const en = locale === "en";

  return (
    <div
      data-marketing
      className="min-h-svh bg-[#f2f0e9] text-[#171815] antialiased"
    >
      <header className="border-b border-[#171815]">
        <div className="mx-auto flex h-16 max-w-[1500px] items-center justify-between px-5 sm:px-8 lg:px-12 xl:px-16">
          <BrandMark />
          <div className="flex items-center gap-4">
            <div className="hidden sm:block">
              <LocaleSwitcher />
            </div>
            <Link href="/" className="text-sm hover:opacity-55">
              {en ? "All products" : "Все направления"}
            </Link>
          </div>
        </div>
      </header>

      <main className="mx-auto grid min-h-[calc(100svh-65px)] max-w-[1500px] lg:grid-cols-[minmax(0,1.35fr)_minmax(360px,0.65fr)]">
        <section className="flex flex-col px-5 py-12 sm:px-8 lg:border-r lg:border-[#171815] lg:px-12 lg:py-16 xl:px-16">
          <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.14em] text-[#5d5f59]">
            <Clock3 className="h-4 w-4" />
            {en ? "In development" : "В разработке"}
          </div>
          <div className="my-auto py-20">
            <Icon className="mb-10 h-12 w-12" strokeWidth={1.35} />
            <h1 className="max-w-5xl text-[clamp(56px,8vw,118px)] font-semibold leading-[0.86] tracking-[-0.065em]">
              {title}
            </h1>
            <p className="mt-10 max-w-2xl text-lg leading-7 text-[#555751] sm:text-xl sm:leading-8">
              {description}
            </p>
          </div>
          <div className="flex flex-wrap gap-3 border-t border-[#171815] pt-6">
            <Link href="/" className="marketing-button marketing-button-secondary">
              <ArrowLeft className="h-4 w-4" />
              {en ? "Back to products" : "Вернуться к направлениям"}
            </Link>
            <Link href="/max" className="marketing-button marketing-button-primary">
              {en ? "Open available MAX Studio" : "Открыть доступную MAX Studio"}
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </section>

        <aside className="flex flex-col bg-[#1a1b18] p-6 text-[#f2f0e9] sm:p-8 lg:p-10">
          <div className="border-b border-white/20 pb-5 font-mono text-[11px] uppercase tracking-[0.14em] text-white/45">
            {en ? "Planned scope" : "План направления"}
          </div>
          <div className="my-auto py-16">
            <p className="text-2xl font-medium tracking-[-0.03em]">
              {en ? "What the dedicated studio will include" : "Что войдёт в отдельную студию"}
            </p>
            <div className="mt-10 border-t border-white/20">
              {capabilities.map((capability, index) => (
                <div
                  key={capability}
                  className="grid grid-cols-[36px_1fr] gap-3 border-b border-white/20 py-5 text-sm"
                >
                  <span className="font-mono text-white/30">0{index + 1}</span>
                  <span>{capability}</span>
                </div>
              ))}
            </div>
          </div>
          <p className="border-t border-white/20 pt-5 text-xs leading-5 text-white/45">
            {en
              ? "The page is intentionally unavailable for generation until its workflow and release checks are complete."
              : "Генерация здесь намеренно недоступна, пока не готов отдельный сценарий сборки и проверки релиза."}
          </p>
        </aside>
      </main>
    </div>
  );
}
