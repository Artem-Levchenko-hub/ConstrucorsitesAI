import { act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FigmaIntegrationHub } from "@/components/max/FigmaIntegrationHub";
import type { AppIntegration, IntegrationCatalog, IntegrationProvider } from "@/lib/api/types";

const boundary = vi.hoisted(() => ({ push: vi.fn(), connect: vi.fn(), verify: vi.fn(), catalog: vi.fn(), pack: vi.fn(), platformAi: vi.fn(), amocrmOptions: vi.fn(), saveAmocrm: vi.fn(), success: vi.fn() }));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(window.location.search), useRouter: () => ({ push: boundary.push }) }));
vi.mock("@/components/max/MaxSectionShell", () => ({ MaxSectionShell: ({ children }: { children: ReactNode }) => children }));
vi.mock("@/lib/api/max-studio", () => ({ syncMaxManagedKit: async () => undefined }));
vi.mock("sonner", () => ({ toast: { success: boundary.success, error: vi.fn(), warning: vi.fn() } }));
vi.mock("@/lib/api/app-integrations", () => ({
  getIntegrationCatalog: boundary.catalog, setPlatformAiEnabled: boundary.platformAi, connectAppIntegration: boundary.connect, verifyAppIntegration: boundary.verify,
  bindAppIntegration: vi.fn(), disconnectAppIntegration: vi.fn(), applyIntegrationPack: boundary.pack, startIntegrationOAuth: vi.fn(),
  getAmocrmOptions: boundary.amocrmOptions, saveAmocrmSettings: boundary.saveAmocrm,
}));
const provider: IntegrationProvider = {
  key: "yookassa", name: "ЮKassa", category: "payments", description: "Оплата", capabilities: ["payments"],
  fields: [{ key: "secret_key", label: "Ключ", placeholder: "", help: "", secret: true, required: true }],
  available: true, recommended: true, requirement: null, docs_url: "https://example.com", oauth_supported: false, oauth_available: false, connection_mode: "credentials",
};
const connection: AppIntegration = {
  id: "connection", provider: "yookassa", status: "active", auth_mode: "credentials", account_scoped: true,
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
function select(element: HTMLSelectElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!.call(element, value);
  element.dispatchEvent(new Event("change", { bubbles: true }));
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
  async function render(connections: AppIntegration[], providers = [provider], recommendedPack = catalog(connections).recommended_pack) {
    const data = { ...catalog(connections), providers, recommended_pack: recommendedPack };
    client.setQueryData(["app-integrations", "project-1"], data);
    boundary.catalog.mockResolvedValue(data);
    await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(FigmaIntegrationHub, { projectId: "project-1", projectName: "Project" }))));
  }
  it("explains a conflicting OAuth return without claiming that access was granted", async () => {
    window.history.replaceState(null, "", "/max/project-1?panel=services&oauth=conflict");
    try {
      await render([]);
      expect(container.querySelector('[role="alert"]')?.textContent).toContain("Подключение не изменено");
      expect(boundary.success).not.toHaveBeenCalled();
      expect(boundary.connect).not.toHaveBeenCalled();
    } finally {
      window.history.replaceState(null, "", "/");
    }
  });
  it.each(["__proto__", "constructor", "toString"])("ignores inherited OAuth outcome %s and keeps the catalog usable", async (outcome) => {
    window.history.replaceState(null, "", `/max/project-1?panel=services&oauth=${outcome}`);
    try {
      await render([]);
      expect(container.querySelector('[role="alert"]')).toBeNull();
      expect(button("Подключить")).toBeDefined();
    } finally {
      window.history.replaceState(null, "", "/");
    }
  });
  it("ignores duplicate OAuth outcomes", async () => {
    window.history.replaceState(null, "", "/max/project-1?panel=services&oauth=connected&oauth=conflict");
    try {
      await render([]);
      expect(container.querySelector('[role="alert"]')).toBeNull();
      expect(button("Подключить")).toBeDefined();
    } finally {
      window.history.replaceState(null, "", "/");
    }
  });
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
  it("shows an empty search result and lets the user reset filters", async () => {
    await render([]);
    const search = container.querySelector<HTMLInputElement>('input[placeholder="Найти сервис"]')!;
    await act(async () => input(search, "нет такого сервиса"));
    expect(container.textContent).toContain("Ничего не найдено");
    expect(button("Подключить")).toBeUndefined();
    await click("Сбросить фильтры");
    expect(search.value).toBe("");
    expect(button("Подключить")).toBeDefined();
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
    vi.spyOn(Object.getPrototypeOf(window.sessionStorage), "setItem").mockImplementation(() => { throw new Error("Storage blocked"); });
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
  it("offers only the four supported services and preserves hidden connection records", async () => {
    const providers: IntegrationProvider[] = [
      provider,
      { ...provider, key: "moysklad", name: "МойСклад", category: "inventory" },
      { ...provider, key: "amocrm", name: "amoCRM", category: "crm" },
      { ...provider, key: "yandex_metrica", name: "Яндекс Метрика", category: "analytics" },
      ...["bitrix24", "iiko", "rkeeper", "one_c", "yclients", "cdek", "future_service"].map(key => ({ ...provider, key, name: key, category: "booking" as const })),
      { ...provider, key: "llmgw", name: "ИИ", category: "ai", connection_mode: "platform", enabled: true },
    ];
    const saved = [{ ...connection, provider: "bitrix24" }, connection];
    await render(saved, providers);
    expect([...container.querySelectorAll("h3")].map(el => el.textContent)).toEqual(["ЮKassaПодключено", "МойСклад", "amoCRM", "Яндекс Метрика"]);
    expect(button("ИИ")).toBeUndefined();
    expect(button("Запись")).toBeUndefined();
    expect(container.querySelector(".max-integration-summary")?.textContent).toContain("1 / 4");
    await click("CRM");
    expect([...container.querySelectorAll("h3")].map(el => el.textContent)).toEqual(["amoCRM"]);
    await click("Все сервисы");
    const search = container.querySelector<HTMLInputElement>('input[aria-label="Найти сервис"]')!;
    await act(async () => input(search, "bitrix24"));
    expect(container.textContent).toContain("Ничего не найдено");
    expect(container.querySelector('[aria-label="Проверить bitrix24"]')).toBeNull();
    expect(client.getQueryData<IntegrationCatalog>(["app-integrations", "project-1"])?.connections).toEqual(saved);
    expect(boundary.connect).not.toHaveBeenCalled();
    expect(boundary.platformAi).not.toHaveBeenCalled();
  });
  it("hides a recommended pack that would connect unsupported services", async () => {
    await render([], [provider], {
      ...catalog([]).recommended_pack!, title: "Bitrix24 bundle", provider_keys: ["yookassa", "bitrix24"],
    });
    expect(button("Подключить рекомендуемые")).toBeUndefined();
    expect(container.textContent).not.toContain("Bitrix24 bundle");
    expect(boundary.pack).not.toHaveBeenCalled();
  });
  it("keeps an entirely supported recommended pack actionable", async () => {
    boundary.pack.mockResolvedValue({ remaining_provider_keys: [] });
    await render([], [provider], {
      ...catalog([]).recommended_pack!, provider_keys: ["yookassa"],
    });
    await click("Подключить рекомендуемые");
    expect(boundary.pack).toHaveBeenCalledWith("project-1");
  });
  it("reports verification as access confirmation", async () => {
    await render([connection]);
    boundary.verify.mockResolvedValue(connection);
    await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Проверить ЮKassa"]')!.click());
    expect(boundary.success).toHaveBeenCalledWith("Доступ к сервису подтверждён");
  });
  it("lets a previously failed connection retry verification without replacing credentials", async () => {
    await render([{ ...connection, status: "error", binding_status: "error" }]);
    expect(button("Подключить")).toBeUndefined();
    const retry = document.querySelector<HTMLButtonElement>('[aria-label="Проверить ЮKassa"]');
    expect(retry).not.toBeNull();
    boundary.verify.mockResolvedValue(connection);
    await act(async () => retry!.click());
    expect(boundary.verify).toHaveBeenCalledWith("project-1", "yookassa");
  });
  it("offers current stock and order capabilities in the MoySklad implementation brief", async () => {
    await render(
      [{ ...connection, provider: "moysklad" }],
      [{ ...provider, key: "moysklad", name: "МойСклад", category: "inventory" }],
    );
    await click("Добавить в приложение");
    const brief = document.querySelector<HTMLTextAreaElement>("textarea")!.value;
    expect(brief).toContain("остатки");
    expect(brief).toContain("заказа покупателя");
    expect(brief).not.toContain("пока недоступны");
  });
  it("offers an amoCRM lead form with reconciliation and an idempotency key", async () => {
    await render(
      [{ ...connection, provider: "amocrm" }],
      [{ ...provider, key: "amocrm", name: "amoCRM", category: "crm" }],
    );
    await click("Добавить в приложение");
    const brief = document.querySelector<HTMLTextAreaElement>("textarea")!.value;
    expect(brief).toContain("Имя");
    expect(brief).toContain("телефон");
    expect(brief).toContain("e-mail");
    expect(brief).toContain("комментарий");
    expect(brief).toContain("getYleumLeads()");
    expect(brief).toContain("getYleumLeadStatus(id)");
    expect(brief).toContain("details_status");
    expect(brief).toContain("идемпотентности");
  });
  it("loads, changes, and saves amoCRM pipeline settings without fixed IDs", async () => {
    const amoConnection: AppIntegration = {
      ...connection,
      provider: "amocrm",
      binding_config: { pipeline_id: 10, status_id: 101 },
    };
    boundary.amocrmOptions.mockResolvedValue({
      pipelines: [
        { id: 10, name: "Продажи", statuses: [{ id: 101, name: "Первичный контакт" }] },
        { id: 20, name: "Поддержка", statuses: [{ id: 201, name: "Новая заявка" }] },
      ],
    });
    await render(
      [amoConnection],
      [{ ...provider, key: "amocrm", name: "amoCRM", category: "crm" }],
    );
    await click("Настроить");
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
    const pipeline = document.querySelector<HTMLSelectElement>("#amocrm-pipeline")!;
    const status = document.querySelector<HTMLSelectElement>("#amocrm-status")!;
    expect(pipeline.value).toBe("10");
    expect(status.value).toBe("101");
    await act(async () => select(pipeline, "20"));
    expect(status.value).toBe("");
    await act(async () => select(status, "201"));
    boundary.saveAmocrm.mockResolvedValue({ status: "saved" });
    await click("Сохранить этап");
    expect(boundary.saveAmocrm).toHaveBeenCalledWith("project-1", 20, 201);
  });
});
