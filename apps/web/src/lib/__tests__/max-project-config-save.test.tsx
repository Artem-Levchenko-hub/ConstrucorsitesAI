import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { MaxProjectSetupDialog } from "@/components/max/MaxProjectSetupDialog";
import type { MaxProjectConfig } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({ get: vi.fn(), save: vi.fn(), success: vi.fn(), error: vi.fn() }));
vi.mock("@/lib/api/max-studio", () => ({
  getMaxProjectConfig: mocks.get, saveMaxProjectConfig: mocks.save,
}));
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

afterEach(() => vi.clearAllMocks());

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
    expect(mocks.success.mock.calls[0][1].description).toContain("Сборка и данные приложения не изменены");
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
