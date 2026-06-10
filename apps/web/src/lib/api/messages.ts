import { apiFetch } from "./client";
import { mockApi, USE_MOCKS } from "./mocks";
import type { Message, PromptResponse, SelectedElement } from "./types";

export async function listMessages(projectId: string): Promise<Message[]> {
  if (USE_MOCKS) return mockApi.listMessages(projectId);
  return apiFetch<Message[]>(`/api/projects/${projectId}/messages`);
}

/** An uncaught JS error the inspector observed inside the live preview. The
 *  server turns it into a chat card (category "client"). Best-effort — the
 *  caller swallows failures (a missing card must never disrupt the preview). */
export async function reportClientError(
  projectId: string,
  err: {
    message: string;
    source?: string;
    line?: number;
    col?: number;
    stack?: string;
    /** Route the error fired on + last user actions (element identity only,
     *  never typed values) — surfaced in the card body and the fix-prompt. */
    route?: string;
    crumbs?: string[];
  },
): Promise<void> {
  if (USE_MOCKS) return;
  await apiFetch<void>(`/api/projects/${projectId}/client-error`, {
    method: "POST",
    json: err,
    timeoutMs: 10_000,
  });
}

export async function sendPrompt(
  projectId: string,
  prompt: string,
  modelId: string,
  selectedElements?: SelectedElement[] | null,
  opts?: { skipClarify?: boolean },
): Promise<PromptResponse> {
  if (USE_MOCKS) {
    const { assistantMessageId } = mockApi.beginPrompt(
      projectId,
      prompt,
      modelId,
    );
    return { message_id: assistantMessageId, snapshot_id: null };
  }
  return apiFetch<PromptResponse>(`/api/projects/${projectId}/prompt`, {
    method: "POST",
    // 30s wall-clock cap on the POST itself. The backend should respond
    // with `202 Accepted` and a `message_id` within ~1s — anything longer
    // means the server is unhealthy (DB down, queue saturated, network
    // wedged). Without the cap the browser hangs forever and the user
    // sees "AI читает контекст" with no end. With the cap, the chat hook
    // catches ApiError(0, "timed out") and shows a real toast within 30s.
    timeoutMs: 30_000,
    // model_id is intentionally NOT sent — the server orchestrates per-role
    // models (no user model picker). `modelId` is kept in the signature only
    // for the mock path / optimistic chat row.
    // Omit selected_elements entirely when there are no picks — keeps old
    // behaviour byte-identical and the backend field optional.
    json: {
      prompt,
      ...(selectedElements && selectedElements.length
        ? { selected_elements: selectedElements }
        : {}),
      // Onboarding quiz already gathered the brief → skip the server clarify
      // interview so this enriched first prompt builds immediately.
      ...(opts?.skipClarify ? { skip_clarify: true } : {}),
    },
  });
}
