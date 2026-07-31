export type MvpChecklistStatus = "done" | "in_progress" | "todo" | "external";

export type MvpChecklistItem = {
  id: string;
  title: string;
  detail: string;
  status: MvpChecklistStatus;
  completedAt?: string;
};

export type MvpChecklistSection = {
  id: string;
  number: string;
  title: string;
  description: string;
  items: MvpChecklistItem[];
};

export const mvpChecklistUpdatedAt = "31 июля 2026";

export const mvpChecklist: MvpChecklistSection[] = [
  {
    id: "access",
    number: "01",
    title: "Вход и бизнес-профиль",
    description: "Пользователь получает законный доступ к MAX Studio и не может обойти бизнес-лимиты новым аккаунтом.",
    items: [
      {
        id: "registration",
        title: "Регистрация и вход",
        detail: "Email, пароль, защищённая сессия, восстановление доступа и выход со всех устройств.",
        status: "done",
        completedAt: "30 июля 2026",
      },
      {
        id: "email-verification",
        title: "Подтверждение email",
        detail: "MAX-проект нельзя создать или оплатить до подтверждения рабочего адреса.",
        status: "done",
        completedAt: "30 июля 2026",
      },
      {
        id: "business-profile",
        title: "Профиль организации, ИП или самозанятого",
        detail: "ИНН уникален, реквизиты проверяются, бесплатный лимит закреплён за бизнесом.",
        status: "done",
        completedAt: "30 июля 2026",
      },
      {
        id: "legal-consents",
        title: "Согласия и документы",
        detail: "Версии оферты, политики и согласий сохраняются вместе с датой принятия.",
        status: "done",
        completedAt: "30 июля 2026",
      },
    ],
  },
  {
    id: "builder",
    number: "02",
    title: "Создание приложения",
    description: "От запроса обычными словами до стабильной версии, которую можно проверить и исправить.",
    items: [
      {
        id: "guided-brief",
        title: "Пошаговый бриф MAX Studio",
        detail: "Продукт, сценарий, стиль, оператор и поддержка собираются в одном управляемом пути.",
        status: "done",
        completedAt: "30 июля 2026",
      },
      {
        id: "durable-generation",
        title: "Восстанавливаемая генерация",
        detail: "Обновление страницы возвращает тот же запуск и прогресс, параллельный дубль не создаётся.",
        status: "done",
        completedAt: "30 июля 2026",
      },
      {
        id: "secure-preview",
        title: "Безопасное живое превью",
        detail: "Preview открывается через защищённую серверную сессию и показывает реальный dev-контейнер.",
        status: "done",
        completedAt: "31 июля 2026",
      },
      {
        id: "editing",
        title: "Правки и версии",
        detail: "Изменения сохраняются снимками, старую версию можно открыть и восстановить.",
        status: "done",
        completedAt: "30 июля 2026",
      },
      {
        id: "integrations",
        title: "Подключение сервисов",
        detail: "Секреты интеграций хранятся отдельно от кода и выдаются приложению по allowlist.",
        status: "done",
        completedAt: "30 июля 2026",
      },
    ],
  },
  {
    id: "money",
    number: "03",
    title: "Ledger и подписки",
    description: "Один бизнес — один финансовый контур, одна активная подписка и проверяемая история операций.",
    items: [
      {
        id: "versioned-plans",
        title: "Версионные тарифы Free, Pro и Business",
        detail: "Купленные условия не меняются задним числом при выпуске новой версии тарифа.",
        status: "done",
        completedAt: "31 июля 2026",
      },
      {
        id: "canonical-ledger",
        title: "Единый журнал операций",
        detail: "Пополнения, использование и возвраты проходят через один журнал с защитой от дублей.",
        status: "done",
        completedAt: "31 июля 2026",
      },
      {
        id: "business-account",
        title: "Единый платёжный аккаунт бизнеса",
        detail: "Кошелёк, журнал и подписка переводятся с пользователя на общий бизнес-контур без потери истории.",
        status: "done",
        completedAt: "31 июля 2026",
      },
      {
        id: "subscription-purchase",
        title: "Покупка и активация тарифа",
        detail: "Успешное подтверждение провайдера атомарно активирует купленную версию и начисляет включённый кредит.",
        status: "done",
        completedAt: "31 июля 2026",
      },
      {
        id: "renewal",
        title: "Продление и льготный период",
        detail: "Явное согласие, повторные попытки, уведомления и безопасное понижение после окончания периода.",
        status: "done",
        completedAt: "31 июля 2026",
      },
      {
        id: "plan-management",
        title: "Управление подпиской",
        detail: "Текущий тариф, следующая дата, отмена, восстановление и смена плана доступны в кабинете.",
        status: "done",
        completedAt: "31 июля 2026",
      },
      {
        id: "live-payment",
        title: "Живая оплата и чеки",
        detail: "Нужны реквизиты магазина и подтверждённая схема чеков для выбранной формы оператора.",
        status: "external",
      },
    ],
  },
  {
    id: "launch",
    number: "04",
    title: "Публикация в MAX",
    description: "Текущая версия получает постоянный HTTPS-адрес, webhook и запускается из настоящего клиента MAX.",
    items: [
      {
        id: "production-deploy",
        title: "Production-деплой и откат",
        detail: "Сборка, контейнер, HTTPS, health-check, история релизов и возврат к предыдущей версии.",
        status: "done",
        completedAt: "30 июля 2026",
      },
      {
        id: "max-bot",
        title: "Подключение MAX-бота",
        detail: "Ключ шифруется, бот проверяется через API, webhook активируется только после HTTPS-деплоя.",
        status: "done",
        completedAt: "30 июля 2026",
      },
      {
        id: "max-session",
        title: "Защищённая сессия пользователя MAX",
        detail: "Launch-параметры проверяются сервером, данные пользователей изолированы.",
        status: "done",
        completedAt: "30 июля 2026",
      },
      {
        id: "real-max-e2e",
        title: "Живой запуск внутри MAX",
        detail: "Нужен прогон с промодерированным ботом и двумя реальными пользователями.",
        status: "external",
      },
      {
        id: "partner-url",
        title: "Привязка URL в MAX Partner",
        detail: "Production URL добавляется к кнопке запуска и подтверждается в мастере Omnia.",
        status: "external",
      },
    ],
  },
  {
    id: "operations",
    number: "05",
    title: "Готовность к пользователям",
    description: "Сбои обнаруживаются автоматически, данные восстанавливаются, а полный путь доказан чистым прогоном.",
    items: [
      {
        id: "release-gate",
        title: "Блокирующая проверка релиза",
        detail: "Exact commit проходит typecheck, runtime и transport-security proof; отсутствие или повреждение attestation блокирует production.",
        status: "done",
        completedAt: "31 июля 2026",
      },
      {
        id: "monitoring",
        title: "Внешний мониторинг и оповещения",
        detail: "GitHub каждые 5 минут проверяет web, API, БД, Redis, worker, deploy/preview control plane, MAX-canary и webhook; incident дедуплицируется и закрывается после recovery.",
        status: "done",
        completedAt: "31 июля 2026",
      },
      {
        id: "offhost-backup",
        title: "Резервная копия вне сервера",
        detail: "PostgreSQL, runtime-конфиг, исходники и MinIO ежедневно шифруются офлайн-ключом и хранятся в GitHub Artifact; обратная загрузка, расшифровка и восстановление проверены.",
        status: "done",
        completedAt: "31 июля 2026",
      },
      {
        id: "key-rotation",
        title: "Ротация ранее использованных ключей",
        detail: "Семь внутренних ключей заменены атомарно, 9 сохранённых токенов перешифрованы; рабочие секреты отсутствуют во всей git-истории и доступны только серверному контуру.",
        status: "done",
        completedAt: "31 июля 2026",
      },
      {
        id: "golden-path",
        title: "Финальный путь нового пользователя",
        detail: "Регистрация → оплата → генерация → публикация → запуск в MAX → отмена продления.",
        status: "todo",
      },
    ],
  },
];
