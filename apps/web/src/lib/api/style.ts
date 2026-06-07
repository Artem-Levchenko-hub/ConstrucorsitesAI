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

const _API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * Upload a user image (raw bytes in the body — the upload endpoint avoids the
 * python-multipart dep). Returns the stored asset URL. Free (no LLM/wallet).
 */
export async function uploadImage(
  projectId: string,
  file: File,
): Promise<{ url: string }> {
  const res = await fetch(`${_API_BASE}/api/projects/${projectId}/uploads`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": file.type || "application/octet-stream" },
    body: file,
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const b = (await res.json()) as { error?: { message?: string } };
      msg = b?.error?.message ?? msg;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(msg);
  }
  return (await res.json()) as { url: string };
}

/**
 * Swap a generated image's `src` for an uploaded asset. Commits a snapshot
 * (no LLM); the caller refreshes the timeline. Mirrors `applyStylePatch`.
 */
export async function applyImagePatch(
  projectId: string,
  payload: { old_src: string; new_src: string },
): Promise<Snapshot> {
  return apiFetch<Snapshot>(`/api/projects/${projectId}/image-patch`, {
    method: "POST",
    json: payload,
  });
}

/**
 * Edit an element's text content directly (no LLM). Commits a snapshot. `index`
 * disambiguates repeated labels. Mirrors `applyImagePatch`.
 */
export async function applyTextPatch(
  projectId: string,
  payload: { old_text: string; new_text: string; index: number },
): Promise<Snapshot> {
  return apiFetch<Snapshot>(`/api/projects/${projectId}/text-patch`, {
    method: "POST",
    json: payload,
  });
}
