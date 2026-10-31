import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { OwnerRecord } from "@/lib/api/max-management";
import { createMaxTemplateLoader } from "./helpers/max-template-loader";
import type * as Identity from "../../../../orchestrator/templates/max-miniapp-nextjs/src/lib/omnia/identity-scope";

const readCatalog = vi.fn();
const readProfile = vi.fn();
const track = vi.fn(async () => {});
const fixtures = resolve(process.cwd(), "src/lib/__tests__/fixtures");
const modules: Record<string, unknown> = { "./sever.css": {} };
const loader = createMaxTemplateLoader(modules);
const identity = loader.load("lib/omnia/identity-scope.ts") as typeof Identity;
let identityWriter: ReturnType<typeof identity.createIdentityController>;
// Consumer tests start below the Provider boundary, with its real protected
// controller already verified. Session/business I/O remains independently tested.
function verifyActor() { identityWriter.commit(identityWriter.begin(), { actor: actorId, mode: "max" }); }
modules["@/lib/omnia/catalog-actions"] = { getYleumCatalog: readCatalog };
modules["@/lib/omnia/app-profile-actions"] = { getYleumAppProfile: readProfile };
for (const name of ["catalog-client", "app-profile-client", "business-client"]) {
  modules[`@/lib/omnia/${name}`] = loader.load(`lib/omnia/${name}.ts`);
}
modules["@/lib/omnia/client"] = { trackMaxActivity: track };
modules["@/lib/sever/catalog-order"] = loader.evaluate(readFileSync(resolve(fixtures, "max-legacy-catalog-order.ts.txt"), "utf8"), resolve(fixtures, "catalog-order.ts"));
const Page = loader.evaluate(readFileSync(resolve(fixtures, "max-legacy-management-page.tsx.txt"), "utf8"), resolve(fixtures, "page.tsx")).default as React.ComponentType;

const catalogItem = (patch = {}) => ({ id: "coffee", title: "Кофе", category: "Напитки", description: "Описание", price: "250 ₽",
  availability: "in_stock", options: [] as string[], image_url: "", action_label: "Заказать", active: true, ...patch });
const catalogResponse = (version: number, items = [catalogItem()]) => ({ ok: true, configVersion: version, items });
const timestamp = "2026-11-02T13:45:00Z";
function order(patch: Partial<OwnerRecord> = {}): OwnerRecord {
  return { id: "00000000-0000-4000-8000-000000000101", resource: "orders", revision: "a".repeat(64),
    status: "new", created_at: timestamp, customer_name: "Тестовый клиент", comment: "Без сахара", items: [
      { catalog_id: "coffee", title: "Кофе", quantity: 1, unit_price_kopecks: 25000 }],
    total_kopecks: 25000, currency: "RUB", fulfillment: { type: "pickup", delay_minutes: 15, scheduled_for: timestamp }, ...patch };
}
let root: Root; let node: HTMLDivElement;
let orders: OwnerRecord[]; let leads: OwnerRecord[];
let orderReplies: Array<Response | Error>; let leadReplies: Array<Response | Error>;
let requests: Array<{ url: string; method: string; body?: Record<string, unknown> }>;
let actorId: string; let holdSession: boolean; let sessionReplies: Array<() => void>;
let loseCommittedResponse: "orders" | "leads" | null;
let receipts: Map<string, OwnerRecord>;
let createdCounts: Record<"orders" | "leads", number>;
let sessionDenied: boolean; let rejectReplay: boolean;
function saveReceipt(resource: "orders" | "leads", key: string, record: OwnerRecord) {
  if (!receipts.has(key)) createdCounts[resource]++;
  receipts.set(key, record);
  if (resource === "orders") orders = [record, ...orders.filter(value => value.id !== record.id)];
  else leads = [record, ...leads.filter(value => value.id !== record.id)];
}
function committedRecord(resource: "orders" | "leads", body: Record<string, unknown>): OwnerRecord {
  if (resource === "leads") return { ...order(), id: "00000000-0000-4000-8000-000000000202", resource,
    customer_name: String(body.customerName), comment: String(body.comment ?? ""), items: [],
    fulfillment: undefined, total_kopecks: undefined, currency: undefined };
  const items = (body.items as { catalogId: string; quantity: number; unitPriceKopecks: number; option?: string }[])
    .map(value => ({ catalog_id: value.catalogId, title: "Кофе", quantity: value.quantity,
      unit_price_kopecks: value.unitPriceKopecks, option: value.option }));
  return order({ customer_name: String(body.customerName), comment: String(body.comment ?? ""), items,
    total_kopecks: items.reduce((sum, value) => sum + value.quantity * value.unit_price_kopecks, 0),
    fulfillment: { type: "pickup", delay_minutes: (body.fulfillment as { delayMinutes: number }).delayMinutes, scheduled_for: timestamp } });
}
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  orders = []; leads = []; requests = []; orderReplies = []; leadReplies = [];
  actorId = "101"; holdSession = false; sessionReplies = []; loseCommittedResponse = null;
  receipts = new Map(); createdCounts = { orders: 0, leads: 0 };
  sessionDenied = false; rejectReplay = false;
  identityWriter = identity.createIdentityController(); verifyActor();
  localStorage.clear(); sessionStorage.clear();
  readCatalog.mockReset().mockResolvedValue(catalogResponse(7)); track.mockClear();
  readProfile.mockReset().mockResolvedValue({ ok: true, configVersion: 7,
    profile: { app_name: "Кофейня пример", summary: "Меню владельца", support: { email: "support@example.test", response_time: "В течение дня" } } });
  vi.stubGlobal("fetch", vi.fn(async (input: string, init: RequestInit = {}) => {
    const url = new URL(String(input), "https://fixture.test");
    const method = init.method ?? "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    requests.push({ url: url.pathname, method, body });
    if (url.pathname === "/api/max/session" && method === "GET") {
      if (sessionDenied) return Response.json({ error: { code: "unauthorized" } }, { status: 401 });
      const verified = actorId;
      if (holdSession) return new Promise<Response>(resolve => sessionReplies.push(() => resolve(Response.json({ user: { id: verified } }))));
      return Response.json({ user: { id: verified } });
    }
    if (!/^\/api\/omnia\/business\/(orders|leads)$/.test(url.pathname)) throw new TypeError("legacy route is not a managed consumer");
    const resource = url.pathname.endsWith("orders") ? "orders" : "leads";
    if (sessionDenied) return Response.json({ error: { code: "unauthorized" } }, { status: 401 });
    if (method === "GET") return Response.json({ records: actorId === "101" ? resource === "orders" ? orders : leads : [], next_cursor: null });
    const receiptKey = `${actorId}:${resource}:${String(body.operationKey)}`;
    if (rejectReplay) return Response.json({ error: { code: "invalid_request" } }, { status: 400 });
    if (receipts.has(receiptKey)) return Response.json(receipts.get(receiptKey));
    if (loseCommittedResponse === resource) {
      loseCommittedResponse = null; saveReceipt(resource, receiptKey, committedRecord(resource, body));
      throw new TypeError("response lost after server commit");
    }
    const reply = (resource === "orders" ? orderReplies : leadReplies).shift();
    if (reply instanceof Error) throw reply;
    if (!reply) throw new Error("Unexpected write");
    if (reply.ok) {
      const saved = await reply.clone().json() as OwnerRecord;
      saveReceipt(resource, receiptKey, saved);
    }
    return reply;
  }));
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
});
afterEach(async () => { await act(async () => root.unmount()); identityWriter.dispose(); node.remove(); localStorage.clear(); sessionStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); }
async function render() { await act(async () => root.render(<Page />)); await settle(); }
async function remount() {
  await act(async () => root.unmount()); node.remove();
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  verifyActor();
  await render();
}
function button(text: string) { return [...node.querySelectorAll<HTMLButtonElement>("button")].find(value => value.textContent?.trim() === text)!; }
async function click(text: string) { expect(button(text), text).toBeTruthy(); await act(async () => button(text).click()); await settle(); }
async function nav(text: string) {
  const target = [...node.querySelectorAll<HTMLButtonElement>("nav button")].find(value => value.querySelector("span")?.textContent === text)!;
  expect(target, text).toBeTruthy(); await act(async () => target.click()); await settle();
}
async function change(id: string, value: string) {
  const field = node.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(id)!; expect(field, id).toBeTruthy();
  const proto = field.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : field.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(field, value);
    field.dispatchEvent(new Event(field.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  }); await settle();
}
async function addCoffee() {
  const add = node.querySelector<HTMLButtonElement>('[aria-label="Добавить в корзину: Кофе"]')!;
  expect(add).toBeTruthy(); expect(add.disabled).toBe(false); await act(async () => add.click()); await nav("Корзина");
}
const writes = (resource: string) => requests.filter(value => value.method === "POST" && value.url.endsWith(resource));

it("denies personal reads and new writes without a verified protected identity", async () => {
  orders = [order({ comment: "Личная история" })];
  identityWriter.dispose();
  await render();
  expect(node.textContent).not.toContain("Личная история");
  expect(node.querySelector<HTMLButtonElement>('[aria-label="Добавить в корзину: Кофе"]')?.disabled).toBe(true);
  expect(requests).toEqual([]);
});

it("renders managed public profile and immutable historical orders with actual status, pickup and comment", async () => {
  orders = [order({ status: "completed", customer_name: "Исторический клиент", total_kopecks: 24690,
    items: [{ catalog_id: "coffee", title: "Исторический кофе", quantity: 2, unit_price_kopecks: 12345 }] })];
  await render();
  expect(node.querySelector("h1")?.textContent).toBe("Кофейня пример");
  await nav("История заказов");
  expect(node.textContent).toContain("Исторический кофе"); expect(node.textContent).toContain("Выполнен");
  expect(node.textContent).toContain("Без сахара"); expect(node.textContent).toContain("246,9");
  expect(node.querySelector('[data-testid="orders-tab-pickup"]')?.textContent).toContain(new Date(timestamp).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }));
  expect(requests.some(value => value.url === "/api/orders")).toBe(false);
});

it("applies the whole changed quote, clears an obsolete option and requires a second explicit confirmation", async () => {
  readCatalog.mockResolvedValue(catalogResponse(7, [catalogItem({ options: ["Овсяное"] })]));
  await render(); await change('select[aria-label="Вариант: Кофе"]', "Овсяное"); await addCoffee();
  await change("#sever-name", "Анна"); await change("#sever-comment", "Без сахара"); await click("Через 15 минут");
  readCatalog.mockResolvedValue(catalogResponse(8));
  await act(async () => window.dispatchEvent(new Event("focus"))); await settle();
  orderReplies.push(Response.json({ error: { code: "quote_changed" }, quote: { configVersion: 8,
    items: [{ catalog_id: "coffee", title: "Новый кофе", quantity: 1, unit_price_kopecks: 30000 }], total_kopecks: 30000, currency: "RUB" } }, { status: 409 }));
  await click("Подтвердить заказ");
  expect(writes("orders")).toHaveLength(1); expect(button("Подтвердить новую сумму")).toBeTruthy();
  expect(node.textContent).toContain("Новый кофе"); expect(node.textContent).not.toContain("Овсяное");
  orderReplies.push(Response.json(order({ total_kopecks: 30000, items: [{ catalog_id: "coffee", title: "Новый кофе", quantity: 1, unit_price_kopecks: 30000 }] })));
  await click("Подтвердить новую сумму");
  expect(writes("orders")).toHaveLength(2);
  const [first, second] = writes("orders");
  expect(second.body).toMatchObject({ operationKey: first.body!.operationKey, customerName: "Анна", comment: "Без сахара", configVersion: 8,
    fulfillment: { type: "pickup", delayMinutes: 15 }, items: [{ catalogId: "coffee", quantity: 1, unitPriceKopecks: 30000 }] });
  expect((second.body!.items as object[])[0]).not.toHaveProperty("option");
  expect(track.mock.calls.flat()).not.toContain("order_created");
  expect(node.querySelector('[data-testid="confirm-pickup"]')?.textContent).toContain(new Date(timestamp).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }));
});

it("retries an ambiguous order with exactly the same body and receipt", async () => {
  await render(); await addCoffee(); await change("#sever-name", "Анна"); await click("Через 30 минут");
  orderReplies.push(new TypeError("offline")); await click("Подтвердить заказ");
  expect(writes("orders")).toHaveLength(1); expect(node.querySelector<HTMLInputElement>("#sever-name")?.disabled).toBe(true);
  orderReplies.push(Response.json(order({ fulfillment: { type: "pickup", delay_minutes: 30, scheduled_for: timestamp } })));
  await click("Проверить отправку");
  expect(writes("orders")[1].body).toEqual(writes("orders")[0].body);
  expect((writes("orders")[0].body!.fulfillment as object)).not.toHaveProperty("scheduled_for");
});

it("creates a lead from explicit inputs, keeps an ambiguous attempt, and renders its managed history", async () => {
  await render(); await nav("Профиль");
  expect(node.querySelector<HTMLInputElement>("#sever-lead-name")?.value).toBe("");
  await change("#sever-lead-name", "Ирина"); await change("#sever-lead-comment", "Нужна консультация");
  leadReplies.push(new TypeError("offline")); await click("Отправить заявку");
  expect(node.querySelector<HTMLInputElement>("#sever-lead-name")?.disabled).toBe(true);
  leadReplies.push(Response.json({ ...order(), resource: "leads", customer_name: "Ирина", comment: "Нужна консультация", items: [],
    fulfillment: undefined, total_kopecks: undefined, currency: undefined }));
  await click("Проверить отправку");
  expect(writes("leads")).toHaveLength(2); expect(writes("leads")[1].body).toEqual(writes("leads")[0].body);
  expect(writes("leads")[1].body).toMatchObject({ customerName: "Ирина", comment: "Нужна консультация" });
  expect(node.textContent).toContain("Заявка сохранена"); expect(node.textContent).toContain("Нужна консультация");
  expect(track.mock.calls.flat()).not.toContain("lead_created");
  expect(requests.some(value => value.url.endsWith("leads") && value.method === "GET")).toBe(true);
});

it("uses current managed category and option selections after a real catalog refresh", async () => {
  readCatalog.mockResolvedValue(catalogResponse(7, [catalogItem({ options: ["Овсяное"] })]));
  await render(); await click("Напитки"); await change('select[aria-label="Вариант: Кофе"]', "Овсяное");
  readCatalog.mockResolvedValue(catalogResponse(8, [catalogItem({ category: "Новое меню" })]));
  await act(async () => window.dispatchEvent(new Event("focus"))); await settle();
  expect(button("Все").getAttribute("aria-pressed")).toBe("true");
  await addCoffee(); await change("#sever-name", "Анна"); orderReplies.push(Response.json(order())); await click("Подтвердить заказ");
  expect((writes("orders")[0].body!.items as object[])[0]).not.toHaveProperty("option");
});

it("blocks new order input until authenticated pending-intent loading finishes", async () => {
  holdSession = true; await render();
  const add = node.querySelector<HTMLButtonElement>('[aria-label="Добавить в корзину: Кофе"]')!;
  expect(add.disabled).toBe(true); expect(node.textContent).toContain("Проверяем отправки");
  expect(sessionReplies.length).toBeGreaterThan(0);
  holdSession = false; await act(async () => sessionReplies.splice(0).forEach(reply => reply())); await settle();
  expect(add.disabled).toBe(false); expect(writes("orders")).toHaveLength(0);
});
it("recovers one committed order after remount with the frozen receipt despite an empty changed catalog", async () => {
  await render(); await addCoffee(); await change("#sever-name", "Анна"); await change("#sever-comment", "Сохранить комментарий");
  loseCommittedResponse = "orders"; await click("Подтвердить заказ");
  const original = writes("orders")[0].body;
  expect(createdCounts.orders).toBe(1);
  readCatalog.mockResolvedValue(catalogResponse(99, [])); await remount();
  expect(node.querySelector<HTMLInputElement>("#sever-name")?.value).toBe("Анна");
  expect(node.querySelector<HTMLTextAreaElement>("#sever-comment")?.value).toBe("Сохранить комментарий");
  expect(node.querySelector<HTMLInputElement>("#sever-name")?.disabled).toBe(true);
  expect(writes("orders")).toHaveLength(1);
  await click("Проверить отправку");
  expect(writes("orders")[1].body).toEqual(original); expect(createdCounts.orders).toBe(1);
  expect(node.textContent).toContain("Заказ сохранён"); await nav("История заказов");
  expect(node.textContent).toContain("Сохранить комментарий"); expect(orders).toHaveLength(1);
});
it("recovers one committed lead after remount without retyping or generating a new operation key", async () => {
  await render(); await nav("Профиль"); await change("#sever-lead-name", "Ирина"); await change("#sever-lead-comment", "Сохранить заявку");
  loseCommittedResponse = "leads"; await click("Отправить заявку");
  const original = writes("leads")[0].body; expect(createdCounts.leads).toBe(1);
  await remount();
  expect(node.querySelector<HTMLInputElement>("#sever-lead-name")?.value).toBe("Ирина");
  expect(node.querySelector<HTMLTextAreaElement>("#sever-lead-comment")?.value).toBe("Сохранить заявку");
  expect(node.querySelector<HTMLInputElement>("#sever-lead-name")?.disabled).toBe(true);
  expect(writes("leads")).toHaveLength(1);
  await click("Проверить отправку");
  expect(writes("leads")[1].body).toEqual(original); expect(createdCounts.leads).toBe(1);
  expect(node.textContent).toContain("Заявка сохранена"); expect(leads).toHaveLength(1);
});
it("never restores another actor's pending lead and can recover it after returning to the original actor", async () => {
  await render(); await nav("Профиль"); await change("#sever-lead-name", "Ирина"); await change("#sever-lead-comment", "Только текущему клиенту");
  loseCommittedResponse = "leads"; await click("Отправить заявку");
  actorId = "202"; await remount(); await nav("Профиль");
  expect(node.querySelector<HTMLInputElement>("#sever-lead-name")?.value).toBe("");
  expect(node.textContent).not.toContain("Только текущему клиенту"); expect(button("Проверить отправку")).toBeUndefined();
  expect(writes("leads")).toHaveLength(1);
  actorId = "101"; await remount();
  expect(node.querySelector<HTMLInputElement>("#sever-lead-name")?.value).toBe("Ирина");
  await click("Проверить отправку"); expect(createdCounts.leads).toBe(1); expect(leads).toHaveLength(1);
});
it("keeps actor A's restored intent on the same mounted page when actor B attempts recovery", async () => {
  await render(); await nav("Профиль"); await change("#sever-lead-name", "Ирина"); await change("#sever-lead-comment", "Отправка первого клиента");
  loseCommittedResponse = "leads"; await click("Отправить заявку");
  const original = writes("leads")[0].body;
  await remount();
  expect(node.querySelector<HTMLInputElement>("#sever-lead-name")?.value).toBe("Ирина");
  actorId = "202";
  // A vulnerable SDK would post A's restored body as a fresh B intent. Let that
  // request commit so the regression proves the forbidden second record too.
  leadReplies.push(Response.json({ ...order(), id: "00000000-0000-4000-8000-000000000303", resource: "leads",
    customer_name: "Ирина", comment: "Отправка первого клиента", items: [],
    fulfillment: undefined, total_kopecks: undefined, currency: undefined }));
  await click("Проверить отправку");
  expect(writes("leads")).toHaveLength(1);
  expect(createdCounts.leads).toBe(1);
  expect([...receipts.keys()].some(key => key.startsWith("202:leads:"))).toBe(false);
  expect(node.querySelector<HTMLInputElement>("#sever-lead-name")?.value).toBe("Ирина");
  expect(node.querySelector<HTMLTextAreaElement>("#sever-lead-comment")?.value).toBe("Отправка первого клиента");
  expect(node.querySelector<HTMLInputElement>("#sever-lead-name")?.disabled).toBe(true);
  expect(() => identity.captureIdentityScope()).toThrow();
  actorId = "101"; verifyActor(); await click("Проверить отправку");
  expect(writes("leads")).toHaveLength(2);
  expect(writes("leads")[1].body).toEqual(original);
  expect(createdCounts.leads).toBe(1); expect(leads).toHaveLength(1);
  expect(node.textContent).toContain("Заявка сохранена");
});
it("does not reveal or discard a saved lead when actor verification is unauthorized", async () => {
  await render(); await nav("Профиль"); await change("#sever-lead-name", "Ирина"); await change("#sever-lead-comment", "Личное сообщение");
  loseCommittedResponse = "leads"; await click("Отправить заявку");
  sessionDenied = true; await remount(); await nav("Профиль");
  expect(node.querySelector<HTMLInputElement>("#sever-lead-name")?.value).toBe("");
  expect(node.querySelector<HTMLInputElement>("#sever-lead-name")?.disabled).toBe(true);
  expect(node.textContent).not.toContain("Личное сообщение"); expect(writes("leads")).toHaveLength(1);
  sessionDenied = false; verifyActor(); await click("Повторить проверку отправок");
  expect(node.querySelector<HTMLInputElement>("#sever-lead-name")?.value).toBe("Ирина");
  expect(writes("leads")).toHaveLength(1); await click("Проверить отправку");
  expect(createdCounts.leads).toBe(1);
});
it("keeps a previously unknown order immutable after a recovery 400 and can still replay its receipt", async () => {
  await render(); await addCoffee(); await change("#sever-name", "Анна");
  loseCommittedResponse = "orders"; await click("Подтвердить заказ");
  const original = writes("orders")[0].body; await remount(); rejectReplay = true;
  await click("Проверить отправку");
  expect(node.querySelector<HTMLInputElement>("#sever-name")?.disabled).toBe(true);
  expect(writes("orders")[1].body).toEqual(original); expect(createdCounts.orders).toBe(1);
  await remount(); expect(node.querySelector<HTMLInputElement>("#sever-name")?.value).toBe("Анна");
  rejectReplay = false; await click("Проверить отправку");
  expect(writes("orders")[2].body).toEqual(original); expect(createdCounts.orders).toBe(1);
});
