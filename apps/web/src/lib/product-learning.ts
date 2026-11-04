export type LearningStep = { id: string; selector: string; title: string; body: string };
export type LearningDefinition = { id: string; title: string; steps: readonly LearningStep[] };
type Rect = { left: number; top: number; width: number; height: number };

const step = (id: string, title: string, body: string, selector = `[data-learning="${id}"]`): LearningStep => ({ id, selector, title, body });
const cabinet: LearningDefinition = { id: "cabinet", title: "Обучение", steps: [
  step("create-project", "Создайте первое приложение", "Начните с описания своей идеи."),
  step("projects-search", "Найдите своё приложение", "Введите название в поиске, чтобы вернуться к нужному проекту."),
  step("projects-help", "Помощь всегда рядом", "Здесь находятся быстрый старт по созданию приложения и подробная инструкция по его запуску в MAX."),
  step("account-menu", "Ваш аккаунт", "В меню аккаунта доступны профиль, безопасность, баланс, операции и тариф."),
] };
const editor: LearningDefinition = { id: "editor", title: "Редактор приложения", steps: [
  step("editor-conversation", "Опишите нужное изменение", "В диалоге с ИИ создают и меняют экраны и функции. Проверяйте результат перед публикацией."),
  step("editor-preview", "Проверьте приложение", "Предпросмотр помогает пройти сценарий пользователя. На узком экране он открывается отдельной кнопкой."),
  step("editor-data", "Данные приложения", "Название, описание, документы и политики собраны в окне «Данные». Обычное сохранение данных не запускает ИИ.", '[data-testid="max-settings-open"]'),
  step("editor-catalog", "Управляйте каталогом", "Раздел «Каталог» позволяет работать с позициями приложения отдельно от изменения экранов."),
  step("editor-services", "Подключите нужные сервисы", "В «Интеграциях» выбирают сервис и проверяют подключение. Обучение не запрашивает доступ и не запускает доработку."),
  step("editor-publish", "Подготовьте запуск", "Кнопка публикации открывает проверку готовности и следующий шаг запуска в MAX."),
  step("editor-projects", "Проекты и аккаунт", "Это меню возвращает к вашим проектам и настройкам аккаунта."),
] };

const panel = (id: string, title: string, body: string): LearningDefinition => ({ id, title, steps: [
  step("editor-panel", title, body),
] });
const panels = {
  publish: panel("publish", "Проверьте готовность к публикации", "Здесь показаны условия запуска и доступное следующее действие. Проверьте сборку, документы и подключение MAX. Публикацию выполняете вы сами."),
  max: panel("max", "Подключите приложение к MAX", "Пройдите этапы подключения бота по порядку. Токены вводят только в предназначенную для них форму, не в диалог с ИИ."),
  services: panel("services", "Выберите нужную интеграцию", "Выберите сервис и изучите условия подключения. Подключение аккаунта и изменение приложения — отдельные действия; обучение их не выполняет."),
  navigation: panel("navigation", "Проекты и аккаунт", "Здесь находятся переходы к проектам и настройкам аккаунта. Обучение не переключает проект: выберите нужную ссылку после его закрытия."),
  tools: panel("tools", "Инструменты редактора", "В этом окне собраны доступные инструменты проекта. Изучите их назначение перед использованием; обучение ничего не запускает."),
  preview: panel("preview", "Предпросмотр приложения", "Здесь можно пройти сценарий пользователя и проверить результат. Обучение не нажимает кнопки внутри приложения и не отправляет его формы."),
};
const details: LearningDefinition = { id: "details", title: "Данные приложения", steps: [
  step("data-sections", "Выберите раздел данных", "Вкладки разделяют основные сведения, контент, документы и политики. Обучение не переключает вкладки и не меняет введённые данные.", '.max-setup-tabs [role="tablist"]'),
  step("data-content", "Проверьте данные перед сохранением", "Изменяйте поля самостоятельно. Для изменения экранов с ИИ предусмотрено отдельное действие.", '[data-testid="max-settings-scroll-region"]'),
] };
const projectSections: Record<string, [string, string]> = {
  catalog: ["Каталог приложения", "Здесь управляют позициями каталога. После сохранения проверьте название, цену и доступность в своём приложении."],
  manage: ["Ежедневная работа", "Здесь собраны доступные разделы управления. Их набор зависит от функций вашего приложения."],
  dashboard: ["Статистика приложения", "Изучайте показатели и состояния, доступные на этой странице. Обучение не читает данные ваших клиентов."],
  settings: ["Настройки приложения", "Здесь находятся параметры приложения. Проверяйте внесённые изменения перед сохранением."],
  integrations: ["Интеграции приложения", "Выберите сервис и проверьте статус его подключения. Доступ предоставляется только вашим отдельным действием."],
  publish: ["Запуск приложения", "Проверьте готовность сборки и условия публикации, затем выполните нужный шаг самостоятельно."],
};
const accounts: Record<string, [string, string, string]> = {
  "/account": ["account", "Ваш профиль", "Основные сведения аккаунта находятся здесь. Обучение ничего не сохраняет и не меняет."],
  "/account/security": ["security", "Безопасность аккаунта", "Здесь можно проверить активные сессии и устройства. Обучение не отзывает доступ."],
  "/billing": ["billing", "Баланс и пополнение", "Изучите доступные пакеты и условия до оплаты. Переход к оплате требует вашего отдельного действия."],
  "/billing/plan": ["plan", "Ваш тариф", "Здесь показаны режим эксплуатации приложения и доступные лимиты. Обучение не меняет тариф."],
  "/billing/transactions": ["transactions", "История операций", "Здесь проверяют платежи, начисления и их статусы. Обучение не выполняет финансовых операций."],
};
const guideChapters = [
  ["start", "Перед началом", "Определите задачу приложения и главный сценарий клиента."],
  ["account", "Регистрация и вход", "Изучите порядок входа и подтверждения аккаунта."],
  ["project", "Создание приложения", "Пройдите от описания идеи до подтверждения данных проекта."],
  ["builder", "Редактор и проверка", "Узнайте, как описывать изменения и проверять результат."],
  ["settings", "Документы и каталог", "Разберитесь, где сохраняются данные приложения и позиции каталога."],
  ["integrations", "Подключение сервисов", "Изучите подключение оплаты, CRM и аналитики."],
  ["amocrm", "Заявки в amoCRM", "Этот пример пригодится, если вашему приложению нужна CRM."],
  ["max-bot", "Бот и подключение MAX", "Подготовьте бота и изучите этапы его подключения."],
  ["publish", "Публикация приложения", "Проверьте условия получения постоянного адреса приложения."],
  ["partner", "Адрес приложения в MAX", "Узнайте, где указать опубликованный адрес в настройках бота."],
  ["acceptance", "Проверка в MAX", "Пройдите основной сценарий настоящего пользователя."],
  ["operations", "Ежедневная работа", "Изучите разделы управления после запуска."],
  ["billing", "Баланс и тариф", "Проверьте условия использования до оплаты."],
  ["errors", "Если что-то не работает", "Найдите подходящий порядок проверки и обращения в поддержку."],
  ["checklists", "Списки для запуска", "Используйте списки для самостоятельной проверки готовности."],
] as const;
const guide: LearningDefinition = { id: "guide", title: "Руководство Yleum", steps: [
  step("guide-journey", "Ваш путь к запуску", "Переходите по шагам кнопками «Далее» и «Назад»: обучение прокрутит руководство и выделит настоящий заголовок. Действия в приложении остаются за вами."),
  ...guideChapters.map(([id, title, body]) => step(id, title, body, `#${id} h2`)),
] };

/** A real open surface can differ from the URL for transient editor dialogs. */
export function learningForSurface(surface: string): LearningDefinition | null {
  if (surface === "details") return details;
  return Object.hasOwn(panels, surface) ? panels[surface as keyof typeof panels] : null;
}

export function learningForRoute(pathname: string, query: string): LearningDefinition | null {
  if (pathname === "/max/guide") return guide;
  if (pathname === "/max") return cabinet;
  const account = accounts[pathname];
  if (account) return { id: account[0], title: account[1], steps: [
    step("account-content", account[1], account[2], ".account-content"),
    step("account-navigation", "Разделы аккаунта", "Переключайтесь между профилем, безопасностью, балансом, операциями и тарифом."),
    step("account-projects", "Вернитесь к приложениям", "Эта ссылка возвращает в вашу студию."),
  ] };
  const match = /^\/max\/([^/]+)(?:\/(catalog|manage|dashboard|settings|integrations|publish))?$/.exec(pathname);
  if (!match || ["onboarding", "start", "guide", "new", "preview"].includes(match[1])) return null;
  if (match[2]) {
    const [title, body] = projectSections[match[2]];
    return { id: match[2], title, steps: [
      step("project-section", title, body),
      step("project-navigation", "Разделы приложения", "Переключайтесь между редактором, данными, управлением и другими доступными разделами."),
      step("project-help", "Подробная инструкция", "Помощь открывает соответствующий раздел руководства."),
    ] };
  }
  const params = new URLSearchParams(query);
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
