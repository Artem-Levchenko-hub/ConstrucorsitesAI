import type { GenerationEventEnvelope, GenerationRecoveryView, GenerationRun, Uuid } from "@/lib/api/types";
import { isGenerationActive } from "@/lib/generation-lifecycle";

export type { GenerationRecoveryView } from "@/lib/api/types";

const LABELS: Record<GenerationRecoveryView["phase"], string> = {
  working: "Продолжаю работу",
  repairing: "Исправляю найденную проблему",
  waiting_dependency: "Работа сохранена, ожидаю восстановления",
  reconciling: "Проверяю результат сохранённого действия",
  verifying: "Проверяю готовность приложения",
  needs_input: "Нужно ваше решение",
};

export const RECOVERY_UNCONFIRMED_LABEL = "Статус сохранённой работы пока не подтверждён";

// Only server-defined public codes may reach the display. Diagnostics stay private.
const ACTIONS = new Set([
  "source_saved", "command_completed", "response_saved", "build_verified",
  "behavior_verified", "snapshot_saved", "publication_confirmed",
]);
const REASONS = new Set([
  "dependency_unavailable", "provider_unavailable", "provider_credentials",
  "provider_balance", "readers_unavailable", "deployment", "operation_pending",
  "state_unavailable", "resource_policy", "progress_required",
]);
const FIELDS = new Set([
  "version", "phase", "last_verified_action", "reason", "next_check_at", "attempt_seq",
]);

function isoDateTime(value: unknown): value is string {
  return typeof value === "string"
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    && Number.isFinite(Date.parse(value));
}

export function parseGenerationRecovery(value: unknown): GenerationRecoveryView | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== FIELDS.size || Object.keys(row).some(key => !FIELDS.has(key))
    || row.version !== 1 || typeof row.phase !== "string" || !Object.hasOwn(LABELS, row.phase)
    || !Number.isSafeInteger(row.attempt_seq) || (row.attempt_seq as number) < 0
    || (row.next_check_at !== null && !isoDateTime(row.next_check_at))
    || (row.reason !== null && typeof row.reason !== "string")
    || (row.last_verified_action !== null && typeof row.last_verified_action !== "string")) return null;
  return {
    version: 1,
    phase: row.phase as GenerationRecoveryView["phase"],
    last_verified_action: typeof row.last_verified_action === "string" && ACTIONS.has(row.last_verified_action)
      ? row.last_verified_action : null,
    reason: typeof row.reason === "string" && REASONS.has(row.reason) ? row.reason : null,
    next_check_at: row.next_check_at as string | null,
    attempt_seq: row.attempt_seq as number,
  };
}

export function generationRecoveryLabel(value: unknown): string {
  const recovery = parseGenerationRecovery(value);
  return recovery ? LABELS[recovery.phase] : RECOVERY_UNCONFIRMED_LABEL;
}

const ACTION_LABELS: Record<string, string> = {
  source_saved: "Изменения сохранены",
  command_completed: "Действие выполнено",
  response_saved: "Ответ сохранён",
  build_verified: "Сборка проверена",
  behavior_verified: "Работа функций проверена",
  snapshot_saved: "Версия сохранена",
  publication_confirmed: "Новая версия подключена",
};

export function generationVerifiedActionLabel(value: unknown): string | null {
  const action = parseGenerationRecovery(value)?.last_verified_action;
  return action ? ACTION_LABELS[action] ?? null : null;
}

export function generationEventBelongsToRun(value: unknown, binding: {
  projectId: string; runId: string; messageId: string;
} | null): value is GenerationEventEnvelope {
  if (!binding || !value || typeof value !== "object" || Array.isArray(value)) return false;
  const event = value as Record<string, unknown>;
  if (event.run_id !== binding.runId || typeof event.event_id !== "string" || !event.event_id
    || typeof event.type !== "string" || !Number.isSafeInteger(event.seq) || (event.seq as number) <= 0
    || !event.data || typeof event.data !== "object" || Array.isArray(event.data)) return false;
  const data = event.data as Record<string, unknown>;
  return data.message_id === binding.messageId
    && (data.project_id === undefined || data.project_id === binding.projectId);
}

export type AcceptedPreviewState = {
  kind: "preparing" | "accepted";
  snapshotId: Uuid | null;
  acceptedAt: string | null;
  pendingRunId: Uuid | null;
  isCurrentRequestComplete: boolean;
};

export function acceptedPreviewLabel(preview: AcceptedPreviewState, currentRun: GenerationRun | null | undefined): string {
  if (preview.kind !== "accepted") return "Готовая версия приложения пока не подтверждена.";
  if (preview.isCurrentRequestComplete) return "Показана сохранённая версия. Текущий запрос завершён.";
  const prefix = "Показана последняя сохранённая версия. ";
  if (isGenerationActive(currentRun)) return prefix + "Текущий запрос ещё не завершён.";
  if (currentRun?.status === "failed") return prefix + "Последний запрос завершился с ошибкой.";
  if (currentRun?.status === "cancelled") return prefix + "Последний запрос отменён.";
  if (currentRun?.status === "completed") return prefix + "Результат последнего запроса пока не подтверждён.";
  return prefix + "Статус последнего запроса пока не подтверждён.";
}

/** Completion events and agent prose cannot replace the exact public receipt. */
export function isGenerationResultConfirmed(run: GenerationRun | null | undefined): boolean {
  const acceptance = run?.acceptance;
  return Boolean(run?.status === "completed" && isoDateTime(run.finished_at)
    && run.recovery_unavailable !== true
    && (run.recovery == null || parseGenerationRecovery(run.recovery) !== null)
    && acceptance?.current_request_complete === true && acceptance.run_id === run.id
    && acceptance.assistant_message_id === run.assistant_message_id);
}

export function acceptedPreviewState({ acceptedSnapshotId, acceptedAt, currentRun }: {
  acceptedSnapshotId: Uuid | null;
  acceptedAt: string | null;
  currentRun: GenerationRun | null | undefined;
}): AcceptedPreviewState {
  const acceptance = currentRun?.acceptance;
  const complete = isGenerationResultConfirmed(currentRun)
    && (acceptance?.snapshot_id === null || acceptance?.snapshot_id === acceptedSnapshotId);
  return {
    kind: acceptedSnapshotId ? "accepted" : "preparing",
    snapshotId: acceptedSnapshotId,
    acceptedAt: acceptedAt && isoDateTime(acceptedAt) ? acceptedAt : null,
    pendingRunId: complete ? null : currentRun?.id ?? null,
    isCurrentRequestComplete: complete,
  };
}
