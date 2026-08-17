import type { GenerationRunStatus } from "@/lib/api/types";

export function agentTranscriptTitle(
  streaming: boolean,
  generationStatus?: GenerationRunStatus | null,
  lastStepFailed = false,
): string {
  if (streaming) return "Собираю приложение";
  if (generationStatus === "failed" || lastStepFailed) return "Сборка не завершена";
  if (generationStatus === "cancelled") return "Сборка отменена";
  return "Изменения готовы";
}
