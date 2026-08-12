"use client";

import { useQuery } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import Link from "next/link";

import { getWallet } from "@/lib/api/wallet";

function generationLabel(value: number): string {
  const lastTwo = value % 100;
  const last = value % 10;
  if (lastTwo >= 11 && lastTwo <= 14) return `${value} пробных сборок`;
  if (last === 1) return `${value} пробная сборка`;
  if (last >= 2 && last <= 4) return `${value} пробные сборки`;
  return `${value} пробных сборок`;
}

export function MaxTrialBadge() {
  const wallet = useQuery({
    queryKey: ["wallet"],
    queryFn: getWallet,
    staleTime: 10_000,
  });
  if (wallet.isPending || wallet.isError) return null;

  const left = wallet.data?.free_generations_left ?? 0;
  const unlimited = wallet.data?.unlimited_generations ?? false;
  if (unlimited) {
    return (
      <div
        className="hidden h-9 items-center gap-1.5 rounded-[8px] border border-accent/30 bg-accent-subtle px-2.5 text-[10px] font-semibold text-accent sm:inline-flex"
        title="Для аккаунта создателя включена безлимитная генерация"
        aria-label="Безлимитная генерация включена"
      >
        <Sparkles className="size-3.5" />
        Безлимит
      </div>
    );
  }
  return (
    <Link
      href="/billing/plan"
      className="hidden h-9 items-center gap-1.5 rounded-[8px] border border-accent/30 bg-accent-subtle px-2.5 text-[10px] font-semibold text-accent hover:border-accent/55 sm:inline-flex"
      title={
        left > 0
          ? "Посмотреть тарифы и доступные возможности"
          : "Пробные сборки закончились — выбрать тариф"
      }
    >
      <Sparkles className="size-3.5" />
      {left > 0 ? generationLabel(left) : "Выбрать тариф"}
    </Link>
  );
}
