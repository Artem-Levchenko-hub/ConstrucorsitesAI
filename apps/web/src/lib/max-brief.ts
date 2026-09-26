export const MAX_BRIEF_LENGTH = 20_000;
export const MAX_PROMPT_LENGTH = 30_000;

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
  if (Array.from(brief.idea.trim()).length > MAX_BRIEF_LENGTH) {
    throw new Error(`Описание слишком длинное: максимум ${MAX_BRIEF_LENGTH} символов. Текст сохранён в форме.`);
  }
  const features =
    brief.features.length > 0
      ? brief.features.join(", ")
      : "определи минимально необходимый набор по задаче";
  const audience = brief.audience.trim() || "определи по описанию продукта";
  const primaryAction =
    brief.primaryAction.trim() || "определи главное действие пользователя";
  const colors =
    brief.brandColors.trim() || "подбери уместную палитру под продукт";

  const prompt = [
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
    "Сразу собери целостный рабочий MVP: мобильную навигацию, все основные экраны, состояния загрузки/пустого списка/ошибки, реальные русские тексты. Начальные данные добавляй только по явному запросу пользователя; иначе оставь списки пустыми.",
    "Используй готовую обвязку MAX Bridge, серверную проверку initData, MAX-профиль пользователя и webhook бота из шаблона. Не добавляй отдельную регистрацию или вход по email.",
  ].join("\n");
  if (Array.from(prompt).length > MAX_PROMPT_LENGTH) {
    throw new Error(`Задание с уточнениями превышает ${MAX_PROMPT_LENGTH} символов. Сократите описание или уточнения.`);
  }
  return prompt;
}

/** Что даёт каждая функция и какой экран показать в предпросмотре мастера. */
export type MaxFeatureScreen =
  | "profile"
  | "catalog"
  | "search"
  | "favorites"
  | "loyalty"
  | "booking"
  | "notifications"
  | "history";

export const MAX_FEATURE_INFO: Record<MaxFeature, { summary: string; screen: MaxFeatureScreen }> = {
  "Профиль пользователя": {
    summary: "Личный экран: имя из MAX, заказы и записи этого человека, его настройки.",
    screen: "profile",
  },
  "Каталог или лента": {
    summary: "Товары, услуги или материалы карточками — с названием, ценой и кнопкой действия.",
    screen: "catalog",
  },
  "Поиск и фильтры": {
    summary: "Строка поиска и быстрые фильтры, чтобы найти нужное за пару касаний.",
    screen: "search",
  },
  "Избранное": {
    summary: "Отметка «сохранить» на карточке и отдельный экран с отложенным.",
    screen: "favorites",
  },
  "Баллы и награды": {
    summary: "Счёт баллов, прогресс до следующего уровня и обмен баллов на награды.",
    screen: "loyalty",
  },
  "Онлайн-запись": {
    summary: "Выбор дня и свободного времени с подтверждением записи.",
    screen: "booking",
  },
  "Уведомления бота": {
    summary: "Бот MAX сам пишет пользователю о статусе заказа, записи или акции.",
    screen: "notifications",
  },
  "История действий": {
    summary: "Лента прошлых заказов, записей и обращений — видно, что уже было.",
    screen: "history",
  },
};
