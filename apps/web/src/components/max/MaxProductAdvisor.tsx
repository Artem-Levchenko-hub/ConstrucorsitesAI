"use client";

import { ArrowDownLeft, Loader2 } from "lucide-react";

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
      aria-label="Рекомендации для приложения"
      className="max-chat-advice-items"
    >
      <div>
        {visibleItems.map((item) => {
          const applying = applyingId === item.id;
          return (
            <div
              key={item.id}
              data-advice-id={item.id}
              className="max-chat-advice-item"
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="max-chat-advice-kind">{item.kind === "feature" ? "Новая возможность" : "Улучшить"}</span>
                </div>
                <h3>{item.title}</h3>
                <p>{item.benefit}</p>
              </div>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={applying}
                aria-busy={applying}
                onClick={() => void onApply(item)}
                className="min-h-11 max-chat-advice-insert"
              >
                {applying ? (
                  <>
                    <Loader2 className="animate-spin" aria-hidden="true" />
                    Вставляем…
                  </>
                ) : (
                  <><ArrowDownLeft aria-hidden="true" /> Вставить в чат</>
                )}
              </Button>
            </div>
          );
        })}
      </div>
    </section>
  );
}
