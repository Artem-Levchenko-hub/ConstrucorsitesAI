"use client";

import { useQuery } from "@tanstack/react-query";
import { Wallet } from "lucide-react";
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

  if (isPending) return <Skeleton className="h-9 w-28 rounded-xl" />;

  const balance = data?.balance_rub ?? 0;
  // Three-tier visual state — colour does the job a label would, so no
  // "Баланс:" prefix needed. The wallet icon + ₽ formatting carry the meaning.
  const low = balance < 10;
  const warn = balance >= 10 && balance < 30;

  return (
    <div
      className={cn(
        "inline-flex items-center gap-2 h-9 px-3 rounded-xl border text-sm font-mono font-medium backdrop-blur-md transition-colors",
        low
          ? "border-danger/40 bg-danger/10 text-danger shadow-[0_0_0_1px_rgb(255_107_138/0.2),0_0_20px_-6px_rgb(255_107_138/0.45)]"
          : warn
            ? "border-warning/40 bg-warning/10 text-warning"
            : "border-success/30 bg-success/[0.08] text-fg-primary shadow-glow-success",
      )}
      aria-label={`Баланс ${formatRub(balance)}`}
    >
      <Wallet
        className={cn(
          "h-4 w-4",
          low ? "text-danger" : warn ? "text-warning" : "text-success",
        )}
      />
      {formatRub(balance)}
    </div>
  );
}
