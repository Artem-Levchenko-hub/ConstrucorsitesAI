import { guideChapters, type GuideLessonId } from "./guide-lessons";
import { maxConnectionSearch } from "./max-editor-entry";

export type LearningStep = { id: string; selector: string; title: string; body: string; lesson?: GuideLessonId };
export type LearningDefinition = { id: string; title: string; steps: readonly LearningStep[]; lesson: GuideLessonId };
type Rect = { left: number; top: number; width: number; height: number };

function step(id: string, title: string, body: string, { selector = `[data-learning="${id}"]`, lesson }: {
  selector?: string; lesson?: GuideLessonId;
} = {}): LearningStep {
  return { id, selector, title, body, lesson };
}

const editor: LearningDefinition = { id: "editor", title: "Редактор приложения", lesson: "builder", steps: [
  step("editor-conversation", "Опишите нужное изменение", "В диалоге с ИИ создают и меняют экраны и функции. Проверяйте результат перед публикацией."),
  step("editor-preview", "Проверьте приложение", "Предпросмотр помогает пройти сценарий пользователя. На узком экране он открывается отдельной кнопкой."),
  step("editor-manage", "Управляйте приложением", "Из «Управления» можно перейти к заказам, каталогу, данным приложения и статистике.", { lesson: "operations" }),
  step("editor-services", "Подключите нужные сервисы", "В «Интеграциях» выбирают сервис и проверяют подключение. Обучение не запрашивает доступ и не запускает доработку.", { lesson: "integrations" }),
  step("editor-publish", "Подготовьте запуск", "Кнопка публикации открывает проверку готовности и следующий шаг запуска в MAX.", { lesson: "publish" }),
  step("editor-projects", "Проекты и аккаунт", "Это меню возвращает к вашим проектам и настройкам аккаунта.", { lesson: "account" }),
] };

const panel = (id: string, title: string, body: string, lesson: GuideLessonId): LearningDefinition => ({ id, title, lesson, steps: [
  step("editor-panel", title, body),
] });
const panels = {
  publish: panel("publish", "Проверьте готовность к публикации", "Здесь показаны условия запуска и доступное следующее действие. Проверьте сборку, документы и подключение MAX. Публикацию выполняете вы сами.", "publish"),
  max: panel("max", "Подключите приложение к MAX", "Пройдите этапы подключения бота по порядку. Токены вводят только в предназначенную для них форму, не в диалог с ИИ.", "max-bot"),
  services: panel("services", "Выберите нужную интеграцию", "Выберите сервис и изучите условия подключения. Подключение аккаунта и изменение приложения — отдельные действия; обучение их не выполняет.", "integrations"),
  navigation: panel("navigation", "Проекты и аккаунт", "Здесь находятся переходы к проектам и настройкам аккаунта. Обучение не переключает проект: выберите нужную ссылку после его закрытия.", "account"),
  tools: panel("tools", "Инструменты редактора", "В этом окне собраны доступные инструменты проекта. Изучите их назначение перед использованием; обучение ничего не запускает.", "builder"),
  preview: panel("preview", "Предпросмотр приложения", "Здесь можно пройти сценарий пользователя и проверить результат. Обучение не нажимает кнопки внутри приложения и не отправляет его формы.", "builder"),
};
const details: LearningDefinition = { id: "details", title: "Данные приложения", lesson: "settings", steps: [
  step("data-sections", "Выберите раздел данных", "Вкладки разделяют основные сведения, контент, документы и политики. Обучение не переключает вкладки и не меняет введённые данные.", { selector: '.max-setup-tabs [role="tablist"]' }),
  step("data-content", "Проверьте данные перед сохранением", "Изменяйте поля самостоятельно. Для изменения экранов с ИИ предусмотрено отдельное действие.", { selector: '[data-testid="max-settings-scroll-region"]' }),
] };
const projectSections: Record<string, [title: string, body: string, lesson: GuideLessonId]> = {
  catalog: ["Каталог приложения", "Здесь управляют позициями каталога. После сохранения проверьте название, цену и доступность в своём приложении.", "settings"],
  manage: ["Ежедневная работа", "Здесь собраны доступные разделы управления. Их набор зависит от функций вашего приложения.", "operations"],
  dashboard: ["Статистика приложения", "Изучайте показатели и состояния, доступные на этой странице. Обучение не читает данные ваших клиентов.", "operations"],
  settings: ["Настройки приложения", "Здесь находятся параметры приложения. Проверяйте внесённые изменения перед сохранением.", "settings"],
  integrations: ["Интеграции приложения", "Выберите сервис и проверьте статус его подключения. Доступ предоставляется только вашим отдельным действием.", "integrations"],
  publish: ["Запуск приложения", "Проверьте готовность сборки и условия публикации, затем выполните нужный шаг самостоятельно.", "publish"],
};
const accounts: Record<string, [string, string, string]> = {
  "/account": ["account", "Ваш профиль", "Основные сведения аккаунта находятся здесь. Обучение ничего не сохраняет и не меняет."],
  "/account/security": ["security", "Безопасность аккаунта", "Здесь можно проверить активные сессии и устройства. Обучение не отзывает доступ."],
  "/billing": ["billing", "Баланс и пополнение", "Изучите доступные пакеты и условия до оплаты. Переход к оплате требует вашего отдельного действия."],
  "/billing/plan": ["plan", "Ваш тариф", "Здесь показаны режим эксплуатации приложения и доступные лимиты. Обучение не меняет тариф."],
  "/billing/transactions": ["transactions", "История операций", "Здесь проверяют платежи, начисления и их статусы. Обучение не выполняет финансовых операций."],
};
const guide: LearningDefinition = { id: "guide", title: "Руководство Yleum", lesson: "start", steps: [
  step("guide-journey", "Ваш путь к запуску", "Переходите по шагам кнопками «Далее» и «Назад»: обучение прокрутит руководство и выделит настоящий заголовок. Действия в приложении остаются за вами."),
  ...guideChapters.map(({ id, title, description }) => step(id, title, description, { selector: `#${id} h2`, lesson: id })),
] };

function projectSectionLearning(id: string, lesson?: GuideLessonId): LearningDefinition {
  const [title, body, sectionLesson] = projectSections[id];
  return { id, title, lesson: lesson ?? sectionLesson, steps: [
    step("project-section", title, body),
    step("project-navigation", "Разделы приложения", "Переключайтесь между редактором, данными, управлением и другими доступными разделами."),
    step("project-help", "Подробная инструкция", "Помощь открывает соответствующий раздел руководства."),
  ] };
}

/** A real open surface can differ from the URL for transient editor dialogs. */
export function learningForSurface(surface: string): LearningDefinition | null {
  if (surface === "details") return details;
  if (surface === "staff-team") return projectSectionLearning("manage", "staff");
  return Object.hasOwn(panels, surface) ? panels[surface as keyof typeof panels] : null;
}

export function learningForRoute(pathname: string, query: string): LearningDefinition | null {
  if (pathname === "/max/guide") return guide;
  const account = accounts[pathname];
  if (account) return { id: account[0], title: account[1], lesson: pathname.startsWith("/billing") ? "billing" : "account", steps: [
    step("account-content", account[1], account[2], { selector: ".account-content" }),
    step("account-navigation", "Разделы аккаунта", "Переключайтесь между профилем, безопасностью, балансом, операциями и тарифом."),
    step("account-projects", "Вернитесь к приложениям", "Эта ссылка возвращает в вашу студию.", { lesson: "project" }),
  ] };
  const match = /^\/max\/([^/]+)(?:\/(catalog|manage|dashboard|settings|integrations|publish))?$/.exec(pathname);
  if (!match || ["onboarding", "start", "guide", "new", "preview"].includes(match[1])) return null;
  const params = new URLSearchParams(query);
  if (match[2]) {
    const botTab = match[2] === "integrations" && !maxConnectionSearch(Object.fromEntries(params)).size;
    return projectSectionLearning(match[2], botTab ? "max-bot" : undefined);
  }
  const selected = params.get("panel");
  const selectedPanel = selected ? learningForSurface(selected) : null;
  if (selectedPanel) return selectedPanel;
  if (params.has("data")) return details;
  return editor;
}

export function placeLearningCard(target: Rect | null, viewport: { width: number; height: number }, card: { width: number; height: number }): { left: number; top: number } {
  const margin = 16;
  const clamp = (value: number, extent: number, size: number) => Math.max(margin, Math.min(value, extent - size - margin));
  let left = (viewport.width - card.width) / 2;
  let top = (viewport.height - card.height) / 2;
  if (target) {
    if (target.left + target.width + card.width + margin * 2 <= viewport.width) {
      left = target.left + target.width + margin;
      top = target.top;
    } else {
      left = target.left;
      const below = target.top + target.height + margin;
      top = below + card.height <= viewport.height - margin ? below : target.top - card.height - margin;
    }
  }
  return { left: clamp(left, viewport.width, card.width), top: clamp(top, viewport.height, card.height) };
}
