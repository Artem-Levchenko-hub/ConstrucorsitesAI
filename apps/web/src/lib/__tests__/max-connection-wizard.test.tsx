import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MaxConnectionWizard } from "@/components/max/MaxConnectionWizard";
import type { DeployStatus, MaxIntegration, MaxProjectConfig } from "@/lib/api/types";

const api = vi.hoisted(() => ({
  integration: vi.fn(), config: vi.fn(), deploy: vi.fn(), connect: vi.fn(), verify: vi.fn(), activate: vi.fn(),
  disconnect: vi.fn(), saveAttached: vi.fn(), navigate: vi.fn(), clipboard: vi.fn(), fetch: vi.fn(),
}));
vi.mock("@/lib/api/max-integration", () => ({
  getMaxIntegration: api.integration, connectMaxIntegration: api.connect, verifyMaxIntegration: api.verify,
  activateMaxIntegration: api.activate, disconnectMaxIntegration: api.disconnect,
}));
vi.mock("@/lib/api/max-studio", () => ({ getMaxProjectConfig: api.config, saveMaxUrlAttached: api.saveAttached }));
vi.mock("@/lib/api/runtime", () => ({ getLastDeploy: api.deploy }));

const disconnected: MaxIntegration = {
  eligible: true, connected: false, status: "disconnected", bot_id: null, bot_name: null, bot_username: null,
  app_url: null, webhook_url: null, deep_link: null, last_error: null, verified_at: null, published_at: null,
};
const connected: MaxIntegration = { ...disconnected, connected: true, status: "verified", bot_name: "Мой бот" };
const config: MaxProjectConfig = {
  project_id: "project-1", config_version: 1, synced_snapshot_id: null, updated_at: null,
  config: {
    app_name: "Приложение", app_type: "custom", summary: "", audience: "", primary_action: "", features: [],
    style: "clean", brand_colors: "", content: [], operator: { legal_name: "", inn: "", ogrn: "", address: "" },
    support: { email: null, phone: "", response_time: "" },
    legal: { age_rating: "0+", has_sales: false, has_user_content: false, marketing_notifications: false, personal_data_consent: false, terms_accepted: false },
    max_url_attached: false,
  },
};
const deploy: DeployStatus = {
  run_id: null, phase: "idle", started_at: null, finished_at: null, prod_url: null, image_tag: null,
  error: null, detail: null, target_label: null, target_id: null, can_cancel: false, logs: [],
};
const published = { ...deploy, phase: "done" as const, prod_url: "https://app.example.com" };

describe("MAX connection wizard", () => {
  let root: Root;
  let container: HTMLDivElement;
  let client: QueryClient;
  beforeEach(() => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    vi.resetAllMocks();
    api.integration.mockResolvedValue(disconnected);
    api.config.mockResolvedValue(config);
    api.deploy.mockResolvedValue(deploy);
    api.clipboard.mockResolvedValue(undefined);
    api.fetch.mockResolvedValue(new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", api.fetch);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: api.clipboard } });
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount()); client.clear(); container.remove(); vi.unstubAllGlobals();
  });
  async function settle() {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
  }
  async function render(onBusyChange?: (busy: boolean) => void) {
    await act(async () => root.render(<QueryClientProvider client={client}><MaxConnectionWizard projectId="project-1" onNavigate={api.navigate} onBusyChange={onBusyChange} /></QueryClientProvider>));
    await settle();
  }
  function button(label: string) {
    return Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find(el => el.textContent?.trim() === label);
  }
  async function click(label: string) {
    const target = button(label);
    expect(target, label).toBeDefined();
    await act(async () => target!.click());
    await settle();
  }
  async function fillToken(value: string) {
    const element = container.querySelector<HTMLInputElement>('input[type="password"]')!;
    expect(element).not.toBeNull();
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(element, value);
      element.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }
  function expectNoWrites() {
    for (const method of [api.connect, api.verify, api.activate, api.disconnect, api.saveAttached, api.fetch]) expect(method).not.toHaveBeenCalled();
  }

  it("starts with one bot-creation step and performs no automatic write", async () => {
    await render();
    expect(container.querySelectorAll("h2")).toHaveLength(1);
    expect(container.querySelector("h2")?.textContent).toBe("Создайте бота в MAX");
    expect(container.querySelector('a[href="https://business.max.ru/"]')).not.toBeNull();
    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(container.querySelector('input[type="checkbox"]')).toBeNull();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expectNoWrites();
  });

  it("connects only on explicit submission, keeps failures editable and clears the secret after success", async () => {
    await render();
    await click("Бот готов — далее");
    expect(container.querySelectorAll("h2")).toHaveLength(1);
    expect(button("Подключить бота")?.disabled).toBe(true);
    await fillToken("short");
    expect(button("Подключить бота")?.disabled).toBe(true);
    await fillToken("test-secret-12345");
    expectNoWrites();
    api.connect.mockRejectedValueOnce(new Error("Не удалось подключить бота"));
    await click("Подключить бота");
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(container.querySelector<HTMLInputElement>('input[type="password"]')?.value).toBe("test-secret-12345");
    expect(button("Перейти к публикации")).toBeUndefined();
    api.connect.mockResolvedValueOnce(connected);
    await click("Подключить бота");
    expect(api.connect).toHaveBeenLastCalledWith("project-1", "test-secret-12345");
    expect(button("Перейти к публикации")).toBeDefined();
    expect(Array.from(container.querySelectorAll<HTMLInputElement>('input[type="password"]')).every(el => el.value === "")).toBe(true);
    expect(api.navigate).not.toHaveBeenCalled();
    await click("Перейти к публикации");
    expect(api.navigate).toHaveBeenCalledWith("publish");
    expect(api.saveAttached).not.toHaveBeenCalled();
  });

  it("starts at publication for a connected bot without a permanent HTTPS address", async () => {
    api.integration.mockResolvedValue(connected);
    await render();
    expect(container.querySelector("h2")?.textContent).toBe("Опубликуйте приложение");
    expect(container.querySelectorAll("h2")).toHaveLength(1);
    expect(button("Обновить связь")?.disabled).toBe(true);
    expectNoWrites();
  });

  it("requires manual acknowledgement after copying, then saves confirmation only after its GET succeeds", async () => {
    api.integration.mockResolvedValue({ ...connected, app_url: published.prod_url });
    api.deploy.mockResolvedValue(published);
    api.saveAttached.mockResolvedValue({ ...config, config: { ...config.config, max_url_attached: true } });
    await render();
    expect(container.querySelector("h2")?.textContent).toBe("Добавьте адрес в MAX");
    expect(button("Сохранить подтверждение")?.disabled).toBe(true);
    await click("Скопировать адрес");
    expect(api.clipboard).toHaveBeenCalledWith("https://app.example.com");
    expect(container.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked).toBe(false);
    expectNoWrites();
    await act(async () => container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    expect(api.saveAttached).not.toHaveBeenCalled();
    api.fetch.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await click("Сохранить подтверждение");
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(api.saveAttached).not.toHaveBeenCalled();
    await click("Сохранить подтверждение");
    expect(api.fetch).toHaveBeenLastCalledWith("https://app.example.com", { method: "GET", mode: "no-cors", cache: "no-store" });
    expect(api.saveAttached).toHaveBeenCalledWith("project-1", true);
    expect(container.textContent).toContain("Подтверждение сохранено");
    expect(container.textContent).not.toContain("Приложение работает");
    expect(container.textContent).not.toContain("MAX проверен");
  });

  it("requires acknowledgement again if the displayed application address changes", async () => {
    api.integration.mockResolvedValue(connected);
    api.deploy.mockResolvedValue(published);
    await render();
    expect(container.querySelector('input[type="checkbox"]')).not.toBeNull();
    await act(async () => container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    expect(button("Сохранить подтверждение")?.disabled).toBe(false);
    await act(async () => client.setQueryData(["deploy", "project-1"], { ...published, prod_url: "https://other.example.com" }));
    await settle();
    expect(container.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked).toBe(false);
    expect(button("Сохранить подтверждение")?.disabled).toBe(true);
    expectNoWrites();
  });

  it.each(["http://app.example.com", "https://user:password@app.example.com", "javascript:alert(1)"])("does not offer address confirmation for invalid production URL %s", async (prod_url) => {
    api.integration.mockResolvedValue(connected);
    api.deploy.mockResolvedValue({ ...published, prod_url });
    await render();
    expect(button("Перейти к публикации")).toBeDefined();
    expect(button("Сохранить подтверждение")).toBeUndefined();
    expectNoWrites();
  });

  it("does not save an old address acknowledgement when publication changes during the network request", async () => {
    api.integration.mockResolvedValue(connected);
    api.deploy.mockResolvedValue(published);
    let finish!: (response: Response) => void;
    api.fetch.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
    await render();
    await act(async () => container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    await click("Сохранить подтверждение");
    await act(async () => client.setQueryData(["deploy", "project-1"], { ...published, prod_url: "https://replacement.example.com" }));
    await act(async () => finish(new Response(null, { status: 200 })));
    await settle();
    expect(api.saveAttached).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(container.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked).toBe(false);
  });

  it("shows loading until all three sources have returned", async () => {
    api.config.mockImplementationOnce(() => new Promise(() => {}));
    await render();
    expect(container.querySelector('[role="status"]')).not.toBeNull();
    expect(button("Бот готов — далее")).toBeUndefined();
    expectNoWrites();
  });

  it.each(["max-integration", "max-config", "deploy"])("hides cached connected state after a %s refresh fails and lets the user retry", async (queryKey) => {
    api.integration.mockResolvedValue(connected);
    api.deploy.mockResolvedValue(published);
    await render();
    const source = queryKey === "max-integration" ? api.integration : queryKey === "max-config" ? api.config : api.deploy;
    source.mockRejectedValueOnce(new Error("Сеть недоступна"));
    await act(async () => { await client.invalidateQueries({ queryKey: [queryKey, "project-1"] }); });
    await settle();
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(button("Сохранить подтверждение")).toBeUndefined();
    expect(container.querySelector("details")).toBeNull();
    expectNoWrites();
    await click("Повторить");
    expect(button("Сохранить подтверждение")).toBeDefined();
  });

  it("does not expose connection actions for an ineligible project", async () => {
    api.integration.mockResolvedValue({ ...connected, eligible: false });
    await render();
    expect(container.textContent).toContain("Подключение MAX недоступно для этого проекта");
    expect(container.querySelector("button")).toBeNull();
    expectNoWrites();
  });

  it("keeps existing connection maintenance in an advanced disclosure with inline disconnect confirmation", async () => {
    api.integration.mockResolvedValue({ ...connected, app_url: published.prod_url });
    api.deploy.mockResolvedValue(published);
    api.verify.mockResolvedValue({ ...connected, app_url: published.prod_url });
    api.activate.mockResolvedValue({ ...connected, app_url: published.prod_url, status: "active" });
    api.disconnect.mockResolvedValue(undefined);
    await render();
    const details = button("Проверить подключение")?.closest("details");
    expect(details).toBeDefined();
    expect(details?.open).toBe(false);
    await act(async () => details!.querySelector("summary")!.click());
    expectNoWrites();
    await click("Проверить подключение");
    expect(api.verify).toHaveBeenCalledWith("project-1");
    await click("Обновить связь");
    expect(api.activate).toHaveBeenCalledWith("project-1");
    await click("Отключить MAX");
    expect(api.disconnect).not.toHaveBeenCalled();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    await click("Отмена");
    expect(api.disconnect).not.toHaveBeenCalled();
    await click("Отключить MAX");
    api.integration.mockResolvedValue(disconnected);
    await click("Да, отключить");
    expect(api.disconnect).toHaveBeenCalledWith("project-1");
    expect(button("Бот готов — далее")).toBeDefined();
    expect(button("Сохранить подтверждение")).toBeUndefined();
  });

  it("does not advance a server connection in error to publication", async () => {
    api.integration.mockResolvedValue({ ...connected, status: "error", last_error: "Токен больше не действует" });
    await render();
    expect(container.querySelector('[role="alert"]')?.textContent ?? "").toContain("Токен больше не действует");
    expect(button("Перейти к публикации")).toBeUndefined();
    expect(container.querySelector('input[type="password"]')).not.toBeNull();
    expectNoWrites();
  });

  it("keeps token submission busy and immutable until its request finishes", async () => {
    const onBusyChange = vi.fn();
    let finish!: (value: MaxIntegration) => void;
    api.connect.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    await render(onBusyChange);
    await click("Бот готов — далее");
    await fillToken("first-token-12345");
    await click("Подключить бота");
    expect(onBusyChange).toHaveBeenLastCalledWith(true);
    expect(container.querySelector<HTMLInputElement>('input[type="password"]')?.disabled).toBe(true);
    await fillToken("second-token-98765");
    await settle();
    expect(onBusyChange).toHaveBeenLastCalledWith(true);
    expect(button("Подключить бота")?.disabled).toBe(true);
    await click("Подключить бота");
    expect(api.connect).toHaveBeenCalledOnce();
    expect(api.connect).toHaveBeenCalledWith("project-1", "first-token-12345");
    await act(async () => finish(connected));
    await settle();
    expect(onBusyChange).toHaveBeenLastCalledWith(false);
  });

  it("clears the parent busy flag when pending wizard content unmounts", async () => {
    const onBusyChange = vi.fn();
    api.connect.mockImplementationOnce(() => new Promise(() => {}));
    await render(onBusyChange);
    await click("Бот готов — далее");
    await fillToken("pending-token-12345");
    await click("Подключить бота");
    expect(onBusyChange).toHaveBeenLastCalledWith(true);
    await act(async () => root.render(null));
    expect(onBusyChange).toHaveBeenLastCalledWith(false);
  });
});
