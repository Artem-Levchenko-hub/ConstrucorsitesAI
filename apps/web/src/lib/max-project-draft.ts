import type { ManagementModule, MaxContentItem } from "./api/types";
import { MAX_APP_TYPES, MAX_FEATURES, MAX_MANAGEMENT_MODULES, type MaxProjectBrief } from "./max-brief";
import { publicInitialCatalog, publicMaxBriefText } from "./max-initial-catalog";

export type MaxProjectDraft = Omit<MaxProjectBrief, "catalogItemCount"> & {
  id: string;
  appTypes: NonNullable<MaxProjectBrief["appTypes"]>;
  managementModules: ManagementModule[];
  managementModuleOptOuts: ManagementModule[];
  catalogDraft: { source: string; items: MaxContentItem[]; tooManyItems: boolean } | null;
};
const ownerKey = (owner: string) => `yleum:max-project-draft:v1:${encodeURIComponent(owner.trim().toLowerCase())}`;
const texts = ["name", "idea", "audience", "primaryAction", "brandColors"] as const;
type Envelope = { version: 1; owner: string; id: string; savedAt: number; completed?: true; draft?: MaxProjectDraft };

export function emptyMaxProjectDraft(): MaxProjectDraft {
  return { id: crypto.randomUUID(), name: "", idea: "", appType: "custom", appTypes: ["custom"],
    audience: "", primaryAction: "", features: [], brandColors: "", managementModules: [],
    managementModuleOptOuts: [], catalogDraft: null };
}

function validDraft(value: unknown): value is MaxProjectDraft {
  if (!value || typeof value !== "object") return false;
  const v = value as MaxProjectDraft;
  const allowed = ["id", ...texts, "appType", "appTypes", "features", "managementModules", "managementModuleOptOuts", "catalogDraft"];
  const list = (items: unknown, options: readonly string[], maximum: number) => Array.isArray(items)
    && items.length <= maximum && new Set(items).size === items.length
    && items.every(item => typeof item === "string" && options.includes(item));
  if (Object.keys(v).some(key => !allowed.includes(key))
    || typeof v.id !== "string" || !/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/u.test(v.id)
    || !texts.every(key => typeof v[key] === "string" && v[key].length <= 100_000)
    || !list(v.appTypes, MAX_APP_TYPES.map(t => t.id), 6) || !v.appTypes.length || v.appType !== v.appTypes[0]
    || !list(v.features, MAX_FEATURES, 8)
    || !list(v.managementModules, MAX_MANAGEMENT_MODULES.map(t => t.id), 4)
    || !list(v.managementModuleOptOuts, MAX_MANAGEMENT_MODULES.map(t => t.id), 4)) return false;
  if (v.catalogDraft === null) return true;
  const catalog = v.catalogDraft;
  return !!catalog && typeof catalog.source === "string" && catalog.source.length <= 100_000
    && typeof catalog.tooManyItems === "boolean" && Array.isArray(catalog.items) && catalog.items.length <= 100
    && new Set(catalog.items.map(item => item?.id)).size === catalog.items.length
    && catalog.items.every(item => !!item && typeof item === "object"
      && Object.keys(item).every(key => ["id", "title", "category", "description", "price", "image_url", "action_label", "availability", "active", "options"].includes(key))
      && ["id", "title", "category", "description", "price", "image_url", "action_label"].every(key =>
        typeof item[key as keyof MaxContentItem] === "string" && String(item[key as keyof MaxContentItem]).length <= 10_000)
      && ["in_stock", "on_request", "out_of_stock"].includes(item.availability)
      && typeof item.active === "boolean" && Array.isArray(item.options) && item.options.length <= 24
      && item.options.every(option => typeof option === "string" && option.length <= 10_000));
}

function envelope(owner: string): Envelope | null {
  try {
    const raw = localStorage.getItem(ownerKey(owner));
    if (!raw || raw.length > 2_000_000) return null;
    const value = JSON.parse(raw) as Envelope;
    return value?.version === 1 && value.owner === owner.trim().toLowerCase()
      && typeof value.id === "string" && typeof value.savedAt === "number" && Number.isFinite(value.savedAt)
      && (value.completed === undefined || value.completed === true) ? value : null;
  } catch { return null; }
}

export function readMaxProjectDraft(owner: string): MaxProjectDraft | null {
  const saved = envelope(owner);
  return saved && !saved.completed && validDraft(saved.draft) && saved.id === saved.draft.id ? saved.draft : null;
}

function persist(owner: string, draft: MaxProjectDraft, now: number, start: boolean, expectedOldId?: string): boolean {
  if (!owner.trim() || !validDraft(draft)) return false;
  const previous = envelope(owner);
  // An old tab cannot resurrect a successfully created draft or overwrite a
  // different in-progress idea. A genuinely new draft may replace a tombstone.
  if (expectedOldId !== undefined) {
    // A confirmed replacement is a new logical idea. Compare the persisted
    // identity immediately before this synchronous write; never replace a
    // different tab's newer draft or reuse an in-flight create's identity.
    if (!previous || previous.completed || previous.id !== expectedOldId || draft.id === expectedOldId) return false;
  } else if (previous && ((previous.completed && previous.id === draft.id)
    || (previous.id !== draft.id && (!start || !previous.completed)))) return false;
  const clean = { ...draft, ...Object.fromEntries(texts.map(key => [key, publicMaxBriefText(draft[key])])),
    catalogDraft: draft.catalogDraft ? { ...draft.catalogDraft, source: publicMaxBriefText(draft.catalogDraft.source),
      items: publicInitialCatalog(draft.catalogDraft.items) } : null };
  try {
    localStorage.setItem(ownerKey(owner), JSON.stringify({ version: 1, owner: owner.trim().toLowerCase(),
      id: draft.id, savedAt: now, draft: clean }));
    return true;
  } catch { return false; }
}

export function saveMaxProjectDraft(owner: string, draft: MaxProjectDraft, now = Date.now()): boolean {
  return persist(owner, draft, now, false);
}

/** Only an explicit new wizard/idea or verified landing handoff starts a new draft. */
export function startMaxProjectDraft(owner: string, draft: MaxProjectDraft, now = Date.now()): boolean {
  return persist(owner, draft, now, true);
}

export function replaceMaxProjectDraft(owner: string, expectedOldId: string, replacement: MaxProjectDraft, now = Date.now()): boolean {
  return persist(owner, replacement, now, false, expectedOldId);
}

export function completeMaxProjectDraft(owner: string, id: string, now = Date.now()): boolean {
  const previous = envelope(owner);
  if (previous && previous.id !== id) return false;
  try {
    // Keep a small completion tombstone so a stale tab's save is refused.
    localStorage.setItem(ownerKey(owner), JSON.stringify({ version: 1, owner: owner.trim().toLowerCase(),
      id, savedAt: now, completed: true }));
    return true;
  } catch { return false; }
}
