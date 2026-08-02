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
    <div data-light-shell className="min-h-svh bg-[#f5f3ee] text-[#171716] antialiased">
      <header className="border-b border-[#d8d4cb] bg-[#fcfbf7]">
        <div className="mx-auto flex h-16 max-w-[1200px] items-center justify-between px-5 sm:px-8">
          <BrandMark />
          <div className="flex items-center gap-4"><div className="hidden sm:block"><LocaleSwitcher /></div><Link href="/" className="text-xs text-[#6d6962] hover:text-[#171716]">{en ? "All products" : "Все направления"}</Link></div>
        </div>
      </header>
      <main className="mx-auto grid min-h-[calc(100svh-65px)] max-w-[1440px] lg:grid-cols-[minmax(0,1.1fr)_minmax(400px,.9fr)]">
        <section className="flex flex-col px-5 py-12 sm:px-8 lg:border-r lg:border-[#d8d4cb] lg:px-12 lg:py-16 xl:px-16">
          <div className="omnia-kicker flex items-center gap-2 text-accent"><Clock3 className="size-4" />{en ? "In development" : "В разработке"}</div>
          <div className="my-auto py-16">
            <span className="mb-8 grid size-12 place-items-center rounded-[8px] bg-[#ece8df] text-accent"><Icon className="size-6" strokeWidth={1.5} /></span>
            <h1 className="max-w-4xl text-[clamp(48px,6.5vw,92px)] font-semibold leading-[.9] tracking-[-.06em]">{title}</h1>
            <p className="mt-8 max-w-2xl text-lg leading-8 text-[#6d6962]">{description}</p>
          </div>
          <div className="flex flex-wrap gap-3 border-t border-[#d8d4cb] pt-6">
            <Link href="/" className="omnia-button omnia-button-secondary"><ArrowLeft className="size-4" />{en ? "Back to products" : "Вернуться к направлениям"}</Link>
            <Link href="/max/product" className="omnia-button omnia-button-primary">{en ? "Open available MAX Studio" : "Открыть доступную MAX Studio"}<ArrowRight className="size-4" /></Link>
          </div>
        </section>
        <aside data-graphite-shell className="flex flex-col bg-[#171716] p-6 text-white sm:p-8 lg:p-10">
          <p className="omnia-kicker border-b border-white/12 pb-5 text-white/30">{en ? "Planned scope" : "План направления"}</p>
          <div className="my-auto py-16">
            <p className="text-2xl font-medium tracking-[-.03em]">{en ? "What the dedicated studio will include" : "Что войдёт в отдельную студию"}</p>
            <div className="mt-10 border-t border-white/12">
              {capabilities.map((capability, index) => <div key={capability} className="grid grid-cols-[36px_1fr] gap-3 border-b border-white/12 py-5 text-sm"><span className="font-mono text-white/25">0{index + 1}</span><span className="text-white/70">{capability}</span></div>)}
            </div>
          </div>
          <p className="border-t border-white/12 pt-5 text-xs leading-5 text-white/35">{en ? "The page is unavailable for generation until its workflow and release checks are complete." : "Генерация недоступна, пока не готов отдельный сценарий сборки и проверки релиза."}</p>
        </aside>
      </main>
    </div>
  );
}
