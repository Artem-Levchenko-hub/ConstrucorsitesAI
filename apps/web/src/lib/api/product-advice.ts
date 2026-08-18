import { apiFetch } from "./client";
import type { Message } from "./types";

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
): string | null {
  const last = messages.at(-1);
  if (
    last?.role !== "assistant" ||
    !last.snapshot_id ||
    last.tokens_out === null ||
    (last.generation_status != null &&
      last.generation_status !== "completed")
  ) {
    return null;
  }
  return last.snapshot_id;
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
