"use client";

import { useMemo, useSyncExternalStore } from "react";

import {
  MAX_DEMO_DRAFT_STORAGE_KEY,
  parseMaxDemoDraft,
  type MaxDemoDraft,
} from "@/lib/max-demo";

const MAX_DEMO_DRAFT_EVENT = "omnia:max:public-demo-change";

function subscribe(callback: () => void): () => void {
  window.addEventListener("storage", callback);
  window.addEventListener(MAX_DEMO_DRAFT_EVENT, callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener(MAX_DEMO_DRAFT_EVENT, callback);
  };
}

function snapshot(): string | null {
  return window.localStorage.getItem(MAX_DEMO_DRAFT_STORAGE_KEY);
}

function serverSnapshot(): null {
  return null;
}

export function useMaxDemoDraft(): MaxDemoDraft | null {
  const raw = useSyncExternalStore(subscribe, snapshot, serverSnapshot);
  return useMemo(() => parseMaxDemoDraft(raw), [raw]);
}

export function saveMaxDemoDraft(draft: MaxDemoDraft): void {
  window.localStorage.setItem(
    MAX_DEMO_DRAFT_STORAGE_KEY,
    JSON.stringify(draft),
  );
  window.dispatchEvent(new Event(MAX_DEMO_DRAFT_EVENT));
}

export function clearMaxDemoDraft(): void {
  window.localStorage.removeItem(MAX_DEMO_DRAFT_STORAGE_KEY);
  window.dispatchEvent(new Event(MAX_DEMO_DRAFT_EVENT));
}
