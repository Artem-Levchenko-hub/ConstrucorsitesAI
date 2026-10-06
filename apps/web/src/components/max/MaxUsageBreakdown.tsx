"use client";

import { useQuery } from "@tanstack/react-query";
import { Check, Coins, RotateCcw } from "lucide-react";

import { getMaxUsage } from "@/lib/api/max-studio";
import type { Uuid } from "@/lib/api/types";
import { CostSource } from "@/components/ui/CostSource";

function rub(value: number): string {
  return new Intl.NumberFormat("ru-RU", {
    minimumFractionDigits: value < 10 ? 2 : 0,
    maximumFractionDigits: 2,
  }).format(value);
}

export function MaxUsageBreakdown({ projectId }: { projectId: Uuid }) {
  const usage = useQuery({
    queryKey: ["max-usage", projectId],
    queryFn: () => getMaxUsage(projectId),
    refetchInterval: 5_000,
    retry: false,
  });
  const current = usage.data?.run_cost_rub ?? 0;
  const total = usage.data?.total_cost_rub ?? 0;
  const currentLabel = usage.isError ? "Недоступно" : usage.isLoading ? "…" : `${rub(current)} ₽`;
  const totalLabel = usage.isError ? "Недоступно" : usage.isLoading ? "…" : `${rub(total)} ₽`;

  return (
    <details className="group relative" data-testid="max-usage-breakdown">
      <summary className="flex h-9 cursor-pointer list-none items-center gap-2 rounded-[6px] border border-border-default bg-surface-raised px-2.5 text-[11px] font-semibold text-fg-secondary hover:bg-surface-base [&::-webkit-details-marker]:hidden">
        <Coins className="size-3.5 text-accent" />
        {!usage.isError && <span className="hidden sm:inline">Расход</span>}
        <span>{usage.isError ? "Данные о расходе недоступны" : currentLabel}</span>
      </summary>
      <section className="absolute right-0 top-11 z-[80] max-h-[70dvh] w-[340px] max-w-[calc(100vw-24px)] overflow-y-auto overscroll-contain rounded-[10px] border border-border-default bg-surface-raised p-4 shadow-[0_24px_70px_rgba(23,23,22,.16)]">
        <div className="flex items-start justify-between gap-4 border-b border-border-default pb-3">
          <div className="min-w-0 flex-1">
            <p className="omnia-kicker text-fg-tertiary">Текущая сборка</p>
            <p className="mt-1 text-xl font-semibold tracking-[-.03em]">{currentLabel}</p>
            {!usage.isError && usage.data && <CostSource label="Текущая сборка" breakdown={usage.data.run_cost_breakdown}
              calls={usage.data.run_id === null ? 0 : usage.data.stages.reduce((sum, stage) => sum + stage.calls, 0)} costRub={current} />}
          </div>
          <div className="min-w-0 flex-1 text-right text-[11px] text-fg-tertiary">
            <p>За всё время</p>
            <p className="mt-1 font-semibold text-fg-secondary">{totalLabel}</p>
            {!usage.isError && usage.data && <CostSource label="За всё время" breakdown={usage.data.total_cost_breakdown} costRub={total} />}
          </div>
        </div>

        {usage.isError ? (
          <p className="py-5 text-xs leading-5 text-fg-tertiary">Данные о расходе недоступны. Разбивка обновится автоматически.</p>
        ) : (
          <div className="mt-3 space-y-2">
            {(usage.data?.stages ?? []).map((stage) => (
              <div key={stage.id} className="rounded-[10px] border border-border-default bg-surface-raised p-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="flex min-w-0 items-center gap-2 text-xs font-medium">
                    {stage.id === "template" ? <Check className="size-3.5 text-success-fg" /> : <Coins className="size-3.5 text-accent" />}
                    <span className="truncate">{stage.label}</span>
                  </span>
                  <strong className="shrink-0 text-xs">{rub(stage.cost_rub)} ₽</strong>
                </div>
                <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-fg-tertiary">
                  <span>{stage.calls ? `${stage.calls} выз.` : "без модели"}</span>
                  {stage.cache_read_tokens > 0 && <span>из кеша {stage.cache_read_tokens.toLocaleString("ru-RU")}</span>}
                  {stage.retries > 0 && <span className="inline-flex items-center gap-1"><RotateCcw className="size-2.5" /> повторов {stage.retries}</span>}
                </p>
                <CostSource label={stage.label} breakdown={stage.cost_breakdown} calls={stage.calls} costRub={stage.cost_rub} />
              </div>
            ))}
          </div>
        )}
        <p className="mt-3 text-[11px] leading-4 text-fg-tertiary">Показаны расходы вашего аккаунта. Оценочные суммы рассчитаны по токенам и тарифам; списания с баланса доступны в разделе оплаты.</p>
      </section>
    </details>
  );
}
