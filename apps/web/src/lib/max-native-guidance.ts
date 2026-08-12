import type { MaxJourneyStageId } from "@/lib/max-journey";

export type MaxNativeGuidance = {
  title: string;
  userAction: string;
  omniaAction: string;
  maxAction: string;
  successSignal: string;
  maxRequiredNow: boolean;
};

const GUIDANCE: Record<MaxJourneyStageId, MaxNativeGuidance> = {
  demo: {
    title: "Соберите и проверьте демо",
    userAction:
      "Напишите в чате, кто будет пользоваться приложением и какое одно действие должно работать первым. Затем нажмите все основные кнопки в превью.",
    omniaAction:
      "Соберёт мобильный сценарий, покажет результат в живом превью и сохранит версию.",
    maxAction:
      "Пока ничего. Аккаунт MAX Partner, бот, токен и модерация для демо не нужны.",
    successSignal:
      "В превью открывается нужный экран и выполняется главное действие.",
    maxRequiredNow: false,
  },
  app: {
    title: "Заполните карточку приложения",
    userAction:
      "Укажите владельца, поддержку, управляемый контент и политики. Отметьте реальные продажи, пользовательский контент и уведомления.",
    omniaAction:
      "Проверит обязательные поля и обновит юридические страницы без новой AI-генерации.",
    maxAction:
      "Пока ничего. Эти данные понадобятся для карточки и модерации позже.",
    successSignal:
      "Этап «Карточка и документы» отмечен галочкой.",
    maxRequiredNow: false,
  },
  access: {
    title: "Подготовьте владельца к запуску",
    userAction:
      "Подтвердите рабочий email, выберите реального владельца, внесите ИНН и подключите Pro только когда решите публиковать.",
    omniaAction:
      "Проверит реквизиты один раз и откроет постоянный HTTPS, webhook и публикацию для проектов этого бизнеса.",
    maxAction:
      "Ничего создавать пока не нужно. Запомните выбранного владельца — бот в MAX должен принадлежать ему же.",
    successSignal:
      "На странице владельца показано «Доступ к запуску готов».",
    maxRequiredNow: false,
  },
  max: {
    title: "Создайте и подключите MAX-бота",
    userAction:
      "Откройте MAX Partner, создайте бота от того же владельца, заполните карточку, дождитесь одобрения и скопируйте секрет Bot API в Studio.",
    omniaAction:
      "Проверит секрет через Bot API, сохранит его зашифрованно и подготовит webhook.",
    maxAction:
      "MAX Partner → Чат-боты → создать или открыть бота → пройти модерацию → скопировать секрет в разделе Bot API.",
    successSignal:
      "В Studio у MAX-бота появился статус «Подключён».",
    maxRequiredNow: true,
  },
  publish: {
    title: "Опубликуйте текущую версию",
    userAction:
      "Запустите публикацию и дождитесь зелёного статуса. Страницу можно закрыть — процесс продолжится на сервере.",
    omniaAction:
      "Зафиксирует текущую версию, соберёт production, проверит health-check и выдаст постоянный HTTPS-адрес.",
    maxAction:
      "Пока дождитесь HTTPS-адреса. Не вставляйте временную ссылку превью в карточку MAX.",
    successSignal:
      "Показаны статус «Версия опубликована» и постоянный HTTPS-адрес.",
    maxRequiredNow: false,
  },
  verify: {
    title: "Привяжите HTTPS-адрес в MAX",
    userAction:
      "Скопируйте production URL, вставьте его в мини-приложение бота в MAX Partner, сохраните и вернитесь в Studio для проверки.",
    omniaAction:
      "Откроет MAX Partner с уже скопированным URL, проверит доступность адреса и сохранит ваше подтверждение.",
    maxAction:
      "MAX Partner → Чат-боты → ваш бот → Расширенные настройки → Мини-приложение → вставить URL → сохранить.",
    successSignal:
      "URL подтверждён, приложение открывается кнопкой бота внутри MAX.",
    maxRequiredNow: true,
  },
};

const COMPLETE_GUIDANCE: MaxNativeGuidance = {
  title: "Проверьте приложение как пользователь",
  userAction:
    "Откройте приложение из бота в MAX, пройдите главный сценарий и повторите проверку со второго аккаунта.",
  omniaAction:
    "Продолжит показывать health-check, состояние webhook, runtime и историю публикаций.",
  maxAction:
    "Откройте кнопку приложения в чате с ботом, выполните главное действие и проверьте сообщение или событие от бота.",
  successSignal:
    "Сценарий проходит внутри MAX без ручного исправления данных.",
  maxRequiredNow: true,
};

export function getMaxNativeGuidance(
  stageId: MaxJourneyStageId | undefined,
): MaxNativeGuidance {
  return stageId ? GUIDANCE[stageId] : COMPLETE_GUIDANCE;
}
