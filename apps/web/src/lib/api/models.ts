import { apiFetch } from "./client";
import { mockApi, USE_MOCKS } from "./mocks";
import type { Model } from "./types";

/**
 * Routes through the API layer (docs/01-api-contract.md): the backend proxies
 * to the LLM gateway, applies the 60s Redis cache, and falls back to a built-in
 * catalog if the gateway is unreachable. Talking to the gateway directly
 * skipped CORS-tightening and leaked the service boundary.
 */
export async function getModels(): Promise<Model[]> {
  if (USE_MOCKS) return mockApi.getModels();
  return apiFetch<Model[]>("/api/models", { cache: "no-store" });
}
