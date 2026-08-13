"use client";

import { useQuery } from "@tanstack/react-query";
import { Images, Loader2 } from "lucide-react";

import { MaxHowToDialog } from "@/components/max/MaxHowToDialog";
import { getMaxReadiness } from "@/lib/api/max-studio";
import { getMaxHowToGuide } from "@/lib/max-how-to";
import { getMaxJourney } from "@/lib/max-journey";
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
  const guide = getMaxHowToGuide(readiness.isError ? "demo" : stage?.id);
  const guideStatus = readiness.isError
    ? "Наглядная помощь"
    : stage
      ? `Следующий шаг · ${stage.position} из ${journey.total}`
      : "Финальная проверка";

  if (readiness.isLoading) {
    return (
      <section
        aria-live="polite"
        className={cn(
          "mt-6 flex min-h-24 items-center justify-center rounded-[14px] border border-[#d8d4cb] bg-[#fcfbf7] text-xs text-[#6d6962]",
          className,
        )}
      >
        <Loader2 className="mr-2 size-4 animate-spin text-accent" />
        Готовим наглядную инструкцию…
      </section>
    );
  }

  return (
    <section
      data-testid="max-how-to-banner"
      className={cn(
        "mt-6 flex flex-col gap-5 overflow-hidden rounded-[14px] border border-accent/35 bg-[#fcfbf7] p-5 shadow-[0_12px_36px_var(--color-accent-subtle)] sm:flex-row sm:items-center sm:justify-between sm:p-6",
        className,
      )}
    >
      <div className="flex min-w-0 gap-4">
        <span className="grid size-11 shrink-0 place-items-center rounded-[12px] bg-accent text-accent-fg shadow-[0_8px_22px_var(--color-accent-subtle)]">
          <Images className="size-5" />
        </span>
        <div className="min-w-0">
          <p className="text-[10px] font-semibold uppercase tracking-[.14em] text-accent">
            {guideStatus}
          </p>
          <h2 className="mt-1 text-lg font-semibold leading-6">{guide.title}</h2>
          <p className="mt-1.5 text-xs leading-5 text-[#6d6962]">
            4 действия с подробным изображением экрана и отметками, куда нажимать.
          </p>
        </div>
      </div>
      <MaxHowToDialog
        guide={guide}
        actionHref={stage?.href}
        actionLabel={stage?.actionLabel}
        triggerClassName="w-full shrink-0 sm:w-auto"
      />
    </section>
  );
}
