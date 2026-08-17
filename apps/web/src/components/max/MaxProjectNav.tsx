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
}: {
  projectId: string;
  active: MaxProjectNavKey;
  showProgress?: boolean;
}) {
  const readiness = useQuery({
    queryKey: ["max-readiness", projectId],
    queryFn: () => getMaxReadiness(projectId),
    retry: false,
  });
  const journey = getMaxJourney(projectId, readiness.data?.items ?? []);

  return (
    <div>
      {showProgress && (
        <div className="mb-3 rounded-[8px] border border-[#d8d4cb] bg-[#f5f3ee] px-3 py-3">
          <div className="flex items-center justify-between gap-2 text-[10px] text-[#8d887f]">
            <span>Путь до запуска</span>
            <span className="tabular-nums">
              {readiness.isSuccess
                ? `${journey.completedCount} из ${journey.total}`
                : "Проверяем…"}
            </span>
          </div>
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-[#d8d4cb]">
            <div
              className="h-full rounded-full bg-[#f15a38] transition-[width]"
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
                  ? "bg-[#ece8df] font-medium text-[#171716]"
                  : "text-[#6d6962] hover:bg-[#f5f3ee] hover:text-[#171716]",
              )}
            >
              <Icon className={cn("size-4 shrink-0", selected && "text-[#f15a38]")} />
              <span className="min-w-0 flex-1 truncate">{item.label}</span>
              {readiness.isSuccess && stage?.status === "completed" && (
                <span className="grid size-4 shrink-0 place-items-center rounded-full bg-[#248a4b]/10 text-[#248a4b]">
                  <Check className="size-2.5" />
                </span>
              )}
              {readiness.isSuccess && stage?.status === "current" && (
                <span
                  className="size-2 shrink-0 rounded-full bg-[#f15a38]"
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
