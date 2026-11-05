"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import {
  Bot,
  ChartNoAxesCombined,
  Check,
  FileCheck2,
  PackageOpen,
  Rocket,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";

import { getMaxReadiness } from "@/lib/api/max-studio";
import { getMaxJourney, type MaxJourneyStageId } from "@/lib/max-journey";
import { cn } from "@/lib/utils";

export type MaxProjectNavKey =
  | "manage"
  | "editor"
  | "app"
  | "catalog"
  | "integrations"
  | "bot"
  | "publish"
  | "dashboard";

// Постоянные адреса разделов: статистика не скрывается за пунктом запуска.
const navigation: Array<{
  key: MaxProjectNavKey;
  /** Ключи разделов, которые подсвечивают этот пункт. */
  keys: MaxProjectNavKey[];
  label: string;
  suffix: string;
  icon: LucideIcon;
  stageId?: MaxJourneyStageId;
}> = [
  { key: "manage", keys: ["manage"], label: "Управление", suffix: "/manage", icon: SlidersHorizontal },
  {
    key: "app",
    keys: ["app"],
    label: "Данные приложения",
    suffix: "/settings?tab=app",
    icon: FileCheck2,
  },
  {
    key: "catalog", keys: ["catalog"], label: "Каталог", suffix: "/catalog", icon: PackageOpen,
  },
  {
    key: "bot",
    keys: ["bot", "integrations"],
    label: "Бот и подключения",
    suffix: "?panel=max",
    icon: Bot,
    stageId: "bot",
  },
  {
    key: "publish",
    keys: ["publish"],
    label: "Запуск и адрес",
    suffix: "?panel=publish",
    icon: Rocket,
    stageId: "publish",
  },
  { key: "dashboard", keys: ["dashboard"], label: "Статистика", suffix: "/dashboard", icon: ChartNoAxesCombined },
];

export function MaxProjectNav({
  projectId,
  active,
  showProgress = true,
  variant = "sidebar",
}: {
  projectId: string;
  active: MaxProjectNavKey;
  showProgress?: boolean;
  variant?: "sidebar" | "mobile";
}) {
  const sidebar = variant === "sidebar";
  const nav = useRef<HTMLElement>(null);
  const readiness = useQuery({
    queryKey: ["max-readiness", projectId],
    queryFn: () => getMaxReadiness(projectId),
    retry: false,
    enabled: sidebar,
  });
  const journey = sidebar ? getMaxJourney(projectId, readiness.data?.items ?? []) : null;
  useEffect(() => {
    if (!sidebar) nav.current?.querySelector('[aria-current="page"]')?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [active, sidebar]);

  return (
    <>
      {showProgress && journey && (
        <div className="mb-3 rounded-[8px] border border-border-default bg-surface-base px-3 py-3">
          <div className="flex items-center justify-between gap-2 text-[11px] text-fg-tertiary">
            <span>Путь до запуска</span>
            <span className="tabular-nums">
              {readiness.isSuccess
                ? `${journey.completedCount} из ${journey.total}`
                : "Проверяем…"}
            </span>
          </div>
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-surface-overlay">
            <div
              className="h-full rounded-full bg-accent transition-[width]"
              style={{ width: `${readiness.isSuccess ? journey.progress : 0}%` }}
            />
          </div>
        </div>
      )}

      <nav ref={nav} className={sidebar ? "space-y-1" : "max-project-nav"} aria-label="Разделы проекта MAX">
        {navigation.map((item) => {
          const selected = item.keys.includes(active);
          const stage = item.stageId
            ? journey?.stages.find((candidate) => candidate.id === item.stageId)
            : undefined;
          const Icon = item.icon;

          return (
            <Link
              key={item.key}
              href={`/max/${projectId}${item.suffix}`}
              aria-current={selected ? "page" : undefined}
              className={sidebar ? cn(
                "flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs transition-colors",
                selected
                  ? "bg-surface-overlay font-medium text-fg-primary"
                  : "text-fg-secondary hover:bg-surface-base hover:text-fg-primary",
              ) : "max-project-nav-link"}
            >
              <Icon aria-hidden="true" className={cn("size-4 shrink-0", sidebar && selected && "text-accent")} />
              <span className={sidebar ? "min-w-0 flex-1 truncate" : undefined}>{item.label}</span>
              {readiness.isSuccess && stage?.status === "completed" && (
                <span className="grid size-4 shrink-0 place-items-center rounded-full bg-success/10 text-success-fg">
                  <Check className="size-2.5" />
                </span>
              )}
              {readiness.isSuccess && stage?.status === "current" && (
                <span
                  className="size-2 shrink-0 rounded-full bg-accent"
                  aria-label="Текущий этап"
                />
              )}
            </Link>
          );
        })}
      </nav>
    </>
  );
}
