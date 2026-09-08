import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import MaxSettingsPage from "@/app/(app)/max/[id]/settings/page";
import MaxPublishPage from "@/app/(app)/max/[id]/publish/page";
import MaxIntegrationsPage from "@/app/(app)/max/[id]/integrations/page";
import { MaxEditorLayout } from "@/components/max/MaxEditorLayout";
import type { Project } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({ load: vi.fn(), get: vi.fn(), params: new URLSearchParams(), navigate: vi.fn() }));
vi.mock("@/lib/max-project-server", () => ({ loadMaxProject: mocks.load }));
vi.mock("@/lib/api/max-studio", () => ({ getMaxProjectConfig: mocks.get, saveMaxProjectConfig: vi.fn() }));
vi.mock("next/navigation", async original => ({ ...await original<object>(),
  useSearchParams: () => mocks.params,
  useRouter: () => ({ push: mocks.navigate, replace: mocks.navigate }),
}));
vi.mock("@/components/marketing/BrandMark", () => ({ BrandMark: () => null }));
const project = { id: "data-test", name: "Калькулятор", template: "max_miniapp" } as Project;
afterEach(() => { vi.restoreAllMocks(); vi.clearAllMocks(); mocks.params = new URLSearchParams(); });

it("redirects the old data page to the editor dialog, after checking project access", async () => {
  mocks.load.mockResolvedValue(project);
  await expect(MaxSettingsPage({ params: Promise.resolve({ id: project.id }), searchParams: Promise.resolve({ tab: "app" }) }))
    .rejects.toMatchObject({ digest: expect.stringContaining(`/max/${project.id}?data=details`) });
  expect(mocks.load).toHaveBeenCalledWith(project.id, `/max/${project.id}/settings?tab=app`);
});

it("does not redirect through a failed ownership check", async () => {
  const denied = new Error("Not your project"); mocks.load.mockRejectedValue(denied);
  await expect(MaxSettingsPage({ params: Promise.resolve({ id: project.id }), searchParams: Promise.resolve({ tab: "app" }) })).rejects.toBe(denied);
});

it.each([["bot", "max"], ["vps", "hosting"]])("redirects old %s settings into its editor modal", async (tab, panel) => {
  mocks.load.mockResolvedValue(project);
  await expect(MaxSettingsPage({ params: Promise.resolve({ id: project.id }), searchParams: Promise.resolve({ tab }) }))
    .rejects.toMatchObject({ digest: expect.stringContaining(`/max/${project.id}?panel=${panel}`) });
});

it.each([[MaxPublishPage, "publish"], [MaxIntegrationsPage, "services"]] as const)("redirects a legacy page only after checking access", async (Page, panel) => {
  mocks.load.mockResolvedValue(project);
  await expect(Page({ params: Promise.resolve({ id: project.id }) })).rejects.toMatchObject({ digest: expect.stringContaining(`/max/${project.id}?panel=${panel}`) });
  const denied = new Error("Access denied"); mocks.load.mockRejectedValue(denied);
  await expect(Page({ params: Promise.resolve({ id: project.id }) })).rejects.toBe(denied);
});

it.each(["details", "owner", "policies"])("opens %s directly over the editor and closes without losing chat input", async section => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mocks.params = new URLSearchParams(`data=${section}&starter=1`);
  mocks.get.mockResolvedValue({ config_version: 1, application_mode: "runtime", synced_snapshot_id: "s1", config: {
    app_name: "Калькулятор", summary: "Считать калории", app_type: "custom", audience: "", primary_action: "", features: [],
    style: "clean", brand_colors: "", content: [], operator: { legal_name: "", inn: "", ogrn: "", address: "" },
    support: { email: null, phone: "", response_time: "" }, legal: { age_rating: "0+", has_sales: false, has_user_content: false, marketing_notifications: false, personal_data_consent: true, terms_accepted: false },
  } });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  const render = () => root.render(<QueryClientProvider client={client}><MaxEditorLayout project={project} navigation={null} tools={null} preview={null} launch={null} launchStatus="" launchOpen={false} onLaunchChange={() => {}}><input aria-label="Черновик чата" defaultValue="Не потерять идею" /></MaxEditorLayout></QueryClientProvider>);
  const originalPush = window.history.pushState.bind(window.history);
  const originalReplace = window.history.replaceState.bind(window.history);
  originalReplace(null, "", `/max/${project.id}?data=${section}&starter=1`);
  const sync = () => { mocks.params = new URLSearchParams(window.location.search); render(); };
  window.addEventListener("popstate", sync);
  vi.spyOn(window.history, "pushState").mockImplementation((data, unused, url) => { originalPush(data, unused, url); sync(); });
  vi.spyOn(window.history, "replaceState").mockImplementation((data, unused, url) => { originalReplace(data, unused, url); sync(); });
  try {
    await act(async () => render());
    await act(async () => { await vi.waitFor(() => expect(document.querySelector('[role="tabpanel"]')).not.toBeNull()); });
    const expected = { details: "#max-config-name", owner: "#max-legal-name", policies: "#max-age-rating" }[section]!;
    expect(document.querySelector(expected)).not.toBeNull();
    const chat = container.querySelector<HTMLInputElement>('input[aria-label="Черновик чата"]')!;
    const close = [...document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')].find(button => button.textContent === "Закрыть")!;
    await act(async () => close.click());
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(mocks.params.get("data")).toBeNull();
    expect(mocks.params.get("starter")).toBe("1");
    expect(container.querySelector('input[aria-label="Черновик чата"]')).toBe(chat);
    expect(chat.value).toBe("Не потерять идею");
    const trigger = container.querySelector<HTMLButtonElement>('button[aria-label="Данные приложения"]');
    expect(trigger).not.toBeNull();
    await act(async () => trigger!.click());
    expect(document.querySelector("#max-config-name")).not.toBeNull();
    // Header entries close by popping, not by replacing with a duplicate editor.
    const back = vi.spyOn(window.history, "back");
    await act(async () => [...document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')].find(button => button.textContent === "Закрыть")!.click());
    expect(back).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.waitFor(() => expect(window.location.search).toBe("?starter=1")); });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    // Forward restores the same modal entry.
    await act(async () => { window.history.forward(); await new Promise(resolve => setTimeout(resolve, 50)); });
    expect(document.querySelector("#max-config-name")).not.toBeNull();
    await act(async () => {
      const name = document.querySelector<HTMLInputElement>("#max-config-name")!;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(name, "Не сохранять");
      name.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { window.history.back(); await new Promise(resolve => setTimeout(resolve, 50)); });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    await act(async () => { window.history.forward(); await new Promise(resolve => setTimeout(resolve, 50)); });
    expect(document.querySelector<HTMLInputElement>("#max-config-name")?.value).toBe("Калькулятор");
    await act(async () => [...document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')].find(button => button.textContent === "Применить к приложению")!.click());
    await act(async () => { await vi.waitFor(() => expect(window.location.search).toBe("?starter=1")); });
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    await act(async () => { window.history.forward(); await new Promise(resolve => setTimeout(resolve, 50)); });
    expect(document.querySelector("#max-config-name")).not.toBeNull();
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
  } finally { window.removeEventListener("popstate", sync); await act(async () => root.unmount()); client.clear(); container.remove(); }
});
