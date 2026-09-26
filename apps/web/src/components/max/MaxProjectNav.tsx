"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Bot,
  Check,
  FileCheck2,
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

/**
 * Четыре пункта вместо шести.
 *
 * «MAX» — это название мессенджера, а не раздел; «После запуска» — момент
 * времени, а не место. Владелец не понимал, где искать адрес приложения и куда
 * вставлять токен бота. Пункты названы делом и объединены по делу: бот и прочие
 * подключения — одно, подготовка запуска и адрес после него — другое. Старые
 * ключи разделов сохранены: страницы и ссылки на них продолжают работать.
 */
const navigation: Array<{
  key: MaxProjectNavKey;
  /** Ключи разделов, которые подсвечивают этот пункт. */
  keys: MaxProjectNavKey[];
  label: string;
  suffix: string;
  /** Куда ведёт пункт, когда приложение уже опубликовано. */
  publishedSuffix?: string;
  icon: typeof Smartphone;
  stageId?: MaxJourneyStageId;
}> = [
  { key: "editor", keys: ["editor"], label: "Сборка", suffix: "", icon: Smartphone, stageId: "build" },
  {
    key: "app",
    keys: ["app"],
    label: "Данные приложения",
    suffix: "?data=details",
    icon: FileCheck2,
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
    keys: ["publish", "dashboard"],
    label: "Запуск и адрес",
    suffix: "?panel=publish",
    publishedSuffix: "/dashboard",
    icon: Rocket,
    stageId: "publish",
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
  // После публикации «Запуск» перестаёт быть подготовкой и становится адресом
  // и историей — ведём туда, где владелец их и ищет.
  const published = readiness.isSuccess && journey.currentStage === undefined;
  const hrefFor = (item: (typeof navigation)[number]) =>
    `/max/${projectId}${published && item.publishedSuffix ? item.publishedSuffix : item.suffix}`;

  if (variant === "mobile") {
    return (
      <nav className="flex min-w-max gap-1 px-3 py-2" aria-label="Разделы проекта MAX">
        {navigation.map((item) => {
          const selected = item.keys.includes(active);
          const Icon = item.icon;
          return (
            <Link
              key={item.key}
              href={hrefFor(item)}
              aria-current={selected ? "page" : undefined}
              className={cn(
                "inline-flex h-10 items-center gap-2 rounded-[8px] px-3 text-xs",
                selected
                  ? "bg-surface-base font-medium text-fg-primary"
                  : "text-fg-secondary hover:bg-surface-base",
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
        <div className="mb-3 rounded-[8px] border border-border-default bg-surface-base px-3 py-3">
          <div className="flex items-center justify-between gap-2 text-[10px] text-fg-tertiary">
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

      <nav className="space-y-1" aria-label="Разделы проекта MAX">
        {navigation.map((item) => {
          const selected = item.keys.includes(active);
          const stage = item.stageId
            ? journey.stages.find((candidate) => candidate.id === item.stageId)
            : undefined;
          const Icon = item.icon;

          return (
            <Link
              key={item.key}
              href={hrefFor(item)}
              aria-current={selected ? "page" : undefined}
              className={cn(
                "flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs transition-colors",
                selected
                  ? "bg-surface-overlay font-medium text-fg-primary"
                  : "text-fg-secondary hover:bg-surface-base hover:text-fg-primary",
              )}
            >
              <Icon className={cn("size-4 shrink-0", selected && "text-accent")} />
              <span className="min-w-0 flex-1 truncate">{item.label}</span>
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
    </div>
  );
}
