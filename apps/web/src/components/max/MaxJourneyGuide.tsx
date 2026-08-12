"use client";

import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  Bot,
  CheckCircle2,
  Loader2,
  Sparkles,
  UserRound,
} from "lucide-react";
import Link from "next/link";

import { getMaxReadiness } from "@/lib/api/max-studio";
import { getMaxJourney } from "@/lib/max-journey";
import { getMaxNativeGuidance } from "@/lib/max-native-guidance";
import { cn } from "@/lib/utils";

export function MaxJourneyGuide({
  projectId,
  className,
}: {
  projectId: string;
  className?: string;
}) {
  const readiness = useQuery({
    queryKey: ["max-readiness", projectId],
    queryFn: () => getMaxReadiness(projectId),
    retry: false,
  });
  const journey = getMaxJourney(projectId, readiness.data?.items ?? []);
  const stage = readiness.isSuccess ? journey.currentStage : undefined;
  const guidance = getMaxNativeGuidance(stage?.id);

  if (readiness.isLoading) {
    return (
      <section
        aria-live="polite"
        className={cn(
          "mt-6 flex items-center gap-3 rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] px-5 py-4 text-xs text-[#6d6962]",
          className,
        )}
      >
        <Loader2 className="size-4 animate-spin text-accent" />
        Определяем следующее действие…
      </section>
    );
  }

  if (readiness.isError) {
    return (
      <section
        className={cn(
          "mt-6 rounded-[12px] border border-[#e8a127]/35 bg-[#fffaf0] px-5 py-4 text-xs leading-5 text-[#6d6962]",
          className,
        )}
      >
        Не удалось загрузить путь запуска. Обновите страницу: сохранённые данные
        проекта не потеряны.
      </section>
    );
  }

  return (
    <section
      aria-labelledby="max-native-guide-title"
      data-testid="max-native-guide"
      className={cn(
        "mt-6 overflow-hidden rounded-[12px] border border-accent/25 bg-[#fcfbf7]",
        className,
      )}
    >
      <div className="flex flex-col gap-4 border-b border-[#e7e3da] bg-accent/[.045] px-5 py-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[.14em] text-accent">
            {stage
              ? `Следующий шаг · ${stage.position} из ${journey.total}`
              : "Запуск настроен"}
          </p>
          <h2 id="max-native-guide-title" className="mt-1 text-lg font-semibold">
            {guidance.title}
          </h2>
        </div>
        {stage && (
          <Link
            href={stage.href}
            className="omnia-button omnia-button-primary min-h-11 shrink-0 px-4 text-xs"
          >
            {stage.actionLabel}
            <ArrowRight className="size-3.5" />
          </Link>
        )}
      </div>

      <div className="grid divide-y divide-[#e7e3da] lg:grid-cols-3 lg:divide-x lg:divide-y-0">
        {[
          {
            Icon: UserRound,
            label: "Что сделать сейчас",
            copy: guidance.userAction,
          },
          {
            Icon: Sparkles,
            label: "Что сделает Omnia",
            copy: guidance.omniaAction,
          },
          {
            Icon: Bot,
            label: guidance.maxRequiredNow ? "Сейчас в MAX" : "Что насчёт MAX",
            copy: guidance.maxAction,
          },
        ].map(({ Icon, label, copy }) => (
          <div key={label} className="p-5">
            <div className="flex items-center gap-2 text-xs font-semibold">
              <Icon className="size-3.5 text-accent" />
              {label}
            </div>
            <p className="mt-2 text-xs leading-5 text-[#6d6962]">{copy}</p>
          </div>
        ))}
      </div>

      <div className="flex items-start gap-2 border-t border-[#e7e3da] px-5 py-3 text-[11px] leading-4 text-[#476451]">
        <CheckCircle2 className="mt-px size-3.5 shrink-0 text-[#248a4b]" />
        <span>
          <strong>Готово, когда:</strong> {guidance.successSignal}
        </span>
      </div>
    </section>
  );
}
