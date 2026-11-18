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
  const group = [...document.querySelectorAll("fieldset")].find(fieldset => fieldset.querySelector("legend")?.textContent === "Что пользователь сможет делать?")!;
  return [...group.querySelectorAll("button")].find(candidate => candidate.querySelector("span")?.textContent?.startsWith(name))!;
}

function moduleCheckbox(name: string) {
  const group = [...document.querySelectorAll("fieldset")].find(fieldset => fieldset.querySelector("legend")?.textContent === "Управление в Yleum");
  const label = [...(group?.querySelectorAll("label") ?? [])].find(candidate => candidate.querySelector("span span")?.textContent === name);
  const checkbox = label?.querySelector<HTMLInputElement>('input[type="checkbox"]');
  expect(checkbox, name).toBeDefined();
  return checkbox!;
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

it("submits free design wishes and colors without introducing a canned style", async () => {
  mocks.create.mockResolvedValue({ id: "free-design-qa" });
  const idea = "Помощник для чтения. Один экран, заголовки Lora, без карточек и градиентов.";
  const colors = "Белый фон, красные кнопки #b71c1c";
  const dispose = await renderStudio();
  try {
    await click("Создать приложение");
    await click("Добавить детали");
    await act(async () => {
      change(document.querySelector<HTMLInputElement>("#max-project-name")!, "Мой помощник");
      change(document.querySelector<HTMLTextAreaElement>("#max-project-idea")!, idea);
    });
    await click("Далее");
    await click("Далее");
    expect(document.querySelector(".max-wizard-option--style")).toBeNull();
    await act(async () => change(document.querySelector<HTMLInputElement>("#max-brand")!, colors));
    expect(mocks.create).not.toHaveBeenCalled();
    await click("Далее");
    expect(document.querySelector('[aria-label="Сводка проекта"]')?.textContent).toContain(idea);
    expect(document.querySelector('[aria-label="Сводка проекта"]')?.textContent).toContain(colors);
    await click("Создать проект");
    await act(async () => { await vi.waitFor(() => expect(mocks.push).toHaveBeenCalled()); });
    expect(mocks.create).toHaveBeenCalledOnce();
    const config = mocks.create.mock.calls[0][0].max_config;
    expect(config.style).toBe("brand");
    expect(config.brand_colors).toBe(colors);
    expect(config.summary).toBe("Мини-приложение для пользователей MAX");
    const prompt = sessionStorage.getItem("omnia:max:starter:free-design-qa");
    expect(prompt).toContain(idea);
    expect(prompt).toContain(`Цвета бренда: ${colors}`);
    expect(prompt).not.toContain("Визуальное направление:");
    expect(mocks.save).not.toHaveBeenCalled();
    expect(mocks.push).toHaveBeenCalledExactlyOnceWith("/max/free-design-qa?starter=1");
  } finally { await dispose(); }
});

it("transfers the entire pasted brief from creation to the chat starter", async () => {
  mocks.create.mockResolvedValue({ id: "long-brief-qa" });
  const brief = "я".repeat(19_992) + "КОНЕЦ ТЗ";
  const dispose = await renderStudio();
  try {
    await click("Создать приложение");
    await click("Добавить детали");
    const input = document.querySelector<HTMLTextAreaElement>("#max-project-idea")!;
    expect(input.maxLength).toBe(-1);
    await act(async () => { change(document.querySelector<HTMLInputElement>("#max-project-name")!, "Склад"); change(input, brief); });
    expect(input.value).toBe(brief);
    await act(async () => { change(input, "я".repeat(20_001)); });
    expect(button("Далее").disabled).toBe(true);
    expect(input.value.length).toBe(20_001);
    await act(async () => { change(input, brief); });
    await click("Далее");
    await click("Далее");
    for (const name of ["Каталог", "Заказы", "Заявки"]) expect(moduleCheckbox(name).checked, name).toBe(true);
    await click("Далее");
    await click("Создать проект");
    await act(async () => { await vi.waitFor(() => expect(mocks.push).toHaveBeenCalled()); });
    expect(mocks.create.mock.calls[0][0].max_config.summary).toBe("Мини-приложение для пользователей MAX");
    expect(JSON.stringify(mocks.create.mock.calls[0][0])).not.toContain(brief);
    expect(mocks.create.mock.calls[0][0].max_config.management_modules).toEqual(["catalog", "orders", "leads"]);
    expect(mocks.save).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("omnia:max:starter:long-brief-qa")).toContain(brief);
    expect(mocks.push).toHaveBeenCalledWith("/max/long-brief-qa?starter=1");
  } finally { await dispose(); }
});

it("opens the home wizard without a tutorial and creates one mixed app after confirming all scenarios and reviewed catalog IDs and prices", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mocks.create.mockResolvedValue({ id: "mixed-app-qa" });
  const fetcher = vi.fn(async (path: string) => {
    if (path === "/api/auth/me") return Response.json({
      generation_initials_available: 1, generation_credit_units_available: 1,
    });
    throw new Error("Unexpected API request");
  });
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  const userKey = "multi-without-tutorial-owner";
  const expectDraftOnly = () => {
    expect(mocks.create).not.toHaveBeenCalled();
    expect(mocks.save).not.toHaveBeenCalled();
    expect(mocks.push).not.toHaveBeenCalled();
    expect(document.querySelector("[data-learning-welcome], .learning-card, .learning-spotlight, .learning-launch")).toBeNull();
    expect(localStorage.getItem(`yleum:learning:v1:${userKey}`)).toBeNull();
  };
  const expectWizardStep = (step: number) => {
    const progress = document.querySelector<HTMLProgressElement>(".max-wizard-progress")!;
    expect(progress.max).toBe(4);
    expect(progress.value).toBe(step);
    expect(progress.getAttribute("aria-label")).toBe(`Шаг ${step} из 4`);
    expectDraftOnly();
  };
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
    await act(async () => { await vi.waitFor(() => expect(document.querySelector(".max-studio-idea")).not.toBeNull()); });
    expect(document.querySelector("#max-project-name")).toBeNull();
    expectDraftOnly();
    expect(client.getQueryData(["projects", userKey])).toEqual([]);
    expect(client.getQueryData(["projects"])).toBeUndefined();
    await click("Создать приложение");
    expect(document.querySelector("#max-project-name")).not.toBeNull();
    expectWizardStep(1);
    await click("Добавить детали");
    await act(async () => {
      change(document.querySelector<HTMLInputElement>("#max-project-name")!, "Кофейня с записью");
      change(document.querySelector<HTMLTextAreaElement>("#max-project-idea")!, "Кофейня с баллами, каталогом и записью.\nЛатте — 300 ₽\nЧай — 150 ₽");
      typeButton("Каталог").click();
    });
    await act(async () => typeButton("Запись").click());
    for (const name of ["Лояльность", "Каталог", "Запись"]) expect(typeButton(name).getAttribute("aria-pressed"), name).toBe("true");
    for (let step = 2; step <= 4; step += 1) { await click("Далее"); expectWizardStep(step); }
    expect(document.querySelector('[aria-label="Сводка проекта"] .max-project-review__facts')?.textContent).toContain("Лояльность, Каталог, Запись");
    const initial = reviewedCatalog();
    expect(initial.map(({ title, price }) => ({ title, price }))).toEqual([{ title: "Латте", price: "300 ₽" }, { title: "Чай", price: "150 ₽" }]);
    await act(async () => change(document.querySelectorAll<HTMLInputElement>(".max-initial-catalog__row input")[1], "325 ₽"));
    for (let step = 3; step >= 1; step -= 1) { await click("Назад"); expectWizardStep(step); }
    await act(async () => typeButton("Запись").click());
    await act(async () => typeButton("Запись").click());
    for (const name of ["Лояльность", "Каталог", "Запись"]) expect(typeButton(name).getAttribute("aria-pressed")).toBe("true");
    for (let step = 2; step <= 4; step += 1) { await click("Далее"); expectWizardStep(step); }
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
  }
});

it("preserves explicit orders and bookings opt-outs for a mixed app through back and submit", async () => {
  mocks.create.mockResolvedValue({ id: "mixed-optout-qa" });
  const dispose = await renderStudio();
  try {
    await click("Создать приложение");
    await click("Добавить детали");
    await act(async () => {
      change(document.querySelector<HTMLInputElement>("#max-project-name")!, "Клуб");
      change(document.querySelector<HTMLTextAreaElement>("#max-project-idea")!, "Бонусы, каталог и запись для клуба");
      typeButton("Запись").click();
    });
    for (let step = 0; step < 2; step += 1) await click("Далее");
    const optedOut = ["Заказы", "Запросы на запись"];
    for (const name of optedOut) {
      const input = moduleCheckbox(name);
      expect(input.disabled, name).toBe(false);
      expect(input.checked, name).toBe(true);
      await act(async () => input.click());
    }
    await click("Назад");
    await click("Назад");
    await act(async () => typeButton("Каталог").click());
    await act(async () => typeButton("Запись").click());
    await act(async () => typeButton("Запись").click());
    for (const name of ["Лояльность", "Каталог", "Запись"]) expect(typeButton(name).getAttribute("aria-pressed")).toBe("true");
    for (let step = 0; step < 2; step += 1) await click("Далее");
    for (const name of optedOut) expect(moduleCheckbox(name).checked, name).toBe(false);
    await click("Далее");
    await click("Создать проект");
    await act(async () => { await vi.waitFor(() => expect(mocks.push).toHaveBeenCalled()); });
    expect(mocks.create).toHaveBeenCalledTimes(1);
    const payload = mocks.create.mock.calls[0][0];
    expect(payload.management_module_opt_outs).toEqual(["orders", "bookings"]);
    expect(payload.max_config.management_modules).toEqual(["catalog", "leads"]);
    expect(payload.max_config.features).toContain("Сценарий: Запись");
    expect(sessionStorage.getItem("omnia:max:starter:mixed-optout-qa")).toContain("Управление в Yleum: Каталог, Заявки.");
  } finally { await dispose(); }
});

it.each([
  { optOutOfOrders: false, expected: ["catalog", "orders", "leads", "bookings"] },
  { optOutOfOrders: true, expected: ["catalog", "leads", "bookings"] },
])("restores dependent selections when catalog returns (orders opted out: $optOutOfOrders)", async ({ optOutOfOrders, expected }) => {
  mocks.create.mockResolvedValue({ id: "catalog-roundtrip-qa" });
  const dispose = await renderStudio();
  try {
    await click("Создать приложение");
    await act(async () => {
      change(document.querySelector<HTMLInputElement>("#max-project-name")!, "Клуб");
      typeButton("Запись").click();
    });
    await click("Далее");
    await click("Далее");
    const dependents = ["Заказы", "Запросы на запись"];
    for (const name of dependents) expect(moduleCheckbox(name).checked, name).toBe(true);
    if (optOutOfOrders) await act(async () => moduleCheckbox("Заказы").click());

    await act(async () => moduleCheckbox("Каталог").click());
    await click("Назад");
    await click("Назад");
    await act(async () => typeButton("Каталог").click());
    await click("Далее");
    await click("Далее");
    for (const name of dependents) {
      expect(moduleCheckbox(name).disabled, name).toBe(true);
      expect(moduleCheckbox(name).checked, name).toBe(false);
    }

    await act(async () => moduleCheckbox("Каталог").click());
    for (const name of dependents) expect(moduleCheckbox(name).disabled, name).toBe(false);
    expect(moduleCheckbox("Заказы").checked).toBe(!optOutOfOrders);
    expect(moduleCheckbox("Запросы на запись").checked).toBe(true);
    await click("Далее");
    await click("Создать проект");
    await act(async () => { await vi.waitFor(() => expect(mocks.push).toHaveBeenCalled()); });
    const payload = mocks.create.mock.calls[0][0];
    expect(payload.management_module_opt_outs).toEqual(optOutOfOrders ? ["orders"] : undefined);
    expect(payload.max_config.management_modules).toEqual(expected);
  } finally { await dispose(); }
});

it("can create an app after opting out of the catalog and leads", async () => {
  mocks.create.mockResolvedValue({ id: "no-modules-qa" });
  const dispose = await renderStudio();
  try {
    await click("Создать приложение");
    await act(async () => change(document.querySelector<HTMLInputElement>("#max-project-name")!, "Справочник"));
    await click("Далее");
    await click("Далее");
    for (const name of ["Каталог", "Заявки"]) {
      const input = moduleCheckbox(name);
      expect(input.checked, name).toBe(true);
      expect(input.disabled, name).toBe(false);
      await act(async () => input.click());
    }
    for (const name of ["Заказы", "Запросы на запись"]) {
      expect(moduleCheckbox(name).checked, name).toBe(false);
      expect(moduleCheckbox(name).disabled, name).toBe(true);
    }
    await click("Далее");
    expect(document.querySelector(".max-initial-catalog")).toBeNull();
    await click("Создать проект");
    await act(async () => { await vi.waitFor(() => expect(mocks.push).toHaveBeenCalled()); });
    const payload = mocks.create.mock.calls[0][0];
    expect(payload.management_module_opt_outs).toEqual(["catalog", "leads"]);
    expect(payload.max_config.management_modules).toEqual([]);
    expect(payload.max_config.content).toEqual([]);
    expect(payload.staff_settings.leads_enabled).toBe(false);
  } finally { await dispose(); }
});

it("replaces the mixed draft with the selected ready idea without creating a project", async () => {
  const dispose = await renderStudio();
  try {
    await click("Создать приложение");
    await click("Добавить детали");
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
