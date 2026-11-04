"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";

export const LEARNING_OPEN_EVENT = "yleum:learning:open";
const RECORD_EVENT = "yleum:learning:record";
export type LearningStatus = "started" | "dismissed" | "completed";
type LearningRecord = { version: 1; tours: Record<string, LearningStatus>; progress: Record<string, string>; creationExempt?: boolean };
const unavailableRecords = new Map<string, { raw: string; base: string | null; storage: Storage | null }>();
const storageKey = (user: string) => `yleum:learning:v1:${user}`;

function rawRecord(user: string): string | null {
  const fallback = unavailableRecords.get(user);
  try {
    const storage = localStorage;
    const raw = storage.getItem(storageKey(user));
    if (fallback && (!fallback.storage || fallback.storage === storage) && fallback.base === raw) return fallback.raw;
    unavailableRecords.delete(user);
    return raw;
  } catch { return fallback?.raw ?? null; }
}

function parseRecord(raw: string | null): LearningRecord {
  try {
    const value = JSON.parse(raw ?? "null");
    if (value?.version === 1 && value.tours && typeof value.tours === "object") {
      return {
        version: 1,
        tours: Object.fromEntries(Object.entries(value.tours).filter((entry): entry is [string, LearningStatus] => ["started", "dismissed", "completed"].includes(String(entry[1])))),
        progress: Object.fromEntries(Object.entries(value.progress ?? {}).filter((entry): entry is [string, string] => typeof entry[1] === "string" && entry[1].length <= 128)),
        ...(value.creationExempt === true ? { creationExempt: true } : {}),
      };
    }
  } catch { /* Missing or malformed preferences start a fresh local tour. */ }
  return { version: 1, tours: {}, progress: {} };
}

export function readLearningRecord(user: string) { return parseRecord(rawRecord(user)); }

function writeRecord(user: string, record: LearningRecord) {
  const raw = JSON.stringify(record);
  if (rawRecord(user) === raw) return;
  let storage: Storage | null = null;
  let base: string | null = null;
  try {
    storage = localStorage;
    base = storage.getItem(storageKey(user));
    storage.setItem(storageKey(user), raw);
    unavailableRecords.delete(user);
  } catch { unavailableRecords.set(user, { raw, base, storage }); }
  window.dispatchEvent(new CustomEvent(RECORD_EVENT, { detail: { userKey: user } }));
}

export function rememberLearning(user: string, id: string, status: LearningStatus, step?: string) {
  const record = readLearningRecord(user);
  // Completion is a latch: replay and dismissal must not revoke an old v1 result.
  if (record.tours[id] === "completed" && status !== "completed") return;
  record.tours[id] = status;
  if (status === "completed") delete record.progress[id];
  else if (step) record.progress[id] = step;
  writeRecord(user, record);
}

export function useLearningProgress(user: string) {
  const subscribe = useCallback((changed: () => void) => {
    const local = (event: Event) => { if ((event as CustomEvent<{ userKey: string }>).detail?.userKey === user) changed(); };
    const external = (event: StorageEvent) => {
      if (event.key === storageKey(user) || event.key === null) { unavailableRecords.delete(user); changed(); }
    };
    window.addEventListener(RECORD_EVENT, local); window.addEventListener("storage", external);
    return () => { window.removeEventListener(RECORD_EVENT, local); window.removeEventListener("storage", external); };
  }, [user]);
  const snapshot = useCallback(() => rawRecord(user), [user]);
  const raw = useSyncExternalStore(subscribe, snapshot, () => null);
  return useMemo(() => parseRecord(raw), [raw]);
}

export function requestLearning(userKey?: string, trigger?: HTMLElement, scope: "product" | "guide" = "product") {
  const surface = trigger?.closest<HTMLElement>("[data-learning-context]")?.dataset.learningContext;
  window.dispatchEvent(new CustomEvent(LEARNING_OPEN_EVENT, { detail: { trigger, scope, surface, userKey } }));
}

type LearningGate = {
  userKey: string;
  completed: boolean;
  creationExempt: boolean;
  active: boolean;
  setActive: (token: object, active: boolean) => void;
  request: (trigger?: HTMLElement) => void;
  grandfatherCreation: () => void;
};
const Context = createContext<LearningGate | null>(null);
export const useLearningGate = () => useContext(Context);

export function LearningProvider({ userKey, children }: { userKey: string; children: ReactNode }) {
  const record = useLearningProgress(userKey);
  const sessions = useRef(new Set<object>());
  const [active, setActiveState] = useState(false);
  const setActive = useCallback((token: object, enabled: boolean) => {
    if (enabled) sessions.current.add(token); else sessions.current.delete(token);
    setActiveState(sessions.current.size > 0);
  }, []);
  const request = useCallback((trigger?: HTMLElement) => requestLearning(userKey, trigger), [userKey]);
  const grandfatherCreation = useCallback(() => {
    const latest = readLearningRecord(userKey);
    if (!latest.creationExempt) writeRecord(userKey, { ...latest, creationExempt: true });
  }, [userKey]);
  const value = useMemo(() => ({ userKey, completed: record.tours.cabinet === "completed", creationExempt: Boolean(record.creationExempt), active, setActive, request, grandfatherCreation }), [userKey, record, active, setActive, request, grandfatherCreation]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
