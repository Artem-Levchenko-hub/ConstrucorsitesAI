"use client";

import {
  ArrowRight,
  CheckCircle2,
  Images,
  MousePointerClick,
  PlayCircle,
} from "lucide-react";
import Link from "next/link";
import type { ReactElement } from "react";

import {
  AppSettingsVisual,
  BuilderVisual,
  DashboardVisual,
  IntegrationVisual,
  LaunchVisual,
  OwnerAccessVisual,
  PartnerVisual,
} from "@/components/max/guide/GuideVisuals";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import type { MaxHowToGuide, MaxHowToVisual } from "@/lib/max-how-to";
import { cn } from "@/lib/utils";

function GuideVisual({ visual }: { visual: MaxHowToVisual }) {
  if (visual === "builder") return <BuilderVisual />;
  if (visual === "app-settings") return <AppSettingsVisual />;
  if (visual === "owner-access") return <OwnerAccessVisual />;
  if (visual === "max-bot") return <IntegrationVisual />;
  if (visual === "publish") return <LaunchVisual />;
  if (visual === "partner") return <PartnerVisual />;
  return <DashboardVisual />;
}

export function MaxHowToDialog({
  guide,
  actionHref,
  actionLabel = "Перейти к этому шагу",
  children,
  triggerClassName,
}: {
  guide: MaxHowToGuide;
  actionHref?: string;
  actionLabel?: string;
  children?: ReactElement;
  triggerClassName?: string;
}) {
  return (
    <Dialog>
      <DialogTrigger asChild>
        {children ?? (
          <button
            type="button"
            className={cn(
              "inline-flex min-h-11 items-center justify-center gap-2 rounded-[8px] bg-accent px-4 text-xs font-semibold text-accent-fg shadow-[0_8px_24px_var(--color-accent-subtle)] transition hover:bg-accent-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40",
              triggerClassName,
            )}
            data-testid="max-how-to-open"
          >
            <PlayCircle className="size-4" />
            Показать, как сделать
          </button>
        )}
      </DialogTrigger>

      <DialogContent
        data-light-shell
        data-testid="max-how-to-dialog"
        className="flex max-h-[94dvh] max-w-[1180px] flex-col gap-0 overflow-hidden rounded-[16px] border-[#d8d4cb] bg-[#f5f3ee] p-0 text-[#171716] shadow-[0_30px_120px_rgba(0,0,0,.42)]"
      >
        <header className="shrink-0 border-b border-[#d8d4cb] bg-[#fcfbf7] px-5 py-5 pr-16 sm:px-8 sm:py-6 sm:pr-20">
          <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[.15em] text-accent">
            <Images className="size-3.5" />
            {guide.eyebrow} · пошагово с изображением
          </div>
          <DialogTitle className="mt-2 max-w-[850px] text-2xl font-semibold tracking-[-.035em] text-[#171716] sm:text-[34px]">
            {guide.title}
          </DialogTitle>
          <DialogDescription className="mt-3 max-w-[850px] text-sm leading-6 text-[#6d6962]">
            {guide.intro}
          </DialogDescription>
        </header>

        <div className="max-studio-scroll min-h-0 flex-1 overflow-y-auto">
          <div className="px-4 py-5 sm:px-8 sm:py-8">
            <div className="overflow-hidden rounded-[14px] ring-1 ring-[#d8d4cb]">
              <GuideVisual visual={guide.visual} />
            </div>

            <section aria-label="Пошаговая инструкция" className="mt-7">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="omnia-kicker text-accent">Сделайте по порядку</p>
                  <h3 className="mt-2 text-xl font-semibold">Четыре действия до результата</h3>
                </div>
                <span className="hidden items-center gap-2 rounded-full border border-[#d8d4cb] bg-[#fcfbf7] px-3 py-2 text-[10px] font-medium text-[#6d6962] sm:inline-flex">
                  <MousePointerClick className="size-3.5 text-accent" />
                  Номера на изображении показывают, куда нажать
                </span>
              </div>

              <ol className="mt-5 grid gap-3 lg:grid-cols-2">
                {guide.steps.map((step, index) => (
                  <li
                    key={step.title}
                    className="grid grid-cols-[38px_minmax(0,1fr)] gap-3 rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-4 sm:p-5"
                  >
                    <span className="grid size-9 place-items-center rounded-full bg-accent text-xs font-bold text-accent-fg shadow-[0_6px_16px_var(--color-accent-subtle)]">
                      {index + 1}
                    </span>
                    <div>
                      <p className="text-sm font-semibold">{step.title}</p>
                      <p className="mt-1.5 text-xs leading-5 text-[#6d6962]">{step.text}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </section>

            <div className="mt-5 grid gap-3 md:grid-cols-[1fr_1fr]">
              <aside className="rounded-[12px] border border-accent/30 bg-accent/[.06] p-4 text-xs leading-5 text-[#4f4a72] sm:p-5">
                <p className="font-semibold text-[#171716]">Что делать в MAX</p>
                <p className="mt-1.5">{guide.maxNote}</p>
              </aside>
              <aside className="flex gap-3 rounded-[12px] border border-[#248a4b]/30 bg-[#248a4b]/[.06] p-4 text-xs leading-5 text-[#476451] sm:p-5">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-[#248a4b]" />
                <p>
                  <strong>Готово, когда:</strong> {guide.readyWhen}
                </p>
              </aside>
            </div>
          </div>
        </div>

        <footer className="flex shrink-0 flex-col-reverse gap-2 border-t border-[#d8d4cb] bg-[#fcfbf7] px-5 py-4 sm:flex-row sm:items-center sm:justify-end sm:px-8">
          <DialogClose asChild>
            <button type="button" className="min-h-11 rounded-[8px] px-4 text-xs font-medium text-[#6d6962] hover:bg-[#f5f3ee]">
              Закрыть инструкцию
            </button>
          </DialogClose>
          {actionHref && (
            <DialogClose asChild>
              <Link href={actionHref} className="omnia-button omnia-button-primary min-h-11 px-5 text-xs">
                {actionLabel}
                <ArrowRight className="size-3.5" />
              </Link>
            </DialogClose>
          )}
        </footer>
      </DialogContent>
    </Dialog>
  );
}
