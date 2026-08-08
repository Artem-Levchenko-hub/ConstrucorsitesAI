import {
  MAX_APP_TYPES,
  MAX_FEATURES,
  MAX_STYLES,
  type MaxAppTypeId,
  type MaxFeature,
  type MaxProjectBrief,
} from "@/lib/max-brief";

export const MAX_DEMO_DRAFT_STORAGE_KEY = "omnia:max:public-demo:v1";
export const MAX_DEMO_DRAFT_VERSION = 1 as const;

export type MaxDemoIndustry =
  | "restaurant"
  | "beauty"
  | "retail"
  | "fitness"
  | "education"
  | "event"
  | "services";

const MAX_DEMO_INDUSTRIES: readonly MaxDemoIndustry[] = [
  "restaurant",
  "beauty",
  "retail",
  "fitness",
  "education",
  "event",
  "services",
];

export type MaxDemoDraft = {
  version: typeof MAX_DEMO_DRAFT_VERSION;
  createdAt: string;
  description: string;
  industry: MaxDemoIndustry;
  industryLabel: string;
  brief: MaxProjectBrief;
  preview: {
    eyebrow: string;
    headline: string;
    subline: string;
    items: Array<{ title: string; meta: string; value: string }>;
    action: string;
  };
};

type DemoProfile = {
  industry: MaxDemoIndustry;
  label: string;
  appType: MaxAppTypeId;
  defaultName: string;
  audience: string;
  primaryAction: string;
  features: MaxFeature[];
  preview: MaxDemoDraft["preview"];
  keywords: string[];
};

const PROFILES: DemoProfile[] = [
  {
    industry: "restaurant",
    label: "Кафе и ресторан",
    appType: "catalog",
    defaultName: "Ваше кафе",
    audience: "гости заведения и постоянные покупатели",
    primaryAction: "выбрать позиции и оформить заказ",
    features: [
      "Каталог или лента",
      "Поиск и фильтры",
      "Профиль пользователя",
      "Уведомления бота",
      "История действий",
    ],
    preview: {
      eyebrow: "Заказ без очереди",
      headline: "Что возьмёте сегодня?",
      subline: "Меню, бонусы и заказ внутри MAX",
      items: [
        { title: "Капучино", meta: "300 мл · на овсяном", value: "290 ₽" },
        { title: "Круассан", meta: "миндальный · свежая выпечка", value: "240 ₽" },
        { title: "Завтрак", meta: "сырники · кофе · ягоды", value: "590 ₽" },
      ],
      action: "Открыть корзину",
    },
    keywords: [
      "кафе",
      "кофе",
      "кофейн",
      "ресторан",
      "бар",
      "еда",
      "доставк",
      "меню",
      "пицц",
      "суши",
    ],
  },
  {
    industry: "beauty",
    label: "Красота и здоровье",
    appType: "booking",
    defaultName: "Ваша студия",
    audience: "новые и постоянные клиенты студии",
    primaryAction: "выбрать услугу и записаться",
    features: [
      "Каталог или лента",
      "Онлайн-запись",
      "Профиль пользователя",
      "Уведомления бота",
      "История действий",
    ],
    preview: {
      eyebrow: "Онлайн-запись",
      headline: "Выберите услугу",
      subline: "Свободные окна и напоминания в MAX",
      items: [
        { title: "Стрижка и укладка", meta: "60 минут · мастер Анна", value: "2 400 ₽" },
        { title: "Маникюр", meta: "90 минут · мастер Ольга", value: "2 100 ₽" },
        { title: "Уход для лица", meta: "45 минут · сегодня 18:30", value: "3 200 ₽" },
      ],
      action: "Выбрать время",
    },
    keywords: [
      "салон",
      "красот",
      "парикмах",
      "маникюр",
      "барбер",
      "космет",
      "массаж",
      "клиник",
      "стомат",
    ],
  },
  {
    industry: "fitness",
    label: "Фитнес и лояльность",
    appType: "loyalty",
    defaultName: "Ваш клуб",
    audience: "участники клуба и постоянные клиенты",
    primaryAction: "посмотреть баланс и выбрать награду",
    features: [
      "Баллы и награды",
      "Профиль пользователя",
      "Онлайн-запись",
      "Уведомления бота",
      "История действий",
    ],
    preview: {
      eyebrow: "Клуб участника",
      headline: "1 250 баллов",
      subline: "До следующего уровня осталось 2 визита",
      items: [
        { title: "Персональная тренировка", meta: "награда · действует 30 дней", value: "900 б." },
        { title: "Гостевой визит", meta: "пригласите друга", value: "600 б." },
        { title: "Расписание", meta: "7 занятий сегодня", value: "Открыть" },
      ],
      action: "Активировать награду",
    },
    keywords: [
      "фитнес",
      "спорт",
      "трениров",
      "йога",
      "клуб",
      "лояльн",
      "бонус",
      "балл",
    ],
  },
  {
    industry: "education",
    label: "Обучение",
    appType: "education",
    defaultName: "Ваша школа",
    audience: "ученики и участники образовательной программы",
    primaryAction: "продолжить урок и выполнить задание",
    features: [
      "Каталог или лента",
      "Профиль пользователя",
      "Уведомления бота",
      "История действий",
    ],
    preview: {
      eyebrow: "Ваш прогресс · 68%",
      headline: "Продолжим обучение?",
      subline: "Уроки, задания и напоминания внутри MAX",
      items: [
        { title: "Практический урок", meta: "12 минут · модуль 4", value: "Продолжить" },
        { title: "Домашнее задание", meta: "срок сегодня до 21:00", value: "1 задание" },
        { title: "Материалы курса", meta: "чек-листы и записи", value: "8 файлов" },
      ],
      action: "Открыть урок",
    },
    keywords: ["школ", "курс", "обуч", "урок", "репетитор", "образован", "вебинар"],
  },
  {
    industry: "event",
    label: "События",
    appType: "event",
    defaultName: "Ваше событие",
    audience: "гости и зарегистрированные участники",
    primaryAction: "выбрать активность и зарегистрироваться",
    features: [
      "Каталог или лента",
      "Избранное",
      "Профиль пользователя",
      "Уведомления бота",
    ],
    preview: {
      eyebrow: "Программа события",
      headline: "Соберите свой день",
      subline: "Расписание, билеты и обновления в MAX",
      items: [
        { title: "Открытие", meta: "10:00 · главный зал", value: "В программе" },
        { title: "Практическая сессия", meta: "12:30 · осталось 18 мест", value: "Добавить" },
        { title: "Нетворкинг", meta: "18:00 · пространство A", value: "Добавить" },
      ],
      action: "Сохранить программу",
    },
    keywords: ["событ", "мероприят", "конферен", "фестивал", "выставк", "билет"],
  },
  {
    industry: "retail",
    label: "Магазин и каталог",
    appType: "catalog",
    defaultName: "Ваш магазин",
    audience: "покупатели магазина",
    primaryAction: "выбрать товар и оформить заказ",
    features: [
      "Каталог или лента",
      "Поиск и фильтры",
      "Избранное",
      "Профиль пользователя",
      "История действий",
    ],
    preview: {
      eyebrow: "Новая коллекция",
      headline: "Подобрано для вас",
      subline: "Каталог и заказ внутри MAX",
      items: [
        { title: "Базовая коллекция", meta: "6 цветов · в наличии", value: "от 1 990 ₽" },
        { title: "Новинки недели", meta: "доставка от 1 дня", value: "от 2 490 ₽" },
        { title: "Подарочный набор", meta: "готов к отправке", value: "3 900 ₽" },
      ],
      action: "Открыть корзину",
    },
    keywords: ["магазин", "товар", "каталог", "одежд", "цвет", "заказ", "продаж"],
  },
  {
    industry: "services",
    label: "Услуги",
    appType: "booking",
    defaultName: "Ваш сервис",
    audience: "клиенты бизнеса",
    primaryAction: "выбрать услугу и оставить заявку",
    features: [
      "Каталог или лента",
      "Онлайн-запись",
      "Профиль пользователя",
      "Уведомления бота",
      "История действий",
    ],
    preview: {
      eyebrow: "Услуги онлайн",
      headline: "Чем помочь?",
      subline: "Заявка и связь со специалистом внутри MAX",
      items: [
        { title: "Быстрая консультация", meta: "ответим в течение 15 минут", value: "Бесплатно" },
        { title: "Расчёт проекта", meta: "подготовим персональное предложение", value: "Заказать" },
        { title: "Поддержка", meta: "для действующих клиентов", value: "Написать" },
      ],
      action: "Оставить заявку",
    },
    keywords: [],
  },
];

function profileFor(description: string): DemoProfile {
  const normalized = description.toLocaleLowerCase("ru-RU");
  return (
    PROFILES.find((profile) =>
      profile.keywords.some((keyword) => normalized.includes(keyword)),
    ) ?? PROFILES[PROFILES.length - 1]
  );
}

function projectName(description: string, fallback: string): string {
  const quoted = description.match(/[«"]([^»"]{2,60})[»"]/u)?.[1]?.trim();
  return quoted || fallback;
}

export function createMaxDemoDraft(
  rawDescription: string,
  createdAt = new Date().toISOString(),
): MaxDemoDraft {
  const description = rawDescription.trim().replace(/\s+/g, " ").slice(0, 600);
  if (description.length < 10) {
    throw new Error("Опишите задачу хотя бы десятью символами");
  }
  const profile = profileFor(description);
  const name = projectName(description, profile.defaultName);
  return {
    version: MAX_DEMO_DRAFT_VERSION,
    createdAt,
    description,
    industry: profile.industry,
    industryLabel: profile.label,
    brief: {
      name,
      idea: description,
      appType: profile.appType,
      audience: profile.audience,
      primaryAction: profile.primaryAction,
      features: [...profile.features],
      style: "brand",
      brandColors: "",
    },
    preview: {
      ...profile.preview,
      items: profile.preview.items.map((item) => ({ ...item })),
    },
  };
}

export function parseMaxDemoDraft(value: string | null): MaxDemoDraft | null {
  if (!value || value.length > 50_000) return null;
  try {
    const parsed = JSON.parse(value) as Partial<MaxDemoDraft>;
    if (
      parsed.version !== MAX_DEMO_DRAFT_VERSION ||
      typeof parsed.createdAt !== "string" ||
      Number.isNaN(Date.parse(parsed.createdAt)) ||
      typeof parsed.description !== "string" ||
      parsed.description.length < 10 ||
      parsed.description.length > 600 ||
      typeof parsed.industry !== "string" ||
      !MAX_DEMO_INDUSTRIES.includes(parsed.industry as MaxDemoIndustry) ||
      typeof parsed.industryLabel !== "string" ||
      parsed.industryLabel.length > 100 ||
      !parsed.brief ||
      typeof parsed.brief.name !== "string" ||
      parsed.brief.name.length < 2 ||
      parsed.brief.name.length > 100 ||
      typeof parsed.brief.idea !== "string" ||
      parsed.brief.idea.length > 600 ||
      typeof parsed.brief.audience !== "string" ||
      parsed.brief.audience.length > 300 ||
      typeof parsed.brief.primaryAction !== "string" ||
      parsed.brief.primaryAction.length > 300 ||
      typeof parsed.brief.brandColors !== "string" ||
      parsed.brief.brandColors.length > 200 ||
      !MAX_APP_TYPES.some((item) => item.id === parsed.brief?.appType) ||
      !MAX_STYLES.some((item) => item.id === parsed.brief?.style) ||
      !Array.isArray(parsed.brief.features) ||
      !parsed.brief.features.every((feature) =>
        MAX_FEATURES.includes(feature as MaxFeature),
      ) ||
      !parsed.preview ||
      typeof parsed.preview.eyebrow !== "string" ||
      parsed.preview.eyebrow.length > 100 ||
      typeof parsed.preview.headline !== "string" ||
      parsed.preview.headline.length > 200 ||
      typeof parsed.preview.subline !== "string" ||
      parsed.preview.subline.length > 300 ||
      typeof parsed.preview.action !== "string" ||
      parsed.preview.action.length > 100 ||
      !Array.isArray(parsed.preview.items) ||
      parsed.preview.items.length < 1 ||
      parsed.preview.items.length > 10 ||
      !parsed.preview.items.every(
        (item) =>
          item &&
          typeof item.title === "string" &&
          item.title.length <= 100 &&
          typeof item.meta === "string" &&
          item.meta.length <= 200 &&
          typeof item.value === "string" &&
          item.value.length <= 100,
      )
    ) {
      return null;
    }
    return parsed as MaxDemoDraft;
  } catch {
    return null;
  }
}
