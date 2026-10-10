import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FigmaIntegrationHub } from "@/components/max/FigmaIntegrationHub";
import type { AppIntegration, IntegrationCatalog, IntegrationProvider } from "@/lib/api/types";
const boundary = vi.hoisted(() => ({ catalog: vi.fn(), install: vi.fn(), start: vi.fn(), claim: vi.fn(), connect: vi.fn(), options: vi.fn(), deleteConnection: vi.fn(), disconnect: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/max-studio", () => ({ syncMaxManagedKit: async () => undefined }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));
vi.mock("@/lib/api/app-integrations", async importOriginal => ({ ...await importOriginal<typeof import("@/lib/api/app-integrations")>(),
  getIntegrationCatalog: boundary.catalog, getMoyskladInstall: boundary.install, startMoyskladInstall: boundary.start,
  claimMoyskladIntegration: boundary.claim, connectAppIntegration: boundary.connect,
  getMoyskladOptions: boundary.options, deleteAccountIntegration: boundary.deleteConnection,
  disconnectAppIntegration: boundary.disconnect,
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
  boundary.options.mockResolvedValue({ organizations: [], stores: [] });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); client.clear(); container.remove(); vi.restoreAllMocks(); });
function button(label: string) { return Array.from(container.querySelectorAll("button")).find(el => el.textContent?.trim() === label); }
async function click(label: string) { expect(button(label)).toBeDefined(); await act(async () => button(label)!.click()); }
async function render(mode: IntegrationProvider["connection_mode"] = "partner", projectId = "selected-project", returned = false, connections: AppIntegration[] = []) {
  const data: IntegrationCatalog = { providers: [{ ...provider, connection_mode: mode }], connections, recommended_pack: {
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
  const fallback = container.querySelector<HTMLDetailsElement>("#moysklad-code-fallback")!;
  expect(fallback).not.toBeNull();
  expect(fallback.open).toBe(false);
  await act(async () => { fallback.open = true; fallback.dispatchEvent(new Event("toggle")); });
  const input = container.querySelector<HTMLInputElement>("#moysklad-pairing-code")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "synthetic-one-time-code-0001");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await click("Подключить склад");
  expect(boundary.claim).toHaveBeenCalledWith("selected-project", "synthetic-one-time-code-0001");
  expect(input.value).toBe("synthetic-one-time-code-0001"); expect(boundary.connect).not.toHaveBeenCalled();
});
it("explains native in-solution login and owned app selection while keeping old code claim optional", async () => {
  await render();
  expect(container.textContent).toContain("В решении войдите в Yleum, выберите своё мини-приложение и нажмите «Подключить выбранный миниапп»");
  expect(container.textContent).toContain("Затем выберите организацию и склад и нажмите «Сохранить настройки»");
  expect(container.textContent).not.toContain("вернитесь к этому проекту и подтвердите подключение одноразовым кодом");
  expect(container.querySelector<HTMLDetailsElement>("#moysklad-code-fallback")?.open).toBe(false);
  expect(boundary.claim).not.toHaveBeenCalled();
  expect(boundary.start).not.toHaveBeenCalled();
  expect(boundary.connect).not.toHaveBeenCalled();
});
it("lets a new customer enter their own token without discovering a legacy fallback", async () => {
  await render("credentials");
  const input = container.querySelector<HTMLInputElement>('input[type="password"]');
  expect(input).not.toBeNull();
  expect(button("Проверить и подключить")?.disabled).toBe(true);
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "synthetic-customer-token");
    input!.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(button("Проверить и подключить")?.disabled).toBe(false);
  expect(container.textContent).toContain("Право создавать заказы");
  expect(container.textContent).toContain("всех ваших мини-приложений");
  boundary.connect.mockResolvedValue({ status: "active" });
  await click("Проверить и подключить");
  expect(boundary.connect).toHaveBeenCalledWith("selected-project", "moysklad", { token: "synthetic-customer-token" });
  expect(container.querySelector('input[type="password"]')).toBeNull();
});
const savedConnection: AppIntegration = {
  id: "synthetic-connection", provider: "moysklad", status: "active", auth_mode: "credentials",
  account_scoped: true, bound_to_project: true, binding_status: "ready", binding_config: {},
  account_label: "Synthetic", public_config: { organization_id: "org", store_id: "store" },
  capabilities: [], configured_fields: ["token"], last_error: null,
  verified_at: null, last_checked_at: null, created_at: "2026-10-10", updated_at: "2026-10-10",
};
it("requires separate acknowledgement before removing a shared owner connection", async () => {
  await render("credentials", "selected-project", true, [savedConnection]);
  await click("Настроить");
  await click("Удалить общее подключение");
  expect(boundary.deleteConnection).not.toHaveBeenCalled();
  expect(boundary.disconnect).not.toHaveBeenCalled();
  expect(container.textContent).toContain("во всех ваших мини-приложениях");
  expect(button("Подтвердить удаление")?.disabled).toBe(true);
  const checkbox = container.querySelector<HTMLInputElement>("#moysklad-delete-confirm");
  expect(checkbox).not.toBeNull();
  await act(async () => checkbox!.click());
  boundary.deleteConnection.mockResolvedValue(undefined);
  await click("Подтвердить удаление");
  expect(boundary.deleteConnection).toHaveBeenCalledWith("selected-project", "moysklad");
  expect(boundary.disconnect).not.toHaveBeenCalled();
});

it("blocks token replacement while shared deletion is pending", async () => {
  await render("credentials", "selected-project", true, [savedConnection]);
  await click("Настроить");
  const token = container.querySelector<HTMLInputElement>('input[type="password"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(token, "synthetic-token");
    token.dispatchEvent(new Event("input", { bubbles: true }));
    container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click();
  });
  expect(button("Проверить и подключить")?.disabled).toBe(false);
  await click("Удалить общее подключение");
  await act(async () => container.querySelector<HTMLInputElement>("#moysklad-delete-confirm")!.click());
  let finish!: () => void;
  boundary.deleteConnection.mockImplementation(() => new Promise<void>(resolve => { finish = resolve; }));
  await click("Подтвердить удаление");
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  expect(button("Проверить и подключить")?.disabled).toBe(true);
  button("Проверить и подключить")!.click();
  expect(boundary.connect).not.toHaveBeenCalled();
  await act(async () => finish());
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
