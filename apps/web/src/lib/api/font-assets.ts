import { ApiError, apiFetch, apiUrl } from "./client";
import type { ApiErrorBody, FontAssetPublic, FontRole } from "./types";

export const FONT_FILE_ACCEPT = ".ttf,.otf,.woff,.woff2";
export const FONT_LIMITS = { count: 6, bytes: 8 * 1024 * 1024 };
export const FONT_ROLE_LABELS: Record<FontRole, string> = {
  all: "Всё приложение", heading: "Заголовки", body: "Основной текст",
  accent: "Акценты", ui: "Кнопки и подписи", code: "Код",
};

export function isFontFile(file: File): boolean {
  return /\.(ttf|otf|woff2?)$/i.test(file.name);
}

export function validateFontFile(file: File): void {
  if (!isFontFile(file)) throw new Error("Поддерживаются TTF, OTF, WOFF и WOFF2.");
  if (!file.size || file.size > FONT_LIMITS.bytes) throw new Error("Файл шрифта должен быть непустым и не больше 8 МБ.");
}

/** Only known public messages are shown; storage/provider errors stay private. */
export function fontErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Войдите снова, чтобы прикрепить шрифт.";
    if (error.status === 413) return "Шрифт слишком большой. Загрузите файл до 8 МБ; сложные файлы могут превышать лимит после обработки.";
    if (error.status === 404) return "Шрифт не найден. Проверьте название, ссылку или доступ к проекту; можно загрузить файл.";
    if ([400, 415, 422].includes(error.status)) return "Не удалось принять шрифт. Проверьте название в бесплатном каталоге или загрузите исправный TTF, OTF, WOFF либо WOFF2.";
    if (error.status === 429) return "Слишком много загрузок. Подождите немного и повторите попытку.";
  }
  return "Не удалось загрузить шрифт. Повторите попытку — выбранный файл или название сохранены.";
}

export async function uploadFontAsset(projectId: string, file: File, signal?: AbortSignal): Promise<FontAssetPublic> {
  validateFontFile(file);
  const body = new FormData(); body.append("file", file);
  const response = await fetch(apiUrl(`/api/projects/${encodeURIComponent(projectId)}/font-assets`), {
    method: "POST", credentials: "include", body,
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(30_000)]) : AbortSignal.timeout(30_000),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as ApiErrorBody | null;
    throw new ApiError(response.status, payload?.error ?? { code: "internal_error", message: "Font upload failed" });
  }
  return response.json() as Promise<FontAssetPublic>;
}

export function resolveFontAsset(projectId: string, familyName: string, signal?: AbortSignal): Promise<FontAssetPublic> {
  return apiFetch<FontAssetPublic>(`/api/projects/${encodeURIComponent(projectId)}/font-assets/resolve`, {
    method: "POST", json: { family_name: familyName }, timeoutMs: 30_000, signal,
  });
}
