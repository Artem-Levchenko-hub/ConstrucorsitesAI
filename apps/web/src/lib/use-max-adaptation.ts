"use client";

import { useCallback, useMemo, useSyncExternalStore } from "react";
import type { RestorationAdaptationReference } from "@/lib/api/messages";

export type MaxAdaptationAttachment = {
  projectId: string;
  prompt: string;
  reference: RestorationAdaptationReference;
  phase?: "cancelling" | "ready";
};
const event = "omnia-max-adaptation";
const key = (project: string) => `omnia:max:adaptation:${project}`;
function subscribe(listener: () => void) {
  window.addEventListener(event, listener);
  window.addEventListener("storage", listener);
  return () => {
    window.removeEventListener(event, listener);
    window.removeEventListener("storage", listener);
  };
}
function read(project: string) {
  try { return window.localStorage.getItem(key(project)); } catch { return null; }
}
export function useMaxAdaptation(projectId: string) {
  const snapshot = useSyncExternalStore(subscribe, useCallback(() => read(projectId), [projectId]), () => null);
  const attachment = useMemo<MaxAdaptationAttachment | null>(() => {
    try {
      const value = JSON.parse(snapshot ?? "null") as MaxAdaptationAttachment | null;
      return value?.projectId === projectId && typeof value.prompt === "string"
        && value.prompt.length <= 30_000 && typeof value.reference?.operation_id === "string"
        && typeof value.reference.expected_draft_snapshot_id === "string"
        && (value.phase === undefined || value.phase === "cancelling" || value.phase === "ready") ? value : null;
    } catch { return null; }
  }, [projectId, snapshot]);
  const attach = useCallback((prompt: string, reference: RestorationAdaptationReference, phase?: MaxAdaptationAttachment["phase"]) => {
    try {
      window.localStorage.setItem(key(projectId), JSON.stringify({ projectId, prompt, reference, ...(phase ? { phase } : {}) }));
      window.dispatchEvent(new Event(event));
      return true;
    } catch { return false; }
  }, [projectId]);
  const clear = useCallback((operationId?: string) => {
    try {
      const current = JSON.parse(read(projectId) ?? "null") as MaxAdaptationAttachment | null;
      if (operationId && current?.reference.operation_id !== operationId) return;
      window.localStorage.removeItem(key(projectId));
      window.dispatchEvent(new Event(event));
    } catch { /* Keep a failed durable attachment visible until storage becomes available. */ }
  }, [projectId]);
  return { attachment, attach, clear };
}
