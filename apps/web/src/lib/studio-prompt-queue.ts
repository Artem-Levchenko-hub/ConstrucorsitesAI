import type { PromptSubmitOptions } from "@/hooks/usePromptStream";
import type { MessageFontSelection, SelectedElement } from "@/lib/api/types";
import { isGenerationEngine } from "@/lib/generation-engine";

export type QueuedPromptPhase = "waiting" | "sending" | "retry" | "images_expired";

export type QueuedPrompt = {
  text: string;
  modelId: string;
  selections?: SelectedElement[];
  opts: PromptSubmitOptions & { idempotencyKey: string };
  phase: QueuedPromptPhase;
  resolve?: (accepted: boolean) => void;
};

export type QueuedPromptDraft = {
  text: string;
  selections: SelectedElement[];
  fontSelections: MessageFontSelection[];
};

export function studioPromptQueueKey(ownerId: string, projectId: string): string {
  return `omnia:studio:prompt-queue:v1:${encodeURIComponent(ownerId)}:${encodeURIComponent(projectId)}`;
}

export function readQueuedPrompt(key: string): QueuedPrompt | null {
  try {
    const entry = JSON.parse(sessionStorage.getItem(key) ?? "null") as QueuedPrompt | null;
    if (!entry || typeof entry.text !== "string" || !entry.text.trim() ||
      typeof entry.modelId !== "string" || typeof entry.opts?.idempotencyKey !== "string" ||
      !entry.opts.idempotencyKey || !["waiting", "sending", "retry", "images_expired"].includes(entry.phase) ||
      (entry.selections && !Array.isArray(entry.selections)) ||
      (entry.opts.referenceImages && !Array.isArray(entry.opts.referenceImages)) ||
      (entry.opts.fontSelections && !Array.isArray(entry.opts.fontSelections))) return null;
    if (entry.opts.generationEngine !== undefined && !isGenerationEngine(entry.opts.generationEngine)) return null;
    // An interrupted POST might have reached the server. Resume only explicitly,
    // with the original request key and exact uploaded attachment identities.
    return { ...entry, phase: entry.phase === "sending" ? "retry" : entry.phase };
  } catch {
    return null;
  }
}

export function writeQueuedPrompt(key: string, entry: QueuedPrompt | null): boolean {
  try {
    if (entry) {
      const { text, modelId, selections, opts, phase } = entry;
      const { idempotencyKey, skipClarify, designPresetId, referenceImages, fontSelections, generationEngine } = opts;
      // Files stay in the live composer. Only already-uploaded attachment
      // metadata is recoverable after refresh; no URL or callback is needed.
      sessionStorage.setItem(key, JSON.stringify({ text, modelId, selections, phase, opts: {
        idempotencyKey, skipClarify, designPresetId,
        ...(generationEngine ? { generationEngine } : {}),
        referenceImages: referenceImages?.map((image) => ({ ...image, url: "" })),
        fontSelections,
      } }));
    } else {
      sessionStorage.removeItem(key);
    }
    return true;
  } catch {
    return false;
  }
}
