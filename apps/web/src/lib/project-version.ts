import { apiUrl } from "./api/client";
import type { ProjectVersion } from "./api/types";

export const versionStatusLabel: Record<ProjectVersion["status"], string> = {
  queued: "В очереди", running: "Создаётся", ready: "Готова", failed: "Ошибка", cancelled: "Отменена", unchanged: "Без изменений",
};
export function projectVersionLabel(version: Pick<ProjectVersion, "prompt_text">): string {
  return version.prompt_text?.replace(/\s+/g, " ").trim() || "Версия приложения";
}

/** Backend-owned authenticated image paths use the API origin, including split-host setups. */
export function versionImageUrl(url: string): string {
  return url.startsWith("/api/") ? apiUrl(url) : url;
}
