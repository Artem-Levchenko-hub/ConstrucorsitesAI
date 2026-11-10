import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxStudio } from "@/components/max/MaxStudio";
import { LearningProvider } from "@/components/learning/LearningProvider";
import { ProductLearning } from "@/components/learning/ProductLearning";
import { saveLandingPrompt } from "@/lib/landing-prompt";
import type { Project } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), save: vi.fn(), push: vi.fn(), readiness: vi.fn(), integrations: vi.fn(), connect: vi.fn(), quota: {
  limited: false, remaining: null as number | null, initials: null as number | null,
  edits: null as number | null, exhausted: false, initialExhausted: false, refresh: vi.fn(),
} }));
vi.mock("@/hooks/useOwnerChatQuota", () => ({ useOwnerChatQuota: () => mocks.quota }));
vi.mock("@/lib/api/projects", () => ({ listProjects: mocks.list, createProject: mocks.create }));
vi.mock("@/lib/api/max-studio", () => ({ saveMaxProjectConfig: mocks.save, getMaxReadiness: mocks.readiness }));
vi.mock("@/lib/api/app-integrations", () => ({ getIntegrationCatalog: mocks.integrations, connectAppIntegration: mocks.connect }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }), usePathname: () => "/max", useSearchParams: () => new URLSearchParams() }));
vi.mock("@/app/(auth)/actions", () => ({ logoutAction: async () => {} }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

const project: Project = { id: "coffee", owner_id: "owner", name: "Кофе рядом", slug: "coffee", template: "max_miniapp", current_snapshot_id: null, created_at: "2026-09-01T12:00:00Z", updated_at: "2026-09-01T12:00:00Z" };
let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
const button = (name: string) => [...document.querySelectorAll("button")].find(b => b.textContent?.trim() === name || b.getAttribute("aria-label") === name)!;
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
  Object.assign(mocks.quota, { limited: false, remaining: null, initials: null, edits: null, exhausted: false, initialExhausted: false });
  mocks.list.mockResolvedValue([]);
  mocks.readiness.mockResolvedValue({ items: [] });
  mocks.create.mockResolvedValue(project);
  mocks.save.mockResolvedValue({});
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => { root.unmount(); }); container.remove(); client.clear(); sessionStorage.clear(); localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function renderHome(userKey: string, strict = false) {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ x: 30, y: 100, left: 30, top: 100, width: 336, height: 300, bottom: 400, right: 366, toJSON() {} });
  HTMLElement.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  const home = <QueryClientProvider client={client}><LearningProvider key={userKey} userKey={userKey}><MaxStudio email="owner@example.test" /><ProductLearning userKey={userKey} /></LearningProvider></QueryClientProvider>;
  await act(async () => root.render(strict ? <StrictMode>{home}</StrictMode> : home));
}

function expectNoTraining() {
  expect(document.querySelector("[data-learning-welcome]")).toBeNull();
  expect(document.querySelector(".learning-card")).toBeNull();
  expect(document.querySelector(".learning-spotlight")).toBeNull();
  expect(container.querySelector(".learning-launch")).toBeNull();
}
function expectNoCreation() {
  expect(mocks.create).not.toHaveBeenCalled();
  expect(mocks.save).not.toHaveBeenCalled();
  expect(mocks.connect).not.toHaveBeenCalled();
  expect(mocks.push).not.toHaveBeenCalled();
}
function controlledFrames() {
  let next = 0;
  const pending = new Map<number, FrameRequestCallback>();
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { const id = ++next; pending.set(id, callback); return id; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => { pending.delete(id); });
  return { pending, async flush() {
    const callbacks = [...pending.values()]; pending.clear();
    await act(async () => { for (const callback of callbacks) callback(0); });
  } };
}

it.each(["fresh", "dismissed", "completed"])("opens creation immediately for %s learning records without a tour", async status => {
  const userKey = `creation-${status}`;
  const key = `yleum:learning:v1:${userKey}`;
  if (status !== "fresh") localStorage.setItem(key, JSON.stringify({ version: 1, tours: { cabinet: status }, progress: { cabinet: "projects-search" } }));
  const previous = localStorage.getItem(key);
  await renderHome(userKey);
  await settle(() => expect(container.textContent).toContain("Первого приложения ещё нет"));
  expectNoTraining();
  await click("Создать приложение");
  expect(document.querySelector("#max-project-name")).not.toBeNull();
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Что создаём?");
  expectNoTraining(); expectNoCreation();
  expect(localStorage.getItem(key)).toBe(previous);
});

it.each(["create", "empty-state", "ready-idea"])("opens the wizard directly from %s before any project or provider request", async entrance => {
  await renderHome(`entrance-${entrance}`);
  await settle(() => expect(container.textContent).toContain("Первого приложения ещё нет"));
  if (entrance === "ready-idea") await act(async () => container.querySelector<HTMLButtonElement>(".max-studio-idea")!.click());
  else await click(entrance === "create" ? "Создать приложение" : "Описать свою идею");
  expect(document.querySelector("#max-project-name")).not.toBeNull();
  const name = document.querySelector<HTMLInputElement>("#max-project-name")!;
  const idea = document.querySelector<HTMLTextAreaElement>("#max-project-idea")!;
  expect(name.value).toBe(entrance === "ready-idea" ? "Кофейня" : "");
  if (entrance === "ready-idea") expect(idea.value).toContain("предзаказ");
  else expect(idea.value).toBe("");
  expectNoTraining(); expectNoCreation();
});

it("consumes the saved landing prompt once under StrictMode and does not create before review", async () => {
  const frames = controlledFrames();
  const prompt = "Приложение моей пекарни с каталогом выпечки";
  saveLandingPrompt(prompt);
  await renderHome("strict-landing-owner", true);
  expect(frames.pending.size).toBe(1);
  expectNoCreation(); expectNoTraining();
  await frames.flush();
  await settle(() => expect(document.querySelector<HTMLTextAreaElement>("#max-project-idea")?.value).toBe(prompt));
  expect(localStorage.getItem("yleum:landing-prompt")).toBeNull();
  expectNoCreation(); expectNoTraining();
  await act(async () => root.render(<></>));
  await renderHome("strict-landing-owner", true);
  await frames.flush();
  expect(document.querySelector("#max-project-idea")).toBeNull();
  await click("Создать приложение");
  expect(document.querySelector<HTMLTextAreaElement>("#max-project-idea")?.value).toBe("");
  expectNoCreation();
});

it("restores an unconsumed landing prompt when unmounted before its scheduled opening", async () => {
  const frames = controlledFrames();
  const prompt = "Каталог с заявками и моим сохранённым описанием";
  saveLandingPrompt(prompt);
  await renderHome("cancelled-landing-owner");
  expect(frames.pending.size).toBe(1);
  await act(async () => root.render(<></>));
  expect(frames.pending.size).toBe(0);
  expect(JSON.parse(localStorage.getItem("yleum:landing-prompt")!).text).toBe(prompt);
  await renderHome("cancelled-landing-owner");
  await frames.flush();
  await settle(() => expect(document.querySelector<HTMLTextAreaElement>("#max-project-idea")?.value).toBe(prompt));
  expect(localStorage.getItem("yleum:landing-prompt")).toBeNull();
  expectNoTraining(); expectNoCreation();
});

it("keeps an established owner able to create after the last project disappears without writing a learning exemption", async () => {
  mocks.list.mockResolvedValue([project]);
  await renderHome("established-owner");
  await settle(() => expect(container.textContent).toContain("Кофе рядом"));
  expectNoTraining();
  expect(localStorage.getItem("yleum:learning:v1:established-owner")).toBeNull();
  mocks.list.mockResolvedValue([]);
  await act(async () => { await client.invalidateQueries({ queryKey: ["projects"] }); });
  await settle(() => expect(container.textContent).toContain("Первого приложения ещё нет"));
  await click("Создать приложение");
  expect(document.querySelector("#max-project-name")).not.toBeNull();
  expectNoTraining(); expectNoCreation();
});

it("ignores old home replay events without changing an open creation draft", async () => {
  localStorage.setItem("yleum:learning:v1:previous-owner", JSON.stringify({ version: 1, tours: { cabinet: "completed" } }));
  await renderHome("previous-owner");
  await settle(() => expect(container.textContent).toContain("Первого приложения ещё нет"));
  await renderHome("current-owner");
  await settle(() => expect(container.textContent).toContain("Первого приложения ещё нет"));
  await click("Создать приложение");
  await change("#max-project-name", "Сохранить мой проект");
  for (const userKey of ["previous-owner", "current-owner"]) {
    await act(async () => window.dispatchEvent(new CustomEvent("yleum:learning:open", { detail: { userKey, scope: "product", surface: "preview", trigger: button("Создать приложение") } })));
    expectNoTraining();
    expect(document.querySelector<HTMLInputElement>("#max-project-name")?.value).toBe("Сохранить мой проект");
  }
  expectNoCreation();
});

it("uses account-scoped project queries without showing cached projects from another owner", async () => {
  mocks.list.mockResolvedValueOnce([project]).mockResolvedValue([]);
  await renderHome("first-owner");
  await settle(() => expect(container.textContent).toContain("Кофе рядом"));
  expect(client.getQueryData(["projects", "first-owner"])).toEqual([project]);
  await renderHome("second-owner");
  await settle(() => expect(container.textContent).toContain("Первого приложения ещё нет"));
  expect(container.textContent).not.toContain("Кофе рядом");
  expect(client.getQueryData(["projects", "second-owner"])).toEqual([]);
  expect(client.getQueryData(["projects"])).toBeUndefined();
  await click("Создать приложение");
  expect(document.querySelector("#max-project-name")).not.toBeNull();
  expectNoTraining(); expectNoCreation();
});

it.each(["write", "all"])("opens creation without training when %s access to local storage is denied", async denied => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Storage unavailable"); });
  if (denied === "all") vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("Storage unavailable"); });
  await renderHome(`storage-denied-${denied}`);
  await settle(() => expect(container.textContent).toContain("Первого приложения ещё нет"));
  await click("Создать приложение");
  expect(document.querySelector("#max-project-name")).not.toBeNull();
  expectNoTraining(); expectNoCreation();
});

it("preserves quota admission while allowing the owner to review a ready idea", async () => {
  Object.assign(mocks.quota, { limited: true, remaining: 0, initials: 0, edits: 0, exhausted: true, initialExhausted: true });
  await renderHome("exhausted-owner");
  await settle(() => expect(container.textContent).toContain("Первого приложения ещё нет"));
  expect(button("Создать приложение").disabled).toBe(true);
  expect(button("Описать свою идею").disabled).toBe(true);
  await click("Создать приложение"); await click("Описать свою идею");
  expect(document.querySelector("#max-project-name")).toBeNull();
  await act(async () => container.querySelector<HTMLButtonElement>(".max-studio-idea")!.click());
  expect(document.querySelector<HTMLInputElement>("#max-project-name")?.value).toBe("Кофейня");
  await click("Далее"); await click("Далее"); await click("Далее");
  expect(button("Создать проект").disabled).toBe(true);
  await click("Создать проект");
  await act(async () => button("Создать проект").closest("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  expectNoTraining(); expectNoCreation();
});
it("keeps search editable with a clear action when no names match", async () => {
  mocks.list.mockResolvedValue([project]);
  await render(); await settle(() => expect(container.textContent).toContain("Кофе рядом"));
  await change('input[placeholder="Найти проект"]', "несуществующий");
  expect(container.querySelector('input[placeholder="Найти проект"]')).toBeTruthy();
  expect(container.textContent).toContain("Ничего не найдено");
  expect(container.textContent).not.toContain("Первого приложения ещё нет");
  await click("Сбросить поиск");
  expect(container.textContent).toContain("Кофе рядом");
});

it("distinguishes loading, query failure, retry and a real empty workspace", async () => {
  let reject!: (error: Error) => void;
  mocks.list.mockReturnValueOnce(new Promise((_, fail) => { reject = fail; }));
  await render();
  expect(container.querySelector('[role="status"]')?.textContent).toContain("Загружаем приложения");
  expect(container.textContent).not.toContain("Первого приложения ещё нет");
  await act(async () => { reject(new Error("network offline")); });
  await settle(() => expect(container.querySelector('[role="alert"]')?.textContent).toContain("Не дозвонились до сервера"));
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("Проекты никуда не делись");
  expect(container.textContent).not.toContain("Первого приложения ещё нет");
  await click("Проверить ещё раз");
  await settle(() => expect(container.textContent).toContain("Первого приложения ещё нет"));
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
  const featuresStep = document.querySelector('[role="dialog"]')!.textContent;
  expect(featuresStep).toContain("Функции и оформление");
  expect(featuresStep).not.toContain("Самовывоз и оплата");
  expect(featuresStep).not.toContain("Время приготовления");
  await change("#max-brand", "молочный");
  await click("Далее");
  expect(document.activeElement?.textContent).toBe("Проверьте описание");
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Выбрать награду");
  expect(mocks.create).not.toHaveBeenCalled();
  await click("Создать проект");
  await settle(() => expect(mocks.push).toHaveBeenCalledWith("/max/coffee?starter=1"));
  expect(mocks.create).toHaveBeenCalledTimes(1);
  expect(mocks.create).toHaveBeenCalledWith({name:"Кофе рядом",template:"max_miniapp",
    staff_settings:{leads_enabled:true,orders_mode:null,prep_minutes:7},max_config:{
    app_name:"Кофе рядом",summary:"Мини-приложение для пользователей MAX",app_type:"loyalty",audience:"Гости кофейни",primary_action:"Выбрать награду",
    features:["Профиль пользователя","История действий"],style:"brand",brand_colors:"молочный",management_modules:["catalog","orders","leads"],content:[],
    operator:{legal_name:""},support:{email:null,response_time:"Ответим в течение 2 рабочих дней"},
    legal:{age_rating:"0+",has_sales:false,has_user_content:false,marketing_notifications:false,personal_data_consent:true,terms_accepted:false,policy_url:""},max_url_attached:false,
  }});
  expect(mocks.save).not.toHaveBeenCalled();
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

it("reviews prompt products, preserves manual edits through Back and creates the canonical catalog in one request", async () => {
  await openWizard();
  await change("#max-project-name", "Кофейня");
  await change("#max-project-idea", "Создай кофейню в стиле брутализм, латте 300 ₽, капучино 200 ₽");
  await click("Далее"); await click("Далее"); await click("Далее");
  const titles = () => [...document.querySelectorAll<HTMLInputElement>('input[id$="-title"]')];
  expect(titles().map(input => input.value)).toEqual(["латте", "капучино"]);
  await change('input[id$="-price"]', "350 ₽");
  await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Убрать позицию 2"]')!.click());
  await click("Назад"); await click("Назад"); await click("Назад");
  await change("#max-project-idea", "Создай кофейню, латте 300 ₽, капучино 200 ₽, американо 180 ₽");
  await click("Далее"); await click("Далее"); await click("Далее");
  expect(titles().map(input => input.value)).toEqual(["латте"]);
  expect(document.querySelector<HTMLInputElement>('input[id$="-price"]')!.value).toBe("350 ₽");
  expect(document.querySelector('[role="dialog"]')!.textContent).toContain("Описание изменилось");
  expect(mocks.create).not.toHaveBeenCalled();
  await click("Создать проект");
  await settle(() => expect(mocks.create).toHaveBeenCalledTimes(1));
  const content = mocks.create.mock.calls[0][0].max_config.content;
  expect(content).toHaveLength(1);
  expect(content[0]).toMatchObject({ title: "латте", price: "350 ₽", active: true, availability: "in_stock" });
  expect(content[0].id).toMatch(/^item-/);
  expect(mocks.save).not.toHaveBeenCalled();
  expect(sessionStorage.getItem("omnia:max:starter:coffee")).toContain("1 позиций уже сохранены");
  expect(sessionStorage.getItem("omnia:max:starter:coffee")).toContain("не вшивай позиции в код");
});

it("honors the owner's explicit empty catalog even when the brief contains priced products", async () => {
  await openWizard();
  await change("#max-project-name", "Кофейня");
  await change("#max-project-idea", "Создай кофейню, латте 300 ₽, капучино 200 ₽");
  await click("Далее"); await click("Далее"); await click("Далее");
  await click("Начать с пустого каталога");
  expect(document.querySelectorAll('input[id$="-title"]')).toHaveLength(0);
  await click("Назад"); await click("Далее");
  expect(document.querySelectorAll('input[id$="-title"]')).toHaveLength(0);
  await click("Создать проект");
  await settle(() => expect(mocks.create).toHaveBeenCalledTimes(1));
  expect(mocks.create.mock.calls[0][0].max_config.content).toEqual([]);
  expect(sessionStorage.getItem("omnia:max:starter:coffee")).toContain("Владелец подтвердил пустой начальный каталог");
});

it("retains the reviewed empty-catalog decision after securely connecting a provider credential", async () => {
  mocks.integrations.mockResolvedValue({ providers: [{
    key: "aitunnel", name: "AITUNNEL", category: "ai", description: "ИИ", capabilities: [],
    fields: [{ key: "api_key", label: "API-ключ", placeholder: "", help: "", secret: true, required: true }],
    available: true, recommended: false, requirement: null, docs_url: "https://docs.aitunnel.ru/",
    oauth_supported: false, oauth_available: false, connection_mode: "credentials",
  }] });
  mocks.connect.mockResolvedValue({});
  const token = "sk-" + "e".repeat(24);
  await openWizard();
  await change("#max-project-name", "Кофейня");
  await change("#max-project-idea", `Кофейня, латте 300 ₽, капучино 200 ₽\nAITUNNEL token: "${token}"`);
  await click("Далее"); await click("Далее"); await click("Далее");
  await click("Начать с пустого каталога");
  await click("Создать проект");
  await settle(() => expect(mocks.push).toHaveBeenCalledWith("/max/coffee?starter=1"));
  expect(mocks.connect).toHaveBeenCalledTimes(1);
  expect(mocks.create.mock.calls[0][0].max_config.content).toEqual([]);
  expect(JSON.stringify(mocks.create.mock.calls[0][0])).not.toContain(token);
  const prompt = sessionStorage.getItem("omnia:max:starter:coffee")!;
  expect(prompt).toContain("Владелец подтвердил пустой начальный каталог");
  expect(prompt).toContain("Интеграция AITUNNEL уже проверена");
  expect(prompt).not.toContain(token);
});

it("blocks an unnamed position and an overflowing proposal until the owner explicitly resolves it", async () => {
  await openWizard();
  await change("#max-project-name", "Каталог");
  await change("#max-project-idea", Array.from({ length: 101 }, (_, index) => `Товар ${index + 1}: 300 ₽`).join("; "));
  await click("Далее"); await click("Далее"); await click("Далее");
  expect(button("Создать проект").disabled).toBe(true);
  await change('input[id$="-price"]', "350 ₽");
  expect(button("Создать проект").disabled).toBe(true);
  await click("Начать с пустого каталога");
  await click("Добавить позицию");
  expect(button("Создать проект").disabled).toBe(true);
  await change('input[id$="-title"]', "Матча");
  expect(button("Создать проект").disabled).toBe(false);
  expect(mocks.create).not.toHaveBeenCalled();
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
  // Потеря связи описывается владельцу тем же правилом, что и остальные
  // ошибки: что случилось, опасно ли это и что делать дальше.
  await settle(() => expect(container.textContent).toContain("Не дозвонились до сервера"));
  expect(container.textContent).toContain("Приложение и данные это не ломает");
  expect(container.querySelector('.max-project-next')?.getAttribute("href")).toBe("/max/coffee/dashboard");
  mocks.readiness.mockResolvedValue({ items: [{ id: "build", done: true }] });
  await act(async () => { await client.invalidateQueries({ queryKey: ["max-readiness", "coffee"] }); });
  await settle(() => expect(container.querySelector('.max-project-next')?.textContent).toContain("Проверить документы"));
  expect(container.querySelector('.max-project-next')?.getAttribute("href")).toBe("/max/coffee/settings?tab=app&section=policies");
});

it("предлагает готовые идеи вместо пустых двух третей экрана", async () => {
  // На первом экране список занимал верхнюю треть, ниже — пустота: ни
  // подсказки «с чего начать», ни примеров.
  mocks.list.mockResolvedValue([project]);
  mocks.readiness.mockResolvedValue({ items: [{ id: "build", done: true }] });
  await render();
  await settle(() => expect(container.textContent).toContain("Готовые приложения"));
  expect(container.textContent).toContain("Открой готовое приложение и поправь его под себя");
  const ideas = [...container.querySelectorAll<HTMLButtonElement>(".max-studio-idea")];
  expect(ideas).toHaveLength(3);

  // Идея не создаёт проект молча — она заполняет мастер, который можно поправить.
  await act(async () => ideas[0].click());
  await settle(() => expect(document.querySelector('[role="dialog"]')).not.toBeNull());
  const dialog = document.querySelector('[role="dialog"]')!;
  expect(dialog.textContent).toContain("Что создаём?");
  expect(dialog.querySelector<HTMLInputElement>("#max-project-name")?.value).toBe("Кофейня");
  expect(dialog.querySelector<HTMLTextAreaElement>("#max-project-idea")?.value).toContain("предзаказ");
  expect(mocks.create).not.toHaveBeenCalled();
});
