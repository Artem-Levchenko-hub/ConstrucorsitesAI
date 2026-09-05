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
