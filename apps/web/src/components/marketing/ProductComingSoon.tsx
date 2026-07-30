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
      className="min-h-svh bg-[#10110f] text-white antialiased"
    >
      <header className="border-b border-white/[0.1] bg-[#10110f]">
        <div className="mx-auto flex h-16 max-w-[1500px] items-center justify-between px-5 sm:px-8 lg:px-12 xl:px-16">
          <BrandMark inverse />
          <div className="flex items-center gap-4">
            <div className="hidden sm:block">
              <LocaleSwitcher />
            </div>
            <Link href="/" className="text-sm text-white/50 hover:text-white">
              {en ? "All products" : "Все направления"}
            </Link>
          </div>
        </div>
      </header>

      <main className="mx-auto grid min-h-[calc(100svh-65px)] max-w-[1440px] lg:grid-cols-[minmax(0,1.15fr)_minmax(400px,0.85fr)]">
        <section className="flex flex-col px-5 py-12 sm:px-8 lg:border-r lg:border-white/[0.1] lg:px-12 lg:py-16 xl:px-16">
          <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.14em] text-[#7897f4]">
            <Clock3 className="h-4 w-4" />
            {en ? "In development" : "В разработке"}
          </div>
          <div className="my-auto py-16">
            <span className="mb-8 flex h-12 w-12 items-center justify-center border border-white/[0.12] bg-white/[0.03] text-[#7897f4]">
              <Icon className="h-6 w-6" strokeWidth={1.5} />
            </span>
            <h1 className="max-w-4xl text-[clamp(48px,6.5vw,92px)] font-semibold leading-[0.9] tracking-[-0.06em]">
              {title}
            </h1>
            <p className="mt-8 max-w-2xl text-lg leading-7 text-white/45 sm:text-xl sm:leading-8">
              {description}
            </p>
          </div>
          <div className="flex flex-wrap gap-3 border-t border-white/[0.1] pt-6">
            <Link
              href="/"
              className="inline-flex h-11 items-center gap-2 border border-white/[0.14] px-4 text-sm font-medium text-white/65 hover:border-white/30 hover:text-white"
            >
              <ArrowLeft className="h-4 w-4" />
              {en ? "Back to products" : "Вернуться к направлениям"}
            </Link>
            <Link
              href="/max"
              className="inline-flex h-11 items-center gap-2 bg-[#315bd7] px-4 text-sm font-semibold text-white hover:bg-[#4169df]"
            >
              {en ? "Open available MAX Studio" : "Открыть доступную MAX Studio"}
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </section>

        <aside className="flex flex-col bg-[#151613] p-6 text-white sm:p-8 lg:p-10">
          <div className="border-b border-white/[0.1] pb-5 font-mono text-[11px] uppercase tracking-[0.14em] text-white/35">
            {en ? "Planned scope" : "План направления"}
          </div>
          <div className="my-auto py-16">
            <p className="text-2xl font-medium tracking-[-0.03em]">
              {en ? "What the dedicated studio will include" : "Что войдёт в отдельную студию"}
            </p>
            <div className="mt-10 border-t border-white/[0.1]">
              {capabilities.map((capability, index) => (
                <div
                  key={capability}
                  className="grid grid-cols-[36px_1fr] gap-3 border-b border-white/[0.1] py-5 text-sm"
                >
                  <span className="font-mono text-white/30">0{index + 1}</span>
                  <span>{capability}</span>
                </div>
              ))}
            </div>
          </div>
          <p className="border-t border-white/[0.1] pt-5 text-xs leading-5 text-white/40">
            {en
              ? "The page is intentionally unavailable for generation until its workflow and release checks are complete."
              : "Генерация здесь намеренно недоступна, пока не готов отдельный сценарий сборки и проверки релиза."}
          </p>
        </aside>
      </main>
    </div>
  );
}
