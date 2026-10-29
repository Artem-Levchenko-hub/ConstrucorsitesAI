import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxOwnerCatalog } from "@/components/max/MaxOwnerCatalog";
import type { MaxContentItem } from "@/lib/api/types";

vi.mock("@/components/max/MaxSectionShell", () => ({ MaxSectionShell: ({ children }: { children: React.ReactNode }) => <main data-shell>{children}</main> }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
const item: MaxContentItem = { id: "one", title: "Услуга", category: "", description: "", price: "По запросу", availability: "on_request", options: [], image_url: "", action_label: "Записаться", active: true };
let root: Root; let node: HTMLDivElement; let client: QueryClient; let items: MaxContentItem[];
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true }); items = [item];
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  vi.stubGlobal("fetch", vi.fn(async () => {
    const state = { state: "not_published", desired_config_version: 1, controller_config_version: null, confirmed_config_version: null, checked_at: null, reason: null };
    return Response.json({ project_id: "owned", config_version: 1, content: items, saved: { state: "saved", config_version: 1 }, public: state, preview: { ...state, state: "not_created" } });
  }));
});
afterEach(async () => { await act(async () => root.unmount()); node.remove(); client.clear(); vi.unstubAllGlobals(); });
async function mount(embedded: boolean, onDirtyChange?: (dirty: boolean) => void) {
  await act(async () => root.render(<QueryClientProvider client={client}><MaxOwnerCatalog projectId="owned" projectName="Проект" embedded={embedded} onDirtyChange={onDirtyChange} /></QueryClientProvider>));
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); });
}
it("embeds the real catalog once while retaining the standalone shell at its existing URL", async () => {
  await mount(true); expect(node.querySelector("[data-shell]")).toBeNull(); expect(node.textContent).toContain("Услуга");
  await mount(false); expect(node.querySelector("[data-shell]")).not.toBeNull();
});
it("shows the inclusive 100-position cap before adding and disables adding at the cap", async () => {
  items = Array.from({ length: 100 }, (_, index) => ({ ...item, id: String(index), active: index % 2 === 0 }));
  await mount(true);
  expect(node.textContent).toContain("100 из 100 позиций, включая скрытые");
  expect([...node.querySelectorAll<HTMLButtonElement>("button")].find(button => button.textContent?.includes("Добавить позицию"))?.disabled).toBe(true);
});
it("reports pending catalog edits to the console before a section can discard them", async () => {
  const changes: boolean[] = []; await mount(true, dirty => changes.push(dirty));
  await act(async () => node.querySelector<HTMLButtonElement>('button[aria-label="Скрыть Услуга"]')!.click());
  expect(changes.at(-1)).toBe(true);
});
