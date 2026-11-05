import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it, vi } from "vitest";

import MaxSettingsPage from "@/app/(app)/max/[id]/settings/page";

const load = vi.hoisted(() => vi.fn());
vi.mock("@/lib/max-project-server", () => ({ loadMaxProject: load }));
vi.mock("next/navigation", () => ({ redirect: (to: string) => { throw new Error(`redirect:${to}`); } }));
vi.mock("@/components/max/MaxProjectSetupDialog", () => ({
  MaxProjectSetupDialog: ({ projectId, display }: { projectId: string; display: string }) => <div data-project={projectId} data-display={display} />,
}));

beforeEach(() => {
  load.mockReset();
  load.mockResolvedValue({ id: "owned", name: "Моё приложение" });
});

const page = (tab?: string) => MaxSettingsPage({ params: Promise.resolve({ id: "owned" }), searchParams: Promise.resolve({ tab }) });

it("opens owner app data as a project page with the chat action and active navigation", async () => {
  const client = new QueryClient();
  const markup = renderToStaticMarkup(<QueryClientProvider client={client}>{await page("app")}</QueryClientProvider>);
  const document = new DOMParser().parseFromString(markup, "text/html");
  expect(load).toHaveBeenCalledWith("owned", "/max/owned/settings?tab=app");
  expect(document.querySelector("main h1")?.textContent).toBe("Данные приложения");
  expect(document.querySelector('main [data-project="owned"][data-display="page"]')).not.toBeNull();
  expect(document.querySelector('nav a[aria-current="page"]')?.getAttribute("href")).toBe("/max/owned/settings?tab=app");
  expect(document.querySelector('header a[href="/max/owned"]')?.textContent).toContain("К чату");
  expect(document.querySelector('nav a[href="/max/owned/dashboard"]')).not.toBeNull();
});

it.each([["bot", "max"], ["vps", "publish"], [undefined, "max"], ["old", "max"]] as const)("preserves legacy settings link %s", async (tab, panel) => {
  await expect(page(tab)).rejects.toThrow(`redirect:/max/owned?panel=${panel}`);
  expect(load).toHaveBeenCalledOnce();
});

it("does not render the app data page when owner admission fails", async () => {
  load.mockRejectedValueOnce(new Error("owner admission denied"));
  await expect(page("app")).rejects.toThrow("owner admission denied");
});
