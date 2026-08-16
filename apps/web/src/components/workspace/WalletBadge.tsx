"use client";

import { useQuery } from "@tanstack/react-query";
import { Sparkles, Wallet } from "lucide-react";
import { getWallet } from "@/lib/api/wallet";
import { formatRub } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function WalletBadge() {
  const { data, isPending } = useQuery({
    queryKey: ["wallet"],
    queryFn: getWallet,
    staleTime: 10_000,
  });

  if (isPending) return <Skeleton className="h-8 w-24" />;

  const balance = data?.balance_rub ?? 0;
  const freeLeft = data?.free_generations_left ?? 0;
  const tone =
    balance < 10
      ? "text-danger"
      : balance < 30
        ? "text-warning"
        : "text-fg-primary";

  return (
    <div className="inline-flex items-center gap-2">
      {freeLeft > 0 && (
        <div
          className="inline-flex items-center gap-1.5 h-8 px-3 rounded-md border border-accent/40 bg-accent/10 text-accent text-xs font-medium"
          title="Omnia сама подбирает модели под задачу. Первые генерации — бесплатно."
          aria-label={`Осталось ${freeLeft} бесплатных генераций`}
        >
          <Sparkles className="h-3.5 w-3.5" />
          {freeLeft} бесплатно
        </div>
      )}
      <div
        className={cn(
          "inline-flex items-center gap-2 h-8 px-3 rounded-md border border-border-default bg-surface-raised text-sm font-mono",
          tone,
        )}
        aria-label={`Баланс ${formatRub(balance)}`}
      >
        <Wallet className="h-3.5 w-3.5" />
        {formatRub(balance)}
      </div>
    </div>
  );
}
