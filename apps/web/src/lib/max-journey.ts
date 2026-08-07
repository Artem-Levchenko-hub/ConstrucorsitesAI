import type { MaxReadiness } from "@/lib/api/types";

export type MaxJourneyStageId =
  | "demo"
  | "app"
  | "access"
  | "max"
  | "publish"
  | "verify";

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
    id: "demo",
    label: "Рабочее демо",
    shortLabel: "Демо",
    description: "Omnia собирает кликабельную версию до верификации и оплаты.",
    itemIds: ["build"],
    suffix: "",
    actionLabel: "Продолжить сборку",
  },
  {
    id: "app",
    label: "Карточка и документы",
    shortLabel: "Материалы",
    description: "Omnia проверит карточку, поддержку и юридическую готовность.",
    itemIds: ["business", "legal"],
    suffix: "/settings?tab=app",
    actionLabel: "Заполнить данные",
  },
  {
    id: "access",
    label: "Доступ к запуску",
    shortLabel: "Доступ",
    description: "Подтвердите бизнес и подключите тариф только перед запуском.",
    itemIds: ["max_business", "plan"],
    suffix: "/onboarding",
    actionLabel: "Подготовить запуск",
  },
  {
    id: "max",
    label: "MAX-бот",
    shortLabel: "MAX-бот",
    description: "В MAX Partner создайте карточку, дождитесь модерации и вставьте секрет.",
    itemIds: ["bot"],
    suffix: "/settings?tab=bot",
    actionLabel: "Подключить MAX-бота",
  },
  {
    id: "publish",
    label: "Публикация",
    shortLabel: "Публикация",
    description: "Omnia развернёт production, подготовит HTTPS-адрес и webhook.",
    itemIds: ["publish", "webhook"],
    suffix: "/publish",
    actionLabel: "Перейти к публикации",
  },
  {
    id: "verify",
    label: "Проверка в MAX",
    shortLabel: "Проверка",
    description: "Добавьте готовый HTTPS-адрес в карточку приложения в MAX.",
    itemIds: ["max_url"],
    suffix: "/settings?tab=bot",
    actionLabel: "Завершить подключение",
  },
];

const ITEM_STAGE: Record<string, MaxJourneyStageId> = {
  build: "demo",
  business: "app",
  legal: "app",
  max_business: "access",
  plan: "access",
  bot: "max",
  publish: "publish",
  webhook: "publish",
  max_url: "verify",
};

function stageIsDone(
  stageId: MaxJourneyStageId,
  itemIds: string[],
  items: ReadinessItem[],
): boolean {
  return itemIds.every((itemId) => items.find((item) => item.id === itemId)?.done === true);
}
export function getMaxJourneyItemHref(projectId: string, itemId: string): string {
  const stageId = ITEM_STAGE[itemId] ?? "demo";
  if (stageId === "access") {
    return `/max/onboarding?next=${encodeURIComponent(`/max/${projectId}`)}`;
  }
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
    href:
      definition.id === "access"
        ? `/max/onboarding?next=${encodeURIComponent(`/max/${projectId}`)}`
        : `/max/${projectId}${definition.suffix}`,
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
