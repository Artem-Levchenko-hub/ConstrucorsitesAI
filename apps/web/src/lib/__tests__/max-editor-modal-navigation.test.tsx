import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { MaxEditorLayout } from "@/components/max/MaxEditorLayout";
import type { Project } from "@/lib/api/types";

const state = vi.hoisted(() => ({ params: new URLSearchParams() }));
vi.mock("next/navigation", () => ({ useSearchParams: () => state.params }));
vi.mock("@/components/marketing/BrandMark", () => ({ BrandMark: () => null }));
vi.mock("@/components/max/MaxConnectionWizard", () => ({ MaxConnectionWizard: ({ onBusyChange }: { onBusyChange: (busy: boolean) => void }) => <><h2>Создайте бота</h2><button onClick={() => onBusyChange(true)}>Начать проверку</button><button onClick={() => onBusyChange(false)}>Завершить проверку</button></> }));
vi.mock("@/components/max/FigmaIntegrationHub", () => ({ FigmaIntegrationHub: ({ embedded }: { embedded?: boolean }) => <div>Каталог {embedded ? "в окне" : "страница"}</div> }));
vi.mock("@/components/workspace/ExternalDeployWizard", () => ({ ExternalDeployWizard: () => <div>Настройка сервера</div> }));
vi.mock("@/components/max/MaxProjectSetupDialog", () => ({ MaxProjectSetupDialog: () => <button aria-label="Данные приложения">Данные</button> }));
afterEach(() => vi.restoreAllMocks());

it("opens direct entry, switches one modal without remounting the editor, and restores it on Forward", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const project = { id: "modal-test", name: "Пример", template: "max_miniapp" } as Project;
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const originalPush = window.history.pushState.bind(window.history);
  const originalReplace = window.history.replaceState.bind(window.history);
  originalReplace({ __NA: true }, "", `/max/${project.id}?panel=publish&starter=1`);
  const render = () => {
    state.params = new URLSearchParams(window.location.search);
    root.render(<QueryClientProvider client={client}><MaxEditorLayout project={project} navigation={<a href={`/max/${project.id}?panel=services`}>Сервисы</a>} tools={null} preview={null}
      launch={<div><h2>Готовность</h2><a href={`/max/${project.id}?panel=max`}>Подключить MAX</a></div>}
      launchOpen={false} onLaunchChange={() => {}} launchStatus="Запуск"><input aria-label="Черновик" defaultValue="Моя идея" /></MaxEditorLayout></QueryClientProvider>);
  };
  // Match Next's native history patch: internal __NA/_N entries bypass search-param synchronization.
  vi.spyOn(window.history, "pushState").mockImplementation((data, unused, url) => { originalPush({ ...data, __NA: true }, unused, url); if (!data?.__NA && !data?._N) render(); });
  vi.spyOn(window.history, "replaceState").mockImplementation((data, unused, url) => { originalReplace({ ...data, __NA: true }, unused, url); if (!data?.__NA && !data?._N) render(); });
  window.addEventListener("popstate", render);
  const close = () => document.querySelector<HTMLButtonElement>('[aria-label="Закрыть окно"]')!.click();
  try {
    await act(async () => render());
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Готовность");
    const chat = container.querySelector("input")!;
    await act(async () => document.querySelector<HTMLAnchorElement>('[role="dialog"] a')!.click());
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Создайте бота");
    expect(window.location.search).toBe("?starter=1&panel=max");
    expect(container.querySelector("input")).toBe(chat);
    const findButton = (text: string) => [...document.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === text)!;
    await act(async () => findButton("Начать проверку").click());
    expect(document.querySelector<HTMLButtonElement>('[aria-label="Закрыть окно"]')!.disabled).toBe(true);
    expect(findButton("К публикации").disabled).toBe(true);
    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    await act(async () => findButton("Завершить проверку").click());
    await act(async () => close());
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(window.location.search).toBe("?starter=1");
    await act(async () => container.querySelector<HTMLButtonElement>('[data-testid="max-navigation-open"]')!.click());
    await act(async () => document.querySelector<HTMLAnchorElement>('[role="dialog"] a')!.click());
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Каталог в окне");
    const back = vi.spyOn(window.history, "back");
    await act(async () => close());
    expect(back).toHaveBeenCalledOnce();
    await act(async () => { await vi.waitFor(() => expect(window.location.search).toBe("?starter=1")); });
    await act(async () => { window.history.forward(); await new Promise(resolve => setTimeout(resolve, 50)); });
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Каталог в окне");
    expect(container.querySelector("input")).toBe(chat);
    expect(chat.value).toBe("Моя идея");
  } finally { window.removeEventListener("popstate", render); await act(async () => root.unmount()); client.clear(); container.remove(); }
});
