import type { MaxContentItem, MaxProjectContent } from "./api/types";

export type MaxCatalogDraft = { version: number; base: MaxContentItem[]; content: MaxContentItem[] };

export function newMaxContentItem(): MaxContentItem {
  return { id: `item-${crypto.randomUUID()}`, title: "", category: "", description: "", price: "", availability: "in_stock", options: [], image_url: "", action_label: "Открыть", active: true };
}

export function catalogDraft(data: MaxProjectContent): MaxCatalogDraft {
  return { version: data.config_version, base: data.content, content: data.content };
}

/** Explicit conflict review keeps server additions and fields the owner did not edit. */
export function rebaseCatalogDraft(draft: MaxCatalogDraft, latest: MaxProjectContent): MaxCatalogDraft {
  const merged = new Map(latest.content.map(item => [item.id, item]));
  for (const item of draft.content) {
    const before = draft.base.find(previous => previous.id === item.id);
    if (JSON.stringify(before) === JSON.stringify(item)) continue;
    const current = merged.get(item.id);
    if (!before || !current) { merged.set(item.id, item); continue; }
    const changes = Object.fromEntries(Object.entries(item).filter(([key, value]) =>
      JSON.stringify(value) !== JSON.stringify(before[key as keyof MaxContentItem])));
    merged.set(item.id, { ...current, ...changes });
  }
  return { version: latest.config_version, base: latest.content, content: [...merged.values()] };
}
