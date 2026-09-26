import type { MaxJourneyStage } from "@/lib/max-journey";

/**
 * Что происходит с приложением — фразой, а не названием этапа.
 *
 * В списке приложений стояло имя этапа («Сборка приложения»), и по нему нельзя
 * понять, идёт сборка, её надо начать или она сломалась. Владельцу нужна
 * законченная фраза о его приложении и одна строка «почему это важно»; техническое
 * имя этапа остаётся внутри маршрута запуска.
 */

export type MaxProjectStateTone = "build" | "connect" | "live" | "checking" | "offline";

export type MaxProjectStateView = {
  title: string;
  hint: string;
  tone: MaxProjectStateTone;
};

const STAGE_STATE: Record<string, MaxProjectStateView> = {
  build: {
    title: "Приложение ещё не собрано",
    hint: "Опишите задачу в чате — ИИ соберёт первую версию",
    tone: "build",
  },
  app: {
    title: "Собрано, остались документы",
    hint: "Подтвердите политику и условия приложения — без них публикация не пройдёт",
    tone: "connect",
  },
  bot: {
    title: "Собрано, осталось подключить бота",
    hint: "Бот MAX нужен, чтобы приложение открывалось у пользователей",
    tone: "connect",
  },
  publish: {
    title: "Готово к публикации",
    hint: "Опубликуем версию и дадим постоянный адрес приложения",
    tone: "connect",
  },
  max: {
    title: "Опубликовано, остался адрес в MAX",
    hint: "Вставьте адрес в кабинет MAX — тогда приложение откроется у людей",
    tone: "connect",
  },
};

export function maxProjectStateView(
  currentStage: MaxJourneyStage | undefined,
  status: "ready" | "loading" | "error",
): MaxProjectStateView {
  if (status === "loading") {
    return {
      title: "Проверяем состояние",
      hint: "Через пару секунд покажем, что делать дальше",
      tone: "checking",
    };
  }
  if (status === "error") {
    // Формулировка по правилу «что случилось → опасно ли → что нажать»:
    // потеря связи с сервером не ломает приложение и не требует паники.
    return {
      title: "Не дозвонились до сервера",
      hint: "Приложение и данные это не ломает — откройте проект и проверим ещё раз",
      tone: "offline",
    };
  }
  if (!currentStage) {
    return {
      title: "Работает у пользователей",
      hint: "Изменения появятся у людей после новой публикации",
      tone: "live",
    };
  }
  return (
    STAGE_STATE[currentStage.id] ?? {
      title: currentStage.label,
      hint: currentStage.description,
      tone: "connect",
    }
  );
}
