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
    "Сразу собери целостный рабочий MVP: мобильную навигацию, все основные экраны, состояния загрузки/пустого списка/ошибки, реальные русские тексты и демонстрационные данные.",
    "Используй готовую обвязку MAX Bridge, серверную проверку initData, MAX-профиль пользователя и webhook бота из шаблона. Не добавляй отдельную регистрацию или вход по email.",
  ].join("\n");
}
