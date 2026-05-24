import { apiFetch } from "./client";
import { mockApi, USE_MOCKS } from "./mocks";
import type { Message, PromptResponse, SelectedElement } from "./types";

export async function listMessages(projectId: string): Promise<Message[]> {
  if (USE_MOCKS) return mockApi.listMessages(projectId);
  return apiFetch<Message[]>(`/api/projects/${projectId}/messages`);
}

export async function sendPrompt(
  projectId: string,
  prompt: string,
  modelId: string,
  selectedElements?: SelectedElement[] | null,
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
    // Omit the field entirely when there are no picks — keeps old behaviour
    // byte-identical and the backend field optional.
    json: {
      prompt,
      model_id: modelId,
      ...(selectedElements && selectedElements.length
        ? { selected_elements: selectedElements }
        : {}),
    },
  });
}
