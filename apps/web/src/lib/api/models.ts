import { mockApi, USE_MOCKS } from "./mocks";
import type { Model } from "./types";

/**
 * In prod we hit the LLM Gateway directly via the nginx /llm/* route — agent
 * B's /api/models proxy currently 500s, and the gateway endpoint is the
 * authoritative catalog anyway (it's the one that actually knows which
 * provider keys are present).
 *
 * The gateway returns `{ object: "list", data: Model[] }`; we unwrap to an
 * array so callers get the same shape that mockApi.getModels() returns.
 */
export async function getModels(): Promise<Model[]> {
  if (USE_MOCKS) return mockApi.getModels();

  const r = await fetch("/llm/v1/models", {
    credentials: "include",
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`models: HTTP ${r.status}`);
  const body = (await r.json()) as { data?: Model[] };
  return body.data ?? [];
}
