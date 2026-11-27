import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { MaxStudio } from "@/components/max/MaxStudio";
import { emptyMaxProjectDraft, readMaxProjectDraft, replaceMaxProjectDraft, saveMaxProjectDraft } from "@/lib/max-project-draft";
import { saveLandingPrompt, peekLandingPrompt } from "@/lib/landing-prompt";

const mocks = vi.hoisted(() => ({ create: vi.fn(), save: vi.fn(), push: vi.fn(), error: vi.fn(), warning: vi.fn() }));
vi.mock("@/lib/api/projects", () => ({ createProject: mocks.create, listProjects: async () => [] }));
vi.mock("@/lib/api/max-studio", () => ({ saveMaxProjectConfig: mocks.save }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }), usePathname: () => "/max", useSearchParams: () => new URLSearchParams() }));
vi.mock("@/app/(auth)/actions", () => ({ logoutAction: async () => {} }));
vi.mock("@/components/max/MaxStudioAccountDisclosure", () => ({ MaxStudioAccountDisclosure: () => null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: mocks.error, warning: mocks.warning } }));
afterEach(() => { vi.clearAllMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear(); localStorage.clear(); });
function change(input: HTMLTextAreaElement | HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}
const button = (name: string) => [...document.querySelectorAll("button")].find(candidate => candidate.textContent?.trim() === name || candidate.getAttribute("aria-label") === name)!;
async function click(name: string) { expect(button(name), name).toBeDefined(); await act(async () => button(name).click()); }
const input = (id: string) => document.querySelector<HTMLInputElement>(id)!;
async function fill(id: string, value: string) { await act(async () => change(input(id), value)); }
function scenario(name: string) { return [...document.querySelectorAll(".max-wizard-option")].find(b => b.querySelector("span")?.textContent?.startsWith(name)) as HTMLButtonElement; }
function module(name: string) { return [...document.querySelectorAll("label")].find(l => l.querySelector("span span")?.textContent === name)?.querySelector<HTMLInputElement>('input[type="checkbox"]')!; }
async function pickModules(...names: string[]) { for (const name of names) await act(async () => module(name).click()); }
async function renderStudio(owner = "qa@example.test") {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("fetch", vi.fn(async (path: string) => {
    if (path === "/api/auth/me") return Response.json({ generation_initials_available: 1, generation_credit_units_available: 1 });
    throw new Error("Unexpected API request");
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  const render = async (email: string) => {
    await act(async () => root.render(<QueryClientProvider client={client}><MaxStudio email={email} /></QueryClientProvider>));
    await act(async () => { await new Promise(resolve => requestAnimationFrame(resolve)); });
    expect(button("Создать приложение").disabled).toBe(false);
  };
  await render(owner);
  return { render, hide: async () => { await act(async () => root.render(null)); },
    dispose: async () => { await act(async () => root.unmount()); client.clear(); container.remove(); } };
}
async function initial(idea = "Помощник для чтения с личными заметками") {
  await click("Создать приложение"); await fill("#max-project-name", "Мой помощник"); await fill("#max-project-idea", idea);
}
async function create() { await click("Создать проект"); await act(async () => { await vi.waitFor(() => expect(mocks.push).toHaveBeenCalled()); }); }

it("keeps the home screen open after dismissing an empty wizard and preserves another tab's answers", async () => {
  const owner = "qa@example.test";
  const page = await renderStudio(owner);
  try {
    await click("Создать приложение");
    await click("Отмена");
    expect(readMaxProjectDraft(owner)).toBeNull();
    await page.hide();
    await page.render(owner);
    expect(input("#max-project-name")).toBeNull();
    await click("Создать приложение");
    const draft = readMaxProjectDraft(owner)!;
    expect(saveMaxProjectDraft(owner, { ...draft, name: "В другой вкладке" })).toBe(true);
    await click("Отмена");
    expect(readMaxProjectDraft(owner)?.name).toBe("В другой вкладке");
  } finally { await page.dispose(); }
});

it("starts neutral, keeps free design and leaves unchosen capabilities available as needed", async () => {
  mocks.create.mockResolvedValue({ id: "neutral" }); const page = await renderStudio();
  try {
    await initial();
    expect(scenario("Своя идея").getAttribute("aria-pressed")).toBe("true");
    expect(scenario("Лояльность").getAttribute("aria-pressed")).toBe("false");
    await click("Далее"); await click("Далее");
    for (const name of ["Каталог", "Заказы", "Заявки", "Запросы на запись"]) expect(module(name).checked).toBe(false);
    expect(document.querySelectorAll('.max-feature-chip[data-selected="true"]')).toHaveLength(0);
    await fill("#max-brand", "Белый фон, красные кнопки #b71c1c"); await click("Далее"); await create();
    const payload = mocks.create.mock.calls[0][0];
    expect(payload.max_config).toMatchObject({ app_type: "custom", features: [], management_modules: ["catalog", "orders", "leads", "bookings"], brand_colors: "Белый фон, красные кнопки #b71c1c", style: "brand" });
    expect(payload.max_config.support.email).toBeNull();
    expect(payload.management_module_opt_outs).toEqual([]);
    expect(payload.staff_settings).toMatchObject({ leads_enabled: false, orders_mode: null });
    expect(sessionStorage.getItem("omnia:max:starter:neutral")).toContain("Цвета бренда: Белый фон, красные кнопки #b71c1c");
    expect(mocks.save).not.toHaveBeenCalled();
    expect(readMaxProjectDraft("qa@example.test")).toBeNull();
  } finally { await page.dispose(); }
});

it.each(["original", "edited", "cleared"])("creates once with the %s support contact reviewed by the owner", async mode => {
  mocks.create.mockResolvedValue({ id: "support-test" });
  const page = await renderStudio();
  try {
    await initial("Создай дневник. Email поддержки: hello@nutrition.ru.");
    await click("Далее"); await click("Далее"); await click("Далее");
    expect(input("#max-public-support-email").value).toBe("hello@nutrition.ru");
    const expected = mode === "original" ? "hello@nutrition.ru" : mode === "edited" ? "care@nutrition.ru" : "";
    if (mode !== "original") {
      await fill("#max-public-support-email", expected);
      await click("Назад"); await click("Далее");
      expect(input("#max-public-support-email").value).toBe(expected);
      expect(readMaxProjectDraft("qa@example.test")?.supportEmail).toBe(expected);
    }
    await create();
    expect(mocks.create).toHaveBeenCalledOnce();
    expect(mocks.create.mock.calls[0][0].max_config.support.email).toBe(expected || null);
    expect(sessionStorage.getItem("omnia:max:starter:support-test")).toContain("Поддержка: /support");
    expect(mocks.save).not.toHaveBeenCalled();
  } finally { await page.dispose(); }
});

it("keeps the full long brief and rejects field overflow early without deleting text", async () => {
  mocks.create.mockResolvedValue({ id: "long" }); const page = await renderStudio();
  const idea = "я".repeat(19_992) + "КОНЕЦ ТЗ";
  try {
    await initial(idea); expect(input("#max-project-idea").maxLength).toBe(-1);
    await fill("#max-project-idea", "я".repeat(20_001)); expect(button("Далее").disabled).toBe(true);
    expect(input("#max-project-idea").value.length).toBe(20_001);
    await fill("#max-project-idea", idea); await click("Далее");
    await fill("#max-audience", "я".repeat(420));
    expect(button("Далее").disabled).toBe(true); expect(input("#max-audience").value.length).toBe(420);
    expect(document.body.textContent).toContain("не более 400 символов");
    expect(mocks.create).not.toHaveBeenCalled();
    await fill("#max-audience", "😀".repeat(400)); await click("Далее");
    await fill("#max-brand", "я".repeat(201)); expect(button("Далее").disabled).toBe(true);
    await fill("#max-brand", "красный"); await click("Далее"); await create();
    expect(sessionStorage.getItem("omnia:max:starter:long")).toContain(idea);
  } finally { await page.dispose(); }
});

it("keeps explicit mixed scenarios, modules, catalog IDs/prices and one submit through Back", async () => {
  mocks.create.mockResolvedValue({ id: "mixed" }); const page = await renderStudio();
  try {
    await initial("Меню: Латте 300 ₽; Капучино 250 ₽");
    await act(async () => scenario("Каталог").click()); await act(async () => scenario("Запись").click());
    expect(scenario("Своя идея").getAttribute("aria-pressed")).toBe("false");
    await click("Далее"); await click("Далее");
    await pickModules("Каталог", "Заказы", "Запросы на запись", "Заявки", "Заявки");
    for (const name of ["Каталог", "Заказы", "Запросы на запись"]) expect(module(name).checked).toBe(true);
    await click("Далее");
    const rows = document.querySelectorAll(".max-initial-catalog__row"); expect(rows).toHaveLength(2);
    const titles = [...rows].map(row => row.querySelector<HTMLInputElement>('input[id$="-title"]')?.value);
    await act(async () => change(rows[0].querySelector<HTMLInputElement>('input[id$="-price"]')!, "325 ₽"));
    const reviewed = readMaxProjectDraft("qa@example.test")!.catalogDraft!.items;
    await click("Назад"); await click("Назад"); await click("Назад");
    expect(scenario("Каталог").getAttribute("aria-pressed")).toBe("true"); expect(scenario("Запись").getAttribute("aria-pressed")).toBe("true");
    await click("Далее"); await click("Далее");
    for (const name of ["Каталог", "Заказы", "Запросы на запись"]) expect(module(name).checked).toBe(true);
    expect(module("Заявки").checked).toBe(false);
    await click("Далее");
    await act(async () => { button("Создать проект").click(); button("Создать проект").click(); });
    await act(async () => { await vi.waitFor(() => expect(mocks.push).toHaveBeenCalled()); });
    expect(mocks.create).toHaveBeenCalledOnce(); const payload = mocks.create.mock.calls[0][0];
    expect(payload.max_config.management_modules).toEqual(["catalog", "orders", "bookings"]);
    expect(payload.management_module_opt_outs).toEqual(["leads"]);
    expect(payload.max_config.features).toEqual(["Сценарий: Каталог", "Сценарий: Запись"]);
    expect(payload.max_config.content.map((item: {title:string}) => item.title)).toEqual(titles);
    expect(payload.max_config.content).toEqual(reviewed);
    expect(new Set(payload.max_config.content.map((item:{id:string}) => item.id)).size).toBe(2);
  } finally { await page.dispose(); }
});

it("preserves deliberate dependent selections while catalog is temporarily disabled", async () => {
  const page = await renderStudio();
  try {
    await initial(); await click("Далее"); await click("Далее"); await pickModules("Каталог", "Заказы", "Запросы на запись");
    await pickModules("Каталог");
    for (const name of ["Заказы", "Запросы на запись"]) { expect(module(name).checked).toBe(false); expect(module(name).disabled).toBe(true); }
    await pickModules("Каталог");
    for (const name of ["Заказы", "Запросы на запись"]) expect(module(name).checked).toBe(true);
    await pickModules("Заказы"); await pickModules("Каталог"); await pickModules("Каталог");
    expect(module("Заказы").checked).toBe(false); expect(module("Запросы на запись").checked).toBe(true);
  } finally { await page.dispose(); }
});

it.each(["Хочу магазин с покупкой и оплатой", "Только API: магазин с покупкой и оплатой"])("an untouched module step does not veto a free-text request: %s", async idea => {
  mocks.create.mockResolvedValue({ id: "text-orders" }); const page = await renderStudio();
  try {
    await initial(idea); await click("Далее"); await click("Далее");
    for (const name of ["Каталог", "Заказы", "Заявки", "Запросы на запись"]) expect(module(name).checked).toBe(false);
    await click("Далее");
    expect(document.querySelector(".max-initial-catalog")).toBeNull();
    await create();
    const payload = mocks.create.mock.calls[0][0];
    expect(payload.max_config.management_modules).toEqual(["catalog", "orders", "leads", "bookings"]);
    expect(payload.management_module_opt_outs).toEqual([]);
    expect(payload.max_config.features).toEqual([]);
    expect(payload.staff_settings).toMatchObject({ leads_enabled: false, orders_mode: null });
    const prompt = sessionStorage.getItem("omnia:max:starter:text-orders")!;
    expect(prompt).toContain(idea);
    expect(prompt).not.toContain("Управление в Yleum:");
    expect(prompt).not.toContain("владелец подтвердил пустой каталог");
    expect(prompt).not.toContain("бизнес-модули не выбраны");
  } finally { await page.dispose(); }
});

it.each(["Хочу магазин с покупкой и оплатой", "Меню: Латте — 300 ₽; Капучино — 200 ₽"])("an explicit catalog exclusion wins over free text without enabling staff queues: %s", async idea => {
  mocks.create.mockResolvedValue({ id: "excluded" }); const page = await renderStudio();
  try {
    await initial(idea); await click("Далее"); await click("Далее");
    await pickModules("Каталог", "Каталог");
    expect(document.body.textContent).toContain("Исключено вами.");
    await click("Далее"); await create();
    const payload = mocks.create.mock.calls[0][0];
    expect(payload.management_module_opt_outs).toEqual(["catalog"]);
    expect(payload.max_config.content).toEqual([]);
    expect(sessionStorage.getItem("omnia:max:starter:excluded")).not.toContain("владелец подтвердил пустой каталог");
    expect(payload.max_config.management_modules).toEqual(["leads"]);
    expect(payload.staff_settings).toMatchObject({ leads_enabled: false, orders_mode: null });
    expect(sessionStorage.getItem("omnia:max:starter:excluded")).toContain("Явно исключено владельцем: Каталог.");
  } finally { await page.dispose(); }
});
it("an explicit lead selection enables its staff queue without requiring other available flows", async () => {
  mocks.create.mockResolvedValue({ id: "leads" }); const page = await renderStudio();
  try {
    await initial("Принимать обращения от читателей"); await click("Далее"); await click("Далее"); await pickModules("Заявки");
    await click("Далее"); await create();
    const payload = mocks.create.mock.calls[0][0];
    expect(payload.max_config.management_modules).toEqual(["catalog", "orders", "leads", "bookings"]);
    expect(payload.management_module_opt_outs).toEqual([]);
    expect(payload.staff_settings).toMatchObject({ leads_enabled: true, orders_mode: null });
    const prompt = sessionStorage.getItem("omnia:max:starter:leads")!;
    expect(prompt).toContain("Управление в Yleum: Заявки.");
    expect(prompt).not.toContain(".omnia/management.json");
    expect(prompt).not.toContain("владелец подтвердил пустой каталог");
  } finally { await page.dispose(); }
});

it("neutral persisted draft remains unspecified, while a deliberate opt-out survives text changes", async () => {
  const page = await renderStudio();
  try {
    await initial();
    expect(readMaxProjectDraft("qa@example.test")?.managementModuleOptOuts).toEqual([]);
    await fill("#max-project-idea", "Каталог с заказами и доставкой"); await click("Далее"); await click("Далее");
    expect(module("Заказы").checked).toBe(false); await pickModules("Каталог", "Заказы", "Заказы");
    await click("Назад"); await click("Назад"); await fill("#max-project-idea", "Каталог с заказами и доставкой для клуба");
    await click("Далее"); await click("Далее"); expect(module("Заказы").checked).toBe(false);
    expect(readMaxProjectDraft("qa@example.test")?.managementModuleOptOuts).toEqual(["orders"]);
  } finally { await page.dispose(); }
});

it("restores the same owner's answers after remount while a different owner remains empty", async () => {
  const first = await renderStudio("a@example.test");
  await initial("Сохранённый личный запрос"); await click("Далее"); await fill("#max-audience", "Своя аудитория"); await click("Далее");
  await fill("#max-brand", "Белый и красный"); await pickModules("Каталог", "Заявки");
  await first.dispose();
  const other = await renderStudio("b@example.test");
  await click("Создать приложение"); expect(input("#max-project-name").value).toBe(""); await other.dispose();
  const restored = await renderStudio("a@example.test");
  try {
    expect(input("#max-project-idea").value).toBe("Сохранённый личный запрос"); await click("Далее");
    expect(input("#max-audience").value).toBe("Своя аудитория"); await click("Далее");
    expect(input("#max-brand").value).toBe("Белый и красный"); expect(module("Каталог").checked).toBe(true); expect(module("Заявки").checked).toBe(true);
  } finally { await restored.dispose(); }
});

it("durably stores a landing handoff before consuming it, and keeps it when storage fails", async () => {
  const text = "😀".repeat(10000) + "ХВОСТ"; saveLandingPrompt(text);
  const page = await renderStudio();
  try { expect(input("#max-project-idea").value).toBe(text); expect(peekLandingPrompt()).toBeNull(); expect(readMaxProjectDraft("qa@example.test")?.idea).toBe(text); }
  finally { await page.dispose(); }
  localStorage.clear(); saveLandingPrompt("Не потеряй этот запрос");
  const original = Storage.prototype.setItem;
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(function(this: Storage, key, value) {
    if (key.startsWith("yleum:max-project-draft")) throw new Error("blocked"); original.call(this,key,value);
  });
  const failed = await renderStudio();
  try { expect(peekLandingPrompt()).toBe("Не потеряй этот запрос"); expect(document.body.textContent).toContain("Не удалось сохранить черновик"); }
  finally { await failed.dispose(); }
});

it("successful creation cannot be restored by a stale writer or a later remount", async () => {
  mocks.create.mockResolvedValue({ id: "done" }); const page = await renderStudio(); await initial();
  const old = readMaxProjectDraft("qa@example.test")!;
  await click("Далее"); await click("Далее"); await click("Далее"); await create();
  expect(saveMaxProjectDraft("qa@example.test",old)).toBe(false); await page.dispose();
  const again = await renderStudio();
  try { expect(document.querySelector("#max-project-name")).toBeNull(); await click("Создать приложение"); expect(input("#max-project-name").value).toBe(""); }
  finally { await again.dispose(); }
});

it("pending owner A completion cannot clear owner B or navigate B to A's app", async () => {
  let resolve!: (value: {id:string}) => void; mocks.create.mockImplementation(() => new Promise(r => { resolve=r; }));
  const page = await renderStudio("a@example.test");
  try {
    await initial("Только задача владельца A"); await click("Далее"); await click("Далее"); await click("Далее"); await click("Создать проект");
    await page.render("b@example.test"); await initial("Только задача владельца B");
    const before = readMaxProjectDraft("b@example.test");
    await act(async () => resolve({id:"owned-by-A"}));
    expect(mocks.push).not.toHaveBeenCalled(); expect(readMaxProjectDraft("b@example.test")).toEqual(before);
    expect(input("#max-project-idea").value).toBe("Только задача владельца B");
    expect(readMaxProjectDraft("a@example.test")).toBeNull();
  } finally { await page.dispose(); }
});

it("failed creation retains a complete editable draft and allows an explicit retry", async () => {
  mocks.create.mockRejectedValueOnce(new Error("Synthetic refusal")).mockResolvedValueOnce({id:"retry"});
  const page = await renderStudio();
  try {
    await initial(); await click("Далее"); await click("Далее"); await fill("#max-brand","Красный"); await click("Далее"); await click("Создать проект");
    await act(async () => { await vi.waitFor(() => expect(mocks.error).toHaveBeenCalled()); });
    expect(readMaxProjectDraft("qa@example.test")?.brandColors).toBe("Красный"); expect(button("Создать проект").disabled).toBe(false);
    await create(); expect(mocks.create).toHaveBeenCalledTimes(2);
  } finally { await page.dispose(); }
});

it("switching to an example is deliberate and does not inherit old colors/features", async () => {
  const page = await renderStudio();
  try {
    await initial(); await click("Далее"); await click("Далее"); await fill("#max-brand","старый красный");
    await act(async () => document.querySelector<HTMLButtonElement>('.max-feature-chip__toggle')!.click());
    await click("Назад"); await click("Назад"); await click("Отмена");
    const example = [...document.querySelectorAll<HTMLButtonElement>(".max-studio-idea")].find(b => b.textContent?.includes("Барбершоп"))!;
    const confirm = vi.spyOn(window,"confirm").mockReturnValue(false);
    await act(async () => example.click()); expect(readMaxProjectDraft("qa@example.test")?.brandColors).toBe("старый красный");
    confirm.mockReturnValue(true); await act(async () => example.click());
    expect(input("#max-project-name").value).toBe("Барбершоп"); await click("Далее"); await click("Далее");
    expect(input("#max-brand").value).toBe(""); expect(document.querySelectorAll('.max-feature-chip[data-selected="true"]')).toHaveLength(0);
    expect(mocks.create).not.toHaveBeenCalled();
  } finally { await page.dispose(); }
});

it("actual confirmed applyIdea survives completion of an earlier same-owner pending create", async () => {
  let resolve!: (value: { id: string }) => void;
  mocks.create.mockImplementation(() => new Promise(r => { resolve = r; }));
  const page = await renderStudio();
  try {
    await initial("Создающийся первый продукт A");
    const old = readMaxProjectDraft("qa@example.test")!;
    await click("Далее"); await click("Далее"); await click("Далее"); await click("Создать проект");
    // Keep the pending mutation alive, but mount a second same-owner view as
    // another tab reading the shared durable draft would do.
    await page.hide(); await page.render("qa@example.test"); await click("Отмена");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await act(async () => [...document.querySelectorAll<HTMLButtonElement>(".max-studio-idea")].find(b => b.textContent?.includes("Барбершоп"))!.click());
    const replacement = readMaxProjectDraft("qa@example.test")!;
    expect(replacement.id).not.toBe(old.id); expect(replacement.name).toBe("Барбершоп");
    await act(async () => resolve({ id: "created-A" }));
    expect(readMaxProjectDraft("qa@example.test")).toEqual(replacement);
    expect(input("#max-project-name").value).toBe("Барбершоп");
    expect(saveMaxProjectDraft("qa@example.test", old)).toBe(false);
    expect(mocks.create).toHaveBeenCalledOnce(); expect(mocks.push).not.toHaveBeenCalled();
  } finally { await page.dispose(); }
});

it("a conflicting newer stored idea refuses confirmed replacement without changing either draft", async () => {
  const page = await renderStudio();
  try {
    await initial("Старый черновик A"); const old = readMaxProjectDraft("qa@example.test")!;
    await click("Отмена");
    const newer = { ...emptyMaxProjectDraft(), name: "Новый C", idea: "Изменение из другой вкладки" };
    vi.spyOn(window, "confirm").mockImplementation(() => {
      expect(replaceMaxProjectDraft("qa@example.test", old.id, newer)).toBe(true);
      return true;
    });
    await act(async () => document.querySelector<HTMLButtonElement>(".max-studio-idea")!.click());
    expect(readMaxProjectDraft("qa@example.test")).toEqual(newer);
    expect(document.querySelector("#max-project-name")).toBeNull();
    expect(mocks.warning).toHaveBeenCalledWith("Не удалось заменить черновик", expect.objectContaining({ description: expect.stringContaining("Текущие ответы не изменены") }));
    expect(mocks.create).not.toHaveBeenCalled();
  } finally { await page.dispose(); }
});

it.each([false, true])("reviews and saves explicit menu prices without requiring a catalog checkbox (edited: %s)", async edited => {
  mocks.create.mockResolvedValue({ id: "priced-menu" }); const page = await renderStudio();
  try {
    await initial("Меню: Латте — 300 ₽; Капучино — 200 ₽"); await click("Далее"); await click("Далее");
    expect(module("Каталог").checked).toBe(false);
    await click("Далее");
    const rows = [...document.querySelectorAll(".max-initial-catalog__row")];
    expect(rows).toHaveLength(2);
    expect(rows.map(row => row.querySelector<HTMLInputElement>('input[id$="-title"]')!.value)).toEqual(["Латте", "Капучино"]);
    expect(rows.map(row => row.querySelector<HTMLInputElement>('input[id$="-price"]')!.value)).toEqual(["300 ₽", "200 ₽"]);
    if (edited) await act(async () => change(rows[0].querySelector<HTMLInputElement>('input[id$="-price"]')!, "325 ₽"));
    await create();
    const payload = mocks.create.mock.calls[0][0];
    expect(payload.max_config.content.map((item: {title:string;price:string}) => [item.title,item.price])).toEqual([["Латте", edited ? "325 ₽" : "300 ₽"], ["Капучино", "200 ₽"]]);
    expect(payload.management_module_opt_outs).toEqual([]);
    expect(payload.max_config.features).toEqual([]);
    const prompt = sessionStorage.getItem("omnia:max:starter:priced-menu")!;
    expect(prompt).toContain("2 позиций уже сохранены в Yleum");
    expect(prompt).not.toContain("Управление в Yleum:");
  } finally { await page.dispose(); }
});

it("retains an explicitly cleared parsed catalog after reload without selecting a module", async () => {
  mocks.create.mockResolvedValue({ id: "cleared-menu" }); let page = await renderStudio();
  try {
    await initial("Меню: Латте — 300 ₽; Капучино — 200 ₽"); await click("Далее"); await click("Далее"); await click("Далее");
    expect(document.querySelectorAll(".max-initial-catalog__row")).toHaveLength(2);
    await click("Начать с пустого каталога");
    expect(readMaxProjectDraft("qa@example.test")?.catalogDraft?.items).toEqual([]);
    await page.dispose(); page = await renderStudio();
    await click("Далее"); await click("Далее"); expect(module("Каталог").checked).toBe(false); await click("Далее");
    expect(document.querySelectorAll(".max-initial-catalog__row")).toHaveLength(0);
    expect(button("Начать с пустого каталога")).toBeDefined();
    await create();
    expect(mocks.create.mock.calls[0][0].max_config.content).toEqual([]);
    const prompt = sessionStorage.getItem("omnia:max:starter:cleared-menu")!;
    expect(prompt).toContain("владелец подтвердил пустой каталог");
    expect(prompt).not.toContain("Управление в Yleum:");
  } finally { await page.dispose(); }
});

it("requires an explicit resolution of an overflowing parsed menu without a catalog checkbox", async () => {
  mocks.create.mockResolvedValue({ id: "overflow-menu" }); const page = await renderStudio();
  try {
    await initial("Меню: " + Array.from({ length: 101 }, (_, index) => `Товар ${index + 1} — 300 ₽`).join("; "));
    await click("Далее"); await click("Далее"); expect(module("Каталог").checked).toBe(false); await click("Далее");
    expect(document.querySelectorAll(".max-initial-catalog__row")).toHaveLength(100);
    expect(button("Создать проект").disabled).toBe(true);
    expect(mocks.create).not.toHaveBeenCalled();
    await click("Начать с пустого каталога"); expect(button("Создать проект").disabled).toBe(false);
    await create();
    expect(mocks.create.mock.calls[0][0].max_config.content).toEqual([]);
    expect(sessionStorage.getItem("omnia:max:starter:overflow-menu")).toContain("владелец подтвердил пустой каталог");
  } finally { await page.dispose(); }
});
