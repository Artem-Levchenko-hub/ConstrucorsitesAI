"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { FONT_LIMITS, fontErrorMessage, resolveFontAsset, uploadFontAsset, validateFontFile } from "@/lib/api/font-assets";
import type { FontAssetPublic, FontRole } from "@/lib/api/types";

export type DraftFontAttachment = {
  key: string;
  role: FontRole;
  file?: File;
  familyName?: string;
  asset?: FontAssetPublic;
  error?: string;
};
type DraftState = { projectId?: string; items: DraftFontAttachment[]; busy: boolean; error: string | null };

export function useFontAttachments(projectId?: string) {
  const [state, setState] = useState<DraftState>({ projectId, items: [], busy: false, error: null });
  const stateRef = useRef(state);
  const scope = useRef<AbortController | null>(null);
  const update = useCallback((next: DraftState) => { stateRef.current = next; setState(next); }, []);
  useEffect(() => {
    const controller = new AbortController();
    scope.current = controller;
    update({ projectId, items: [], busy: false, error: null });
    return () => { controller.abort(); };
  }, [projectId, update]);

  const upload = useCallback(async (entry: DraftFontAttachment, controller: AbortController) => {
    if (!projectId || controller.signal.aborted) return;
    try {
      const asset = entry.file
        ? await uploadFontAsset(projectId, entry.file, controller.signal)
        : await resolveFontAsset(projectId, entry.familyName!, controller.signal);
      if (scope.current !== controller || controller.signal.aborted) return;
      update({ ...stateRef.current, items: stateRef.current.items.map(item => item.key === entry.key ? { ...item, asset, error: undefined } : item) });
    } catch (error) {
      if (scope.current !== controller || controller.signal.aborted) return;
      update({ ...stateRef.current, items: stateRef.current.items.map(item => item.key === entry.key ? { ...item, error: fontErrorMessage(error) } : item) });
    }
  }, [projectId, update]);

  const enqueue = useCallback(async (entries: DraftFontAttachment[]) => {
    const controller = scope.current;
    if (!projectId || !controller || controller.signal.aborted || stateRef.current.busy || !entries.length) return;
    if (stateRef.current.items.length + entries.length > FONT_LIMITS.count) {
      update({ ...stateRef.current, error: "К запросу можно прикрепить до 6 шрифтов." });
      return;
    }
    update({ ...stateRef.current, busy: true, error: null, items: [...stateRef.current.items, ...entries] });
    try { for (const entry of entries) await upload(entry, controller); }
    finally {
      if (scope.current === controller && !controller.signal.aborted) update({ ...stateRef.current, busy: false });
    }
  }, [projectId, update, upload]);

  const addFiles = useCallback(async (files: File[], role: FontRole = "all") => {
    if (stateRef.current.busy) return;
    try { files.forEach(validateFontFile); }
    catch (error) { update({ ...stateRef.current, error: (error as Error).message }); return; }
    await enqueue(files.map(file => ({ key: crypto.randomUUID(), file, role })));
  }, [enqueue, update]);
  const addName = useCallback(async (name: string, role: FontRole = "all") => {
    const familyName = name.trim();
    if (!familyName) return;
    await enqueue([{ key: crypto.randomUUID(), familyName, role }]);
  }, [enqueue]);
  const retry = useCallback(async (key: string) => {
    const entry = stateRef.current.items.find(item => item.key === key);
    const controller = scope.current;
    if (!entry || !controller || controller.signal.aborted || stateRef.current.busy) return;
    update({ ...stateRef.current, busy: true, items: stateRef.current.items.map(item => item.key === key ? { ...item, error: undefined } : item) });
    try { await upload(entry, controller); }
    finally { if (scope.current === controller && !controller.signal.aborted) update({ ...stateRef.current, busy: false }); }
  }, [update, upload]);
  const remove = useCallback((key: string) => {
    if (!stateRef.current.busy) update({ ...stateRef.current, items: stateRef.current.items.filter(item => item.key !== key), error: null });
  }, [update]);
  const clear = useCallback(() => update({ ...stateRef.current, items: [], error: null }), [update]);
  const current = state.projectId === projectId ? state : { items: [], busy: false, error: null };
  return { ...current, addFiles, addName, retry, remove, clear };
}
