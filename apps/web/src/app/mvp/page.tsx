import type { Metadata } from "next";
import {
  ArrowLeft,
  Check,
  Circle,
  Clock3,
  ExternalLink,
  LoaderCircle,
} from "lucide-react";
import Link from "next/link";

import { BrandMark } from "@/components/marketing/BrandMark";
import {
  mvpChecklist,
  mvpChecklistUpdatedAt,
  type MvpChecklistStatus,
} from "@/data/mvp-checklist";

export const metadata: Metadata = {
  title: "Путь до рабочего MVP",
  description: "Публичный чек-лист готовности Omnia: от регистрации до запуска приложения внутри MAX.",
  alternates: { canonical: "/mvp" },
};

const statusMeta: Record<
  MvpChecklistStatus,
  { label: string; icon: typeof Check; className: string }
> = {
  done: {
    label: "Готово",
    icon: Check,
    className: "border-[#248a4b] bg-[#248a4b] text-white",
  },
  in_progress: {
    label: "В работе",
    icon: LoaderCircle,
    className: "border-accent bg-accent/10 text-accent",
  },
  todo: {
    label: "Дальше",
    icon: Circle,
    className: "border-[#aaa59b] text-[#aaa59b]",
  },
  external: {
    label: "Нужен внешний доступ",
    icon: Clock3,
    className: "border-[#b98618] bg-[#e8c547]/15 text-[#8a650e]",
  },
};

export default function MvpChecklistPage() {
  const items = mvpChecklist.flatMap((section) => section.items);
  const completed = items.filter((item) => item.status === "done").length;
  const progress = Math.round((completed / items.length) * 100);

  return (
    <main className="min-h-screen bg-[#f5f3ee] text-[#171716]">
      <header className="border-b border-[#d8d4cb] bg-[#fcfbf7]">
        <div className="mx-auto flex h-18 max-w-[1200px] items-center justify-between px-5 sm:px-8">
          <div className="[&>a]:min-h-11">
            <BrandMark />
          </div>
          <div className="flex items-center gap-4 text-xs font-medium sm:text-sm">
            <Link
              className="inline-flex min-h-11 items-center text-[#6d6962] hover:text-[#171716]"
              href="/otchet/"
            >
              Полный отчёт
            </Link>
            <Link
              className="inline-flex min-h-11 items-center gap-2 text-accent"
              href="/"
            >
              <ArrowLeft className="size-4" />
              <span className="hidden sm:inline">На главную</span>
            </Link>
          </div>
        </div>
      </header>

      <section data-graphite-shell className="border-b border-[#d8d4cb] bg-[#171716] text-white">
        <div className="mx-auto grid max-w-[1200px] gap-10 px-5 py-16 sm:px-8 sm:py-20 lg:grid-cols-[1fr_320px] lg:items-end">
          <div>
            <p className="omnia-kicker text-accent">Публичный трекер продукта</p>
            <h1 className="mt-5 max-w-[820px] text-[42px] font-semibold leading-[1] tracking-[-.05em] sm:text-[64px]">
              Путь до полностью рабочего MVP
            </h1>
            <p className="mt-6 max-w-[720px] text-[15px] leading-7 text-white/55 sm:text-base">
              Чек-лист обновляется вместе с кодом. Галочка появляется только после проверки
              и доставки изменения в production.
            </p>
          </div>
          <aside className="rounded-[12px] border border-white/15 bg-white/[.04] p-5">
            <div className="flex items-end justify-between gap-4">
              <div>
                <p className="omnia-kicker text-white/35">Общий прогресс</p>
                <p className="mt-3 text-4xl font-semibold tabular-nums">{progress}%</p>
              </div>
              <p className="pb-1 text-sm text-white/45">
                {completed} из {items.length}
              </p>
            </div>
            <div className="mt-5 h-2 overflow-hidden rounded-full bg-white/10">
              <div
                className="h-full rounded-full bg-accent-on-dark"
                style={{ width: `${progress}%` }}
              />
            </div>
            <p className="mt-4 text-xs text-white/35">Обновлено: {mvpChecklistUpdatedAt}</p>
          </aside>
        </div>
      </section>

      <div className="mx-auto max-w-[1200px] px-5 py-12 sm:px-8 sm:py-16">
        <div className="space-y-6">
          {mvpChecklist.map((section) => {
            const sectionDone = section.items.filter((item) => item.status === "done").length;
            return (
              <section
                key={section.id}
                id={section.id}
                className="overflow-hidden rounded-[14px] border border-[#d8d4cb] bg-[#fcfbf7]"
              >
                <div className="grid gap-5 border-b border-[#d8d4cb] p-5 sm:p-7 lg:grid-cols-[90px_1fr_auto] lg:items-start">
                  <p className="font-mono text-sm text-accent">{section.number}</p>
                  <div>
                    <h2 className="text-2xl font-semibold tracking-[-.03em]">
                      {section.title}
                    </h2>
                    <p className="mt-2 max-w-[720px] text-sm leading-6 text-[#6d6962]">
                      {section.description}
                    </p>
                  </div>
                  <p className="text-xs font-medium tabular-nums text-[#8d887f]">
                    {sectionDone} / {section.items.length}
                  </p>
                </div>

                <ol className="divide-y divide-[#e7e3da]">
                  {section.items.map((item) => {
                    const meta = statusMeta[item.status];
                    const Icon = meta.icon;
                    return (
                      <li
                        key={item.id}
                        className="grid gap-3 px-5 py-5 sm:grid-cols-[32px_1fr_auto] sm:items-start sm:px-7"
                      >
                        <span
                          className={`mt-0.5 grid size-6 place-items-center rounded-full border ${meta.className}`}
                        >
                          <Icon
                            className={`size-3.5 ${
                              item.status === "in_progress" ? "animate-spin" : ""
                            }`}
                          />
                        </span>
                        <div>
                          <h3 className="text-sm font-semibold">{item.title}</h3>
                          <p className="mt-1 text-xs leading-5 text-[#6d6962]">
                            {item.detail}
                          </p>
                        </div>
                        <div className="pl-9 text-left sm:pl-0 sm:text-right">
                          <p className="text-[10px] font-semibold uppercase tracking-[.12em] text-[#8d887f]">
                            {meta.label}
                          </p>
                          {item.completedAt && (
                            <p className="mt-1 text-[11px] text-[#248a4b]">
                              {item.completedAt}
                            </p>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ol>
              </section>
            );
          })}
        </div>

        <section data-graphite-shell className="mt-8 rounded-[14px] bg-[#171716] p-6 text-white sm:p-8">
          <p className="omnia-kicker text-accent">Критерий финиша</p>
          <h2 className="mt-3 text-2xl font-semibold tracking-[-.03em]">
            Новый пользователь проходит путь без ручного исправления данных
          </h2>
          <p className="mt-3 max-w-[800px] text-sm leading-6 text-white/55">
            Регистрация, оплата, генерация, публикация, запуск из MAX, работа двух
            изолированных пользователей и отмена продления должны пройти одним чистым
            сценарием. После этого чек-лист MVP считается закрытым.
          </p>
          <Link
            href="/max/register"
            className="mt-6 inline-flex min-h-11 items-center gap-2 rounded-[8px] bg-accent px-5 text-sm font-semibold text-white hover:bg-accent-hover"
          >
            Открыть MAX Studio
            <ExternalLink className="size-4" />
          </Link>
        </section>
      </div>
    </main>
  );
}
