import type { ProjectVersion } from "./api/types";

/** A capture belongs to a completed version, never to a failed run's base. */
export function maxVersionImage(version?: ProjectVersion | null) {
  if (!version || !["ready", "unchanged"].includes(version.status) || version.preview_status !== "ready") return undefined;
  return version.previews.filter((image) => image.url.trim() && image.width > 0 && image.height > 0)
    .sort((a, b) => Math.abs(a.width - 390) - Math.abs(b.width - 390))[0];
}

export function maxHistoryState(version?: ProjectVersion | null, imageFailed = false) {
  if (!version) return { label: "Версия недоступна", hint: "Обновите историю или выберите другую версию.", tone: "warning" };
  if (version.status === "failed") return { label: "Не завершилась", hint: "Эта попытка не создала готовую версию. Предыдущий результат остался в истории.", tone: "danger" };
  if (version.status === "cancelled") return { label: "Отменена", hint: "Генерация остановлена. Готовой версии у этой попытки нет.", tone: "muted" };
  if (version.status === "queued") return { label: "В очереди", hint: "Запрос принят. Результат появится после завершения генерации.", tone: "progress" };
  if (version.status === "running") return { label: "Создаётся", hint: "Генерация ещё идёт. Готовая версия появится здесь со снимком.", tone: "progress" };
  if (imageFailed) return { label: "Не удалось загрузить снимок", hint: "Версия сохранена. Можно повторить загрузку её изображения.", tone: "warning" };
  if (version.preview_status === "pending") return { label: "Снимок готовится", hint: "Версия сохранена. Когда её снимок будет готов, она появится в ленте.", tone: "progress" };
  return { label: "Снимок не сохранён", hint: "Версия сохранена, но просмотр пока недоступен. История и номер версии не изменились.", tone: "warning" };
}
