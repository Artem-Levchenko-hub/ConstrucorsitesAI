import type { GenerationRunStatus } from "@/lib/api/types";

export const CAPACITY_WAITING_COPY = {
  title: "Ожидаю ресурсы сервера",
  detail:
    "Проект сохранён и запустится автоматически, как только освободится мощность.",
} as const;

export function agentTranscriptTitle(
  streaming: boolean,
  generationStatus?: GenerationRunStatus | null,
  lastStepFailed = false,
): string {
  if (generationStatus === "queued_for_capacity") {
    return CAPACITY_WAITING_COPY.title;
  }
  if (streaming) return "Собираю приложение";
  if (generationStatus === "failed" || lastStepFailed) return "Сборка не завершена";
  if (generationStatus === "cancelled") return "Сборка отменена";
  return "Изменения готовы";
}
