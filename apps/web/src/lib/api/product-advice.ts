import { apiFetch } from "./client";
import type { Message } from "./types";
import { isChatMessageStreaming } from "@/lib/chat-message-status";

export type ProductAdviceItem = {
  id: string;
  kind: "feature" | "improvement";
  title: string;
  benefit: string;
  prompt: string;
};

export type ProductAdviceResponse = {
  version: string;
  project_id: string;
  current_snapshot_id: string;
  analysis_snapshot_id: string;
  archetype: string;
  source: "model" | "fallback" | "cache";
  items: ProductAdviceItem[];
};

type AdviceMessage = Pick<
  Message,
  "role" | "snapshot_id" | "tokens_out" | "generation_status"
>;

export function getProductAdviceSnapshotId(
  messages: readonly AdviceMessage[],
  currentSnapshotId?: string | null,
): string | null {
  const last = messages.at(-1);
  if (!last || last.role !== "assistant" || isChatMessageStreaming(last)) return null;
  const ready = messages.findLast((message) => message.role === "assistant" &&
    message.snapshot_id && message.tokens_out !== null &&
    message.generation_status !== "failed" && message.generation_status !== "cancelled");
  return ready ? currentSnapshotId ?? ready.snapshot_id : null;
}

export function submitProductAdvice(
  item: ProductAdviceItem,
  submit: (prompt: string, selections: []) => Promise<boolean>,
): Promise<boolean> {
  return submit(item.prompt, []);
}

export async function requestProductAdvice(
  projectId: string,
): Promise<ProductAdviceResponse> {
  return apiFetch<ProductAdviceResponse>(
    `/api/projects/${projectId}/product-advice`,
    {
      method: "POST",
      timeoutMs: 20_000,
    },
  );
}
