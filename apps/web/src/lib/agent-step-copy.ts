import type { AgentStep } from "@/lib/api/types";

/**
 * Лента работы агента глазами владельца, а не разработчика.
 *
 * Строка вида «Читаю src/app/(dashboard)/page.tsx» ничего не говорит человеку,
 * который первый раз собирает приложение: непонятно ни что происходит, ни зачем.
 * Поэтому у каждого действия здесь есть три вещи: короткое название понятными
 * словами, объяснение «зачем это нужно» и разряд, который красит строку. Путь к
 * файлу не выбрасываем — он уходит в подсказку и в подробности строки.
 */

export type StepTone = "work" | "think" | "check" | "wait" | "done" | "fail";

/** Имя значка строки; сам значок выбирает компонент ленты. */
export type StepIconName =
  | "explore"
  | "read"
  | "search"
  | "docs"
  | "write"
  | "edit"
  | "media"
  | "terminal"
  | "build"
  | "logs"
  | "browser"
  | "shield"
  | "wait"
  | "retry"
  | "rethink"
  | "boost"
  | "done";

export type StepCopy = {
  title: string;
  /** Одно предложение: что это значит для владельца. */
  hint: string;
  tone: StepTone;
  icon: StepIconName;
};

/** Инструмент агента → как это назвать и зачем оно нужно. */
const TOOL_COPY: Record<string, StepCopy> = {
  list_dir: {
    title: "Смотрю, что уже есть в проекте",
    hint: "Агент осматривает файлы приложения, чтобы не переписать то, что уже работает.",
    tone: "think",
    icon: "explore",
  },
  read_file: {
    title: "Читаю код",
    hint: "Прежде чем менять экран, агент читает его текущий вид.",
    tone: "think",
    icon: "read",
  },
  grep: {
    title: "Ищу нужное место в коде",
    hint: "Поиск по проекту: где именно лежит то, что нужно поправить.",
    tone: "think",
    icon: "search",
  },
  docs: {
    title: "Сверяюсь с документацией",
    hint: "Агент читает описание технологии, чтобы сделать по правилам, а не наугад.",
    tone: "think",
    icon: "docs",
  },
  provider_docs: {
    title: "Читаю документацию сервиса",
    hint: "Перед подключением внешнего сервиса агент сверяется с его инструкцией.",
    tone: "think",
    icon: "docs",
  },
  write_file: {
    title: "Создаю новый экран",
    hint: "Появляется новая часть приложения — экран, блок или обработчик.",
    tone: "work",
    icon: "write",
  },
  edit_file: {
    title: "Правлю готовое",
    hint: "Агент меняет то, что уже написано, не трогая остальное.",
    tone: "work",
    icon: "edit",
  },
  generate_media: {
    title: "Рисую картинки",
    hint: "Для приложения готовятся изображения вместо серых заглушек.",
    tone: "work",
    icon: "media",
  },
  bash: {
    title: "Выполняю команду",
    hint: "Служебная команда внутри изолированной среды проекта.",
    tone: "work",
    icon: "terminal",
  },
  build: {
    title: "Проверяю, что всё собирается",
    hint: "Сборка приложения: так ошибка находится до того, как её увидит пользователь.",
    tone: "check",
    icon: "build",
  },
  read_logs: {
    title: "Смотрю, на что ругается приложение",
    hint: "Агент читает журнал ошибок, чтобы понять причину, а не угадывать.",
    tone: "check",
    icon: "logs",
  },
  runtime_check: {
    title: "Открываю приложение как пользователь",
    hint: "Экран открывается по-настоящему: проверяем, что он работает, а не только собирается.",
    tone: "check",
    icon: "browser",
  },
  probe: {
    title: "Проверяю действие вживую",
    hint: "Агент сам нажимает кнопку и смотрит, что получилось.",
    tone: "check",
    icon: "browser",
  },
  verify_isolation: {
    title: "Проверяю, что данные не видны чужим",
    hint: "Каждый пользователь приложения должен видеть только свои записи — это проверяется на настоящей базе.",
    tone: "check",
    icon: "shield",
  },
  done: {
    title: "Готово",
    hint: "Работа закончена, результат можно смотреть в превью.",
    tone: "done",
    icon: "done",
  },
};

/** Событие не про инструмент, а про ход работы. */
const KIND_COPY: Record<string, StepCopy> = {
  heartbeat: {
    title: "Работаю над задачей",
    hint: "Долгий шаг продолжается — связь есть, агент не завис.",
    tone: "wait",
    icon: "wait",
  },
  retry: {
    title: "Пробую ещё раз",
    hint: "Ответ не дошёл или пришёл неполным — агент повторяет тот же запрос.",
    tone: "wait",
    icon: "retry",
  },
  stalled: {
    title: "Меняю подход",
    hint: "Прежний путь не сработал, агент ищет другой способ сделать то же самое.",
    tone: "think",
    icon: "rethink",
  },
  escalate: {
    title: "Беру модель посильнее",
    hint: "Задача оказалась сложнее ожидаемого — агент переключается на более мощную модель.",
    tone: "think",
    icon: "boost",
  },
};

const WAITING_TITLE = "Ожидаю ресурсы сервера";

/** Путь к файлу → короткое человеческое имя. Сам путь остаётся в подробностях. */
export function humanPath(path: string | undefined): string {
  const clean = (path ?? "").replace(/\\/g, "/").trim();
  if (!clean) return "";
  if (/^https?:\/\//.test(clean) || clean.startsWith("/")) {
    // runtime_check передаёт маршрут, а не файл: «/», «/orders».
    return clean === "/" ? "главный экран" : clean;
  }
  const base = clean.split("/").pop()?.toLowerCase() ?? "";
  if (base.startsWith("page.")) {
    const segments = clean
      .split("/")
      .filter(part => part && !part.startsWith("(") && !["src", "app"].includes(part));
    const name = segments.at(-2);
    return name ? `экран «${name}»` : "главный экран";
  }
  if (base.startsWith("layout.")) return "каркас приложения";
  if (base.startsWith("route.")) return "серверный обработчик";
  if (base.includes("schema")) return "структура данных";
  if (base.endsWith(".css")) return "оформление";
  if (base.endsWith(".sql")) return "миграция базы";
  if (base.endsWith(".tsx") || base.endsWith(".jsx")) {
    return `блок «${base.replace(/\.[jt]sx$/, "")}»`;
  }
  return base || clean;
}

function capitalize(value: string): string {
  return value ? value[0].toUpperCase() + value.slice(1) : value;
}

/**
 * Что показать в строке ленты: название, пояснение и разряд.
 *
 * Бэкенд уже присылает готовую фразу в `action` — она точнее, когда описывает
 * конкретный файл («Пишу главную страницу»), поэтому берём её как название, а
 * пояснение подставляем по инструменту. Словарь ниже работает и для старых
 * сохранённых лент, где в `action` лежит сырое имя инструмента.
 */
export function stepCopy(step: AgentStep): StepCopy {
  if (step.ok === false) {
    // Одна и та же фраза красным и серым различается плохо: у неудачи должно
    // быть видно словами, что шаг не получился, а не только цветом.
    return {
      title: `Не получилось: ${(step.action || "шаг").toLowerCase()}`,
      hint: "Шаг не получился — агент разбирается и пробует иначе.",
      tone: "fail",
      icon: TOOL_COPY[step.tool ?? ""]?.icon ?? "retry",
    };
  }
  if (step.action === WAITING_TITLE) {
    return {
      title: WAITING_TITLE,
      hint: "Сейчас заняты все мощности. Проект сохранён и стартует сам, как только освободится место.",
      tone: "wait",
      icon: "wait",
    };
  }
  const byKind = step.kind !== "step" ? KIND_COPY[step.kind] : undefined;
  const byTool = TOOL_COPY[step.tool ?? step.action];
  const base = byKind ?? byTool;
  // Для обычного шага фраза бэкенда точнее словаря: она называет конкретное
  // место («Пишу страницу «orders»»). А служебные события бэкенд описывает
  // технически («усиливаю модель → opus»), и там понятнее наш текст.
  const fromBackend =
    !byKind && step.action && !TOOL_COPY[step.action] ? capitalize(step.action) : "";
  return {
    title: fromBackend || base?.title || capitalize(step.action) || "Работаю",
    hint: base?.hint ?? "Агент продолжает собирать приложение.",
    tone: base?.tone ?? "work",
    icon: base?.icon ?? "write",
  };
}
