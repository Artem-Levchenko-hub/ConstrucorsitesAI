import { maxStaffEnabled } from "@/lib/max-staff-path";

const allGuideChapters = [
  { id: "start", number: "00", title: "Перед началом", description: "Определите задачу приложения и главный сценарий клиента." },
  { id: "account", number: "01", title: "Регистрация и вход", description: "Изучите порядок входа и подтверждения аккаунта." },
  { id: "project", number: "02", title: "Создание приложения", description: "Пройдите от описания идеи до подтверждения данных проекта." },
  { id: "builder", number: "03", title: "Редактор и проверка", description: "Узнайте, как описывать изменения и проверять результат." },
  { id: "settings", number: "04", title: "Документы и каталог", description: "Разберитесь, где сохраняются данные приложения и позиции каталога." },
  { id: "integrations", number: "05", title: "Подключение сервисов", description: "Узнайте, как подключить сервис и проверить его в приложении." },
  { id: "max-bot", number: "06", title: "Бот и подключение MAX", description: "Подготовьте бота и изучите этапы его подключения." },
  { id: "publish", number: "07", title: "Публикация приложения", description: "Проверьте условия получения постоянного адреса приложения." },
  { id: "partner", number: "08", title: "Адрес приложения в MAX", description: "Узнайте, где указать опубликованный адрес в настройках бота." },
  { id: "acceptance", number: "09", title: "Проверка в MAX", description: "Пройдите основной сценарий настоящего пользователя." },
  { id: "operations", number: "10", title: "Ежедневная работа", description: "Изучите разделы управления после запуска." },
  { id: "staff", number: "10.1", title: "Команда и рабочие очереди", description: "Настройте доступ сотрудников и разберите, как они обрабатывают заказы и заявки." },
  { id: "billing", number: "11", title: "Баланс и тариф", description: "Проверьте условия использования до оплаты." },
  { id: "errors", number: "12", title: "Если что-то не работает", description: "Найдите подходящий порядок проверки и обращения в поддержку." },
  { id: "checklists", number: "13", title: "Списки для запуска", description: "Используйте списки для самостоятельной проверки готовности." },
] as const;

export const guideChapters = allGuideChapters.filter(({ id }) => maxStaffEnabled || id !== "staff");

export type GuideLessonId = (typeof allGuideChapters)[number]["id"];

export function guideChapterHref(id: GuideLessonId) {
  return `/max/guide#${id}`;
}
