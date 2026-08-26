"use client";

import { useQuery } from "@tanstack/react-query";
import { Check, Coins, RotateCcw } from "lucide-react";

import { getMaxUsage } from "@/lib/api/max-studio";
import type { Uuid } from "@/lib/api/types";

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

  return (
    <details className="group relative" data-testid="max-usage-breakdown">
      <summary className="flex h-9 cursor-pointer list-none items-center gap-2 rounded-[8px] border border-[#2b2d32] bg-[#191b20] px-2.5 text-[10px] font-semibold text-[#9fa1b1] hover:bg-[#121519] [&::-webkit-details-marker]:hidden">
        <Coins className="size-3.5 text-[#4f81f7]" />
        <span className="hidden sm:inline">Расход</span>
        <span>{usage.isLoading ? "…" : `${rub(current)} ₽`}</span>
      </summary>
      <section className="absolute right-0 top-11 z-[80] w-[340px] max-w-[calc(100vw-24px)] rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-4 shadow-[0_24px_70px_rgba(23,23,22,.16)]">
        <div className="flex items-start justify-between gap-4 border-b border-[#2b2d32] pb-3">
          <div>
            <p className="omnia-kicker text-[#828491]">Текущая сборка</p>
            <p className="mt-1 text-xl font-semibold tracking-[-.03em]">{rub(current)} ₽</p>
          </div>
          <div className="text-right text-[10px] text-[#828491]">
            <p>За всё время</p>
            <p className="mt-1 font-semibold text-[#9fa1b1]">{rub(usage.data?.total_cost_rub ?? 0)} ₽</p>
          </div>
        </div>

        {usage.isError ? (
          <p className="py-5 text-xs leading-5 text-[#828491]">Не удалось загрузить разбивку. Она обновится автоматически.</p>
        ) : (
          <div className="mt-3 space-y-2">
            {(usage.data?.stages ?? []).map((stage) => (
              <div key={stage.id} className="rounded-[9px] border border-[#2b2d32] bg-[#191b20] p-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="flex min-w-0 items-center gap-2 text-xs font-medium">
                    {stage.id === "template" ? <Check className="size-3.5 text-success-fg" /> : <Coins className="size-3.5 text-[#4f81f7]" />}
                    <span className="truncate">{stage.label}</span>
                  </span>
                  <strong className="shrink-0 text-xs">{rub(stage.cost_rub)} ₽</strong>
                </div>
                <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[9px] text-[#828491]">
                  <span>{stage.calls ? `${stage.calls} выз.` : "без модели"}</span>
                  {stage.cache_read_tokens > 0 && <span>из кеша {stage.cache_read_tokens.toLocaleString("ru-RU")}</span>}
                  {stage.retries > 0 && <span className="inline-flex items-center gap-1"><RotateCcw className="size-2.5" /> повторов {stage.retries}</span>}
                </p>
              </div>
            ))}
          </div>
        )}
        <p className="mt-3 text-[9px] leading-4 text-[#828491]">Данные берутся из фактического gateway-ledger и обновляются во время работы.</p>
      </section>
    </details>
  );
}
