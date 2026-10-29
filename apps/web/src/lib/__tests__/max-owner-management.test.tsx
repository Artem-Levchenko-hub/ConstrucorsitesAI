import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { onlineManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import contract from "../../../../api/tests/fixtures/max_management_contract_v1.json";
import { MaxOwnerConsole } from "@/components/max/MaxOwnerConsole";
import { MaxOwnerRecords } from "@/components/max/MaxOwnerRecords";
import { MaxOwnerSettings } from "@/components/max/MaxOwnerSettings";
import { MaxProjectNav } from "@/components/max/MaxProjectNav";
import type { ManagementOverview, OwnerRecord, ResourceDefinition } from "@/lib/api/max-management";
import type { MaxProjectConfig } from "@/lib/api/types";

vi.mock("@/components/max/MaxSectionShell", () => ({ MaxSectionShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));
vi.mock("@/components/max/MaxOwnerCatalog", () => ({ MaxOwnerCatalog: ({ embedded }: { embedded?: boolean }) => <div data-embedded={embedded}>Каталог до 100 позиций</div> }));
vi.mock("@/components/max/MaxOwnerAnalytics", () => ({ MaxOwnerAnalytics: () => <div>Статистика приложения</div> }));
const resources = contract.resources as ResourceDefinition[];
const first: OwnerRecord = { id: "00000000-0000-4000-8000-000000000001", resource: "orders", created_at: "2026-10-29T09:00:00Z", revision: "a".repeat(64), status: "new", customer_name: "<img src=x onerror=alert(1)>", contact_phone: null, contact_email: "client@example.test", comment: "<script>private()</script>", requested_for: null, service_title: null, service_price_label: null, items: [{ catalog_id: "old-id", title: "Историческая услуга", quantity: 2, unit_price_kopecks: 12345, option: "30 минут" }], total_kopecks: 24690, currency: "RUB", note: "" };
let overview: ManagementOverview;
let config: MaxProjectConfig;
let requests: Array<{ url: string; init: RequestInit }>;
let writeMode: "ok" | "network" | "conflict" | "pending";
let enableMode: "ok" | "network" | "conflict";
let resolveWrite: ((response: Response) => void) | undefined;
let root: Root; let node: HTMLDivElement; let client: QueryClient;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  overview = { state: "active", configured_modules: ["catalog", "orders"], active_modules: ["catalog", "orders"], resources: [resources[0]], publication_id: "published", contract_hash: contract.contract_hash, catalog_limit: 100 };
  config = { project_id: "owned", config_version: 7, config: { app_name: "Приложение", summary: "Описание", app_type: "custom", audience: "Клиенты", primary_action: "Написать", features: ["Другое"], style: "clean", brand_colors: "blue", content: [], management_modules: ["catalog", "orders"], operator: { legal_name: "ИП" }, support: { email: "old@example.test", response_time: "24 часа" }, legal: { age_rating: "0+", has_sales: false, has_user_content: false, marketing_notifications: false, personal_data_consent: false, policy_url: "", terms_accepted: true }, max_url_attached: false }, synced_snapshot_id: null, updated_at: null };
  requests = []; writeMode = "ok"; enableMode = "ok"; resolveWrite = undefined;
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    requests.push({ url, init });
    if (url.endsWith("/management/enable") && init.method === "POST") {
      if (enableMode === "network") throw new TypeError("offline");
      if (enableMode === "conflict") return Response.json({ error: { code: "conflict", message: "private diagnostic" } }, { status: 409 });
      config = { ...config, config_version: config.config_version + 1,
        config: { ...config.config, management_modules: ["catalog", "orders", "leads"] } };
      overview = { ...overview, configured_modules: ["catalog", "orders", "leads"] };
      return Response.json(config);
    }
    if (init.method === "PATCH") {
      if (writeMode === "network") throw new TypeError("offline");
      if (writeMode === "conflict") return Response.json({ error: { code: "revision_conflict", message: "stale" } }, { status: 409 });
      if (writeMode === "pending") return new Promise<Response>(resolve => { resolveWrite = resolve; });
      return Response.json({ ...first, ...JSON.parse(init.body as string), revision: "b".repeat(64) });
    }
    if (url.endsWith("/content")) return Response.json({config_version:config.config_version,public:{state:"pending",desired_config_version:config.config_version},preview:{state:"not_created",desired_config_version:config.config_version}});
    if (url.endsWith("/config")) {
      if (init.method === "PUT") return Response.json({ ...config, config_version: 8, config: JSON.parse(init.body as string) });
      return Response.json(config);
    }
    if (url.includes("/records?")) {
      const query = new URL(url, "https://yleum.test").searchParams;
      return Response.json({ records: query.has("cursor") ? [{ ...first, id: "second", customer_name: "Вторая страница" }] : [first, { ...first, id: "another", customer_name: "Другой клиент" }], next_cursor: query.has("cursor") ? null : "cursor+safe/=" });
    }
    if (url.endsWith("/readiness")) return Response.json({ items: [] });
    return Response.json(overview);
  }));
});
afterEach(async () => { await act(async () => root.unmount()); node.remove(); client.clear(); onlineManager.setOnline(true); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); }); }
async function render(element: React.ReactNode) { await act(async () => root.render(<QueryClientProvider client={client}>{element}</QueryClientProvider>)); await settle(); }
function button(label: string) { return [...node.querySelectorAll<HTMLButtonElement>("button")].find(element => element.textContent?.trim() === label)!; }
async function click(label: string) { expect(button(label), label).toBeTruthy(); await act(async () => button(label).click()); await settle(); }
async function change(label: string, value: string) {
  await act(async () => {
    const field = node.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(`[aria-label="${label}"]`)!;
    expect(field, label).toBeTruthy();
    const proto = field.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : field.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(field, value);
    field.dispatchEvent(new Event(field.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  }); await settle();
}
function patches() { return requests.filter(item => item.init.method === "PATCH"); }
function records() { return requests.filter(item => item.url.includes("/records?")); }
async function mountRecords(resource = resources[0]) { await render(<MaxOwnerRecords projectId="owned" resource={resource} />); }

it("links to management from project navigation", async () => {
  await render(<MaxProjectNav projectId="owned" active="manage" showProgress={false} />);
  expect(node.querySelector('a[href="/max/owned/manage"]')?.textContent).toContain("Управление");
});
it("waits for the section overview before mounting an editable default section", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  let resolveOverview: ((response: Response) => void) | undefined;
  vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith("/management") ? new Promise<Response>(resolve => { resolveOverview = resolve; }) : original(url, init));
  await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />);
  expect(node.textContent).toContain("Загружаем разделы");
  expect(node.querySelector('[aria-label="Название приложения"]')).toBeNull();
  await act(async () => resolveOverview!(Response.json(overview))); await settle();
  expect(button("Заказы")).toBeTruthy();
});
it("shows only enabled relevant resources, embedded catalog and baseline sections", async () => {
  await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />);
  expect(button("Заказы")).toBeTruthy(); expect(button("Заявки")).toBeUndefined(); expect(button("Записи")).toBeUndefined();
  await click("Каталог"); expect(node.querySelector('[data-embedded="true"]')).not.toBeNull();
  await click("Настройки"); expect(node.textContent).toContain("Название приложения");
  await click("Статистика"); expect(node.textContent).toContain("Статистика приложения");
});
it.each(["not_published", "unsupported", "unavailable"] as const)("explains %s without fetching a fake empty register", async state => {
  overview = { ...overview, state, active_modules: [], resources: [] };
  await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />);
  expect(records()).toHaveLength(0);
  expect(node.textContent).not.toContain("Обращений пока нет");
  expect(node.textContent).toMatch(state === "not_published" ? /после публикации/i : state === "unsupported" ? /не поддерживает/i : /недоступн/i);
  expect(button("Настройки")).toBeTruthy(); expect(button("Каталог")).toBeTruthy();
});
function legacyUnsupported() {
  overview = { ...overview, state: "unsupported", configured_modules: [], active_modules: [], resources: [] };
  config = { ...config, config: { ...config.config, app_type: "catalog", management_modules: null } };
}
function enables() { return requests.filter(request => request.url.endsWith("/management/enable")); }
it("prepares legacy management only on an explicit click using a fresh version and preserves the mounted catalog", async () => {
  legacyUnsupported(); await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />);
  const catalog = node.querySelector('[data-embedded="true"]');
  expect(enables()).toHaveLength(0);
  config = { ...config, config_version: 9 };
  await click("Подключить управление");
  expect(enables()).toHaveLength(1);
  expect(JSON.parse(enables()[0].init.body as string)).toEqual({ expected_config_version: 9 });
  expect(node.textContent).toContain("Подготовлено к доработке");
  expect(button("Подключить управление")).toBeUndefined();
  expect(node.querySelector('[data-embedded="true"]')).toBe(catalog);
  expect(node.querySelector('a[href="/max/owned"]')?.textContent).toContain("редактор");
  expect(requests.filter(request => request.init.method === "POST")).toEqual(enables());
  expect(records()).toHaveLength(0);
});
it("does not repeat preparation when an unsupported app already has configured business modules", async () => {
  overview = { ...overview, state: "unsupported", active_modules: [], resources: [] };
  await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />);
  expect(node.textContent).toContain("Подготовлено к доработке");
  expect(button("Подключить управление")).toBeUndefined();
  expect(enables()).toHaveLength(0);
});
it("retains the section and retries a preparation conflict only with another click and fresh version", async () => {
  legacyUnsupported(); enableMode = "conflict";
  await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />);
  const catalog = node.querySelector('[data-embedded="true"]');
  await click("Подключить управление");
  expect(enables()).toHaveLength(1);
  expect(node.textContent).not.toContain("private diagnostic");
  expect(node.textContent).toContain("Повторите попытку");
  expect(node.querySelector('[data-embedded="true"]')).toBe(catalog);
  config = { ...config, config_version: 8 }; enableMode = "ok";
  await click("Подключить управление");
  expect(enables()).toHaveLength(2);
  expect(JSON.parse(enables()[1].init.body as string)).toEqual({ expected_config_version: 8 });
});
it("blocks preparation while settings contain an unsaved draft", async () => {
  legacyUnsupported(); await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />);
  await click("Настройки"); await change("Название приложения", "Не потерять");
  expect(button("Подключить управление").disabled).toBe(true);
  await click("Подключить управление");
  expect(enables()).toHaveLength(0);
  expect((node.querySelector('[aria-label="Название приложения"]') as HTMLInputElement).value).toBe("Не потерять");
});
it("rechecks dirty state after the fresh read before sending the enable POST", async () => {
  legacyUnsupported(); await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />); await click("Настройки");
  const original = vi.mocked(fetch).getMockImplementation()!;
  let release: ((response: Response) => void) | undefined;
  vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith("/config")
    ? new Promise<Response>(resolve => { release = resolve; }) : original(url, init));
  await click("Подключить управление");
  await change("Название приложения", "Черновик во время чтения");
  await act(async () => release!(Response.json(config))); await settle();
  expect(enables()).toHaveLength(0);
  expect((node.querySelector('[aria-label="Название приложения"]') as HTMLInputElement).value).toBe("Черновик во время чтения");
});
it("keeps the current settings section and a draft created while enable POST is in flight", async () => {
  legacyUnsupported(); config = { ...config, config: { ...config.config, app_type: "custom" } };
  await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />);
  const original = vi.mocked(fetch).getMockImplementation()!;
  let release: ((response: Response) => void) | undefined;
  vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith("/management/enable")
    ? new Promise<Response>(resolve => { requests.push({ url: String(url), init: init ?? {} }); release = resolve; }) : original(url, init));
  await click("Подключить управление");
  await change("Название приложения", "Черновик во время подключения");
  config = { ...config, config_version: 8, config: { ...config.config, management_modules: ["catalog", "leads"] } };
  overview = { ...overview, configured_modules: ["catalog", "leads"] };
  await act(async () => release!(Response.json(config))); await settle();
  expect(node.querySelector('[aria-label="Название приложения"]')).not.toBeNull();
  expect((node.querySelector('[aria-label="Название приложения"]') as HTMLInputElement).value).toBe("Черновик во время подключения");
  expect(enables()).toHaveLength(1);
});
it("does not grant configured draft resources active access or show diary business modules", async () => {
  overview = { ...overview, configured_modules: ["catalog", "orders", "leads", "bookings"], active_modules: ["leads"], resources: [resources[1]] };
  await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />);
  expect(button("Заявки")).toBeTruthy(); expect(button("Заказы")).toBeUndefined(); expect(button("Записи")).toBeUndefined();
  overview = { ...overview, configured_modules: [], active_modules: [], resources: [] };
  client.clear(); await render(<MaxOwnerConsole projectId="diary" projectName="Дневник" />);
  expect(button("Заказы")).toBeUndefined(); expect(button("Заявки")).toBeUndefined(); expect(button("Записи")).toBeUndefined();
});
it("paginates and resets its opaque cursor for search/status/local-day filters", async () => {
  await mountRecords(); expect(new URL(records()[0].url, "https://test").searchParams.get("limit")).toBe("25");
  await click("Следующая страница"); expect(node.textContent).toContain("Вторая страница");
  expect(new URL(records().at(-1)!.url, "https://test").searchParams.get("cursor")).toBe("cursor+safe/=");
  await change("Поиск обращений", "Клиент");
  expect(new URL(records().at(-1)!.url, "https://test").searchParams.has("cursor")).toBe(false);
  await change("Фильтр статуса", "in_progress"); await change("С даты", "2026-10-28"); await change("По дату", "2026-10-29");
  const params = new URL(records().at(-1)!.url, "https://test").searchParams;
  expect(params.get("query")).toBe("Клиент"); expect(params.get("status")).toBe("in_progress");
  expect(params.get("created_from")).toBe(new Date(2026, 9, 28).toISOString());
  expect(params.get("created_to")).toBe(new Date(2026, 9, 29, 23, 59, 59, 999).toISOString());
  const count = records().length; await change("С даты", "2026-10-30");
  expect(node.querySelector('[role="alert"]')?.textContent).toMatch(/начала.*позже/i); expect(records()).toHaveLength(count);
});
it("renders immutable historical snapshots and escaped customer text with only permitted status changes", async () => {
  await mountRecords(); await click(first.customer_name);
  expect(node.querySelector("img")).toBeNull(); expect(node.querySelector("script")).toBeNull();
  expect(node.textContent).toContain(first.comment); expect(node.textContent).toContain("Историческая услуга");
  expect(node.textContent).toContain("246,90"); expect(node.textContent).toContain("123,45");
  const options = [...node.querySelectorAll('select[aria-label="Статус обращения"] option')].map(option => option.textContent);
  expect(options).toEqual(["Новый", "В работе", "Отменён"]);
  expect(node.querySelector('input[value="Историческая услуга"]')).toBeNull();
  await change("Статус обращения", "in_progress"); await change("Заметка владельца", "Позвонить"); await click("Сохранить обращение");
  expect(JSON.parse(patches()[0].init.body as string)).toMatchObject({ expected_revision: first.revision, status: "in_progress", note: "Позвонить" });
  expect(patches()[0].url).toContain(`/records/${first.id}?resource=orders`);
  expect(node.textContent).toContain("Изменения сохранены");
});
it("renders pickup fulfillment as readonly server data and leaves legacy orders unchanged", async () => {
  const scheduledFor = "2026-11-02T13:45:00Z";
  const orders: OwnerRecord[] = [first,
    { ...first, id: "pickup-now", customer_name: "Самовывоз сейчас", fulfillment: { type: "pickup", delay_minutes: 0, scheduled_for: scheduledFor } },
    { ...first, id: "pickup-later", customer_name: "Самовывоз позже", fulfillment: { type: "pickup", delay_minutes: 15, scheduled_for: scheduledFor } }];
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation((url, init) => String(url).includes("/records?")
    ? Promise.resolve(Response.json({ records: orders, next_cursor: null })) : original(url, init));
  await mountRecords(); await click(first.customer_name);
  expect(node.querySelector(".max-owner-record-card")?.textContent).not.toContain("Самовывоз");
  await click("Самовывоз сейчас");
  expect(node.querySelector(".max-owner-record-card")?.textContent).toContain("Самовывоз: как можно скорее");
  await click("Самовывоз позже");
  const card = node.querySelector(".max-owner-record-card")!;
  expect(card.textContent).toContain("Самовывоз к");
  expect(card.querySelector(`time[datetime="${scheduledFor}"]`)?.textContent).toBe(new Date(scheduledFor).toLocaleString("ru-RU"));
  expect(card.querySelector('input[type="datetime-local"]')).toBeNull();
  expect([...card.querySelectorAll("input,select,textarea")].some(control => /самовывоз|получени/i.test(control.getAttribute("aria-label") ?? ""))).toBe(false);
  await change("Заметка владельца", "Учесть самовывоз"); await click("Сохранить обращение");
  expect(JSON.parse(patches()[0].init.body as string)).not.toHaveProperty("fulfillment");
});
it("retains the draft and the same operation key after network errors and revision conflicts", async () => {
  await mountRecords(); await click(first.customer_name); await change("Заметка владельца", "Не потерять"); writeMode = "network";
  await click("Сохранить обращение"); expect(patches()).toHaveLength(1);
  const original = JSON.parse(patches()[0].init.body as string);
  expect((node.querySelector('[aria-label="Заметка владельца"]') as HTMLTextAreaElement).value).toBe("Не потерять");
  writeMode = "conflict"; await click("Сохранить обращение");
  expect(JSON.parse(patches()[1].init.body as string)).toEqual(original);
  expect(node.textContent).toContain("изменилось"); expect(node.textContent).toContain("черновик");
  await click("Сохранить обращение"); expect(JSON.parse(patches()[2].init.body as string)).toEqual(original);
  await click("Загрузить актуальную версию");
  expect((node.querySelector('[aria-label="Заметка владельца"]') as HTMLTextAreaElement).value).toBe("Не потерять");
  expect(node.textContent).toContain("Применить к актуальной версии");
});
it.each([
  { label: "malformed JSON", response: () => new Response('{"id":', { status: 200, headers: { "Content-Type": "application/json" } }) },
  { label: "terminated body", response: () => new Response(new ReadableStream({ start(controller) { controller.error(new TypeError("terminated")); } }), { status: 200, headers: { "Content-Type": "application/json" } }) },
  { label: "body read timeout", response: () => new Response(new ReadableStream({ start(controller) { controller.error(new DOMException("body timed out", "TimeoutError")); } }), { status: 200, headers: { "Content-Type": "application/json" } }) },
])("preserves the original write receipt after successful PATCH headers but $label", async ({ response }) => {
  const originalFetch = vi.mocked(fetch).getMockImplementation()!;
  let loseResponse = true;
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    if (init?.method === "PATCH" && loseResponse) {
      requests.push({ url: String(url), init });
      // The server may already have committed; only consuming its 200 body fails.
      return response();
    }
    return originalFetch(url, init);
  });
  await mountRecords(); await click(first.customer_name);
  await change("Статус обращения", "in_progress"); await change("Заметка владельца", "Уже могло сохраниться");
  await click("Сохранить обращение");
  const operation = JSON.parse(patches()[0].init.body as string);
  const note = node.querySelector<HTMLTextAreaElement>('[aria-label="Заметка владельца"]')!;
  expect(note.value).toBe("Уже могло сохраниться"); expect(note.disabled).toBe(true);
  expect(node.querySelector<HTMLSelectElement>('[aria-label="Статус обращения"]')!.disabled).toBe(true);
  expect(button("Отменить правки").disabled).toBe(true);
  expect(button("Сохранить обращение").disabled).toBe(false);
  expect(node.textContent).toContain("Неизвестно, сохранились ли изменения");
  expect(node.textContent).not.toContain("Изменения сохранены");
  await settle(); expect(patches()).toHaveLength(1);
  loseResponse = false; await click("Сохранить обращение");
  expect(JSON.parse(patches()[1].init.body as string)).toEqual(operation);
  expect(node.textContent).toContain("Изменения сохранены");
  expect(node.querySelector<HTMLTextAreaElement>('[aria-label="Заметка владельца"]')!.disabled).toBe(false);
  expect(node.textContent).not.toContain("Неизвестно, сохранились ли изменения");
});
it("guards dirty row/filter changes and duplicate writes; stale project completion cannot change new project", async () => {
  await mountRecords(); await click(first.customer_name); await change("Заметка владельца", "Черновик");
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  await click("Другой клиент"); expect(confirm).toHaveBeenCalled();
  expect((node.querySelector('[aria-label="Заметка владельца"]') as HTMLTextAreaElement).value).toBe("Черновик");
  const before = records().length; await change("Поиск обращений", "другой"); expect(records()).toHaveLength(before);
  writeMode = "pending"; await click("Сохранить обращение"); expect(button("Сохранить обращение").disabled).toBe(true);
  await render(<MaxOwnerRecords projectId="other" resource={resources[0]} />);
  await act(async () => resolveWrite!(Response.json({ ...first, note: "Черновик" }))); await settle();
  expect(node.textContent).not.toContain("Изменения сохранены");
});
it("uses booking registry labels and never reopens a terminal status", async () => {
  const booking = { ...first, resource: "bookings", status: "completed", service_title: "Приём", service_price_label: "По запросу", requested_for: "2026-11-01T10:00:00Z", items: [], total_kopecks: null, currency: null };
  vi.mocked(fetch).mockImplementation(async () => Response.json({ records: [booking], next_cursor: null }));
  await mountRecords(resources[2]); await click(first.customer_name);
  expect(node.textContent).toContain("По запросу");
  expect([...node.querySelectorAll('select[aria-label="Статус обращения"] option')].map(option => option.textContent)).toEqual(["Завершена"]);
});
it("explains offline, server errors and empty results separately and retries only on request", async () => {
  onlineManager.setOnline(false); await mountRecords();
  expect(node.textContent).toContain("Нет соединения"); expect(records()).toHaveLength(0);
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (url, init) => String(url).includes("/records?") ? Response.json({ error: { code: "unavailable", message: "private runtime diagnostic" } }, { status: 503 }) : original(url, init));
  await act(async () => onlineManager.setOnline(true)); await settle();
  expect(node.textContent).toContain("недоступно"); expect(node.textContent).not.toContain("Обращений пока нет"); expect(node.textContent).not.toContain("private runtime diagnostic");
  const count = records().length; await settle(); expect(records()).toHaveLength(count);
  vi.mocked(fetch).mockImplementation(async () => Response.json({ records: [], next_cursor: null }));
  await click("Повторить загрузку"); expect(node.textContent).toContain("Обращений пока нет");
});
it("keeps retries identical until a definite error is edited, then creates a fresh logical operation", async () => {
  await mountRecords(); await click(first.customer_name); await change("Заметка владельца", "Первый вариант");
  writeMode = "conflict"; await click("Сохранить обращение");
  const firstWrite = JSON.parse(patches()[0].init.body as string);
  await change("Заметка владельца", "Второй вариант"); await click("Сохранить обращение");
  const secondWrite = JSON.parse(patches()[1].init.body as string);
  expect(secondWrite.operation_key).not.toBe(firstWrite.operation_key); expect(secondWrite.note).toBe("Второй вариант");
  await click("Загрузить актуальную версию"); await click("Применить к актуальной версии");
  writeMode = "ok"; await click("Сохранить обращение");
  expect(JSON.parse(patches()[2].init.body as string).operation_key).not.toBe(secondWrite.operation_key);
});
it("blocks a dirty settings section switch until the owner explicitly discards the draft", async () => {
  await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />); await click("Настройки");
  await change("Название приложения", "Не потерять");
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  await click("Статистика"); expect(confirm).toHaveBeenCalled();
  expect((node.querySelector('[aria-label="Название приложения"]') as HTMLInputElement).value).toBe("Не потерять");
  confirm.mockReturnValue(true); await click("Статистика"); expect(node.textContent).toContain("Статистика приложения");
});
it("preserves settings after version conflicts and explicitly rebases over all latest unrelated fields", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  let conflict = true;
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    if (init?.method === "PUT" && conflict) { requests.push({ url: String(url), init }); return Response.json({ error: { code: "conflict", message: "stale" } }, { status: 409 }); }
    return original(url, init);
  });
  await render(<MaxOwnerSettings projectId="owned" />); await change("Email поддержки", "new@example.test"); await click("Сохранить настройки");
  expect((node.querySelector('[aria-label="Email поддержки"]') as HTMLInputElement).value).toBe("new@example.test");
  config = { ...config, config_version: 8, config: { ...config.config, audience: "Новая аудитория", support: { ...config.config.support, response_time: "Новый срок" } } };
  await click("Загрузить актуальные настройки"); await click("Перенести мои настройки"); conflict = false; await click("Сохранить настройки");
  const write = requests.filter(item => item.init.method === "PUT").at(-1)!;
  expect(write.init.headers).toMatchObject({ "X-Max-Config-Version": "8" });
  expect(JSON.parse(write.init.body as string)).toMatchObject({ audience: "Новая аудитория", support: { email: "new@example.test", response_time: "Новый срок" } });
});
it("saves only supported settings over the captured full-config baseline and version", async () => {
  await render(<MaxOwnerSettings projectId="owned" />);
  const original = structuredClone(config.config);
  await change("Название приложения", "Новое название"); await change("Описание приложения", "Новое описание"); await change("Email поддержки", "support@example.test");
  client.setQueryData(["max-config", "owned"], { ...config, config_version: 99, config: { ...config.config, audience: "Другие" } });
  await click("Сохранить настройки");
  const write = requests.find(item => item.init.method === "PUT")!;
  expect(write.init.headers).toMatchObject({ "X-Max-Config-Version": "7" });
  expect(JSON.parse(write.init.body as string)).toEqual({ ...original, app_name: "Новое название", summary: "Новое описание", support: { ...original.support, email: "support@example.test" } });
  expect(node.textContent).toContain("Настройки сохранены в Yleum");
  expect(node.textContent).not.toContain("Приложение обновлено");
});

it("booking dates target appointments and display service and requested time", async () => {
  const booking = {...first, resource:"bookings",requested_for:"2030-06-10T08:00:00Z",service_title:"Маникюр"};
  const original=vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation((url,init)=>String(url).includes("/records?") ? Promise.resolve(Response.json({records:[booking],next_cursor:null})) : original(url,init));
  await mountRecords(resources[2]);
  await change("Дата записи с", "2030-06-10");
  const query=new URL(String(vi.mocked(fetch).mock.calls.filter(c=>String(c[0]).includes("/records?")).at(-1)![0]),"https://yleum.test").searchParams;
  expect(query.get("requested_from")).toBe(new Date(2030,5,10).toISOString());
  expect(query.has("created_from")).toBe(false);
  expect(node.textContent).toContain("Маникюр");
  expect(node.querySelector('time')?.dateTime).toBe(booking.requested_for);
});

it("settings observe whole-config confirmation and reapply the same saved version", async () => {
  let applied=false;
  const original=vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation((url,init)=>String(url).endsWith("/content") ? Promise.resolve(Response.json({config_version:8,public:{state:applied?"applied":"unknown",desired_config_version:8,confirmed_config_version:applied?8:null},preview:{state:"not_created",desired_config_version:8}})) : original(url,init));
  await render(<MaxOwnerSettings projectId="owned" />);
  await change("Название приложения","Новое имя"); await click("Сохранить настройки");
  expect(node.textContent).toContain("Применение не подтверждено");
  const firstSave=requests.find(r=>r.init.method==="PUT")!;
  applied=true;
  await click("Повторить применение");
  const saves=requests.filter(r=>r.init.method==="PUT");
  expect(saves).toHaveLength(2); expect(saves[1].init.body).toBe(firstSave.init.body);
  expect(new Headers(saves[1].init.headers).get("X-Max-Config-Version")).toBe("8");
  expect(node.textContent).toContain("Применение подтверждено");
  expect(requests.some(r=>/generate|snapshot|publish$/.test(r.url))).toBe(false);
});

it("settings keep saved version visible when application retry fails and after reopening", async () => {
  config = {...config,config_version:8};
  const original=vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation((url,init)=>init?.method==="PUT" ? Promise.reject(new TypeError("offline")) : original(url,init));
  await render(<MaxOwnerSettings projectId="owned" />);
  await click("Повторить применение");
  expect(node.textContent).toContain("Версия 8");
  expect(node.querySelector('[role="alert"]')).not.toBeNull();
  expect(node.textContent).not.toContain("Применение подтверждено");
  expect(button("Повторить применение").disabled).toBe(false);
  await act(async()=>root.render(<QueryClientProvider client={client}><div /></QueryClientProvider>));
  await render(<MaxOwnerSettings projectId="owned" />);
  expect(node.textContent).toContain("Версия 8");
  expect(node.textContent).toContain("Применяются");
});
