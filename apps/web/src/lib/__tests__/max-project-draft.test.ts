import { beforeEach, expect, it, vi } from "vitest";
import { completeMaxProjectDraft, emptyMaxProjectDraft, readMaxProjectDraft, replaceMaxProjectDraft, saveMaxProjectDraft, startMaxProjectDraft } from "@/lib/max-project-draft";
import { newMaxContentItem } from "@/lib/max-catalog";

beforeEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

it("starts neutral with no implicit functions or managed data", () => {
  expect(emptyMaxProjectDraft()).toMatchObject({ appType: "custom", appTypes: ["custom"], features: [], managementModules: [], managementModuleOptOuts: [] });
});
it("restores complete owner draft, including invalid editable text and reviewed catalog identities", () => {
  const item = { ...newMaxContentItem(), title: "Свой товар", price: "325 ₽" };
  const draft = { ...emptyMaxProjectDraft(), idea: "😀".repeat(10000) + "КОНЕЦ", audience: "я".repeat(420), brandColors: "Белый и красный",
    catalogDraft: { source: "исходный каталог", items: [item], tooManyItems: false } };
  expect(startMaxProjectDraft("A@example.test", draft, 100)).toBe(true);
  expect(readMaxProjectDraft("a@example.test")).toEqual(draft);
  expect(readMaxProjectDraft("b@example.test")).toBeNull();
});
it("does not resurrect a completed draft or let an old tab replace a newer one", () => {
  const old = { ...emptyMaxProjectDraft(), idea: "Старая задача" };
  expect(startMaxProjectDraft("a", old, 100)).toBe(true);
  expect(completeMaxProjectDraft("a", old.id, 101)).toBe(true);
  expect(saveMaxProjectDraft("a", old, 102)).toBe(false);
  expect(readMaxProjectDraft("a")).toBeNull();
  const next = { ...emptyMaxProjectDraft(), idea: "Новая задача" };
  expect(saveMaxProjectDraft("a", next, 104)).toBe(false);
  expect(startMaxProjectDraft("a", next, 104)).toBe(true);
  expect(saveMaxProjectDraft("a", old, 105)).toBe(false);
  expect(completeMaxProjectDraft("a", old.id, 106)).toBe(false);
  expect(readMaxProjectDraft("a")).toEqual(next);
  expect(completeMaxProjectDraft("a", next.id, 108)).toBe(true);
  expect(saveMaxProjectDraft("a", old, 109)).toBe(false);
});
it("ignores malformed, foreign or unknown-choice stored envelopes", () => {
  const draft = emptyMaxProjectDraft(); startMaxProjectDraft("a", draft, 100);
  const key = localStorage.key(0)!; const saved = JSON.parse(localStorage.getItem(key)!);
  for (const change of [{ owner: "b" }, { savedAt: "invalid" }, { draft: { ...draft, appTypes: ["unknown"] } }, { draft: { ...draft, features: ["invented"] } }, { draft: { ...draft, unexpected: "private" } }]) {
    localStorage.setItem(key, JSON.stringify({ ...saved, ...change }));
    expect(readMaxProjectDraft("a")).toBeNull();
  }
  localStorage.setItem(key, JSON.stringify(saved));
  expect(readMaxProjectDraft("a")).toEqual(draft);
});
it("never persists a recognised provider credential in a draft", () => {
  const secret = "sk-ant-api03-" + "a".repeat(70);
  startMaxProjectDraft("a", { ...emptyMaxProjectDraft(), idea: `Anthropic API key ${secret}` });
  expect(localStorage.getItem(localStorage.key(0)!)).not.toContain(secret);
});
it("reports blocked persistence instead of pretending a draft is durable", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("denied"); });
  expect(startMaxProjectDraft("a", emptyMaxProjectDraft())).toBe(false);
  expect(readMaxProjectDraft("a")).toBeNull();
});

it("keeps a nonempty owner draft beyond a week until explicit completion", () => {
  vi.useFakeTimers();
  try {
    const started = Date.UTC(2026, 0, 1);
    vi.setSystemTime(started);
    const draft = { ...emptyMaxProjectDraft(), name: "Продолжить позже", idea: "Сохранить все мои требования", brandColors: "Белый и красный" };
    expect(startMaxProjectDraft("a", draft)).toBe(true);
    vi.setSystemTime(started + 366 * 24 * 60 * 60 * 1000);
    expect(readMaxProjectDraft("a")).toEqual(draft);
    expect(completeMaxProjectDraft("a", draft.id)).toBe(true);
    expect(readMaxProjectDraft("a")).toBeNull();
  } finally { vi.useRealTimers(); }
});

it("confirmed replacement gets a new identity that a late old creation cannot clear", () => {
  const old = { ...emptyMaxProjectDraft(), idea: "Создающийся A" };
  const replacement = { ...emptyMaxProjectDraft(), idea: "Другой B" };
  expect(startMaxProjectDraft("a", old, 100)).toBe(true);
  expect(replaceMaxProjectDraft("a", old.id, replacement, 101)).toBe(true);
  expect(replacement.id).not.toBe(old.id);
  expect(completeMaxProjectDraft("a", old.id, 102)).toBe(false);
  expect(saveMaxProjectDraft("a", old, 103)).toBe(false);
  expect(readMaxProjectDraft("a")).toEqual(replacement);
});

it("replacement refuses changed persisted identity, reused identity and a completed old draft", () => {
  const old = emptyMaxProjectDraft(); const newer = { ...emptyMaxProjectDraft(), idea: "Новая C" };
  const attempted = { ...emptyMaxProjectDraft(), idea: "Подтверждённая B" };
  startMaxProjectDraft("a", old, 100);
  expect(replaceMaxProjectDraft("a", old.id, { ...attempted, id: old.id }, 101)).toBe(false);
  expect(replaceMaxProjectDraft("a", old.id, newer, 102)).toBe(true);
  expect(replaceMaxProjectDraft("a", old.id, attempted, 103)).toBe(false);
  expect(readMaxProjectDraft("a")).toEqual(newer);
  completeMaxProjectDraft("a", newer.id, 105);
  expect(replaceMaxProjectDraft("a", newer.id, attempted, 106)).toBe(false);
  expect(readMaxProjectDraft("a")).toBeNull();
});
