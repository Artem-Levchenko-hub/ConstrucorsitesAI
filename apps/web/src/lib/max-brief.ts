import { redactCredentialsBeforeTransport } from "@/lib/credential-safety";

export const MAX_APP_TYPES = [
  {
    id: "loyalty",
    label: "Лояльность",
    description: "Баллы, уровни, акции и награды",
  },
  {
    id: "catalog",
    label: "Каталог",
    description: "Товары, услуги, карточки и заказ",
  },
  {
    id: "booking",
    label: "Запись",
    description: "Расписание, специалисты и бронь",
  },
  {
    id: "event",
    label: "Событие",
    description: "Программа, билеты и участники",
  },
  {
    id: "education",
    label: "Обучение",
    description: "Уроки, задания и прогресс",
  },
  {
    id: "custom",
    label: "Своя идея",
    description: "Любой другой сценарий внутри MAX",
  },
] as const;

export const MAX_FEATURES = [
  "Профиль пользователя",
  "Каталог или лента",
  "Поиск и фильтры",
  "Избранное",
  "Баллы и награды",
  "Онлайн-запись",
  "Уведомления бота",
  "История действий",
] as const;

export const MAX_STYLES = [
  {
    id: "brand",
    label: "В стиле бренда",
    description: "Акцент на ваших цветах и характере",
  },
  {
    id: "clean",
    label: "Чистый",
    description: "Спокойный интерфейс, максимум ясности",
  },
  {
    id: "bright",
    label: "Яркий",
    description: "Энергичные акценты и промо-подача",
  },
] as const;

export type MaxAppTypeId = (typeof MAX_APP_TYPES)[number]["id"];
export type MaxStyleId = (typeof MAX_STYLES)[number]["id"];
export type MaxFeature = (typeof MAX_FEATURES)[number];
export type MaxPrimaryActionKind =
  | "local_navigation"
  | "managed_write"
  | "catalog_read";

export type MaxProjectBrief = {
  name: string;
  idea: string;
  appType: MaxAppTypeId;
  audience: string;
  primaryAction: string;
  features: MaxFeature[];
  style: MaxStyleId;
  brandColors: string;
};

export type MaxProductSpec = {
  purpose: string;
  audience: string;
  screens: string[];
  primary_action: string;
  primary_action_kind: MaxPrimaryActionKind;
  capabilities: string[];
  data: string[];
  history: boolean;
  integrations: string[];
  style: string;
  acceptance: string[];
};

export type MaxStarterHandoff = {
  version: 1;
  prompt: string;
  productSpec: MaxProductSpec;
};

const MAX_STARTER_PROMPT_CHARS = 16_000;

function isStringArray(value: unknown): value is string[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.length <= 16 &&
    value.every((item) => typeof item === "string" && item.trim().length > 0)
  );
}

function isMaxProductSpec(value: unknown): value is MaxProductSpec {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const spec = value as Record<string, unknown>;
  return (
    typeof spec.purpose === "string" &&
    spec.purpose.trim().length > 0 &&
    typeof spec.audience === "string" &&
    spec.audience.trim().length > 0 &&
    isStringArray(spec.screens) &&
    typeof spec.primary_action === "string" &&
    spec.primary_action.trim().length > 0 &&
    ["local_navigation", "managed_write", "catalog_read"].includes(
      String(spec.primary_action_kind),
    ) &&
    Array.isArray(spec.capabilities) &&
    spec.capabilities.length <= 8 &&
    spec.capabilities.every(
      (item) => typeof item === "string" && item.trim().length > 0,
    ) &&
    isStringArray(spec.data) &&
    typeof spec.history === "boolean" &&
    isStringArray(spec.integrations) &&
    typeof spec.style === "string" &&
    spec.style.trim().length > 0 &&
    isStringArray(spec.acceptance)
  );
}

/** Keep the exact strict brief attached to a MAX starter retry. */
export function serializeMaxStarterHandoff(
  prompt: string,
  productSpec: MaxProductSpec,
): string {
  const value: MaxStarterHandoff = { version: 1, prompt, productSpec };
  return JSON.stringify(value);
}

/** Fail closed: a partial/legacy handoff must never enter the old MAX loop. */
export function parseMaxStarterHandoff(raw: string | null): MaxStarterHandoff | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Record<string, unknown>;
    if (
      value.version !== 1 ||
      typeof value.prompt !== "string" ||
      value.prompt.trim().length === 0 ||
      value.prompt.length > MAX_STARTER_PROMPT_CHARS ||
      !isMaxProductSpec(value.productSpec)
    ) {
      return null;
    }
    return value as MaxStarterHandoff;
  } catch {
    return null;
  }
}

export type SanitizedMaxProjectBrief = {
  brief: MaxProjectBrief;
  credentialsRemoved: boolean;
};

const MAX_TYPE_SCREENS: Record<MaxAppTypeId, string[]> = {
  loyalty: ["Главная с балансом", "Награды", "История действий", "Профиль"],
  catalog: ["Каталог", "Карточка предложения", "Заказ", "Профиль"],
  booking: ["Выбор услуги", "Расписание", "Подтверждение записи", "Профиль"],
  event: ["Программа", "Карточка события", "Билеты", "Профиль"],
  education: ["Каталог уроков", "Урок", "Прогресс", "Профиль"],
  custom: ["Главный экран", "Основной сценарий", "История действий", "Профиль"],
};

const MAX_TYPE_DATA: Record<MaxAppTypeId, string[]> = {
  loyalty: ["Баланс и операции лояльности", "Награды"],
  catalog: ["Предложения", "Заказы пользователя"],
  booking: ["Услуги и доступные слоты", "Записи пользователя"],
  event: ["События и программа", "Билеты пользователя"],
  education: ["Уроки и задания", "Прогресс пользователя"],
  custom: ["Данные основного сценария", "Данные профиля пользователя"],
};

function sanitizeFreeText(
  value: string,
  maxChars: number,
): { text: string; credentialsRemoved: boolean } {
  const safe = redactCredentialsBeforeTransport(value);
  return {
    text: safe.text.replace(/\s+/g, " ").trim().slice(0, maxChars),
    credentialsRemoved: safe.credentialsRemoved,
  };
}

/** Sanitize every user-editable field before it reaches project/config/prompt transport. */
export function sanitizeMaxProjectBrief(
  brief: MaxProjectBrief,
): SanitizedMaxProjectBrief {
  const fields = [
    ["name", brief.name, 100],
    ["idea", brief.idea, 600],
    ["audience", brief.audience, 400],
    ["primaryAction", brief.primaryAction, 240],
    ["brandColors", brief.brandColors, 180],
  ] as const;
  const sanitizedFields = fields.map(([key, value, maxChars]) => ({
    key,
    safe: sanitizeFreeText(value, maxChars),
  }));
  const sanitized = Object.fromEntries(
    sanitizedFields.map(({ key, safe }) => [key, safe.text]),
  ) as Pick<
    MaxProjectBrief,
    "name" | "idea" | "audience" | "primaryAction" | "brandColors"
  >;
  const credentialsRemoved = sanitizedFields.some(
    ({ safe }) => safe.credentialsRemoved,
  );

  return {
    brief: { ...brief, ...sanitized },
    credentialsRemoved,
  };
}

function optionLabel<T extends { id: string; label: string }>(
  options: readonly T[],
  id: string,
): string {
  return options.find((option) => option.id === id)?.label ?? id;
}
export function buildMaxProjectPrompt(brief: MaxProjectBrief): string {
  const features =
    brief.features.length > 0
      ? brief.features.join(", ")
      : "определи минимально необходимый набор по задаче";
  const audience = brief.audience.trim() || "определи по описанию продукта";
  const primaryAction =
    brief.primaryAction.trim() || "определи главное действие пользователя";
  const colors =
    brief.brandColors.trim() || "подбери уместную палитру под продукт";

  return [
    "Создай готовое мини-приложение именно для мессенджера MAX.",
    "Не превращай его в обычный сайт, Telegram Mini App, VK Mini App или отдельное веб-приложение.",
    "",
    `Название продукта: ${brief.name.trim()}.`,
    `Сценарий: ${optionLabel(MAX_APP_TYPES, brief.appType)}.`,
    `Что должно делать приложение: ${brief.idea.trim()}.`,
    `Целевая аудитория: ${audience}.`,
    `Главное действие пользователя: ${primaryAction}.`,
    `Нужные возможности: ${features}.`,
    `Визуальное направление: ${optionLabel(MAX_STYLES, brief.style)}.`,
    `Цвета бренда: ${colors}.`,
    "",
    "Сразу собери целостное рабочее приложение: мобильную навигацию, все основные экраны, состояния загрузки/пустого списка/ошибки и реальные русские тексты. Не используй вымышленные записи: пользовательские данные и история должны честно показывать пустое состояние до появления реальных данных.",
    "Используй готовую обвязку MAX Bridge, серверную проверку initData, MAX-профиль пользователя и webhook бота из шаблона. Не добавляй отдельную регистрацию или вход по email.",
  ].join("\n");
}

/** Build an explicit, bounded contract from the Studio questionnaire. */
export function buildMaxProductSpec(brief: MaxProjectBrief): MaxProductSpec {
  const audience = brief.audience.trim() || "Пользователи MAX";
  const primaryAction = brief.primaryAction.trim() || "Выполнить основной сценарий";
  const colors = brief.brandColors.trim() || "подходящая для продукта палитра";
  const data = MAX_TYPE_DATA[brief.appType];
  const historyScreenRequested = brief.features.includes("История действий");
  // Every built-in business flow creates user-owned state (reward/order/
  // booking/ticket/progress) even when the owner hides a separate history tab.
  // A custom read-only product opts in explicitly through the history feature.
  const persistenceRequired = brief.appType !== "custom" || historyScreenRequested;
  const primaryActionKind: MaxPrimaryActionKind = persistenceRequired
    ? "managed_write"
    : "local_navigation";
  const screens = historyScreenRequested
    ? MAX_TYPE_SCREENS[brief.appType]
    : MAX_TYPE_SCREENS[brief.appType].filter(
        (screen) => !screen.toLocaleLowerCase("ru-RU").includes("истори"),
      );

  return {
    purpose: `${brief.name.trim()}: ${brief.idea.trim()}`,
    audience,
    screens,
    primary_action: primaryAction,
    primary_action_kind: primaryActionKind,
    capabilities: [...brief.features],
    data,
    history: persistenceRequired,
    integrations: ["MAX Bridge", "MAX-профиль пользователя", "Webhook бота MAX"],
    style: `${optionLabel(MAX_STYLES, brief.style)}; цвета: ${colors}`,
    acceptance: [
      `Пользователь может ${primaryAction.toLocaleLowerCase("ru-RU")} через основной сценарий.`,
      "Каждый основной экран доступен из мобильной навигации и имеет loading, empty и error состояния.",
      persistenceRequired
        ? "Пользовательское действие сохраняется только для текущего пользователя и восстанавливается после перезагрузки."
        : "Профиль использует данные текущего пользователя; вымышленные записи запрещены.",
    ],
  };
}
