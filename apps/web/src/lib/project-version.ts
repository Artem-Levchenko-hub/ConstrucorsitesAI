import { apiUrl } from "./api/client";
import type { ProjectVersion } from "./api/types";

export const versionStatusLabel: Record<ProjectVersion["status"], string> = {
  queued: "В очереди", running: "Создаётся", ready: "Готова", failed: "Ошибка", cancelled: "Отменена", unchanged: "Без изменений",
};
export function projectVersionLabel(version: Pick<ProjectVersion, "prompt_text">): string {
  return version.prompt_text?.replace(/\s+/g, " ").trim() || "Версия приложения";
}

/** Compact display title; the disclosure uses the unmodified prompt_text. */
export function projectVersionTitle(version: Pick<ProjectVersion, "prompt_text">): string {
  const firstLine = version.prompt_text?.trim().split(/\r?\n/, 1)[0] ?? "";
  const title = firstLine.replace(/\s+/g, " ").trim() || "Версия приложения";
  return title.length > 72 ? `${title.slice(0, 71).trimEnd()}…` : title;
}

/** Backend-owned authenticated image paths use the API origin, including split-host setups. */
export function versionImageUrl(url: string): string {
  return url.startsWith("/api/") ? apiUrl(url) : url;
}
