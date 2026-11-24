import type { ManagementModule, MaxProjectConfigPayload } from "./api/types";
import { characterCount } from "./codepoints";

export const MAX_BRIEF_LENGTH = 20_000;
export const MAX_PROMPT_LENGTH = 30_000;
export const MAX_DEFAULT_SUMMARY = "Мини-приложение для пользователей MAX";
export const MAX_BRIEF_FIELD_LIMITS = { name: 100, audience: 400, primaryAction: 200, brandColors: 200 } as const;

export function maxBriefFieldError(field: keyof typeof MAX_BRIEF_FIELD_LIMITS, value: string): string | null {
  const labels = { name: "Название", audience: "Аудитория", primaryAction: "Главное действие", brandColors: "Цвета бренда" };
  const limit = MAX_BRIEF_FIELD_LIMITS[field];
  return characterCount(value.trim()) > limit ? `${labels[field]}: не более ${limit} символов. Текст сохранён — сократите его.` : null;
}

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

export type MaxAppTypeId = (typeof MAX_APP_TYPES)[number]["id"];
export type MaxFeature = (typeof MAX_FEATURES)[number];

/** Keep the selection order: the first scenario is the compatible primary. */
export function selectedMaxAppTypes(appType: MaxAppTypeId, appTypes?: readonly MaxAppTypeId[]): MaxAppTypeId[] {
  const selected = appTypes === undefined ? [appType] : Array.from(appTypes);
  if (selected.length === 0 || selected.some(id => !MAX_APP_TYPES.some(option => option.id === id))) {
    throw new Error("Выберите хотя бы один известный тип приложения.");
  }
  return [...new Set(selected)];
}

export const MAX_MANAGEMENT_MODULES: { id: ManagementModule; label: string; description: string }[] = [
  { id: "catalog", label: "Каталог", description: "До 100 товаров или услуг: цены, фото, разделы и наличие." },
  { id: "orders", label: "Заказы", description: "Состав, сумма и статус каждого заказа." },
  { id: "leads", label: "Заявки", description: "Обращения клиентов и заметки для работы с ними." },
  { id: "bookings", label: "Запросы на запись", description: "Выбранная услуга и дата; запись подтверждаете вы." },
];
const DEFAULT_MANAGEMENT_MODULES: readonly ManagementModule[] = ["catalog", "leads"];

function withDefaultManagementModules(selected: readonly ManagementModule[]): ManagementModule[] {
  const modules = new Set<ManagementModule>([...DEFAULT_MANAGEMENT_MODULES, ...selected]);
  return MAX_MANAGEMENT_MODULES.filter(module => modules.has(module.id)).map(module => module.id);
}

export function defaultManagementModules(appType: MaxAppTypeId, appTypes?: readonly MaxAppTypeId[]): ManagementModule[] {
  const defaults = selectedMaxAppTypes(appType, appTypes).flatMap<ManagementModule>(type => {
    if (type === "catalog" || type === "loyalty") return ["orders"];
    return type === "booking" ? ["bookings"] : [];
  });
  return withDefaultManagementModules(defaults);
}

function withoutExcludedManagementModules(modules: readonly ManagementModule[], optedOut: readonly ManagementModule[]): ManagementModule[] {
  const excluded = new Set(optedOut);
  if (excluded.has("catalog")) {
    excluded.add("orders");
    excluded.add("bookings");
  }
  return modules.filter(module => !excluded.has(module));
}

/** Explicit UI choices; saved legacy callers retain their original defaults. */
export function newProjectManagementModules(
  appType: MaxAppTypeId,
  selected?: readonly ManagementModule[],
  appTypes?: readonly MaxAppTypeId[],
  optedOut: readonly ManagementModule[] = [],
): ManagementModule[] {
  const modules = selected === undefined ? defaultManagementModules(appType, appTypes)
    : MAX_MANAGEMENT_MODULES.filter(module => selected.includes(module.id)).map(module => module.id);
  if ((modules.includes("orders") || modules.includes("bookings")) && !modules.includes("catalog")) modules.unshift("catalog");
  return withoutExcludedManagementModules(modules, optedOut);
}

/** SDK availability is not a request to implement every available product flow. */
export function availableManagementModules(optedOut: readonly ManagementModule[] = []): ManagementModule[] {
  return withoutExcludedManagementModules(MAX_MANAGEMENT_MODULES.map(module => module.id), optedOut);
}

/** Classifiers inspect unquoted requests; the full original is always retained. */
function unquotedRequest(value: string): string {
  const pairs: Record<string, string> = { '"': '"', "'": "'", "«": "»", "“": "”", "‘": "’", "`": "`" };
  let closing = "";
  let fence = "";
  let result = "";
  for (let i = 0; i < value.length; i += 1) {
    const char = value[i];
    const marker = value.slice(i, i + 3);
    if ((marker === "```" || marker === "~~~") && (!fence || marker === fence)) {
      fence = fence ? "" : marker;
      closing = ""; result += "   "; i += 2;
      continue;
    }
    if (fence) { result += char === "\n" ? "\n" : " "; continue; }
    if (closing) {
      if (char === closing || char === "\n") closing = "";
      result += char === "\n" ? "\n" : " ";
    } else if (pairs[char] && !(char === "'" && /\p{L}/u.test(value[i - 1] ?? ""))) {
      closing = pairs[char]; result += " ";
    } else result += char;
  }
  return result;
}

export function isExplicitApiOnlyBrief(idea: string): boolean {
  // Quotes around the requested scope do not negate an explicit command;
  // quoted examples/names without that command remain ordinary mention data.
  const requested = idea.replace(/(^|[.!?;\n]\s*)((?:мне\s+)?(?:нужен|нужно|нужна|создай|сделай|реализуй|разработай|implement|build|create|sort|i\s+need)\s+(?:an?\s+)?)["«“'‘`](api[- ]only|backend[- ]only|only\s+api|api\s+only|только\s+api)["»”'’`]/giu, "$1$2$3");
  const text = unquotedRequest(requested);
  if (/(?:не\s+только\s+api|not\s+only\s+api)/iu.test(text)) return false;
  return text.split(/[.!?;\n]/u).some(clause => {
    const value = clause.trim();
    const scope = /^(?:(?:мне\s+)?(?:нужен|нужно|нужна|создай|сделай|реализуй|разработай|implement|build|create|sort|i\s+need)\s+(?:an?\s+)?)?(?:api[- ]only|backend[- ]only|only\s+api|api\s+only|только\s+api)\b/iu.exec(value);
    if (!scope) return false;
    return !/^\s*(?:[—–:-]\s*)?(?:не\s+(?:нужен|нужна|нужны|нужно|хочу|требуется)|is\s+not\s+(?:needed|required)|not\s+(?:needed|required))(?:\s+(?:совсем|вообще))?\s*(?=[:,;]|$)/iu.test(value.slice(scope[0].length));
  });
}
const MODULE_NOUN = "каталог(?:а|ом|е|у|и|ов|ами|ах)?|заказ(?:а|ы|ов|ами|ах|у|ом)?|заявк(?:а|и|у|е|ой|ами|ах)|заявок|бронировани(?:е|я|й)|catalog(?:ue|s)?|orders?|leads?|bookings?";
const MODULE_EXCLUSION = new RegExp(`(?<!\\p{L})(?:без|without|no|не\\s+(?:нужен|нужна|нужны|нужно|хочу|добавляй|добавлять|создавай|создавать))\\s+(?:a\\s+)?(${MODULE_NOUN})(?:\\s*(?:,|и|and)\\s*(${MODULE_NOUN}))?(?!\\p{L})`, "gu");
const nounModule = (noun: string): ManagementModule => noun.startsWith("каталог") || noun.startsWith("catalog") ? "catalog"
  : noun.startsWith("заказ") || noun.startsWith("order") ? "orders"
    : noun.startsWith("бронирован") || noun.startsWith("booking") ? "bookings" : "leads";

export function briefManagementModuleOptOuts(idea: string): ManagementModule[] {
  const excluded = new Set<ManagementModule>();
  const request = unquotedRequest(idea).toLocaleLowerCase("ru-RU");
  for (const match of request.matchAll(MODULE_EXCLUSION)) {
    if (/(?:не|not)\s*$/u.test(request.slice(0, match.index))) continue;
    const [, first, second] = match;
    for (const noun of [first, second]) {
      if (noun) excluded.add(nounModule(noun));
    }
  }
  for (const match of request.matchAll(new RegExp(`(?<!\\p{L})(${MODULE_NOUN})\\s+не\\s+(?:нужен|нужна|нужны|нужно)(?!\\p{L})`, "gu"))) excluded.add(nounModule(match[1]));
  return MAX_MANAGEMENT_MODULES.filter(module => excluded.has(module.id)).map(module => module.id);
}

// Read an explicitly labelled contact. Account, order and example addresses in
// free text must never become the public support mailbox just because they occur first.
export function briefSupportEmail(idea: string): string | null {
  const contacts = new Set<string>();
  const label = /^(?:(?:(?:почта|адрес|e-?mail|контакт)\s+(?:для\s+)?|для\s+)?(?:тех)?поддержк[аиу]|(?:напишите|пишите)\s+в\s+(?:тех)?поддержку|(?:customer\s+)?support(?:\s+e-?mail)?)(?:\s+(?:пользователей|клиентов|приложения))?(?:\s+(?:пишите\s+)?(?:на(?:\s+почту)?|at))?(?:\s*[:=—–-]\s*|\s+)/iu;
  // Keep an example marker attached to its clause when it is followed by a comma.
  const request = unquotedRequest(idea).replace(/(?<!\p{L})(например|пример|(?:for\s+)?example)\s*,/giu, "$1 ");
  for (const clause of request.split(/[,;\n]|[.!?](?=\s|$)/u)) {
    const text = clause.trim();
    const prefix = label.exec(text);
    if (!prefix) continue;
    const address = text.slice(prefix[0].length).trim();
    // Prefer an empty field the owner can fill to guessing an ambiguous contact.
    if (!/^[a-z0-9_+%-]+(?:\.[a-z0-9_+%-]+)*@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/iu.test(address)
      || address.length > 254 || address.indexOf("@") > 64
      || /\.(?:arpa|invalid|local|localhost|onion|test)$/iu.test(address)) return null;
    contacts.add(address);
  }
  return contacts.size === 1 ? [...contacts][0] : null;
}

export type MaxProjectBrief = {
  name: string;
  idea: string;
  appType: MaxAppTypeId;
  /** Optional for older single-scenario parents; first selected type stays primary. */
  appTypes?: MaxAppTypeId[];
  audience: string;
  primaryAction: string;
  features: MaxFeature[];
  /** @deprecated Accepted from older clients; never supplies visual instructions. */
  style?: MaxProjectConfigPayload["style"];
  brandColors: string;
  /** An explicit edit on the review step. Empty means clear the detected address. */
  supportEmail?: string;
  managementModules?: ManagementModule[];
  managementModuleOptOuts?: readonly ManagementModule[];
  /** The reviewed catalog is already saved in the same project-creation request. */
  catalogItemCount?: number;
};

function optionLabel<T extends { id: string; label: string }>(
  options: readonly T[],
  id: string,
): string {
  return options.find((option) => option.id === id)?.label ?? id;
}

/** Persist every scenario in the existing API string list, never in UI MaxFeature[]. */
export function serializeMaxProjectFeatures(brief: Pick<MaxProjectBrief, "appType" | "appTypes" | "features">): string[] {
  const appTypes = selectedMaxAppTypes(brief.appType, brief.appTypes);
  if (appTypes.length === 1) return [...brief.features];
  const scenarios = appTypes
    .map(type => `Сценарий: ${optionLabel(MAX_APP_TYPES, type)}`);
  return [...new Set([...brief.features, ...scenarios])];
}

export function buildMaxProjectPrompt(brief: MaxProjectBrief): string {
  const appTypes = selectedMaxAppTypes(brief.appType, brief.appTypes);
  const scenarios = appTypes.map(type => MAX_APP_TYPES.find(option => option.id === type)!);
  const idea = brief.idea.trim();
  const apiOnly = isExplicitApiOnlyBrief(idea);
  for (const field of Object.keys(MAX_BRIEF_FIELD_LIMITS) as (keyof typeof MAX_BRIEF_FIELD_LIMITS)[]) {
    const error = maxBriefFieldError(field, brief[field]);
    if (error) throw new Error(error);
  }
  if (brief.catalogItemCount !== undefined && (!Number.isInteger(brief.catalogItemCount) || brief.catalogItemCount < 0 || brief.catalogItemCount > 100)) {
    throw new Error("В начальном каталоге может быть от 0 до 100 позиций.");
  }
  if (Array.from(idea).length > MAX_BRIEF_LENGTH) {
    throw new Error(`Описание слишком длинное: максимум ${MAX_BRIEF_LENGTH} символов. Текст сохранён в форме.`);
  }
  const scenarioDetails = idea || scenarios.map(option => option.description).join(". ");
  const features =
    brief.features.length > 0
      ? brief.features.join(", ")
      : "определи минимально необходимый набор по задаче";
  const audience = brief.audience.trim() || "определи по описанию продукта";
  const primaryAction =
    brief.primaryAction.trim() || "определи главное действие пользователя";
  const colors =
    brief.brandColors.trim() || "подбери уместную палитру под продукт";
  const optedOut = [...new Set([...(brief.managementModuleOptOuts ?? []), ...briefManagementModuleOptOuts(idea)])];
  const managementModules = newProjectManagementModules(brief.appType, brief.managementModules, brief.appTypes, optedOut);
  const moduleLabels = (modules: readonly ManagementModule[]) => MAX_MANAGEMENT_MODULES.filter(item => modules.includes(item.id)).map(item => item.label).join(", ");

  const prompt = [
    apiOnly
      ? "Реализуй запрошенный API для MAX. Оставь технический корневой маршрут готовности."
      : "Создай готовое мини-приложение именно для мессенджера MAX.",
    "Не превращай его в обычный сайт, Telegram Mini App, VK Mini App или отдельное веб-приложение.",
    "",
    `Название продукта: ${brief.name.trim()}.`,
    `${scenarios.length === 1 ? "Сценарий" : "Сценарии"}: ${scenarios.map(option => option.label).join(", ")}.`,
    `Что должно делать приложение: ${scenarioDetails}.`,
    "Это служебное задание для создания приложения, а не публичное описание. Не выводи инструкции из задания посетителю. Публичное описание владелец задаёт отдельно в данных приложения.",
    `Целевая аудитория: ${audience}.`,
    ...(!apiOnly || brief.primaryAction.trim() ? [`Главное действие пользователя: ${primaryAction}.`] : []),
    ...(!apiOnly || brief.features.length ? [`Нужные возможности: ${features}.`] : []),
    ...(!apiOnly || brief.brandColors.trim() ? [`Цвета бренда: ${colors}.`] : []),
    ...(brief.supportEmail !== undefined ? ["Контакт поддержки берётся из настроек проекта. Для обращений используй страницу /support, чтобы изменения контакта применялись без изменений кода."] : []),
    ...(apiOnly ? ["Режим только API не требует продуктового интерфейса. Явные функциональные пожелания выше сохраняются; пожелания к оформлению применимы только если интерфейс запрошен отдельно."] : []),
    `Управление в Yleum: ${moduleLabels(managementModules) || "не уточнено — используй возможности по задаче"}. Это явные пожелания, а не закрытый список функций. Перечень .omnia/management.json описывает доступные SDK-возможности, а не обязательные экраны или функции.${optedOut.length ? ` Явно исключено владельцем: ${moduleLabels(optedOut)}.` : ""} Личные данные пользователей не превращай в общий реестр владельца.`,
    "",
    ...(apiOnly ? [] : [
      "Сразу собери целостный рабочий MVP. Экраны и навигацию добавляй по запросу владельца, а не по отраслевому шаблону. Предусмотри состояния загрузки/пустого списка/ошибки, реальные русские тексты. Начальные данные добавляй только по явному запросу пользователя; иначе оставь списки пустыми.",
    ]),
    ...(brief.catalogItemCount === undefined || !availableManagementModules(optedOut).includes("catalog") ? [] : [
      brief.catalogItemCount === 0
        ? "Владелец подтвердил пустой начальный каталог. Не добавляй позиции из описания или примеров. Покажи честное пустое состояние и читай каталог через штатный SDK Yleum."
        : `Владелец подтвердил начальный каталог: ${brief.catalogItemCount} позиций уже сохранены в Yleum. Названия, цены и дальнейшие изменения читай из штатного SDK каталога. Этот сохранённый список имеет приоритет перед примерами в описании; не вшивай позиции в код и не создавай вторую копию данных.`,
    ]),
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
    summary: "Личный экран: настройки, заказы и записи этого человека. MAX подтверждает его идентификатор; остальные данные он заполняет сам.",
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
    summary: "Запрос на удобную дату и время; владелец подтверждает запись.",
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
