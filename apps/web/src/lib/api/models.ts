import { apiFetch } from "./client";
import { mockApi, USE_MOCKS } from "./mocks";
import type { Model } from "./types";

export async function getModels(): Promise<Model[]> {
  if (USE_MOCKS) return mockApi.getModels();
  return apiFetch<Model[]>("/api/models");
}
