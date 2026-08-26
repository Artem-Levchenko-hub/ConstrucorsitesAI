"use client";

import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  Bot,
  Check,
  FileCheck2,
  Plug,
  Rocket,
  Smartphone,
} from "lucide-react";
import Link from "next/link";

import { getMaxReadiness } from "@/lib/api/max-studio";
import { getMaxJourney, type MaxJourneyStageId } from "@/lib/max-journey";
import { cn } from "@/lib/utils";

export type MaxProjectNavKey =
  | "editor"
  | "app"
  | "integrations"
  | "bot"
  | "publish"
  | "dashboard";

const navigation: Array<{
  key: MaxProjectNavKey;
  label: string;
  suffix: string;
  icon: typeof Smartphone;
  stageId?: MaxJourneyStageId;
}> = [
  { key: "editor", label: "Редактор", suffix: "", icon: Smartphone, stageId: "build" },
  {
    key: "app",
    label: "Данные приложения",
    suffix: "/settings?tab=app",
    icon: FileCheck2,
    stageId: "app",
  },
  { key: "integrations", label: "Интеграции", suffix: "/integrations", icon: Plug },
  {
    key: "bot",
    label: "MAX-бот",
    suffix: "/settings?tab=bot",
    icon: Bot,
    stageId: "max",
  },
  {
    key: "publish",
    label: "Публикация",
    suffix: "/publish",
    icon: Rocket,
    stageId: "publish",
  },
  {
    key: "dashboard",
    label: "После запуска",
    suffix: "/dashboard",
    icon: BarChart3,
    stageId: "verify",
  },
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
  const readiness = useQuery({
    queryKey: ["max-readiness", projectId],
    queryFn: () => getMaxReadiness(projectId),
    retry: false,
  });
  const journey = getMaxJourney(projectId, readiness.data?.items ?? []);

  if (variant === "mobile") {
    return (
      <nav className="flex min-w-max gap-1 px-3 py-2" aria-label="Разделы проекта MAX">
        {navigation.map((item) => {
          const selected = item.key === active;
          const Icon = item.icon;
          return (
            <Link
              key={item.key}
              href={`/max/${projectId}${item.suffix}`}
              aria-current={selected ? "page" : undefined}
              className={cn(
                "inline-flex h-10 items-center gap-2 rounded-[8px] px-3 text-xs",
                selected
                  ? "bg-[#121519] font-medium text-white"
                  : "text-[#9fa1b1] hover:bg-[#121519]",
              )}
            >
              <Icon className="size-3.5" />
              {item.label}
            </Link>
          );
        })}
      </nav>
    );
  }

  return (
    <div>
      {showProgress && (
        <div className="mb-3 rounded-[8px] border border-[#2b2d32] bg-[#121519] px-3 py-3">
          <div className="flex items-center justify-between gap-2 text-[10px] text-[#828491]">
            <span>Путь до запуска</span>
            <span className="tabular-nums">
              {readiness.isSuccess
                ? `${journey.completedCount} из ${journey.total}`
                : "Проверяем…"}
            </span>
          </div>
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-[#2b2d32]">
            <div
              className="h-full rounded-full bg-[#4f81f7] transition-[width]"
              style={{ width: `${readiness.isSuccess ? journey.progress : 0}%` }}
            />
          </div>
        </div>
      )}

      <nav className="space-y-1" aria-label="Разделы проекта MAX">
        {navigation.map((item) => {
          const selected = item.key === active;
          const stage = item.stageId
            ? journey.stages.find((candidate) => candidate.id === item.stageId)
            : undefined;
          const Icon = item.icon;

          return (
            <Link
              key={item.key}
              href={`/max/${projectId}${item.suffix}`}
              aria-current={selected ? "page" : undefined}
              className={cn(
                "flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs transition-colors",
                selected
                  ? "bg-[#2b2d32] font-medium text-white"
                  : "text-[#9fa1b1] hover:bg-[#121519] hover:text-white",
              )}
            >
              <Icon className={cn("size-4 shrink-0", selected && "text-[#4f81f7]")} />
              <span className="min-w-0 flex-1 truncate">{item.label}</span>
              {readiness.isSuccess && stage?.status === "completed" && (
                <span className="grid size-4 shrink-0 place-items-center rounded-full bg-[#248a4b]/10 text-success-fg">
                  <Check className="size-2.5" />
                </span>
              )}
              {readiness.isSuccess && stage?.status === "current" && (
                <span
                  className="size-2 shrink-0 rounded-full bg-[#4f81f7]"
                  aria-label="Текущий этап"
                />
              )}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
