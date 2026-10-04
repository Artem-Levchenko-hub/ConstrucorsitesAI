import type {
  GenerationRun,
  GenerationRunStatus,
  Message,
  Uuid,
  WsEvent,
} from "@/lib/api/types";

export const ACTIVE_GENERATION_STATUSES: ReadonlySet<GenerationRunStatus> =
  new Set(["pending", "queued_for_capacity", "running", "cancel_requested"]);

export function isGenerationActive(
  generation: Pick<GenerationRun, "status"> | null | undefined,
): boolean {
  return (
    generation !== null &&
    generation !== undefined &&
    ACTIVE_GENERATION_STATUSES.has(generation.status)
  );
}

export function isActiveGenerationForMessage(
  generation: Pick<GenerationRun, "status" | "assistant_message_id"> | null | undefined,
  messageId: Uuid,
): boolean {
  return (
    isGenerationActive(generation) &&
    generation?.assistant_message_id === messageId
  );
}

/** Return the assistant message whose stream received meaningful activity.
 *
 * Agent builds can spend minutes emitting only `agent.step` events. Treating
 * only chat-content changes as activity produced a false three-minute timeout:
 * the browser closed its socket while the paid server-side build kept running.
 */
export function streamEventMessageId(event: WsEvent): Uuid | null {
  const data: unknown = event.data;
  if (typeof data !== "object" || data === null || !("message_id" in data)) {
    return null;
  }
  const messageId = (data as { message_id?: unknown }).message_id;
  return typeof messageId === "string" ? messageId : null;
}

export function isMaxBuildReady({
  snapshotsLoaded,
  generationLoaded,
  hasGeneratedSnapshot,
  generation,
}: {
  snapshotsLoaded: boolean;
  generationLoaded: boolean;
  hasGeneratedSnapshot: boolean;
  generation: Pick<GenerationRun, "status" | "response_mode"> | null | undefined;
}): boolean {
  if (!snapshotsLoaded || !generationLoaded || !hasGeneratedSnapshot) {
    return false;
  }
  if (isGenerationActive(generation)) {
    return false;
  }
  return !(
    generation?.response_mode === "build" && generation.status === "failed"
  );
}


/** Bind latest server truth for display without rewriting historical chat rows. */
export function bindMessageGeneration(message: Message, generation: GenerationRun | null | undefined, projectId: string): Message {
  if (message.role !== "assistant" || message.project_id !== projectId || generation?.project_id !== projectId
    || generation.assistant_message_id !== message.id) return message;
  return { ...message, generation_status: generation.status,
    generation_started_at: generation.started_at, generation_finished_at: generation.finished_at };
}
