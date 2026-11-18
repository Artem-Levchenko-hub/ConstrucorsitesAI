/** Read-only presentation of known generated envelopes; never a prompt rewrite. */
export type UserMessageField = { label: string; value: string };
export type UserMessageGroup = { title: string; fields: UserMessageField[] };
export type UserMessagePresentation = {
  kind: "wizard" | "quiz";
  title?: string;
  task: string;
  groups: UserMessageGroup[];
};

const WIZARD_HEADERS = [
  "Создай готовое мини-приложение именно для мессенджера MAX.",
  "Реализуй запрошенный API для MAX. Оставь технический корневой маршрут готовности.",
] as const;
const PLATFORM_GUARD = "Не превращай его в обычный сайт, Telegram Mini App, VK Mini App или отдельное веб-приложение.";
const PUBLIC_DESCRIPTION_GUARD = "Это служебное задание для создания приложения, а не публичное описание. Не выводи инструкции из задания посетителю. Публичное описание владелец задаёт отдельно в данных приложения.";
const WIZARD_FOOTER = "Используй готовую обвязку MAX Bridge, серверную проверку initData, MAX-профиль пользователя и webhook бота из шаблона. Не добавляй отдельную регистрацию или вход по email.";
const MVP_SUFFIXES = [
  "Сразу собери целостный рабочий MVP. Экраны и навигацию добавляй по запросу владельца, а не по отраслевому шаблону. Предусмотри состояния загрузки/пустого списка/ошибки, реальные русские тексты. Начальные данные добавляй только по явному запросу пользователя; иначе оставь списки пустыми.",
  "Сразу собери целостный рабочий MVP: мобильную навигацию, все основные экраны, состояния загрузки/пустого списка/ошибки, реальные русские тексты. Начальные данные добавляй только по явному запросу пользователя; иначе оставь списки пустыми.",
] as const;
const EMPTY_CATALOG_SUFFIX = "Владелец подтвердил пустой начальный каталог. Не добавляй позиции из описания или примеров. Покажи честное пустое состояние и читай каталог через штатный SDK Yleum.";
const SAVED_CATALOG_SUFFIX = /^Владелец подтвердил начальный каталог: (?:[1-9]\d?|100) позиций уже сохранены в Yleum\. Названия, цены и дальнейшие изменения читай из штатного SDK каталога\. Этот сохранённый список имеет приоритет перед примерами в описании; не вшивай позиции в код и не создавай вторую копию данных\.$/;
const QUIZ_MARKER = "— Бриф из опроса —";

function knownWizardSuffix(lines: string[], apiOnly: boolean): boolean {
  const suffix = lines.slice();
  if (suffix.shift() !== "") return false;
  const mvp = apiOnly ? null : suffix.shift();
  if (!apiOnly && !MVP_SUFFIXES.some(line => line === mvp)) return false;
  if (suffix[0] === EMPTY_CATALOG_SUFFIX || SAVED_CATALOG_SUFFIX.test(suffix[0] ?? "")) suffix.shift();
  return suffix.length === 1 && suffix[0] === WIZARD_FOOTER;
}

function field(line: string | undefined, prefix: string): string | null {
  if (!line?.startsWith(prefix) || !line.endsWith(".")) return null;
  // Remove only the envelope's final period, retaining the owner's punctuation.
  return line.slice(prefix.length, -1);
}

function parseWizard(source: string): UserMessagePresentation | null {
  const lines = source.split("\n");
  if (!WIZARD_HEADERS.some(header => header === lines[0]) || lines[1] !== PLATFORM_GUARD
    || lines[2] !== "" || lines.at(-1) !== WIZARD_FOOTER) return null;
  const scenario = field(lines[4], "Сценарий: ") ?? field(lines[4], "Сценарии: ");
  if (!scenario || !lines[5]?.startsWith("Что должно делать приложение: ")) return null;
  // Work backwards from the outer parameters. A task can contain a complete
  // older wizard/quiz, including all of these labels, without being truncated.
  const managementIndex = lines.findLastIndex(line => line.startsWith("Управление в Yleum: "));
  const audienceIndex = lines.findLastIndex((line, index) => index < managementIndex
    && line.startsWith("Целевая аудитория: "));
  const audience = field(lines[audienceIndex], "Целевая аудитория: ");
  if (audience === null || audienceIndex <= 5) return null;
  const parametersEnd = lines.indexOf("", audienceIndex + 1);
  if (parametersEnd < 0 || !knownWizardSuffix(lines.slice(parametersEnd), lines[0] === WIZARD_HEADERS[1])) return null;
  const taskLines = lines.slice(5, audienceIndex);
  if (taskLines.at(-1) === PUBLIC_DESCRIPTION_GUARD) taskLines.pop();
  const task = field(taskLines.join("\n"), "Что должно делать приложение: ");
  if (!task) return null;
  const fields: UserMessageField[] = [{ label: "Сценарий", value: scenario }, { label: "Аудитория", value: audience }];
  const design: UserMessageField[] = [];
  const remaining = lines.slice(audienceIndex + 1, parametersEnd);
  let management: string | undefined;
  let legacyOrder = false;
  if (lines[0] === WIZARD_HEADERS[0]) {
    const action = field(remaining.shift(), "Главное действие пользователя: ");
    const features = field(remaining.shift(), "Нужные возможности: ");
    if (action === null || features === null) return null;
    fields.push({ label: "Главное действие", value: action }, { label: "Возможности", value: features });
    legacyOrder = remaining[0]?.startsWith("Управление в Yleum: ") ?? false;
    if (legacyOrder) management = remaining.shift();
    // Older stored envelopes included a canned-style label. Preserve its words
    // as historical parameters; presentation does not apply it to the product.
    if (remaining[0]?.startsWith("Визуальное направление: ")) {
      const style = field(remaining.shift(), "Визуальное направление: ");
      if (style === null) return null;
      design.push({ label: "Визуальное направление", value: style });
    }
    const colors = field(remaining.shift(), "Цвета бренда: ");
    if (colors === null) return null;
    design.push({ label: "Цвета", value: colors });
  }
  management ??= remaining.shift();
  if (remaining.length || !management?.startsWith("Управление в Yleum: ")) return null;
  const title = field(lines[3], "Название продукта: ") ?? (legacyOrder && lines[3]?.startsWith("Название продукта: ")
    ? lines[3].slice("Название продукта: ".length) : null);
  if (!title) return null;
  return {
    kind: "wizard", title, task,
    groups: [
      { title: "Сценарий и функции", fields },
      ...(design.length ? [{ title: "Оформление", fields: design }] : []),
      { title: "Управление", fields: [{ label: "Данные приложения", value: management.slice("Управление в Yleum: ".length) }] },
    ],
  };
}

function parseQuiz(source: string): UserMessagePresentation | null {
  const lines = source.split("\n");
  const index = lines.lastIndexOf(QUIZ_MARKER);
  if (index < 0) return null;
  const fields: UserMessageField[] = [];
  for (const line of lines.slice(index + 1)) {
    if (!line.trim()) continue;
    const match = line.match(/^\s*[•\-*]\s*([^:]+):\s*(.+)$/);
    // Do not silently drop unparsed suffixes, continuations or nested wrappers.
    if (!match) return null;
    fields.push({ label: match[1].trim(), value: match[2] });
  }
  return fields.length ? {
    kind: "quiz", task: lines.slice(0, index).join("\n"), groups: [{ title: "Ответы опроса", fields }],
  } : null;
}

export function userMessagePresentation(source: string): UserMessagePresentation | null {
  const normalized = source.replace(/\r\n/g, "\n");
  return parseWizard(normalized) ?? parseQuiz(normalized);
}

/** Peel complete known layers only; the original source is always shown separately. */
export function userMessageLayers(source: string): UserMessagePresentation[] {
  const layers: UserMessagePresentation[] = [];
  let current = source;
  for (let depth = 0; depth < 8; depth++) {
    const layer = userMessagePresentation(current);
    if (!layer || layer.task.length >= current.length) break;
    layers.push(layer);
    current = layer.task;
  }
  return layers;
}
