import type { AgentStep, GenerationRunStatus } from "@/lib/api/types";
import { generationRecoveryLabel, parseGenerationRecovery } from "@/lib/generation-recovery";

export const CAPACITY_WAITING_COPY = {
  title: "Ожидаю ресурсы сервера",
  detail:
    "Проект сохранён и запустится автоматически, как только освободится мощность.",
} as const;

export function isPromptPreparationPending(step: AgentStep | undefined): boolean {
  return step?.kind === "heartbeat" && step.tool === "prompt_preparation"
    && step.ok === null && step.skipped !== true;
}

export function agentTranscriptTitle(
  streaming: boolean,
  generationStatus?: GenerationRunStatus | null,
  lastStepFailed = false,
  recovery?: unknown,
  currentRequestComplete?: boolean,
  historicalRequest = false,
  preparingPrompt = false,
): string {
  if (generationStatus === "queued_for_capacity") {
    return CAPACITY_WAITING_COPY.title;
  }
  if (generationStatus === "failed") return "Подготовка на паузе";
  if (generationStatus === "cancelled") return "Работа остановлена";
  if (generationStatus === "completed") {
    if (currentRequestComplete === true) return "Итог запроса";
    // A later request replacing `latest` does not revoke this request's durable
    // completion. History makes no claim about the currently served preview.
    return historicalRequest ? "Запрос завершён" : "Проверка результата не подтверждена";
  }
  if (generationStatus === "cancel_requested") return "Останавливаю работу";
  const active = streaming || generationStatus === "pending" || generationStatus === "running";
  const ordinaryRecovery = recovery == null || parseGenerationRecovery(recovery)?.phase === "working";
  if (active && preparingPrompt && ordinaryRecovery) return "Подготавливаю промпт";
  if (recovery !== null && recovery !== undefined) return generationRecoveryLabel(recovery);
  if (active) return "Собираю приложение";
  if (lastStepFailed) return "Подготовка на паузе";
  return "Ход работы";
}
