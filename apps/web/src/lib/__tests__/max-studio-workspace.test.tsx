import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxStudio } from "@/components/max/MaxStudio";
import type { Project } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), save: vi.fn(), push: vi.fn(), readiness: vi.fn() }));
vi.mock("@/lib/api/projects", () => ({ listProjects: mocks.list, createProject: mocks.create }));
vi.mock("@/lib/api/max-studio", () => ({ saveMaxProjectConfig: mocks.save, getMaxReadiness: mocks.readiness }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/app/(auth)/actions", () => ({ logoutAction: async () => {} }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

const project: Project = { id: "coffee", owner_id: "owner", name: "Кофе рядом", slug: "coffee", template: "max_miniapp", current_snapshot_id: null, created_at: "2026-09-01T12:00:00Z", updated_at: "2026-09-01T12:00:00Z" };
let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
const button = (name: string) => [...document.querySelectorAll("button")].find(b => b.textContent?.trim() === name)!;
async function click(name: string) { expect(button(name), `button ${name}`).toBeTruthy(); await act(async () => { button(name).click(); }); }
async function change(selector: string, value: string) {
  const input = document.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector)!;
  expect(input).toBeTruthy();
  await act(async () => {
    Object.getOwnPropertyDescriptor(input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function settle(check: () => void) { await act(async () => { await vi.waitFor(check); }); }
async function render() { await act(async () => { root.render(<QueryClientProvider client={client}><MaxStudio email="owner@example.ru" /></QueryClientProvider>); }); }
async function openWizard() { await render(); await settle(() => expect(button("Создать приложение")).toBeTruthy()); await click("Создать приложение"); }

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.list.mockResolvedValue([]);
  mocks.readiness.mockResolvedValue({ items: [] });
  mocks.create.mockResolvedValue(project);
  mocks.save.mockResolvedValue({});
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => { root.unmount(); }); container.remove(); client.clear(); sessionStorage.clear(); });

it("keeps search editable with a clear action when no names match", async () => {
  mocks.list.mockResolvedValue([project]);
  await render(); await settle(() => expect(container.textContent).toContain("Кофе рядом"));
  await change('input[placeholder="Найти проект"]', "несуществующий");
  expect(container.querySelector('input[placeholder="Найти проект"]')).toBeTruthy();
  expect(container.textContent).toContain("Ничего не найдено");
  expect(container.textContent).not.toContain("Первого проекта ещё нет");
  await click("Сбросить поиск");
  expect(container.textContent).toContain("Кофе рядом");
});

it("distinguishes loading, query failure, retry and a real empty workspace", async () => {
  let reject!: (error: Error) => void;
  mocks.list.mockReturnValueOnce(new Promise((_, fail) => { reject = fail; }));
  await render();
  expect(container.querySelector('[role="status"]')?.textContent).toContain("Загружаем приложения");
  expect(container.textContent).not.toContain("Первого проекта ещё нет");
  await act(async () => { reject(new Error("network offline")); });
  await settle(() => expect(container.querySelector('[role="alert"]')?.textContent).toContain("Не удалось загрузить приложения"));
  expect(container.textContent).not.toContain("Первого проекта ещё нет");
  await click("Повторить");
  await settle(() => expect(container.textContent).toContain("Первого проекта ещё нет"));
});

it("validates name and idea, retains answers through Back, and creates only after review", async () => {
  await openWizard();
  expect(button("Далее")?.disabled).toBe(true);
  await change("#max-project-name", "Кофе рядом");
  await change("#max-project-idea", "мало");
  expect(button("Далее").disabled).toBe(true);
  await change("#max-project-idea", "Получать баллы и выбирать награды");
  await click("Далее");
  expect(document.activeElement?.textContent).toBe("Для кого приложение?");
  await change("#max-audience", "Гости кофейни");
  await change("#max-action", "Выбрать награду");
  await click("Назад");
  expect(document.querySelector<HTMLInputElement>("#max-project-name")?.value).toBe("Кофе рядом");
  await click("Далее");
  expect(document.querySelector<HTMLInputElement>("#max-audience")?.value).toBe("Гости кофейни");
  await click("Далее");
  await change("#max-brand", "молочный");
  await click("Далее");
  expect(document.activeElement?.textContent).toBe("Проверьте описание");
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Выбрать награду");
  expect(mocks.create).not.toHaveBeenCalled();
  await click("Создать проект");
  await settle(() => expect(mocks.push).toHaveBeenCalledWith("/max/coffee?starter=1"));
  expect(mocks.create).toHaveBeenCalledTimes(1);
  expect(mocks.save).toHaveBeenCalledWith("coffee", expect.objectContaining({ app_type: "loyalty", audience: "Гости кофейни", primary_action: "Выбрать награду", features: ["Профиль пользователя", "История действий"], style: "brand", brand_colors: "молочный" }));
  expect(sessionStorage.getItem("omnia:max:starter:coffee")).toContain("Получать баллы и выбирать награды");
});

it("keeps optional answers optional and locks duplicate submit and dismissal while creating", async () => {
  mocks.create.mockReturnValue(new Promise(() => {}));
  await openWizard();
  await change("#max-project-name", "Кофе рядом");
  await change("#max-project-idea", "Получать баллы и выбирать награды");
  await click("Далее"); await click("Далее"); await click("Далее");
  await act(async () => { button("Создать проект").click(); button("Создать проект").click(); });
  await settle(() => expect(mocks.create).toHaveBeenCalledTimes(1));
  expect(button("Создать проект").disabled).toBe(true);
  expect(button("Назад").disabled).toBe(true);
  await click("Закрыть");
  await act(async () => { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
  expect(document.querySelector('[role="dialog"]')).toBeTruthy();
});

it("retains the review after a failed create so the same answers can be retried", async () => {
  mocks.create.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(project);
  await openWizard();
  await change("#max-project-name", "Кофе рядом");
  await change("#max-project-idea", "Получать баллы и выбирать награды");
  await click("Далее"); await click("Далее"); await click("Далее");
  await click("Создать проект");
  await settle(() => expect(button("Создать проект").disabled).toBe(false));
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Получать баллы и выбирать награды");
  await click("Создать проект");
  await settle(() => expect(mocks.push).toHaveBeenCalledWith("/max/coffee?starter=1"));
  expect(mocks.create).toHaveBeenCalledTimes(2);
});

it("uses readiness for the next project action and falls back to management when status fails", async () => {
  mocks.list.mockResolvedValue([project]);
  mocks.readiness.mockRejectedValueOnce(new Error("status unavailable"));
  await render();
  await settle(() => expect(container.textContent).toContain("Не удалось проверить готовность"));
  expect(container.querySelector('.max-project-next')?.getAttribute("href")).toBe("/max/coffee/dashboard");
  mocks.readiness.mockResolvedValue({ items: [{ id: "build", done: true }] });
  await act(async () => { await client.invalidateQueries({ queryKey: ["max-readiness", "coffee"] }); });
  await settle(() => expect(container.querySelector('.max-project-next')?.textContent).toContain("Заполнить данные"));
  expect(container.querySelector('.max-project-next')?.getAttribute("href")).toBe("/max/coffee/settings?tab=app");
});
