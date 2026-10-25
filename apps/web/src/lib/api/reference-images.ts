import { apiUrl } from "./client";
import type { ReferenceImage } from "./types";

export const REFERENCE_IMAGE_LIMITS = { count: 4, bytes: 5 * 1024 * 1024, totalBytes: 12 * 1024 * 1024 };
export const REFERENCE_IMAGE_ACCEPT = "image/png,image/jpeg,image/webp";

export function referenceImageUrl(projectId: string, imageId: string): string {
  return apiUrl(`/api/projects/${encodeURIComponent(projectId)}/reference-images/${encodeURIComponent(imageId)}`);
}

export async function uploadReferenceImage(projectId: string, file: File): Promise<ReferenceImage> {
  const body = new FormData(); body.append("file", file);
  const response = await fetch(apiUrl(`/api/projects/${encodeURIComponent(projectId)}/reference-images`), {
    method: "POST", credentials: "include", body, signal: AbortSignal.timeout(30_000),
  });
  // Never echo provider/storage errors or private URLs into the composer.
  if (!response.ok) throw new Error(response.status === 401 ? "Войдите снова, чтобы прикрепить изображение." : "Не удалось загрузить изображение. Повторите попытку.");
  return response.json() as Promise<ReferenceImage>;
}

export async function validateReferenceImage(file: File): Promise<void> {
  if (!REFERENCE_IMAGE_ACCEPT.split(",").includes(file.type)) throw new Error("Поддерживаются PNG, JPEG и WebP. SVG и другие форматы не принимаются.");
  if (!file.size || file.size > REFERENCE_IMAGE_LIMITS.bytes) throw new Error(`Изображение должно быть не больше ${REFERENCE_IMAGE_LIMITS.bytes / 1024 / 1024} МБ.`);
  // The server decodes and validates again, including frame/decompression limits.
  const bitmap = await createImageBitmap(file).catch(() => { throw new Error("Не удалось прочитать изображение."); });
  try {
    if (bitmap.width > 8000 || bitmap.height > 8000 || bitmap.width * bitmap.height > 20_000_000) throw new Error("Изображение слишком большое: максимум 8000 пикселя по стороне и 20 Мп.");
  } finally { bitmap.close(); }
}
