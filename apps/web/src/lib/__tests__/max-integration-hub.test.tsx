import { act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FigmaIntegrationHub } from "@/components/max/FigmaIntegrationHub";
import type { AppIntegration, IntegrationCatalog, IntegrationProvider } from "@/lib/api/types";

const boundary = vi.hoisted(() => ({ push: vi.fn(), connect: vi.fn(), verify: vi.fn(), catalog: vi.fn(), platformAi: vi.fn(), success: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: boundary.push }) }));
vi.mock("@/components/max/MaxSectionShell", () => ({ MaxSectionShell: ({ children }: { children: ReactNode }) => children }));
vi.mock("@/lib/api/max-studio", () => ({ syncMaxManagedKit: async () => undefined }));
vi.mock("sonner", () => ({ toast: { success: boundary.success, error: vi.fn(), warning: vi.fn() } }));
vi.mock("@/lib/api/app-integrations", () => ({
  getIntegrationCatalog: boundary.catalog, setPlatformAiEnabled: boundary.platformAi, connectAppIntegration: boundary.connect, verifyAppIntegration: boundary.verify,
  bindAppIntegration: vi.fn(), disconnectAppIntegration: vi.fn(), applyIntegrationPack: vi.fn(), startIntegrationOAuth: vi.fn(),
}));
const provider: IntegrationProvider = {
  key: "yookassa", name: "ЮKassa", category: "payments", description: "Оплата", capabilities: ["payments"],
  fields: [{ key: "secret_key", label: "Ключ", placeholder: "", help: "", secret: true, required: true }],
  available: true, recommended: true, requirement: null, docs_url: "https://example.com", oauth_supported: false, oauth_available: false, connection_mode: "credentials",
};
const connection: AppIntegration = {
  id: "connection", provider: "yookassa", status: "active", auth_mode: "credentials", business_scoped: true,
  bound_to_project: true, binding_status: "ready", binding_config: { private: "binding-value-must-not-leak" }, account_label: "account-must-not-leak", public_config: { shop_id: "shop-value-must-not-leak" },
  capabilities: ["untrusted-capability-must-not-leak"], configured_fields: ["secret_key"], last_error: null,
  verified_at: null, last_checked_at: null, created_at: "2026-09-06", updated_at: "2026-09-06",
};
function catalog(connections: AppIntegration[]): IntegrationCatalog {
  return { providers: [provider], connections, recommended_pack: { key: "base", title: "Набор", description: "", provider_keys: [], bound_count: 0, reusable_count: 0 } };
}
function button(label: string) {
  return Array.from(document.querySelectorAll<HTMLButtonElement>("button")).find((el) => el.textContent?.trim() === label);
}
async function click(label: string) {
  const target = button(label);
  expect(target, `button ${label}`).toBeDefined();
  await act(async () => target!.click());
}
function input(element: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("Integration Hub implementation handoff", () => {
  let root: Root;
  let container: HTMLDivElement;
  let client: QueryClient;
  beforeEach(() => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    vi.clearAllMocks();
    vi.restoreAllMocks();
    window.sessionStorage.clear();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  });
  afterEach(() => { act(() => root.unmount()); client.clear(); container.remove(); });
  async function render(connections: AppIntegration[], providers = [provider]) {
    const data = { ...catalog(connections), providers };
    client.setQueryData(["app-integrations", "project-1"], data);
    boundary.catalog.mockResolvedValue(data);
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(FigmaIntegrationHub, { projectId: "project-1", projectName: "Project" }))));
  }
  it("requires confirmation and transfers the edited proposal without account data", async () => {
    await render([connection]);
    await click("Добавить в приложение");
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    const textarea = dialog!.querySelector("textarea")!;
    expect(textarea.value).toContain("ЮKassa");
    expect(textarea.value).toContain("статус");
    expect(textarea.value).not.toContain("must-not-leak");
    expect(boundary.push).not.toHaveBeenCalled();
    expect(window.sessionStorage.length).toBe(0);
    await act(async () => input(textarea, "Добавь оплату заказа и проверку статуса через подключённую ЮKassa."));
    await click("Запустить доработку");
    expect(window.sessionStorage.getItem("omnia:max:starter:project-1")).toBe("Добавь оплату заказа и проверку статуса через подключённую ЮKassa.");
    expect(boundary.push).toHaveBeenCalledWith("/max/project-1?starter=1");
  });
  it("does not dispatch a blank proposal or a cancelled dialog", async () => {
    await render([connection]);
    await click("Добавить в приложение");
    await act(async () => input(document.querySelector("textarea")!, "   "));
    expect(button("Запустить доработку")?.disabled).toBe(true);
    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(boundary.push).not.toHaveBeenCalled();
    expect(window.sessionStorage.length).toBe(0);
  });
  it("keeps the proposal available if browser storage prevents handoff", async () => {
    await render([connection]);
    await click("Добавить в приложение");
    const proposal = document.querySelector("textarea")!.value;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Storage blocked"); });
    await click("Запустить доработку");
    expect(document.querySelector("textarea")!.value).toBe(proposal);
    expect(boundary.push).not.toHaveBeenCalled();
    expect(window.sessionStorage.length).toBe(0);
  });
  it("never starts generation when credentials connect", async () => {
    await render([]);
    boundary.connect.mockResolvedValue(connection);
    boundary.catalog.mockResolvedValue(catalog([connection]));
    await click("Подключить");
    await act(async () => input(document.querySelector<HTMLInputElement>('#integration-secret_key')!, "secret-must-not-leak"));
    await click("Проверить и подключить");
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(boundary.push).not.toHaveBeenCalled();
    expect(window.sessionStorage.length).toBe(0);
    expect(container.textContent).toContain("не добавляет экраны автоматически");
    expect(boundary.success).toHaveBeenCalledWith("Доступ к сервису подтверждён");
  });
  it.each(["error", "needs_setup", null] as const)("does not offer generation or mark connected for binding status %s", async (binding_status) => {
    await render([{ ...connection, binding_status }]);
    expect(button("Добавить в приложение")).toBeUndefined();
    expect(container.textContent).not.toContain("Подключено");
    expect(container.textContent).toContain("Требуется настройка");
  });
  it("offers built-in LLMGW without any credentials or business connection", async () => {
    await render([], [{
      ...provider, key: "llmgw", name: "ИИ", category: "ai", connection_mode: "platform",
      fields: [], capabilities: ["ai"], enabled: true,
    }]);
    expect(container.textContent).toContain("Работает через LLMGW; расходы с баланса владельца");
    expect(container.textContent).toContain("Встроено");
    expect(button("Подключить")).toBeUndefined();
    expect(button("Использовать")).toBeUndefined();
    expect(button("Настроить")).toBeUndefined();
    expect(container.querySelector('[aria-label="Проверить ИИ"]')).toBeNull();
    expect(container.querySelector('[aria-label="Отключить ИИ"]')).toBeNull();
    expect(boundary.push).not.toHaveBeenCalled();
    await click("Добавить в приложение");
    expect(document.querySelector('input[type="password"]')).toBeNull();
    const prompt = document.querySelector("textarea")!.value;
    expect(prompt).toContain("LLMGW");
    expect(prompt).not.toContain("AITunnel");
    expect(boundary.push).not.toHaveBeenCalled();
    await click("Запустить доработку");
    expect(window.sessionStorage.getItem("omnia:max:starter:project-1")).toBe(prompt);
    expect(boundary.push).toHaveBeenCalledWith("/max/project-1?starter=1");
    expect(boundary.connect).not.toHaveBeenCalled();
    expect(boundary.platformAi).not.toHaveBeenCalled();
  });
  it("requires the owner to explicitly enable platform AI before offering implementation", async () => {
    const builtin: IntegrationProvider = {
      ...provider, key: "llmgw", name: "ИИ", category: "ai", connection_mode: "platform",
      fields: [], enabled: false,
    };
    await render([], [builtin]);
    expect(button("Добавить в приложение")).toBeUndefined();
    expect(boundary.platformAi).not.toHaveBeenCalled();
    boundary.platformAi.mockResolvedValue({ enabled: true });
    boundary.catalog.mockResolvedValue({ ...catalog([]), providers: [{ ...builtin, enabled: true }] });
    await click("Включить ИИ");
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
    expect(boundary.platformAi).toHaveBeenCalledWith("project-1", true);
    expect(button("Добавить в приложение")).toBeDefined();
    expect(boundary.push).not.toHaveBeenCalled();
    expect(window.sessionStorage.length).toBe(0);
    boundary.platformAi.mockResolvedValue({ enabled: false });
    boundary.catalog.mockResolvedValue({ ...catalog([]), providers: [builtin] });
    await click("Выключить ИИ");
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
    expect(boundary.platformAi).toHaveBeenLastCalledWith("project-1", false);
    expect(button("Добавить в приложение")).toBeUndefined();
    expect(button("Включить ИИ")).toBeDefined();
  });
  it("does not expose configuration or generation for an unavailable platform provider", async () => {
    await render([], [{
      ...provider, key: "llmgw", name: "ИИ", category: "ai", connection_mode: "platform",
      fields: [], available: false,
    }]);
    expect(button("Добавить в приложение")).toBeUndefined();
    expect(button("Подключить")).toBeUndefined();
    expect(button("Настроить")).toBeUndefined();
    expect(container.textContent).not.toContain("Встроено");
  });
  it("reports verification as access confirmation", async () => {
    await render([connection]);
    boundary.verify.mockResolvedValue(connection);
    await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Проверить ЮKassa"]')!.click());
    expect(boundary.success).toHaveBeenCalledWith("Доступ к сервису подтверждён");
  });
});
