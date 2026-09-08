import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FigmaIntegrationHub } from "@/components/max/FigmaIntegrationHub";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import type { AppIntegration, IntegrationCatalog, IntegrationProvider } from "@/lib/api/types";

const boundary = vi.hoisted(() => ({ push: vi.fn(), connect: vi.fn(), catalog: vi.fn(), send: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: boundary.push }) }));
vi.mock("@/lib/api/messages", () => ({ sendPrompt: boundary.send }));
vi.mock("@/lib/api/max-studio", () => ({
  syncMaxManagedKit: async () => undefined,
  getMaxReadiness: async () => ({ items: [] }),
}));
vi.mock("@/lib/api/app-integrations", () => ({
  getIntegrationCatalog: boundary.catalog,
  connectAppIntegration: boundary.connect,
  setPlatformAiEnabled: vi.fn(), verifyAppIntegration: vi.fn(), bindAppIntegration: vi.fn(),
  disconnectAppIntegration: vi.fn(), applyIntegrationPack: vi.fn(), startIntegrationOAuth: vi.fn(),
}));

const provider: IntegrationProvider = {
  key: "yookassa", name: "ЮKassa", category: "payments", description: "Оплата заказов", capabilities: ["payments"],
  fields: [{ key: "secret_key", label: "Ключ", placeholder: "", help: "", secret: true, required: true }],
  available: true, recommended: true, requirement: null, docs_url: "https://example.com", oauth_supported: false,
  oauth_available: false, connection_mode: "credentials",
};
const connection: AppIntegration = {
  id: "connection", provider: "yookassa", status: "active", auth_mode: "credentials", business_scoped: true,
  bound_to_project: true, binding_status: "ready", binding_config: { private: "binding-must-not-leak" },
  account_label: "account-must-not-leak", public_config: { shop_id: "shop-must-not-leak" },
  capabilities: ["payments"], configured_fields: ["secret_key"], last_error: null, verified_at: null,
  last_checked_at: null, created_at: "2026-09-06", updated_at: "2026-09-06",
};
function catalog(connections: AppIntegration[]): IntegrationCatalog {
  return {
    providers: [provider], connections,
    recommended_pack: { key: "base", title: "Набор", description: "", provider_keys: [], bound_count: 0, reusable_count: 0 },
  };
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
async function settleMutation() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
}

describe("Integration Hub inside the editor modal", () => {
  let root: Root;
  let container: HTMLDivElement;
  let client: QueryClient;
  beforeEach(() => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    vi.resetAllMocks();
    boundary.send.mockResolvedValue({ run_id: "run-1", message_id: "message-1", snapshot_id: null, run_status: "pending" });
    window.sessionStorage.clear();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  });
  afterEach(() => {
    act(() => root.unmount());
    client.clear();
    container.remove();
    vi.restoreAllMocks();
  });
  async function render(connections: AppIntegration[], onExit?: () => void, onBusyChange?: (busy: boolean) => void) {
    const data = catalog(connections);
    client.setQueryData(["app-integrations", "project-1"], data);
    boundary.catalog.mockResolvedValue(data);
    await act(async () => root.render(
      createElement(QueryClientProvider, { client },
        createElement(Dialog, { defaultOpen: true },
          createElement(DialogContent, null,
            createElement(DialogTitle, null, "Интеграции проекта"),
            createElement(DialogDescription, null, "Сервисы приложения"),
            createElement(FigmaIntegrationHub, { projectId: "project-1", projectName: "Project", embedded: true, onExit, onBusyChange }),
          ),
        ),
      ),
    ));
    return document.querySelector('[role="dialog"]')!;
  }

  it("uses the editor's modal without adding a page header or project navigation", async () => {
    const dialog = await render([]);
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    expect(dialog.querySelector("h1")).toBeNull();
    expect(dialog.querySelector('nav[aria-label="Разделы проекта MAX"]')).toBeNull();
    expect(dialog.querySelector('a[aria-label="Все проекты"]')).toBeNull();
    expect(dialog.querySelector('input[aria-label="Найти сервис"]')).not.toBeNull();
  });

  it("replaces the catalog with credentials and restores the filters when going back", async () => {
    const dialog = await render([]);
    await act(async () => input(dialog.querySelector('input[aria-label="Найти сервис"]')!, "ЮKassa"));
    await click("Подключить");
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    expect(document.querySelector('[role="dialog"]')).toBe(dialog);
    expect(dialog.querySelector('input[aria-label="Найти сервис"]')).toBeNull();
    expect(dialog.querySelector("h2")?.textContent).toBe("Интеграции проекта");
    const secret = dialog.querySelector<HTMLInputElement>('#integration-secret_key')!;
    expect(secret).not.toBeNull();
    await act(async () => input(secret, "discard-this-secret"));
    await click("Назад к сервисам");
    expect(dialog.querySelector<HTMLInputElement>('input[aria-label="Найти сервис"]')?.value).toBe("ЮKassa");
    expect(dialog.querySelector('#integration-secret_key')).toBeNull();
    expect(boundary.connect).not.toHaveBeenCalled();
    await click("Подключить");
    expect(dialog.querySelector<HTMLInputElement>('#integration-secret_key')?.value).toBe("");
  });

  it("keeps failed credentials editable and returns to the catalog after a successful retry", async () => {
    const dialog = await render([]);
    await click("Подключить");
    const secret = document.querySelector<HTMLInputElement>('#integration-secret_key')!;
    expect(button("Проверить и подключить")?.disabled).toBe(true);
    await act(async () => input(secret, "credential-for-test"));
    boundary.connect.mockRejectedValueOnce(new Error("Доступ не подтверждён"));
    await click("Проверить и подключить");
    await settleMutation();
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    expect(dialog.querySelector<HTMLInputElement>('#integration-secret_key')?.value).toBe("credential-for-test");
    expect(boundary.connect).toHaveBeenCalledWith("project-1", "yookassa", { secret_key: "credential-for-test" });
    expect(boundary.push).not.toHaveBeenCalled();
    boundary.connect.mockResolvedValueOnce(connection);
    boundary.catalog.mockResolvedValueOnce(catalog([connection]));
    await click("Проверить и подключить");
    await settleMutation();
    expect(document.querySelector('[role="dialog"]')).toBe(dialog);
    expect(dialog.querySelector('#integration-secret_key')).toBeNull();
    expect(dialog.textContent).toContain("Подключено");
    expect(button("Добавить в приложение")).toBeDefined();
    expect(window.sessionStorage.length).toBe(0);
    expect(boundary.push).not.toHaveBeenCalled();
  });

  it("prevents leaving the credential step while its connection request is pending", async () => {
    const onBusyChange = vi.fn();
    const dialog = await render([], undefined, onBusyChange);
    await click("Подключить");
    await act(async () => input(document.querySelector<HTMLInputElement>('#integration-secret_key')!, "credential-for-test"));
    let finish!: (value: AppIntegration) => void;
    boundary.connect.mockImplementationOnce(() => new Promise<AppIntegration>((resolve) => { finish = resolve; }));
    await click("Проверить и подключить");
    await settleMutation();
    expect(button("Назад к сервисам")?.disabled).toBe(true);
    expect(onBusyChange).toHaveBeenLastCalledWith(true);
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    await act(async () => finish(connection));
    await settleMutation();
    expect(onBusyChange).toHaveBeenLastCalledWith(false);
    expect(dialog.querySelector('input[aria-label="Найти сервис"]')).not.toBeNull();
  });

  it("replaces the catalog with a cancellable implementation proposal and hands off only after confirmation", async () => {
    const onExit = vi.fn();
    const dialog = await render([connection], onExit);
    await click("Добавить в приложение");
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    expect(dialog.querySelector('input[aria-label="Найти сервис"]')).toBeNull();
    expect(dialog.querySelector<HTMLTextAreaElement>("textarea")?.value).toContain("ЮKassa");
    expect(dialog.querySelector<HTMLTextAreaElement>("textarea")?.value).not.toContain("must-not-leak");
    expect(dialog.textContent).toContain("баланс владельца");
    expect(boundary.send).not.toHaveBeenCalled();
    await act(async () => input(dialog.querySelector("textarea")!, "   "));
    expect(button("Запустить доработку")?.disabled).toBe(true);
    await click("Назад к сервисам");
    expect(dialog.querySelector("textarea")).toBeNull();
    expect(button("Добавить в приложение")).toBeDefined();
    expect(window.sessionStorage.length).toBe(0);
    expect(onExit).not.toHaveBeenCalled();
    await click("Добавить в приложение");
    await act(async () => input(dialog.querySelector("textarea")!, "Добавь оплату заказа через ЮKassa."));
    await click("Запустить доработку");
    await settleMutation();
    expect(boundary.send).toHaveBeenCalledWith("project-1", "Добавь оплату заказа через ЮKassa.", "topmix-v1", [], { skipClarify: true, idempotencyKey: expect.any(String) });
    expect(window.sessionStorage.getItem("omnia:max:starter:project-1")).toBeNull();
    expect(onExit).toHaveBeenCalledOnce();
    expect(boundary.push).not.toHaveBeenCalled();
  });

  it("preserves the implementation step if browser storage blocks its handoff", async () => {
    const onExit = vi.fn();
    const dialog = await render([connection], onExit);
    await click("Добавить в приложение");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Storage blocked"); });
    await click("Запустить доработку");
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    expect(dialog.querySelector("textarea")).not.toBeNull();
    expect(onExit).not.toHaveBeenCalled();
    expect(boundary.push).not.toHaveBeenCalled();
    expect(boundary.send).not.toHaveBeenCalled();
  });

  it("submits once while pending, marks the modal busy and refreshes the mounted chat after acceptance", async () => {
    const onExit = vi.fn();
    const onBusyChange = vi.fn();
    for (const key of ["messages", "generation", "project-versions"]) client.setQueryData([key, "project-1"], []);
    const dialog = await render([connection], onExit, onBusyChange);
    let finish!: (value: { run_id: string; message_id: string; snapshot_id: null }) => void;
    boundary.send.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    await click("Добавить в приложение");
    const submit = button("Запустить доработку")!;
    await act(async () => { submit.click(); submit.click(); });
    await settleMutation();
    expect(boundary.send).toHaveBeenCalledOnce();
    expect(submit.disabled).toBe(true);
    expect(button("Назад к сервисам")?.disabled).toBe(true);
    expect(dialog.querySelector<HTMLTextAreaElement>("textarea")?.disabled).toBe(true);
    expect(onBusyChange).toHaveBeenLastCalledWith(true);
    expect(onExit).not.toHaveBeenCalled();
    await act(async () => finish({ run_id: "run-1", message_id: "message-1", snapshot_id: null }));
    await settleMutation();
    expect(onExit).toHaveBeenCalledOnce();
    expect(onBusyChange).toHaveBeenLastCalledWith(false);
    expect(boundary.push).not.toHaveBeenCalled();
    for (const key of ["messages", "generation", "project-versions"]) expect(client.getQueryState([key, "project-1"])?.isInvalidated).toBe(true);
  });

  it("never dispatches an integration proposal containing an inserted secret", async () => {
    const dialog = await render([connection]);
    await click("Добавить в приложение");
    await act(async () => input(dialog.querySelector("textarea")!, "Добавь оплату. API key: secret_value_1234567890"));
    await click("Запустить доработку");
    expect(dialog.querySelector('[role="alert"]')?.textContent).toContain("ключ");
    expect(boundary.send).not.toHaveBeenCalled();
    expect(boundary.push).not.toHaveBeenCalled();
    expect(window.sessionStorage.length).toBe(0);
  });

  it("reuses an uncertain attempt after reopening and only allocates a new key for a deliberately edited proposal", async () => {
    const onExit = vi.fn();
    boundary.send.mockRejectedValueOnce(new Error("Ответ потерян"));
    const dialog = await render([connection], onExit);
    await click("Добавить в приложение");
    await click("Запустить доработку");
    await settleMutation();
    expect(dialog.querySelector('[role="alert"]')?.textContent).toContain("Ответ потерян");
    expect(onExit).not.toHaveBeenCalled();
    expect(boundary.send).toHaveBeenCalledOnce();
    const firstKey = boundary.send.mock.calls[0][4].idempotencyKey;
    await click("Назад к сервисам");
    await click("Добавить в приложение");
    await click("Запустить доработку");
    await settleMutation();
    expect(boundary.send.mock.calls[1][4].idempotencyKey).toBe(firstKey);
    await act(async () => input(dialog.querySelector("textarea")!, "Добавь новую форму оплаты заказа."));
    await click("Запустить доработку");
    await settleMutation();
    expect(boundary.send.mock.calls[2][4].idempotencyKey).not.toBe(firstKey);
  });

  it.each(["failed", "cancelled"])("offers an explicit new attempt after terminal %s and reuses that key on an uncertain retry", async (run_status) => {
    const onExit = vi.fn();
    boundary.send.mockResolvedValueOnce({ run_id: "terminal", message_id: "m", snapshot_id: null, run_status, replayed: true })
      .mockRejectedValueOnce(new Error("Ответ потерян"));
    const dialog = await render([connection], onExit);
    await click("Добавить в приложение");
    await click("Запустить доработку");
    await settleMutation();
    expect(onExit).not.toHaveBeenCalled();
    expect(dialog.querySelector('[role="alert"]')).not.toBeNull();
    const originalKey = boundary.send.mock.calls[0][4].idempotencyKey;
    await click("Повторить доработку");
    await settleMutation();
    const retryKey = boundary.send.mock.calls[1][4].idempotencyKey;
    expect(retryKey).not.toBe(originalKey);
    expect(onExit).not.toHaveBeenCalled();
    await click("Запустить доработку");
    await settleMutation();
    expect(boundary.send.mock.calls[2][4].idempotencyKey).toBe(retryKey);
    expect(onExit).toHaveBeenCalledOnce();
  });

  it("clears parent busy state when pending integration content unmounts", async () => {
    const onBusyChange = vi.fn();
    boundary.connect.mockImplementationOnce(() => new Promise(() => {}));
    await render([], undefined, onBusyChange);
    await click("Подключить");
    await act(async () => input(document.querySelector<HTMLInputElement>('#integration-secret_key')!, "credential-for-test"));
    await click("Проверить и подключить");
    await settleMutation();
    expect(onBusyChange).toHaveBeenLastCalledWith(true);
    await act(async () => root.render(null));
    expect(onBusyChange).toHaveBeenLastCalledWith(false);
  });
});
