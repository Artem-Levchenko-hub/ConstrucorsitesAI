import { apiFetch } from "./client";
import { MOCK_USER, USE_MOCKS } from "./mocks";
import type { User } from "./types";

export function getOwnerProfile(): Promise<User> {
  if (USE_MOCKS) return Promise.resolve(MOCK_USER);
  return apiFetch<User>("/api/auth/me", { cache: "no-store", timeoutMs: 10_000 });
}
