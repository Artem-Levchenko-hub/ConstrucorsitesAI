import { act } from "react";
import Link from "next/link";
import { createRoot, type Root } from "react-dom/client";
import { onlineManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import contract from "../../../../api/tests/fixtures/max_management_contract_v1.json";
import { MaxOwnerConsole } from "@/components/max/MaxOwnerConsole";
import { MaxOwnerRecords } from "@/components/max/MaxOwnerRecords";
import { MaxProjectNav } from "@/components/max/MaxProjectNav";
import type { ManagementOverview, OwnerRecord, ResourceDefinition } from "@/lib/api/max-management";
import type { MaxProjectConfig } from "@/lib/api/types";
import type { TeamView } from "@/lib/api/max-staff";

vi.mock("@/components/max/MaxSectionShell", () => ({ MaxSectionShell: ({ children, projectId }: { children: React.ReactNode; projectId: string }) => (
  <div className="max-project-workspace">
    <Link href={`/max/${projectId}`}>К чату</Link>
    <MaxProjectNav projectId={projectId} active="manage" showProgress={false} variant="mobile" />
    <main>{children}</main>
  </div>
) }));
vi.mock("@/components/max/MaxOwnerCatalog", () => ({ MaxOwnerCatalog: ({ embedded }: { embedded?: boolean }) => <div data-embedded={embedded}>Каталог до 100 позиций</div> }));
const resources = contract.resources as ResourceDefinition[];
const first: OwnerRecord = { id: "00000000-0000-4000-8000-000000000001", resource: "orders", created_at: "2026-10-29T09:00:00Z", revision: "a".repeat(64), status: "new", customer_name: "<img src=x onerror=alert(1)>", contact_phone: null, contact_email: "client@example.test", comment: "<script>private()</script>", requested_for: null, service_title: null, service_price_label: null, items: [{ catalog_id: "old-id", title: "Историческая услуга", quantity: 2, unit_price_kopecks: 12345, option: "30 минут" }], total_kopecks: 24690, currency: "RUB", note: "" };
let overview: ManagementOverview;
let config: MaxProjectConfig;
let team: TeamView;
let requests: Array<{ url: string; init: RequestInit }>;
let writeMode: "ok" | "network" | "conflict" | "pending";
let enableMode: "ok" | "network" | "conflict";
let resolveWrite: ((response: Response) => void) | undefined;
let root: Root; let node: HTMLDivElement; let client: QueryClient;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  overview = { state: "active", configured_modules: ["catalog", "orders"], active_modules: ["catalog", "orders"], resources: [resources[0]], publication_id: "published", contract_hash: contract.contract_hash, catalog_limit: 100 };
  team = { settings: { leads_enabled: false, orders_mode: null, prep_minutes: 7 }, members: [], invitations: [] };
  config = { project_id: "owned", config_version: 7, config: { app_name: "Приложение", summary: "Описание", app_type: "custom", audience: "Клиенты", primary_action: "Написать", features: ["Другое"], style: "clean", brand_colors: "blue", content: [], management_modules: ["catalog", "orders"], operator: { legal_name: "ИП" }, support: { email: "old@example.test", response_time: "24 часа" }, legal: { age_rating: "0+", has_sales: false, has_user_content: false, marketing_notifications: false, personal_data_consent: false, policy_url: "", terms_accepted: true }, max_url_attached: false }, synced_snapshot_id: null, updated_at: null };
  requests = []; writeMode = "ok"; enableMode = "ok"; resolveWrite = undefined;
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    requests.push({ url, init });
    if (url.endsWith("/max/team")) return Response.json(team);
    if (url.endsWith("/team/settings") && init.method === "PUT") {
      team = { ...team, settings: JSON.parse(String(init.body)) };
      return Response.json(team);
    }
    if (url.endsWith("/team/invitations") && init.method === "POST") {
      const input = JSON.parse(String(init.body));
      const expires_at = "2026-11-08T09:00:00Z";
      team = { ...team, invitations: [{ ...input, id: "invitation", expires_at }] };
      return Response.json({ token: "fixture-".padEnd(43, "a"), expires_at }, { status: 201 });
    }
    if (url.endsWith("/max/staff")) return Response.json({ project_id: "owned", project_name: "Проект", actor_id: "owner", is_owner: true, permissions: ["orders"] });
    if (url.includes("/staff/records?")) return Response.json({ records: [], next_cursor: null, server_time: "2026-11-05T09:00:00Z" });
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
    if (url.endsWith("/config")) return Response.json(config);
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
  const field = node.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(`[aria-label="${label}"]`)!;
  expect(field, label).toBeTruthy();
  await changeField(field, value);
}
async function changeField(field: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string) {
  await act(async () => {
    const proto = field.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : field.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(field, value);
    field.dispatchEvent(new Event(field.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  }); await settle();
}
const teamName = () => node.querySelector<HTMLInputElement>('[aria-label="Команда"] input[type="text"]')!;
function patches() { return requests.filter(item => item.init.method === "PATCH"); }
function records() { return requests.filter(item => item.url.includes("/records?")); }
async function mountRecords(resource = resources[0]) { await render(<MaxOwnerRecords projectId="owned" resource={resource} />); }

it("links to management from project navigation", async () => {
  await render(<MaxProjectNav projectId="owned" active="manage" showProgress={false} />);
  expect(node.querySelector('a[href="/max/owned/manage"]')?.textContent).toContain("Управление");
});
it("keeps Team reachable while waiting for the queue overview", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  let resolveOverview: ((response: Response) => void) | undefined;
  vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith("/management") ? new Promise<Response>(resolve => { resolveOverview = resolve; }) : original(url, init));
  await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />);
  expect(node.textContent).toContain("Загружаем разделы");
  expect(button("Заказы")).toBeUndefined();
  await click("Команда");
  expect(node.textContent).toContain("Настройки очередей");
  await act(async () => resolveOverview!(Response.json(overview))); await settle();
  expect(button("Заказы")).toBeTruthy();
  expect(button("Команда").getAttribute("aria-current")).toBe("page");
});
it("keeps the catalog in project navigation and shows only enabled resources and Team in management", async () => {
  await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />);
  expect(button("Заказы")).toBeTruthy(); expect(button("Заявки")).toBeUndefined(); expect(button("Записи")).toBeUndefined();
  expect(node.querySelector('a[href="/max/owned/catalog"]')?.textContent).toContain("Каталог");
  expect(button("Каталог")).toBeUndefined();
  expect(node.querySelector('[data-embedded="true"]')).toBeNull();
  expect(button("Настройки")).toBeUndefined();
  await click("Команда"); expect(node.textContent).toContain("Настройки очередей");
  expect(button("Статистика")).toBeUndefined();
});

it("invites a staff member before publication with a private, single-use fragment link", async () => {
  overview = { ...overview, state: "not_published", active_modules: [], resources: [] };
  await render(<MaxOwnerConsole projectId="owned" projectName="Проект" initialTeam />);
  const form = [...node.querySelectorAll("form")].find(item => item.querySelector("h3")?.textContent === "Пригласить сотрудника")!;
  for (const [selector, value] of [["input[type=text]", "Бариста"], ["input[type=email]", "staff@example.test"]]) {
    await act(async () => {
      const field = form.querySelector<HTMLInputElement>(selector)!;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(field, value);
      field.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }
  expect(button("Создать приглашение")?.disabled).toBe(true);
  await act(async () => form.querySelector<HTMLInputElement>("input[type=checkbox]")!.click());
  await click("Создать приглашение");
  const post = requests.find(request => request.url.endsWith("/team/invitations") && request.init.method === "POST");
  expect(JSON.parse(String(post?.init.body))).toEqual({ name: "Бариста", email: "staff@example.test", permissions: ["orders"] });
  const field = node.querySelector<HTMLInputElement>("#max-staff-invite-link")!;
  const invite = new URL(field.value);
  expect(invite.pathname).toBe("/max/staff/join");
  expect(invite.search).toBe("");
  expect(invite.hash).toBe(`#token=${"fixture-".padEnd(43, "a")}`);
});

it("switches an enabled resource to the live staff queue and leaves prior records read-only", async () => {
  await render(<MaxOwnerConsole projectId="owned" projectName="Проект" initialTeam />);
  const mode = node.querySelector<HTMLSelectElement>(".max-staff-settings-fields select")!;
  await act(async () => { mode.value = "pickup"; mode.dispatchEvent(new Event("change", { bubbles: true })); });
  await click("Сохранить настройки");
  const save = requests.find(request => request.url.endsWith("/team/settings") && request.init.method === "PUT");
  expect(JSON.parse(String(save?.init.body))).toEqual({ leads_enabled: false, orders_mode: "pickup", prep_minutes: 7 });
  expect(mode.disabled).toBe(true);
  await click("Заказы");
  expect(node.querySelector('[aria-label="Рабочая очередь"]')?.textContent).toContain("Готовить сейчас");
  expect(requests.some(request => request.url.includes("/management/records"))).toBe(false);
  const details = node.querySelector<HTMLDetailsElement>("details.max-staff-legacy")!;
  await act(async () => details.querySelector("summary")!.click()); await settle();
  await click("Другой клиент");
  expect(node.textContent).toContain("Обращения, полученные до подключения команды");
  expect(node.querySelector('[aria-label="Статус обращения"]')).toBeNull();
  expect(button("Сохранить обращение")).toBeUndefined();
});

it("keeps the online queue draft and guides the owner to publish if the current version is incompatible", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  const message = "Добавьте в приложение оплату по сохранённому заказу через ЮKassa и опубликуйте обновление.";
  vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith("/team/settings")
    ? Promise.resolve(Response.json({ error: { code: "unsupported", message } }, { status: 409 }))
    : original(url, init));
  await render(<MaxOwnerConsole projectId="owned" projectName="Проект" initialTeam />);
  const mode = node.querySelector<HTMLSelectElement>(".max-staff-settings-fields select")!;
  await changeField(mode, "yookassa");
  await click("Сохранить настройки");
  const alert = node.querySelector('[role="alert"]')!;
  expect(alert.textContent).toContain(message);
  expect(alert.querySelector('a[href="/max/owned"]')?.textContent).toBe("К чату");
  expect(alert.querySelector('a[href="/max/owned/publish"]')?.textContent).toBe("К публикации");
  expect(mode.value).toBe("yookassa");
  expect(mode.disabled).toBe(false);
  expect(requests.some(request => request.init.method === "POST")).toBe(false);
});
it.each(["not_published", "unsupported", "unavailable"] as const)("explains %s without fetching a fake empty register", async state => {
  overview = { ...overview, state, active_modules: [], resources: [] };
  await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />);
  expect(records()).toHaveLength(0);
  expect(node.textContent).not.toContain("Обращений пока нет");
  expect(node.textContent).toMatch(state === "not_published" ? /после публикации/i : state === "unsupported" ? /не поддерживает/i : /недоступн/i);
  expect(button("Команда")?.getAttribute("aria-current")).toBe("page");
  expect(button("Настройки")).toBeUndefined(); expect(button("Каталог")).toBeUndefined();
});
function legacyUnsupported() {
  overview = { ...overview, state: "unsupported", configured_modules: [], active_modules: [], resources: [] };
  config = { ...config, config: { ...config.config, app_type: "catalog", management_modules: null } };
}
function enables() { return requests.filter(request => request.url.endsWith("/management/enable")); }
it("prepares legacy management only on an explicit click using a fresh version and retains Team", async () => {
  legacyUnsupported(); await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />);
  const memberName = teamName();
  expect(memberName).not.toBeNull();
  expect(enables()).toHaveLength(0);
  config = { ...config, config_version: 9 };
  await click("Подключить управление");
  expect(enables()).toHaveLength(1);
  expect(JSON.parse(enables()[0].init.body as string)).toEqual({ expected_config_version: 9 });
  expect(node.textContent).toContain("Подготовлено к доработке");
  expect(button("Подключить управление")).toBeUndefined();
  expect(teamName()).toBe(memberName);
  expect(node.querySelector('main a[href="/max/owned"]')?.textContent).toContain("редактор");
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
  const memberName = teamName();
  expect(memberName).not.toBeNull();
  await click("Подключить управление");
  expect(enables()).toHaveLength(1);
  expect(node.textContent).not.toContain("private diagnostic");
  expect(node.textContent).toContain("Повторите попытку");
  expect(teamName()).toBe(memberName);
  config = { ...config, config_version: 8 }; enableMode = "ok";
  await click("Подключить управление");
  expect(enables()).toHaveLength(2);
  expect(JSON.parse(enables()[1].init.body as string)).toEqual({ expected_config_version: 8 });
});
it("blocks preparation while Team contains an unsaved invitation draft", async () => {
  legacyUnsupported(); await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />);
  await changeField(teamName(), "Не потерять");
  expect(button("Подключить управление").disabled).toBe(true);
  await click("Подключить управление");
  expect(enables()).toHaveLength(0);
  expect(teamName().value).toBe("Не потерять");
});
it("rechecks dirty state after the fresh read before sending the enable POST", async () => {
  legacyUnsupported(); await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />);
  const original = vi.mocked(fetch).getMockImplementation()!;
  let release: ((response: Response) => void) | undefined;
  vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith("/config")
    ? new Promise<Response>(resolve => { release = resolve; }) : original(url, init));
  await click("Подключить управление");
  await changeField(teamName(), "Черновик во время чтения");
  await act(async () => release!(Response.json(config))); await settle();
  expect(enables()).toHaveLength(0);
  expect(teamName().value).toBe("Черновик во время чтения");
});
it("keeps the current Team section and an invitation draft created while enable POST is in flight", async () => {
  legacyUnsupported(); config = { ...config, config: { ...config.config, app_type: "custom" } };
  await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />);
  const original = vi.mocked(fetch).getMockImplementation()!;
  let release: ((response: Response) => void) | undefined;
  vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith("/management/enable")
    ? new Promise<Response>(resolve => { requests.push({ url: String(url), init: init ?? {} }); release = resolve; }) : original(url, init));
  await click("Подключить управление");
  await changeField(teamName(), "Черновик во время подключения");
  config = { ...config, config_version: 8, config: { ...config.config, management_modules: ["catalog", "leads"] } };
  overview = { ...overview, configured_modules: ["catalog", "leads"] };
  await act(async () => release!(Response.json(config))); await settle();
  expect(teamName().value).toBe("Черновик во время подключения");
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
it("protects a dirty invitation when opening the chat or switching sections", async () => {
  await render(<MaxOwnerConsole projectId="owned" projectName="Проект" />); await click("Команда");
  await changeField(teamName(), "Не потерять");
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  const chat = node.querySelector("a[href='/max/owned']")!;
  let canceled = false;
  chat.addEventListener("click", (event) => { canceled = event.defaultPrevented; event.preventDefault(); }, { once: true });
  chat.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
  expect(canceled).toBe(true);
  await click("Заказы"); expect(confirm).toHaveBeenCalled();
  expect(teamName().value).toBe("Не потерять");
  confirm.mockReturnValue(true); await click("Заказы");
  expect(node.querySelector('section[aria-label="Заказы"]')).not.toBeNull();
  expect(teamName()).toBeNull();
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
