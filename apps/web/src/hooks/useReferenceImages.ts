"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { REFERENCE_IMAGE_LIMITS, uploadReferenceImage, validateReferenceImage } from "@/lib/api/reference-images";
import type { ReferenceImage } from "@/lib/api/types";

export type DraftReferenceImage = { key: string; file: File; preview: string; image?: ReferenceImage; error?: string };

export function useReferenceImages(projectId?: string) {
  const [items, setItems] = useState<DraftReferenceImage[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const itemsRef = useRef(items);
  const lock = useRef(false);
  const scope = useRef(projectId);
  const update = useCallback((next: DraftReferenceImage[]) => { itemsRef.current = next; setItems(next); }, []);
  useEffect(() => {
    scope.current = projectId;
    return () => { scope.current = undefined; for (const item of itemsRef.current) URL.revokeObjectURL(item.preview); };
  }, [projectId]);
  const upload = useCallback(async (entry: DraftReferenceImage) => {
    if (!projectId) return;
    try {
      await validateReferenceImage(entry.file);
      const image = await uploadReferenceImage(projectId, entry.file);
      if (scope.current === projectId) update(itemsRef.current.map((item) => item.key === entry.key ? { ...item, image, error: undefined } : item));
    } catch (reason) {
      if (scope.current === projectId) update(itemsRef.current.map((item) => item.key === entry.key ? { ...item, error: reason instanceof Error ? reason.message : "Не удалось загрузить изображение." } : item));
    }
  }, [projectId, update]);
  const add = useCallback(async (files: File[]) => {
    if (!projectId || lock.current || !files.length) return;
    const existing = itemsRef.current;
    if (existing.length + files.length > REFERENCE_IMAGE_LIMITS.count) { setError(`Можно прикрепить до ${REFERENCE_IMAGE_LIMITS.count} изображений.`); return; }
    if ([...existing.map((item) => item.file), ...files].reduce((sum, file) => sum + file.size, 0) > REFERENCE_IMAGE_LIMITS.totalBytes) { setError(`Общий размер изображений — не больше ${REFERENCE_IMAGE_LIMITS.totalBytes / 1024 / 1024} МБ.`); return; }
    lock.current = true; setBusy(true); setError(null);
    try {
      // Validate the complete batch before uploading any of it.
      for (const file of files) await validateReferenceImage(file);
      const added = files.map((file) => ({ key: crypto.randomUUID(), file, preview: URL.createObjectURL(file) }));
      update([...existing, ...added]);
      for (const entry of added) await upload(entry);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось прочитать изображение."); }
    finally { lock.current = false; setBusy(false); }
  }, [projectId, update, upload]);
  const retry = useCallback(async (key: string) => {
    const entry = itemsRef.current.find((item) => item.key === key);
    if (!entry || lock.current) return;
    lock.current = true; setBusy(true);
    try { await upload(entry); } finally { lock.current = false; setBusy(false); }
  }, [upload]);
  const reuploadExpired = useCallback(async () => {
    if (!projectId || lock.current) return;
    lock.current = true; setBusy(true); setError(null);
    // A definitive server refusal guarantees that this draft was not accepted.
    // Drop every old ID before uploading, so a partial failure cannot resend it.
    const expired = itemsRef.current.map((item) => ({ ...item, image: undefined, error: undefined }));
    update(expired);
    try { for (const item of expired) await upload(item); }
    finally { lock.current = false; setBusy(false); }
  }, [projectId, update, upload]);
  const remove = useCallback((key: string) => {
    const entry = itemsRef.current.find((item) => item.key === key);
    if (entry) URL.revokeObjectURL(entry.preview);
    update(itemsRef.current.filter((item) => item.key !== key)); setError(null);
  }, [update]);
  const clear = useCallback(() => { for (const item of itemsRef.current) URL.revokeObjectURL(item.preview); update([]); setError(null); }, [update]);
  return { items, busy, error, add, retry, reuploadExpired, remove, clear };
}
