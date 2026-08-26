import type { MaxReadiness } from "@/lib/api/types";

export type MaxJourneyStageId =
  | "project"
  | "build"
  | "app"
  | "bot"
  | "publish"
  | "max";

export type MaxJourneyStageStatus = "completed" | "current" | "upcoming";

export type MaxJourneyStage = {
  id: MaxJourneyStageId;
  label: string;
  shortLabel: string;
  description: string;
  done: boolean;
  href: string;
  position: number;
  status: MaxJourneyStageStatus;
  actionLabel: string;
};

type ReadinessItem = MaxReadiness["items"][number];

const STAGE_DEFINITIONS: Array<{
  id: MaxJourneyStageId;
  label: string;
  shortLabel: string;
  description: string;
  itemIds: string[];
  suffix: string;
  actionLabel: string;
}> = [
  {
    id: "project",
    label: "Проект создан",
    shortLabel: "Проект",
    description: "Название и основной сценарий приложения сохранены.",
    itemIds: [],
    suffix: "",
    actionLabel: "Открыть проект",
  },
  {
    id: "build",
    label: "Сборка приложения",
    shortLabel: "Сборка",
    description: "Соберите рабочую версию и проверьте основные экраны.",
    itemIds: ["build"],
    suffix: "",
    actionLabel: "Продолжить сборку",
  },
  {
    id: "app",
    label: "Данные и политики",
    shortLabel: "Данные",
    description: "Заполните сведения о продукте, владельце, поддержке и правилах.",
    itemIds: ["business", "legal"],
    suffix: "/settings?tab=app",
    actionLabel: "Заполнить данные",
  },
  {
    id: "bot",
    label: "Безопасный вход MAX",
    shortLabel: "MAX-доступ",
    description: "Подключите промодерированного бота, чтобы сервер проверял пользователей MAX.",
    itemIds: ["bot"],
    suffix: "/settings?tab=bot",
    actionLabel: "Подключить безопасный вход",
  },
  {
    id: "publish",
    label: "Публикация",
    shortLabel: "Публикация",
    description: "Разверните production-версию и получите постоянный HTTPS-адрес.",
    itemIds: ["publish"],
    suffix: "/publish",
    actionLabel: "Перейти к публикации",
  },
  {
    id: "max",
    label: "Подключение в MAX",
    shortLabel: "MAX",
    description: "Откройте MAX Partner, вставьте production URL и сохраните запуск mini app.",
    itemIds: ["max_url"],
    suffix: "/settings?tab=bot",
    actionLabel: "Подключить в MAX",
  },
];

const ITEM_STAGE: Record<string, MaxJourneyStageId> = {
  build: "build",
  business: "app",
  legal: "app",
  publish: "publish",
  bot: "bot",
  webhook: "bot",
  max_url: "max",
};

function stageIsDone(
  stageId: MaxJourneyStageId,
  itemIds: string[],
  items: ReadinessItem[],
): boolean {
  if (stageId === "project") return true;
  return itemIds.every((itemId) => items.find((item) => item.id === itemId)?.done === true);
}
export function getMaxJourneyItemHref(projectId: string, itemId: string): string {
  const stageId = ITEM_STAGE[itemId] ?? "build";
  const definition = STAGE_DEFINITIONS.find((stage) => stage.id === stageId);
  return `/max/${projectId}${definition?.suffix ?? ""}`;
}

export function getMaxJourney(
  projectId: string,
  items: ReadinessItem[],
): {
  completedCount: number;
  currentStage: MaxJourneyStage | undefined;
  progress: number;
  stages: MaxJourneyStage[];
  total: number;
} {
  const prepared = STAGE_DEFINITIONS.map((definition, index) => ({
    ...definition,
    done: stageIsDone(definition.id, definition.itemIds, items),
    href: `/max/${projectId}${definition.suffix}`,
    position: index + 1,
  }));
  const currentIndex = prepared.findIndex((stage) => !stage.done);
  const stages: MaxJourneyStage[] = prepared.map((stage, index) => ({
    id: stage.id,
    label: stage.label,
    shortLabel: stage.shortLabel,
    description: stage.description,
    done: stage.done,
    href: stage.href,
    position: stage.position,
    status: stage.done
      ? "completed"
      : index === currentIndex
        ? "current"
        : "upcoming",
    actionLabel: stage.actionLabel,
  }));
  const completedCount = stages.filter((stage) => stage.done).length;

  return {
    completedCount,
    currentStage: currentIndex === -1 ? undefined : stages[currentIndex],
    progress: Math.round((completedCount / stages.length) * 100),
    stages,
    total: stages.length,
  };
}
