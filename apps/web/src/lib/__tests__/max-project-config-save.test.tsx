import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { MaxProjectSetupDialog } from "@/components/max/MaxProjectSetupDialog";
import { MaxProjectDataApplyDialog } from "@/components/max/MaxProjectDataApplyDialog";
import type { MaxProjectConfig } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({ get: vi.fn(), save: vi.fn(), success: vi.fn(), error: vi.fn(), send: vi.fn(), push: vi.fn() }));
vi.mock("@/lib/api/max-studio", () => ({
  getMaxProjectConfig: mocks.get, saveMaxProjectConfig: mocks.save,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/lib/api/messages", () => ({ sendPrompt: mocks.send }));
vi.mock("sonner", () => ({ toast: { success: mocks.success, error: mocks.error } }));

const record: MaxProjectConfig = {
  project_id: "qa", config_version: 1, synced_snapshot_id: "same-build",
  updated_at: null, application_mode: "runtime",
  config: {
    app_name: "QA", app_type: "custom", summary: "QA profile", audience: "",
    primary_action: "", features: [], style: "clean", brand_colors: "", content: [],
    operator: { legal_name: "QA owner", inn: "", ogrn: "", address: "" },
    support: { email: null, phone: "", response_time: "One day" },
    legal: { age_rating: "0+", has_sales: false, has_user_content: false,
      marketing_notifications: false, personal_data_consent: true, terms_accepted: false },
    max_url_attached: false,
  },
};

afterEach(() => { vi.resetAllMocks(); sessionStorage.clear(); });

it("allows an explicit new attempt after terminal failure and persists it before dispatch", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mocks.get.mockResolvedValue(record);
  mocks.send.mockResolvedValueOnce({ run_id: "failed", run_status: "failed", replayed: true })
    .mockRejectedValueOnce(new Error("Ответ потерян"))
    .mockResolvedValue({ run_id: "retry", run_status: "pending", replayed: true });
  const close = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  const click = async (label: string) => act(async () => {
    [...document.querySelectorAll<HTMLButtonElement>("button")].find(b => b.textContent === label)!.click();
  });
  const render = () => <QueryClientProvider client={client}><MaxProjectDataApplyDialog config={record} onClose={close} /></QueryClientProvider>;
  try {
    await act(async () => root.render(render()));
    await click("Запустить доработку");
    await act(async () => { await vi.waitFor(() => expect(document.body.textContent).toContain("Повторить доработку")); });
    expect(close).not.toHaveBeenCalled();
    await click("Повторить доработку");
    await act(async () => { await vi.waitFor(() => expect(document.body.textContent).toContain("Ответ потерян")); });
    const key = mocks.send.mock.calls[1][4].idempotencyKey;
    expect(key).not.toBe(mocks.send.mock.calls[0][4].idempotencyKey);
    expect(sessionStorage.getItem("omnia:max-config-apply-qa-1")).toBe(key);
    await act(async () => root.render(null));
    await act(async () => root.render(render()));
    await click("Запустить доработку");
    await act(async () => { await vi.waitFor(() => expect(close).toHaveBeenCalledTimes(1)); });
    expect(mocks.send.mock.calls[2][4].idempotencyKey).toBe(key);
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); }
});

it.each(["stale", "lost-response"])("keeps failed application review open: %s", async (failure) => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mocks.get.mockResolvedValue(failure === "stale" ? { ...record, config_version: 2 } : record);
  mocks.send.mockRejectedValueOnce(new Error("Ответ потерян"))
    .mockResolvedValue({ run_id: "original", message_id: "a", replayed: true });
  const close = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  const launch = () => [...document.querySelectorAll<HTMLButtonElement>("button")]
    .find(b => b.textContent?.includes("Запустить доработку"))!;
  try {
    await act(async () => root.render(<QueryClientProvider client={client}>
      <MaxProjectDataApplyDialog config={record} onClose={close} />
    </QueryClientProvider>));
    await act(async () => { launch().click(); launch().click(); });
    await act(async () => { await vi.waitFor(() => expect(document.querySelector('[role="alert"]')).not.toBeNull()); });
    expect(close).not.toHaveBeenCalled();
    expect(mocks.push).not.toHaveBeenCalled();
    if (failure === "stale") {
      expect(mocks.send).not.toHaveBeenCalled();
      expect(document.querySelector('[role="alert"]')?.textContent).toContain("Данные приложения изменились");
    } else {
      expect(mocks.send).toHaveBeenCalledTimes(1);
      await act(async () => launch().click());
      await act(async () => { await vi.waitFor(() => expect(close).toHaveBeenCalledTimes(1)); });
      expect(mocks.send).toHaveBeenCalledTimes(2);
      expect(mocks.send.mock.calls[1][4].idempotencyKey).toBe(mocks.send.mock.calls[0][4].idempotencyKey);
    }
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); }
});

it("groups product and appearance fields and gives content controls visible associated labels", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mocks.get.mockResolvedValue(record);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><MaxProjectSetupDialog projectId="qa" /></QueryClientProvider>));
    await act(async () => container.querySelector<HTMLButtonElement>("button")!.click());
    await act(async () => { await vi.waitFor(() => expect(document.querySelector("#max-config-name")).not.toBeNull()); });
    const appearance = document.querySelector("#max-config-colors")?.closest("fieldset");
    expect(appearance?.querySelector("legend")?.textContent).toBe("Оформление");
    expect(appearance?.contains(document.querySelector("#max-config-style"))).toBe(true);
    expect(appearance?.contains(document.querySelector("#max-config-name"))).toBe(false);
    await act(async () => document.querySelectorAll<HTMLButtonElement>('[role="tab"]')[1].click());
    await act(async () => [...document.querySelectorAll<HTMLButtonElement>('[role="tabpanel"] button')].find(b => b.textContent?.includes("Добавить"))!.click());
    const inputs = [...document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('[role="tabpanel"] input, [role="tabpanel"] textarea')];
    expect(inputs).toHaveLength(4);
    for (const input of inputs) {
      expect(input.labels?.length).toBeGreaterThan(0);
      expect(input.labels?.[0].textContent?.trim()).toBeTruthy();
    }
    const visibility = document.querySelector<HTMLButtonElement>('[role="switch"]')!;
    expect(visibility.getAttribute("aria-checked")).toBe("true");
    await act(async () => visibility.click());
    expect(visibility.getAttribute("aria-checked")).toBe("false");
    expect(mocks.save).not.toHaveBeenCalled();
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); }
});

it("keeps owner/support and policy choices distinguishable without changing consent defaults", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mocks.get.mockResolvedValue(record);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><MaxProjectSetupDialog projectId="qa" /></QueryClientProvider>));
    await act(async () => container.querySelector<HTMLButtonElement>("button")!.click());
    await act(async () => { await vi.waitFor(() => expect(document.querySelector("#max-config-name")).not.toBeNull()); });
    const tabs = [...document.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
    await act(async () => tabs[2].click());
    expect(document.querySelector("#max-legal-name")?.closest("fieldset")?.querySelector("legend")?.textContent).toBe("Реквизиты владельца");
    expect(document.querySelector("#max-support-email")?.closest("fieldset")?.querySelector("legend")?.textContent).toBe("Связь с поддержкой");
    await act(async () => tabs[3].click());
    const checkboxes = [...document.querySelectorAll<HTMLInputElement>('[role="tabpanel"] input[type="checkbox"]')];
    expect(checkboxes.map(input => input.checked)).toEqual([false, true]);
    expect(document.querySelectorAll('[role="tabpanel"] button[aria-pressed="false"]')).toHaveLength(3);
    expect(mocks.save).not.toHaveBeenCalled();
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); }
});

it("explains blocked saving on any tab instead of leaving an unexplained disabled action", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mocks.get.mockResolvedValue({ ...record, config: { ...record.config, app_name: "", summary: "" } });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><MaxProjectSetupDialog projectId="qa" /></QueryClientProvider>));
    await act(async () => container.querySelector<HTMLButtonElement>("button")!.click());
    await act(async () => { await vi.waitFor(() => expect(document.querySelector("#max-config-name")).not.toBeNull()); });
    await act(async () => document.querySelectorAll<HTMLButtonElement>('[role="tab"]')[3].click());
    const footer = document.querySelector('[data-testid="max-settings-footer"]')!;
    expect(footer.querySelector('[role="status"]')).not.toBeNull();
    expect(footer.querySelector('[role="status"]')?.textContent).toMatch(/название.*описание/);
    expect(footer.querySelector<HTMLButtonElement>('button')?.disabled).toBe(true);
    expect(mocks.save).not.toHaveBeenCalled();
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); }
});

it("supports keyboard tab selection, retains edited fields and closes accessibly without saving", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mocks.get.mockResolvedValue(record);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><MaxProjectSetupDialog projectId="qa" /></QueryClientProvider>));
    await act(async () => container.querySelector<HTMLButtonElement>("button")!.click());
    await act(async () => { await vi.waitFor(() => expect(document.querySelector("#max-config-name")).not.toBeNull()); });
    const tabs = [...document.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
    await act(async () => { tabs[0].focus(); tabs[0].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })); });
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tabs[1]);
    expect(document.querySelector('[role="tabpanel"]')?.getAttribute("aria-labelledby")).toBe(tabs[1].id);
    await act(async () => tabs[2].click());
    await act(async () => {
      const input = document.querySelector<HTMLInputElement>("#max-legal-name")!;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "Несохранённый владелец");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => tabs[0].click());
    await act(async () => tabs[2].click());
    expect(document.querySelector<HTMLInputElement>("#max-legal-name")!.value).toBe("Несохранённый владелец");
    const close = [...document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')].find(button => button.textContent === "Закрыть");
    expect(close).toBeDefined();
    await act(async () => close!.click());
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(mocks.save).not.toHaveBeenCalled();
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); }
});

it.each([false, true])("saves/retries the owner tab and refreshes preview without a build (pending=%s)", async (pending) => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  mocks.get.mockResolvedValue(pending ? { ...record, synced_snapshot_id: null } : record);
  const updated = pending ? record : { ...record, config_version: 2,
    config: { ...record.config, operator: { ...record.config.operator, legal_name: "Updated QA owner" } } };
  mocks.save.mockImplementation(async () => {
    mocks.get.mockResolvedValue(updated);
    return updated;
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const wait = async (read: () => void) => { await act(async () => { await vi.waitFor(read); }); };
  try {
    await act(async () => {
      root.render(<QueryClientProvider client={client}><MaxProjectSetupDialog projectId="qa" /></QueryClientProvider>);
    });
    await act(async () => { container.querySelector<HTMLButtonElement>("button")!.click(); });
    await wait(() => expect(document.querySelector("#max-config-name")).not.toBeNull());
    await act(async () => {
      [...document.querySelectorAll<HTMLButtonElement>("[role=tab]")].find(b => b.textContent === "Владелец")!.click();
    });
    expect((document.querySelector("#max-legal-name") as HTMLInputElement).value).toBe("QA owner");
    if (!pending) {
      await act(async () => {
        const input = document.querySelector("#max-legal-name") as HTMLInputElement;
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "Updated QA owner");
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    }
    await act(async () => {
      [...document.querySelectorAll<HTMLButtonElement>("button")].find(b => b.textContent?.includes("Сохранить и проверить"))!.click();
    });
    await wait(() => expect(mocks.success).toHaveBeenCalled());
    expect(mocks.save).toHaveBeenCalledWith("qa", updated.config);
    expect(mocks.success.mock.calls[0][1].description).toContain("Конфигурация, поддержка и документы обновлены");
    expect(mocks.send).not.toHaveBeenCalled();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["max-preview-session", "qa"] });
    await act(async () => { container.querySelector<HTMLButtonElement>("button")!.click(); });
    await wait(() => expect(document.querySelector("#max-config-name")).not.toBeNull());
    expect((document.querySelector("#max-config-name") as HTMLInputElement).value).toBe("QA");
  } finally {
    await act(async () => root.unmount());
    client.clear();
    container.remove();
  }
});

it.each(["я".repeat(19_992) + "КОНЕЦ ТЗ", "я".repeat(20_001)])("keeps the entire pasted description and validates before saving (%#.0)", async (brief) => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  mocks.get.mockResolvedValue(record);
  mocks.save.mockResolvedValue(record);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => { root.render(<QueryClientProvider client={client}><MaxProjectSetupDialog projectId="qa" /></QueryClientProvider>); });
    await act(async () => { container.querySelector<HTMLButtonElement>("button")!.click(); });
    await act(async () => { await vi.waitFor(() => expect(document.querySelector("#max-config-summary")).not.toBeNull()); });
    const input = document.querySelector<HTMLTextAreaElement>("#max-config-summary")!;
    expect(input.maxLength).toBe(-1);
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, brief);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(input.value).toBe(brief);
    const save = [...document.querySelectorAll<HTMLButtonElement>("button")].find(b => b.textContent?.includes("Сохранить и проверить"))!;
    expect(save.disabled).toBe(brief.length > 20_000);
    await act(async () => { save.click(); });
    if (brief.length > 20_000) expect(mocks.save).not.toHaveBeenCalled();
    else {
      await act(async () => { await vi.waitFor(() => expect(mocks.save).toHaveBeenCalled()); });
      expect(mocks.save.mock.calls[0][1].summary).toBe(brief);
    }
  } finally {
    await act(async () => { root.unmount(); });
    container.remove(); client.clear();
  }
});


it("saves content before showing an explicit, version-bound AI application action", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mocks.get.mockResolvedValue(record);
  let finishSave!: (value: MaxProjectConfig) => void;
  mocks.save.mockImplementation(() => new Promise(resolve => { finishSave = resolve; }));
  mocks.send.mockResolvedValue({ run_id: "run", message_id: "a", mode: "edit", run_status: "pending" });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  const button = (label: string) => [...document.querySelectorAll<HTMLButtonElement>("button")].find(b => b.textContent?.trim() === label);
  const click = async (label: string) => { expect(button(label)).toBeDefined(); await act(async () => button(label)!.click()); };
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><MaxProjectSetupDialog projectId="qa" /></QueryClientProvider>));
    await act(async () => container.querySelector<HTMLButtonElement>("button")!.click());
    await act(async () => { await vi.waitFor(() => expect(document.querySelector("#max-config-name")).not.toBeNull()); });
    await click("Контент");
    await click("Добавить элемент");
    await click("Сохранить и применить");
    expect(mocks.send).not.toHaveBeenCalled();
    expect(mocks.push).not.toHaveBeenCalled();
    expect(button("Запустить доработку")).toBeUndefined();
    const updated = { ...record, config_version: 2, config: mocks.save.mock.calls[0][1] };
    expect(updated.config.content).toHaveLength(1);
    mocks.get.mockResolvedValue(updated);
    await act(async () => finishSave(updated));
    await act(async () => { await vi.waitFor(() => expect(button("Запустить доработку")).toBeDefined()); });
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("баланс");
    expect(mocks.send).not.toHaveBeenCalled();
    await click("Запустить доработку");
    await act(async () => { await vi.waitFor(() => expect(mocks.send).toHaveBeenCalledTimes(1)); });
    expect(mocks.send.mock.calls[0][0]).toBe("qa");
    expect(mocks.send.mock.calls[0][4]).toMatchObject({ skipClarify: true, maxConfigVersion: 2, idempotencyKey: "max-config-apply-qa-2" });
    expect(mocks.push).toHaveBeenCalledWith("/max/qa");
  } finally {
    await act(async () => root.unmount()); container.remove(); client.clear();
  }
});
