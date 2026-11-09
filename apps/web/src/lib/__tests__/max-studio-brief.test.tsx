import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { MaxStudio } from "@/components/max/MaxStudio";
import { LearningProvider } from "@/components/learning/LearningProvider";
import { ProductLearning } from "@/components/learning/ProductLearning";

const mocks = vi.hoisted(() => ({ create: vi.fn(), save: vi.fn(), push: vi.fn() }));
vi.mock("@/lib/api/projects", () => ({ createProject: mocks.create, listProjects: async () => [] }));
vi.mock("@/lib/api/max-studio", () => ({ saveMaxProjectConfig: mocks.save }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }), usePathname: () => "/max", useSearchParams: () => new URLSearchParams() }));
vi.mock("@/app/(auth)/actions", () => ({ logoutAction: async () => {} }));
vi.mock("@/components/max/MaxStudioAccountDisclosure", () => ({ MaxStudioAccountDisclosure: () => null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

afterEach(() => { vi.clearAllMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear(); localStorage.clear(); });

function change(input: HTMLTextAreaElement | HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

const button = (name: string) => [...document.querySelectorAll("button")].find(candidate => candidate.textContent?.trim() === name || candidate.getAttribute("aria-label") === name)!;
async function click(name: string) { expect(button(name), name).toBeDefined(); await act(async () => button(name).click()); }

function typeButton(name: string) {
  const group = [...document.querySelectorAll("fieldset")].find(fieldset => fieldset.querySelector("legend")?.textContent === "Тип приложения")!;
  return [...group.querySelectorAll("button")].find(candidate => candidate.querySelector("span")?.textContent?.startsWith(name))!;
}

async function renderStudio() {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("fetch", vi.fn(async (path: string) => {
    if (path === "/api/auth/me") return Response.json({ generation_initials_available: 1, generation_credit_units_available: 1 });
    throw new Error("Unexpected API request");
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  await act(async () => root.render(<QueryClientProvider client={client}><MaxStudio email="qa@example.test" /></QueryClientProvider>));
  await act(async () => { await vi.waitFor(() => expect(document.querySelector(".max-studio-idea")).not.toBeNull()); });
  return async () => { await act(async () => root.unmount()); client.clear(); container.remove(); };
}

it("transfers the entire pasted brief from creation to the chat starter", async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  mocks.create.mockResolvedValue({ id: "long-brief-qa" });
  mocks.save.mockResolvedValue({});
  const brief = "я".repeat(19_992) + "КОНЕЦ ТЗ";
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => { root.render(<QueryClientProvider client={client}><MaxStudio email="qa@example.ru" /></QueryClientProvider>); });
    await act(async () => { await vi.waitFor(() => expect([...container.querySelectorAll("button")].some(b => b.textContent?.includes("Создать приложение"))).toBe(true)); });
    await act(async () => { [...container.querySelectorAll("button")].find(b => b.textContent?.includes("Создать приложение"))!.click(); });
    const input = document.querySelector<HTMLTextAreaElement>("#max-project-idea")!;
    expect(input.maxLength).toBe(-1);
    await act(async () => { change(document.querySelector<HTMLInputElement>("#max-project-name")!, "Склад"); change(input, brief); });
    expect(input.value).toBe(brief);
    const next = () => [...document.querySelectorAll("button")].find(b => b.textContent?.trim() === "Далее")!;
    await act(async () => { change(input, "я".repeat(20_001)); });
    expect(next().disabled).toBe(true);
    expect(input.value.length).toBe(20_001);
    await act(async () => { change(input, brief); });
    for (let step = 0; step < 2; step++) await act(async () => { next().click(); });
    const modules = [...document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')];
    expect(modules[0].checked && modules[0].disabled).toBe(true);
    expect(modules[2].checked && modules[2].disabled).toBe(true);
    await act(async () => { modules[0].click(); modules[2].click(); });
    expect(modules[0].checked && modules[2].checked).toBe(true);
    expect(modules[1].checked).toBe(true);
    await act(async () => { modules[1].click(); });
    expect(modules[1].checked).toBe(false);
    await act(async () => { next().click(); });
    const submit = [...document.querySelectorAll("button")].find(b => b.textContent?.includes("Создать проект"))!;
    await act(async () => { submit.click(); });
    await act(async () => { await vi.waitFor(() => expect(mocks.push).toHaveBeenCalled()); });
    expect(mocks.create.mock.calls[0][0].max_config.summary).toBe("Мини-приложение для пользователей MAX");
    expect(JSON.stringify(mocks.create.mock.calls[0][0])).not.toContain(brief);
    expect(mocks.create.mock.calls[0][0].max_config.management_modules).toEqual(["catalog", "leads"]);
    expect(mocks.save).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("omnia:max:starter:long-brief-qa")).toContain(brief);
    expect(mocks.push).toHaveBeenCalledWith("/max/long-brief-qa?starter=1");
  } finally {
    await act(async () => { root.unmount(); });
    container.remove(); client.clear(); sessionStorage.clear();
  }
});

it("creates one mixed app only after tutorial completion with all scenarios and the reviewed catalog IDs and prices", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mocks.create.mockResolvedValue({ id: "mixed-app-qa" });
  const fetcher = vi.fn(async (path: string) => {
    if (path === "/api/auth/me") return Response.json({
      generation_initials_available: 1, generation_credit_units_available: 1,
    });
    throw new Error("Unexpected API request");
  });
  vi.stubGlobal("fetch", fetcher);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ x: 30, y: 100, left: 30, top: 100, width: 336, height: 300, bottom: 400, right: 366, toJSON() {} });
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  const previousScroll = HTMLElement.prototype.scrollIntoView;
  HTMLElement.prototype.scrollIntoView = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  const userKey = "multi-after-tutorial-owner";
  const reviewedCatalog = () => [...document.querySelectorAll(".max-initial-catalog__row")].map(row => {
    const inputs = row.querySelectorAll<HTMLInputElement>("input");
    const id = inputs[0].id.match(/(item-[a-f0-9-]{36})-title$/)?.[1];
    expect(id).toBeDefined();
    return { id, title: inputs[0].value, price: inputs[1].value };
  });
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><LearningProvider userKey={userKey}>
      <MaxStudio email="qa@example.test" /><ProductLearning userKey={userKey} />
    </LearningProvider></QueryClientProvider>));
    await act(async () => { await vi.waitFor(() => expect(document.querySelector("[data-learning-welcome]")).not.toBeNull()); });
    await click("Создать приложение");
    expect(document.querySelector("#max-project-name")).toBeNull();
    expect(mocks.create).not.toHaveBeenCalled();
    await click("Начать обучение");
    for (let step = 0; step < 8 && button("Далее"); step += 1) await click("Далее");
    await click("Завершить");
    expect(document.querySelector("#max-project-name")).toBeNull();
    expect(mocks.create).not.toHaveBeenCalled();
    expect(JSON.parse(localStorage.getItem(`yleum:learning:v1:${userKey}`)!).tours.cabinet).toBe("completed");
    expect(client.getQueryData(["projects", userKey])).toEqual([]);
    expect(client.getQueryData(["projects"])).toBeUndefined();
    await click("Создать приложение");
    await act(async () => {
      change(document.querySelector<HTMLInputElement>("#max-project-name")!, "Кофейня с записью");
      change(document.querySelector<HTMLTextAreaElement>("#max-project-idea")!, "Кофейня с баллами, каталогом и записью.\nЛатте — 300 ₽\nЧай — 150 ₽");
      typeButton("Каталог").click();
    });
    await act(async () => typeButton("Запись").click());
    for (const name of ["Лояльность", "Каталог", "Запись"]) expect(typeButton(name).getAttribute("aria-pressed"), name).toBe("true");
    for (let step = 0; step < 3; step += 1) await click("Далее");
    expect(document.querySelector('[aria-label="Сводка проекта"] .max-project-review__facts')?.textContent).toContain("Лояльность, Каталог, Запись");
    const initial = reviewedCatalog();
    expect(initial.map(({ title, price }) => ({ title, price }))).toEqual([{ title: "Латте", price: "300 ₽" }, { title: "Чай", price: "150 ₽" }]);
    await act(async () => change(document.querySelectorAll<HTMLInputElement>(".max-initial-catalog__row input")[1], "325 ₽"));
    for (let step = 0; step < 3; step += 1) await click("Назад");
    await act(async () => typeButton("Запись").click());
    await act(async () => typeButton("Запись").click());
    for (const name of ["Лояльность", "Каталог", "Запись"]) expect(typeButton(name).getAttribute("aria-pressed")).toBe("true");
    for (let step = 0; step < 3; step += 1) await click("Далее");
    const expected = [{ ...initial[0], price: "325 ₽" }, initial[1]];
    expect(reviewedCatalog()).toEqual(expected);
    await act(async () => { button("Создать проект").click(); button("Создать проект").click(); });
    await act(async () => { await vi.waitFor(() => expect(mocks.push).toHaveBeenCalled()); });
    expect(mocks.create).toHaveBeenCalledTimes(1);
    const payload = mocks.create.mock.calls[0][0];
    expect(payload.template).toBe("max_miniapp");
    expect(payload.max_config.app_type).toBe("loyalty");
    expect(payload.max_config.features).toEqual(["Профиль пользователя", "История действий", "Сценарий: Лояльность", "Сценарий: Каталог", "Сценарий: Запись"]);
    expect(payload.max_config.management_modules).toEqual(["catalog", "orders", "leads", "bookings"]);
    expect(payload.max_config.legal.has_sales).toBe(true);
    expect(payload.max_config.content.map(({ id, title, price }: { id: string; title: string; price: string }) => ({ id, title, price }))).toEqual(expected);
    expect(mocks.save).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("omnia:max:starter:mixed-app-qa")).toContain("Сценарии: Лояльность, Каталог, Запись.");
    expect(mocks.push).toHaveBeenCalledExactlyOnceWith("/max/mixed-app-qa?starter=1");
    expect(fetcher.mock.calls.every(([path]) => path === "/api/auth/me")).toBe(true);
  } finally {
    await act(async () => root.unmount()); client.clear(); container.remove();
    HTMLElement.prototype.scrollIntoView = previousScroll;
  }
});

it("preserves explicit orders and bookings opt-outs for a mixed app through back and submit", async () => {
  mocks.create.mockResolvedValue({ id: "mixed-optout-qa" });
  const dispose = await renderStudio();
  try {
    await click("Создать приложение");
    await act(async () => {
      change(document.querySelector<HTMLInputElement>("#max-project-name")!, "Клуб");
      change(document.querySelector<HTMLTextAreaElement>("#max-project-idea")!, "Бонусы, каталог и запись для клуба");
      typeButton("Запись").click();
    });
    for (let step = 0; step < 2; step += 1) await click("Далее");
    const optionalModules = () => [...document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].filter(input => !input.disabled);
    expect(optionalModules()).toHaveLength(2);
    for (const input of optionalModules()) { expect(input.checked).toBe(true); await act(async () => input.click()); }
    await click("Назад");
    await click("Назад");
    await act(async () => typeButton("Каталог").click());
    await act(async () => typeButton("Запись").click());
    await act(async () => typeButton("Запись").click());
    for (const name of ["Лояльность", "Каталог", "Запись"]) expect(typeButton(name).getAttribute("aria-pressed")).toBe("true");
    for (let step = 0; step < 2; step += 1) await click("Далее");
    expect(optionalModules().every(input => !input.checked)).toBe(true);
    await click("Далее");
    await click("Создать проект");
    await act(async () => { await vi.waitFor(() => expect(mocks.push).toHaveBeenCalled()); });
    expect(mocks.create).toHaveBeenCalledTimes(1);
    expect(mocks.create.mock.calls[0][0].max_config.management_modules).toEqual(["catalog", "leads"]);
    expect(mocks.create.mock.calls[0][0].max_config.features).toContain("Сценарий: Запись");
    expect(sessionStorage.getItem("omnia:max:starter:mixed-optout-qa")).toContain("Управление в Yleum: Каталог, Заявки.");
  } finally { await dispose(); }
});

it("replaces the mixed draft with the selected ready idea without creating a project", async () => {
  const dispose = await renderStudio();
  try {
    await click("Создать приложение");
    await act(async () => {
      change(document.querySelector<HTMLInputElement>("#max-project-name")!, "Клуб");
      change(document.querySelector<HTMLTextAreaElement>("#max-project-idea")!, "Бонусы, каталог и запись для клуба");
    });
    await act(async () => typeButton("Каталог").click());
    await act(async () => typeButton("Запись").click());
    for (let step = 0; step < 2; step += 1) await click("Далее");
    for (const input of document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')) {
      if (!input.disabled && input.checked) await act(async () => input.click());
    }
    await click("Назад"); await click("Назад");
    await click("Отмена");
    await act(async () => [...document.querySelectorAll<HTMLButtonElement>(".max-studio-idea")].find(candidate => candidate.querySelector(".max-studio-idea-title")?.textContent === "Барбершоп")!.click());
    expect(document.querySelector<HTMLInputElement>("#max-project-name")?.value).toBe("Барбершоп");
    expect(typeButton("Запись").getAttribute("aria-pressed")).toBe("true");
    for (const name of ["Лояльность", "Каталог"]) expect(typeButton(name).getAttribute("aria-pressed")).toBe("false");
    for (let step = 0; step < 2; step += 1) await click("Далее");
    expect([...document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].map(input => input.checked)).toEqual([true, false, true, true]);
    await click("Далее");
    expect(document.querySelector('[aria-label="Сводка проекта"] .max-project-review__facts')?.textContent).toContain("Запись");
    expect(mocks.create).not.toHaveBeenCalled();
    expect(mocks.push).not.toHaveBeenCalled();
  } finally { await dispose(); }
});
