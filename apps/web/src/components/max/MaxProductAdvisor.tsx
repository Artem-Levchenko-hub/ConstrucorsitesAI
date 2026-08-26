"use client";

import { Lightbulb, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ProductAdviceItem } from "@/lib/api/product-advice";

export function MaxProductAdvisor({
  items,
  applyingId,
  onApply,
}: {
  items: ProductAdviceItem[];
  applyingId: string | null;
  onApply: (item: ProductAdviceItem) => void | Promise<void>;
}) {
  const visibleItems = items.slice(0, 3);
  if (visibleItems.length === 0) return null;

  return (
    <section
      data-testid="max-product-advisor"
      aria-labelledby="max-product-advisor-heading"
      className="mx-3 mb-3 overflow-hidden rounded-xl border border-[#2b2d32] bg-[#191b20] text-white shadow-sm"
    >
      <header className="flex items-center gap-2.5 border-b border-[#25272b] px-3.5 py-3">
        <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-[#4f81f7]/10 text-[#6a95fa]">
          <Lightbulb className="size-3.5" aria-hidden="true" />
        </span>
        <div>
          <h2
            id="max-product-advisor-heading"
            className="text-xs font-semibold"
          >
            Что улучшить дальше
          </h2>
          <p className="mt-0.5 text-[10px] leading-4 text-[#828491]">
            Подсказки по вашему приложению
          </p>
        </div>
      </header>

      <div className="divide-y divide-[#25272b]">
        {visibleItems.map((item) => {
          const applying = applyingId === item.id;
          return (
            <div
              key={item.id}
              data-advice-id={item.id}
              className="grid gap-2.5 px-3.5 py-3 sm:grid-cols-[1fr_auto] sm:items-center"
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded-full bg-[#121519] px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[0.08em] text-[#9fa1b1]">
                    {item.kind === "feature" ? "Добавить" : "Улучшить"}
                  </span>
                  <h3 className="text-xs font-semibold">{item.title}</h3>
                </div>
                <p className="mt-1 text-[11px] leading-4 text-[#9fa1b1]">
                  {item.benefit}
                </p>
              </div>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={applying}
                aria-busy={applying}
                onClick={() => void onApply(item)}
                className="min-h-11 w-full border-[#2b2d32] bg-[#191b20] px-3 text-[11px] text-[#6a95fa] hover:border-[#4f81f7] hover:bg-[#1c1e23] sm:w-auto"
              >
                {applying ? (
                  <>
                    <Loader2 className="animate-spin" aria-hidden="true" />
                    Добавляем…
                  </>
                ) : (
                  "Добавить"
                )}
              </Button>
            </div>
          );
        })}
      </div>
    </section>
  );
}
