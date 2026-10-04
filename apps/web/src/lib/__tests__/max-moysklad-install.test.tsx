import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FigmaIntegrationHub } from "@/components/max/FigmaIntegrationHub";
import type { IntegrationCatalog, IntegrationProvider } from "@/lib/api/types";
const boundary = vi.hoisted(() => ({ catalog: vi.fn(), install: vi.fn(), start: vi.fn(), claim: vi.fn(), connect: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/max-studio", () => ({ syncMaxManagedKit: async () => undefined }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));
vi.mock("@/lib/api/app-integrations", async importOriginal => ({ ...await importOriginal<typeof import("@/lib/api/app-integrations")>(),
  getIntegrationCatalog: boundary.catalog, getMoyskladInstall: boundary.install, startMoyskladInstall: boundary.start,
  claimMoyskladIntegration: boundary.claim, connectAppIntegration: boundary.connect,
}));
const provider: IntegrationProvider = {
  key: "moysklad", name: "МойСклад", category: "inventory", description: "Товары и остатки", capabilities: [],
  fields: [{ key: "token", label: "API-токен", placeholder: "", help: "", secret: true, required: true }],
  available: true, recommended: false, requirement: null, docs_url: "https://example.invalid/docs", oauth_supported: false,
  oauth_available: false, connection_mode: "partner",
};
let root: Root; let container: HTMLDivElement; let client: QueryClient;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true }); vi.resetAllMocks();
  window.history.replaceState({}, "", "/");
  boundary.install.mockResolvedValue({ available: false, install_url: null });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); client.clear(); container.remove(); vi.restoreAllMocks(); });
function button(label: string) { return Array.from(container.querySelectorAll("button")).find(el => el.textContent?.trim() === label); }
async function click(label: string) { expect(button(label)).toBeDefined(); await act(async () => button(label)!.click()); }
async function render(mode: IntegrationProvider["connection_mode"] = "partner", projectId = "selected-project", returned = false) {
  const data: IntegrationCatalog = { providers: [{ ...provider, connection_mode: mode }], connections: [], recommended_pack: {
    key: "fixture", title: "", description: "", provider_keys: [], bound_count: 0, reusable_count: 0,
  } };
  boundary.catalog.mockResolvedValue(data); client.setQueryData(["app-integrations", projectId], data);
  await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(FigmaIntegrationHub, {
    projectId, projectName: "Выбранный проект", embedded: true,
  }))));
  if (!returned) await click("Подключить");
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
}
it("reopens native setup on the validated return route for the currently selected project without automatic claim", async () => {
  window.history.replaceState({}, "", "/max/selected-project?panel=services&integration=moysklad");
  await render("partner", "selected-project", true);
  expect(container.querySelector("#moysklad-pairing-code")).not.toBeNull();
  expect(boundary.install).toHaveBeenCalledWith("selected-project");
  expect(boundary.claim).not.toHaveBeenCalled(); expect(boundary.start).not.toHaveBeenCalled();
});
it("shows honest publication pending instructions without inventing a native URL or asking for API secrets", async () => {
  const open = vi.spyOn(window, "open").mockReturnValue(null); await render();
  expect(boundary.install).toHaveBeenCalledWith("selected-project");
  expect(container.textContent).toContain("Публикация решения в каталоге МойСклад ещё ожидается");
  expect(button("Установить решение в МойСклад")).toBeUndefined();
  expect(container.querySelector('input[type="password"]')).toBeNull(); expect(container.textContent).not.toContain("API-токен");
  expect(container.querySelector("#moysklad-pairing-code")).not.toBeNull();
  expect(boundary.start).not.toHaveBeenCalled(); expect(open).not.toHaveBeenCalled();
});
it("starts installation for exactly the selected project and opens only the returned official URL safely", async () => {
  boundary.install.mockResolvedValue({ available: true, install_url: "https://online.moysklad.ru/app/catalog/configured" });
  boundary.start.mockResolvedValue({ install_url: "https://online.moysklad.ru/app/catalog/returned" });
  const open = vi.spyOn(window, "open").mockReturnValue(null); await render();
  expect(boundary.start).not.toHaveBeenCalled(); expect(open).not.toHaveBeenCalled();
  await click("Установить решение в МойСклад");
  expect(boundary.start).toHaveBeenCalledTimes(1); expect(boundary.start).toHaveBeenCalledWith("selected-project");
  expect(open).toHaveBeenCalledWith("https://online.moysklad.ru/app/catalog/returned", "_blank", "noopener,noreferrer");
  const link = container.querySelector<HTMLAnchorElement>('a[href="https://online.moysklad.ru/app/catalog/returned"]');
  expect(link?.target).toBe("_blank"); expect(link?.rel).toContain("noopener"); expect(link?.rel).toContain("noreferrer");
});
it("keeps a failed install honest and does not launch a new tab", async () => {
  boundary.install.mockResolvedValue({ available: true, install_url: "https://online.moysklad.ru/app/catalog/configured" });
  boundary.start.mockRejectedValue(new Error("local fake failure"));
  const open = vi.spyOn(window, "open").mockReturnValue(null); await render(); await click("Установить решение в МойСклад");
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  expect(container.textContent).toContain("Не удалось начать установку"); expect(open).not.toHaveBeenCalled();
});
it("retains explicit pairing-code claim for the selected project", async () => {
  boundary.claim.mockResolvedValue({ status: "vendor_sync_pending" }); await render();
  const input = container.querySelector<HTMLInputElement>("#moysklad-pairing-code")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "synthetic-one-time-code-0001");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await click("Подключить склад");
  expect(boundary.claim).toHaveBeenCalledWith("selected-project", "synthetic-one-time-code-0001");
  expect(input.value).toBe("synthetic-one-time-code-0001"); expect(boundary.connect).not.toHaveBeenCalled();
});
it("retains legacy credentials only as an explicit closed manual alternative", async () => {
  await render("credentials");
  const details = Array.from(container.querySelectorAll("details")).find(el => el.textContent?.includes("Ранее настроенное ручное подключение"));
  expect(details).toBeDefined(); expect(details?.open).toBe(false); expect(button("Проверить и подключить")).toBeUndefined();
  await act(async () => { details!.open = true; details!.dispatchEvent(new Event("toggle")); });
  expect(container.querySelector('input[type="password"]')).not.toBeNull();
  expect(button("Проверить и подключить")).toBeDefined();
});
it("shows unavailable lookup errors distinctly from publication pending", async () => {
  boundary.install.mockRejectedValue(new Error("local network failure"));
  await render();
  expect(container.textContent).toContain("Не удалось проверить доступность решения");
  expect(container.textContent).not.toContain("Публикация решения в каталоге МойСклад ещё ожидается");
  expect(button("Установить решение в МойСклад")).toBeUndefined();
  expect(boundary.start).not.toHaveBeenCalled();
});
it("does not send two install starts for synchronous repeated clicks", async () => {
  boundary.install.mockResolvedValue({ available: true, install_url: "https://online.moysklad.ru/app/catalog/configured" });
  let finish!: (value: { install_url: string }) => void;
  boundary.start.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  vi.spyOn(window, "open").mockReturnValue(null);
  await render();
  await act(async () => { button("Установить решение в МойСклад")!.click(); button("Установить решение в МойСклад")!.click(); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  expect(boundary.start).toHaveBeenCalledTimes(1);
  expect(button("Установить решение в МойСклад")?.disabled).toBe(true);
  await act(async () => finish({ install_url: "https://online.moysklad.ru/app/catalog/returned" }));
});
