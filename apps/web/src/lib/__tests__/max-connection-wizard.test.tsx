import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MaxConnectionWizard } from "@/components/max/MaxConnectionWizard";
import { ApiError } from "@/lib/api/client";
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
const connected: MaxIntegration = { ...disconnected, connected: true, status: "verified", bot_id: "current-bot", bot_name: "Мой бот" };
const config: MaxProjectConfig = {
  project_id: "project-1", config_version: 1, synced_snapshot_id: null, updated_at: null,
  config: {
    app_name: "Приложение", app_type: "custom", summary: "", audience: "", primary_action: "", features: [],
    style: "clean", brand_colors: "", content: [], operator: { legal_name: "" },
    support: { email: null, response_time: "" },
    legal: { age_rating: "0+", has_sales: false, has_user_content: false, marketing_notifications: false, personal_data_consent: false, terms_accepted: false, policy_url: "" },
    max_url_attached: false,
  },
};
const deploy: DeployStatus = {
  run_id: null, phase: "idle", started_at: null, finished_at: null, prod_url: null, image_tag: null,
  error: null, detail: null, target_label: null, target_id: null, can_cancel: false, logs: [],
};
const published = { ...deploy, phase: "done" as const, prod_url: "https://app.example.com" };
const confirmAddress = "Я сохранил адрес в MAX";

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

  it("connects a reusable bot without disconnecting another project", async () => {
    await render();
    await click("Бот готов — далее");
    await fillToken("test-secret-12345");
    expect(container.textContent).toContain("одного бота к нескольким приложениям");
    api.connect.mockResolvedValueOnce({ ...connected, bot_id: "shared-bot" });
    await click("Подключить бота");
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(button("Перейти к публикации")).toBeDefined();
    expect(api.disconnect).not.toHaveBeenCalled();
    expect(api.activate).not.toHaveBeenCalled();
    expect(api.connect).toHaveBeenLastCalledWith("project-1", "test-secret-12345");
  });

  it("explains an in-progress bot connection without suggesting an invalid token", async () => {
    await render();
    await click("Бот готов — далее");
    await fillToken("test-secret-12345");
    api.connect.mockRejectedValueOnce(new ApiError(409, {
      code: "max_bot_binding_busy",
      message: "Подключение этого бота уже выполняется. Повторите попытку позже.",
    }));
    await click("Подключить бота");
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("для этого приложения уже изменяется");
    expect(api.disconnect).not.toHaveBeenCalled();
    expect(button("Перейти к публикации")).toBeUndefined();
  });

  it("keeps back and connect in the same step action group and returns without submitting", async () => {
    await render();
    await click("Бот готов — далее");
    const actions = container.querySelector('[role="group"][aria-label="Действия шага"]');
    expect(actions).not.toBeNull();
    expect(actions!.contains(button("Подключить бота")!)).toBe(true);
    const back = actions!.querySelector<HTMLButtonElement>('button[aria-label="Назад к созданию бота"]');
    expect(back).not.toBeNull();
    await fillToken("test-secret-not-submitted");
    await act(async () => back!.click());
    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(button("Бот готов — далее")).toBeDefined();
    expectNoWrites();
  });

  it("starts at publication for a connected bot without a permanent HTTPS address", async () => {
    api.integration.mockResolvedValue(connected);
    await render();
    expect(container.querySelector("h2")?.textContent).toBe("Опубликуйте приложение");
    expect(container.querySelectorAll("h2")).toHaveLength(1);
    expect(button("Обновить связь")?.disabled).toBe(true);
    expectNoWrites();
  });

  it("uses a single explicit address confirmation and lets the user retry", async () => {
    api.integration.mockResolvedValue({ ...connected, app_url: published.prod_url });
    api.deploy.mockResolvedValue(published);
    api.saveAttached.mockResolvedValue({ ...config, config: { ...config.config, max_url_attached: true } });
    await render();
    expect(container.querySelector("h2")?.textContent).toBe("Добавьте адрес в MAX");
    expect(button(confirmAddress)?.disabled).toBe(false);
    await click("Скопировать адрес");
    expect(api.clipboard).toHaveBeenCalledWith("https://app.example.com");
    expect(container.querySelector('input[type="checkbox"]')).toBeNull();
    expect(container.textContent).not.toContain("Что подтверждает этот шаг");
    expectNoWrites();
    api.fetch.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await click(confirmAddress);
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(api.saveAttached).not.toHaveBeenCalled();
    await click(confirmAddress);
    expect(api.fetch).toHaveBeenLastCalledWith("https://app.example.com", { method: "GET", mode: "no-cors", cache: "no-store" });
    expect(api.saveAttached).toHaveBeenCalledWith("project-1", true, "current-bot");
    expect(container.textContent).toContain("Подтверждение сохранено");
    expect(document.activeElement?.textContent?.trim()).toBe("Адрес приложения в MAX");
    expect(container.textContent).not.toContain("Приложение работает");
    expect(container.textContent).not.toContain("MAX проверен");
  });

  it("only confirms the address shown at the time of the explicit click", async () => {
    api.integration.mockResolvedValue(connected);
    api.deploy.mockResolvedValue(published);
    api.saveAttached.mockResolvedValue({ ...config, config: { ...config.config, max_url_attached: true } });
    await render();
    await act(async () => client.setQueryData(["deploy", "project-1"], { ...published, prod_url: "https://other.example.com" }));
    await settle();
    expect(container.querySelector("code")?.textContent).toBe("https://other.example.com");
    expectNoWrites();
    await click(confirmAddress);
    expect(api.fetch).toHaveBeenCalledWith("https://other.example.com", expect.any(Object));
    expect(api.saveAttached).toHaveBeenCalledWith("project-1", true, "current-bot");
  });

  it.each(["verified", "active"] as const)("shows a compact saved bot in status %s and keeps address actions", async status => {
    api.integration.mockResolvedValue({ ...connected, status });
    api.deploy.mockResolvedValue(published);
    api.config.mockResolvedValue({ ...config, config: { ...config.config, max_url_attached: true } });
    await render();

    const summaries = Array.from(container.querySelectorAll("summary"));
    const bot = summaries.find(summary => summary.textContent?.includes("Мой бот"));
    const address = summaries.find(summary => summary.textContent?.trim() === "Адрес приложения в MAX");
    expect(bot?.textContent).toContain("Бот подключён");
    expect(bot?.closest("details")?.open).toBe(false);
    expect(address?.closest("details")?.open).toBe(false);
    expect(container.querySelector('ol[aria-label="Шаги подключения MAX"]')).toBeNull();
    expect(container.textContent).not.toContain("Подтверждение сохранено");
    expect(container.textContent).not.toContain("в сети");
    expectNoWrites();

    await act(async () => address!.click());
    expect(address?.closest("details")?.open).toBe(true);
    expect(address?.closest("details")?.textContent).toContain("Адрес подтверждали ранее");
    await click("Скопировать адрес");
    expect(api.clipboard).toHaveBeenCalledWith(published.prod_url);
    expect(button(confirmAddress)?.disabled).toBe(false);
    expectNoWrites();
  });

  it("reopens the address step when the known URL changes for the same bot", async () => {
    api.integration.mockResolvedValue(connected);
    api.deploy.mockResolvedValue(published);
    const previous = { ...config, config: { ...config.config, max_url_attached: true } };
    api.config.mockResolvedValue(previous);
    api.saveAttached.mockResolvedValue(previous);
    await render();
    expect(container.querySelector(".max-connect-address-settings")).not.toBeNull();

    const prod_url = "https://other.example.com";
    await act(async () => client.setQueryData(["deploy", "project-1"], { ...published, prod_url }));
    await settle();
    expect(container.querySelector("h2")?.textContent).toBe("Добавьте адрес в MAX");
    expect(container.querySelector(".max-connect-address-settings")).toBeNull();
    expect(container.querySelector("code")?.textContent).toBe(prod_url);
    expectNoWrites();

    await click(confirmAddress);
    expect(api.fetch).toHaveBeenCalledWith(prod_url, expect.any(Object));
    expect(api.saveAttached).toHaveBeenCalledWith("project-1", true, "current-bot");
    expect(container.querySelector(".max-connect-address-settings")).not.toBeNull();
  });

  it("reopens the address step for a replacement bot before config finishes refetching", async () => {
    api.integration.mockResolvedValue(connected);
    api.deploy.mockResolvedValue(published);
    api.config.mockResolvedValue({ ...config, config: { ...config.config, max_url_attached: true } });
    api.connect.mockResolvedValue({ ...connected, bot_id: "replacement-bot", bot_name: "Новый бот" });
    await render();
    expect(container.querySelector(".max-connect-address-settings")).not.toBeNull();

    await act(async () => container.querySelector<HTMLElement>(".max-connect-advanced > summary")!.click());
    await act(async () => container.querySelector<HTMLElement>(".max-connect-technical > summary")!.click());
    await fillToken("replacement-token-12345");
    let finishConfig!: (result: MaxProjectConfig) => void;
    api.config.mockImplementationOnce(() => new Promise(resolve => { finishConfig = resolve; }));
    await click("Заменить токен");

    expect(api.config).toHaveBeenCalledTimes(2);
    expect(container.querySelector(".max-connect-advanced > summary")?.textContent).toContain("Новый бот");
    expect(container.querySelector("h2")?.textContent).toBe("Добавьте адрес в MAX");
    expect(container.querySelector(".max-connect-address-settings")).toBeNull();
    expect(api.saveAttached).not.toHaveBeenCalled();

    await act(async () => finishConfig({ ...config, config_version: 2 }));
    await settle();
    expect(container.querySelector(".max-connect-address-settings")).toBeNull();
  });

  it.each(["http://app.example.com", "https://user:password@app.example.com", "javascript:alert(1)"])("does not offer address confirmation for invalid production URL %s", async (prod_url) => {
    api.integration.mockResolvedValue(connected);
    api.deploy.mockResolvedValue({ ...published, prod_url });
    await render();
    expect(button("Перейти к публикации")).toBeDefined();
    expect(button(confirmAddress)).toBeUndefined();
    expectNoWrites();
  });

  it.each(["publication", "bot"])("rejects confirmation if the %s changes during the network request", async changed => {
    api.integration.mockResolvedValue(connected);
    api.deploy.mockResolvedValue(published);
    let finish!: (response: Response) => void;
    api.fetch.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
    await render();
    await click(confirmAddress);
    expect(button(confirmAddress)?.disabled).toBe(true);
    await act(async () => {
      if (changed === "publication") client.setQueryData(["deploy", "project-1"], { ...published, prod_url: "https://replacement.example.com" });
      else client.setQueryData(["max-integration", "project-1"], { ...connected, bot_id: "replacement-bot" });
    });
    await act(async () => finish(new Response(null, { status: 200 })));
    await settle();
    expect(api.saveAttached).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(button(confirmAddress)?.disabled).toBe(false);
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
    expect(button(confirmAddress)).toBeUndefined();
    expect(container.querySelector("details")).toBeNull();
    expectNoWrites();
    await click("Повторить");
    expect(button(confirmAddress)).toBeDefined();
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
    expect(button(confirmAddress)).toBeUndefined();
  });

  it("does not advance a server connection in error to publication", async () => {
    api.integration.mockResolvedValue({ ...connected, status: "error", last_error: "Токен больше не действует" });
    await render();
    expect(container.querySelector('[role="alert"]')?.textContent ?? "").toContain("Токен больше не действует");
    expect(container.querySelector("summary")?.textContent).toContain("Нужна проверка");
    expect(container.querySelector("summary")?.textContent).not.toContain("Бот подключён");
    expect(button("Перейти к публикации")).toBeUndefined();
    expect(container.querySelector('input[type="password"]')).not.toBeNull();
    expectNoWrites();
  });

  it("refreshes the persisted connection error after a failed bot check", async () => {
    api.integration.mockResolvedValue(connected);
    api.deploy.mockResolvedValue(published);
    await render();
    expect(button(confirmAddress)).toBeDefined();
    api.verify.mockRejectedValueOnce(new Error("Токен больше не действует"));
    api.integration.mockResolvedValue({ ...connected, status: "error", last_error: "Токен больше не действует" });
    await click("Проверить подключение");
    expect(button(confirmAddress)).toBeUndefined();
    expect(container.querySelector('input[type="password"]')).not.toBeNull();
  });

  it("keeps settings open and focused when checking an errored bot restores the compact view", async () => {
    api.integration.mockResolvedValue({ ...connected, status: "error" });
    api.config.mockResolvedValue({ ...config, config: { ...config.config, max_url_attached: true } });
    api.deploy.mockResolvedValue(published);
    let finishVerify!: (result: MaxIntegration) => void;
    api.verify.mockImplementationOnce(() => new Promise(resolve => { finishVerify = resolve; }));
    await render();

    const disclosure = container.querySelector<HTMLDetailsElement>(".max-connect-advanced")!;
    await act(async () => disclosure.querySelector("summary")!.click());
    const verifyButton = button("Проверить подключение")!;
    verifyButton.focus();
    await click("Проверить подключение");
    expect(verifyButton.getAttribute("aria-disabled")).toBe("true");
    expect(document.activeElement).toBe(verifyButton);
    await click("Проверить подключение");
    expect(api.verify).toHaveBeenCalledTimes(1);
    await act(async () => finishVerify(connected));
    await settle();

    expect(container.querySelector(".max-connect-address-settings")).not.toBeNull();
    expect(container.querySelector(".max-connect-advanced")).toBe(disclosure);
    expect(disclosure.open).toBe(true);
    expect(document.activeElement).toBe(verifyButton);
    expect(disclosure.querySelector('[role="status"]')?.textContent).toBe("Данные подключения обновлены");
  });

  it("refreshes the bot and confirmation after a replacement persists but its response fails", async () => {
    api.integration.mockResolvedValue(connected);
    api.config.mockResolvedValue({ ...config, config: { ...config.config, max_url_attached: true } });
    api.deploy.mockResolvedValue(published);
    await render();
    expect(container.querySelector(".max-connect-address-settings")).not.toBeNull();
    await fillToken("replacement-token-12345");
    api.connect.mockRejectedValueOnce(new Error("Связь с сервером потеряна"));
    api.integration.mockResolvedValue({ ...connected, bot_id: "replacement-bot", bot_name: "Новый бот" });
    api.config.mockResolvedValue({ ...config, config_version: 2 });
    await click("Заменить токен");
    expect(container.textContent).toContain("Новый бот");
    expect(container.querySelector(".max-connect-address-settings")).toBeNull();
    expect(container.querySelector("h2")?.textContent).toBe("Добавьте адрес в MAX");
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
