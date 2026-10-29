import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import ts from "typescript";
import { afterEach, expect, it, vi } from "vitest";

type Item = {
  id: string; title: string; category: string; description: string; price: string;
  availability: "in_stock"; options: string[]; image_url: string; action_label: string; active: boolean;
};
type Catalog = { items: Item[]; configVersion: number | null; status: string; refresh: () => Promise<void> };
type Selection = {
  category: string | null; setCategory: (value: string | null) => void;
  selectedOptions: Record<string, string>;
  selectOption: (itemId: string, value: string) => void;
  getOption: (item: Pick<Item, "id">) => string | undefined;
};
const readCatalog = vi.fn();
const nodeRequire = createRequire(import.meta.url);
const source = readFileSync(resolve(process.cwd(),
  "../orchestrator/templates/max-miniapp-nextjs/src/lib/omnia/catalog-client.ts",
), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const sdk = { exports: {} as Record<string, unknown> };
new Function("require", "module", "exports", compiled)(
  (id: string) => id === "@/lib/omnia/catalog-actions"
    ? { getYleumCatalog: readCatalog } : nodeRequire(id),
  sdk, sdk.exports,
);
const useCatalog = sdk.exports.useYleumCatalog as () => Catalog;
const useSelection = sdk.exports.useYleumCatalogSelection as (items: readonly Item[]) => Selection;
const item = (patch: Partial<Item> = {}): Item => ({
  id: "coffee", title: "Кофе", category: "Напитки", description: "", price: "250 ₽",
  availability: "in_stock", options: ["Обычное", "Овсяное"], image_url: "", action_label: "Заказать", active: true,
  ...patch,
});
const response = (version: number, items: Item[] = [item()]) => ({ ok: true, configVersion: version, items });
const roots: Array<{ root: Root; node: HTMLDivElement }> = [];
async function renderSelection(freshItems = false) {
  expect(useSelection, "the shipped SDK exposes managed selection state").toBeTypeOf("function");
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  let current!: { catalog: Catalog; selection: Selection };
  function Harness() {
    const catalog = useCatalog();
    const selection = useSelection(freshItems ? catalog.items.map(value => ({ ...value })) : catalog.items);
    current = { catalog, selection };
    return null;
  }
  const node = document.createElement("div"); document.body.append(node);
  const root = createRoot(node); roots.push({ root, node });
  await act(async () => root.render(<Harness />));
  return { get current() { return current; } };
}
afterEach(async () => {
  for (const { root, node } of roots.splice(0)) {
    await act(async () => root.unmount()); node.remove();
  }
  vi.restoreAllMocks(); readCatalog.mockReset();
});

it("removes a deleted option from both exposed state and a new order payload after an actual refresh", async () => {
  readCatalog.mockResolvedValueOnce(response(7)).mockResolvedValueOnce(response(8, [item({ options: [] })]));
  const state = await renderSelection();
  act(() => state.current.selection.selectOption("coffee", "Овсяное"));
  expect(state.current.selection.getOption(item())).toBe("Овсяное");
  await act(async () => state.current.catalog.refresh());
  expect(state.current.catalog.configVersion).toBe(8);
  expect(state.current.selection.selectedOptions).toEqual({});
  expect(state.current.selection.getOption(item())).toBeUndefined();
  const payload = JSON.parse(JSON.stringify({ catalogId: "coffee", quantity: 1,
    option: state.current.selection.getOption(item()) }));
  expect(payload).toEqual({ catalogId: "coffee", quantity: 1 });
});

it("falls back to all items when the selected category disappears on refresh", async () => {
  readCatalog.mockResolvedValueOnce(response(7)).mockResolvedValueOnce(response(8, [item({ category: "Новое меню" })]));
  const state = await renderSelection();
  act(() => state.current.selection.setCategory("Напитки"));
  expect(state.current.selection.category).toBe("Напитки");
  await act(async () => state.current.catalog.refresh());
  expect(state.current.selection.category).toBeNull();
  expect(state.current.catalog.items).toHaveLength(1);
});

it("requires an explicit valid option instead of choosing the first replacement", async () => {
  readCatalog.mockResolvedValueOnce(response(7)).mockResolvedValueOnce(response(8, [item({ options: ["Классическое"] })]));
  const state = await renderSelection();
  expect(state.current.selection.getOption(item())).toBeUndefined();
  act(() => state.current.selection.selectOption("coffee", "Недоступное"));
  expect(state.current.selection.selectedOptions).toEqual({});
  act(() => state.current.selection.selectOption("coffee", "Овсяное"));
  await act(async () => state.current.catalog.refresh());
  expect(state.current.selection.getOption(item())).toBeUndefined();
  expect(state.current.selection.selectedOptions).toEqual({});
  act(() => state.current.selection.selectOption("coffee", "Классическое"));
  expect(state.current.selection.getOption(item())).toBe("Классическое");
});

it("preserves valid choices across a new version and cannot select a removed item", async () => {
  readCatalog.mockResolvedValueOnce(response(7)).mockResolvedValueOnce(response(8, [item({ price: "300 ₽" })]))
    .mockResolvedValueOnce(response(9, []));
  const state = await renderSelection();
  act(() => { state.current.selection.setCategory("Напитки"); state.current.selection.selectOption("coffee", "Овсяное"); });
  await act(async () => state.current.catalog.refresh());
  expect(state.current.selection.category).toBe("Напитки");
  expect(state.current.selection.getOption(item())).toBe("Овсяное");
  await act(async () => state.current.catalog.refresh());
  act(() => state.current.selection.selectOption("coffee", "Овсяное"));
  expect(state.current.selection.selectedOptions).toEqual({});
  expect(state.current.selection.getOption(item())).toBeUndefined();
});

it("handles fresh item arrays on each render without state-update effects or loops", async () => {
  readCatalog.mockResolvedValue(response(7));
  const state = await renderSelection(true);
  act(() => { state.current.selection.setCategory("Напитки"); state.current.selection.selectOption("coffee", "Обычное"); });
  expect(state.current.selection.category).toBe("Напитки");
  expect(state.current.selection.getOption(item())).toBe("Обычное");
  expect(readCatalog).toHaveBeenCalledTimes(1);
});
