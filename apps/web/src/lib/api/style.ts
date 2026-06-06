import { apiFetch } from "./client";
import type { FontOption, Snapshot, StylePatchPayload } from "./types";

/** Fonts the in-preview picker can apply. Static catalog → cache for the session. */
export async function listFonts(): Promise<FontOption[]> {
  return apiFetch<FontOption[]>("/api/fonts");
}

/**
 * Apply a direct color/font edit. Commits a snapshot (no LLM) and returns it —
 * the caller refreshes the timeline. Mirrors `rollback()` in snapshots.ts.
 */
export async function applyStylePatch(
  projectId: string,
  payload: StylePatchPayload,
): Promise<Snapshot> {
  return apiFetch<Snapshot>(`/api/projects/${projectId}/style-patch`, {
    method: "POST",
    json: payload,
  });
}
