import { apiFetch } from "./client";

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
