import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AdminControlCenter } from "@/components/account/AdminControlCenter";
import type { AdminUser, AdminAuditEvent } from "@/lib/api/admin";
import type { BusinessReview } from "@/lib/api/max-account";

vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
const org: BusinessReview = { id: "org", kind: "legal_entity", inn: "7707083893", ogrn: "1027700132195", legal_name: "Test company", status: "pending", verification_source: null, verification_note: null, verified_at: null, created_at: "2026-09-07T00:00:00Z", owner_email: "owner@example.test" };
const person: AdminUser = { id: "person", email: "person@example.test", role: "user", is_admin: false, status: "suspended", email_verified_at: null, created_at: "2026-09-07T00:00:00Z", last_login_at: null, wallet_balance_rub: "1250", business: org };
const self: AdminUser = { ...person, id: "self", email: "admin@example.test", role: "admin", is_admin: true, status: "active", email_verified_at: "2026-09-07", business: null };
const event: AdminAuditEvent = { id: "event", actor_email: self.email, target_email: person.email, action: "admin.user.update", details: { before: { role: "user", status: "active" }, after: { role: "admin", status: "suspended" }, note: "Manual review" }, created_at: "2026-09-07T12:00:00Z" };
let people: AdminUser[], reviews: BusinessReview[], requests: { path: string; method: string; body: unknown }[], auditError: boolean;
let respondMutation: (path: string, body: Record<string, unknown>) => Promise<Response>;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  people = [self, person]; reviews = [org, { ...org, id: "verified", inn: "1234567890", legal_name: "Verified company", status: "verified" }]; requests = []; auditError = false;
  respondMutation = async (path, body) => {
    if (path.startsWith("/api/admin/users/")) {
      const updated = { ...person, ...body } as AdminUser;
      people = people.map(user => user.id === person.id ? updated : user);
      return Response.json(updated);
    }
    return Response.json({ ...org, status: body.approved ? "verified" : "rejected" });
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname;
    if (init?.method && init.method !== "GET") {
      const body = JSON.parse(String(init.body)); requests.push({ path, method: init.method, body }); return respondMutation(path, body);
    }
    if (path === "/api/admin/users") return Response.json(people);
    if (path === "/api/max/account/admin/businesses") return Response.json(reviews);
    if (path === "/api/admin/audit") return auditError ? Promise.reject(new Error("Audit unavailable")) : Response.json([event]);
    throw new Error(`Unexpected endpoint ${path}`);
  }));
});
afterEach(() => vi.unstubAllGlobals());
async function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  await act(async () => root.render(<QueryClientProvider client={client}><AdminControlCenter currentEmail={self.email} /></QueryClientProvider>));
  return async () => { await act(async () => root.unmount()); client.clear(); container.remove(); };
}
const wait = async (check: () => void) => { await act(async () => { await vi.waitFor(check); }); };
function button(text: string) { return [...document.querySelectorAll<HTMLButtonElement>("button")].find(item => item.textContent?.trim() === text)!; }
async function click(text: string) { await act(async () => button(text).click()); }
async function fill(selector: string, value: string) {
  const input = document.querySelector<HTMLInputElement>(selector); expect(input).not.toBeNull();
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value); input!.dispatchEvent(new Event("input", { bubbles: true })); });
}
async function openActions(email: string) {
  const trigger = document.querySelector<HTMLButtonElement>(`button[aria-label="Действия с аккаунтом ${email}"]`)!;
  expect(trigger).not.toBeNull();
  await act(async () => { trigger.focus(); trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); });
  await wait(() => expect(document.querySelector('[role="menu"]')).not.toBeNull());
}
function menuItem(text: string) { return [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find(item => item.textContent?.includes(text))!; }

it("keeps account identity, roles, statuses and balance in labelled table cells and filters real records", async () => {
  const close = await mount();
  try {
    await wait(() => expect(document.querySelectorAll("tbody tr")).toHaveLength(2));
    expect(document.querySelector('table[aria-label="Аккаунты"]')).not.toBeNull();
    const row = [...document.querySelectorAll("tbody tr")].find(item => item.textContent?.includes(person.email))!;
    expect(row.querySelector('[headers="admin-users-status"]')?.textContent).toContain("Приостановлен");
    expect(row.querySelector('[headers="admin-users-balance"]')?.textContent).toMatch(/1\s250/);
    await fill('[aria-label="Поиск аккаунтов"]', org.inn);
    expect(document.querySelectorAll("tbody tr")).toHaveLength(1);
    await fill('[aria-label="Поиск аккаунтов"]', "no-such-user");
    expect(document.body.textContent).toContain("Ничего не найдено");
    expect(requests).toEqual([]);
  } finally { await close(); }
});

it("opens a non-mutating action menu and keeps self-demotion and self-suspension disabled", async () => {
  const close = await mount();
  try {
    await wait(() => expect(document.body.textContent).toContain(self.email)); await openActions(self.email);
    expect(menuItem("Снять права").getAttribute("aria-disabled")).toBe("true");
    expect(menuItem("Приостановить").getAttribute("aria-disabled")).toBe("true");
    expect(requests).toEqual([]);
  } finally { await close(); }
});

it("labels the actual server deletion_pending status without treating it as active", async () => {
  people = [{ ...person, status: "deletion_pending" }]; const close = await mount();
  try {
    await wait(() => expect(document.querySelector('[headers="admin-users-status"]')?.textContent).toContain("Удаление запрошено"));
    expect(document.querySelector('[headers="admin-users-status"]')?.textContent).not.toContain("Активен");
  } finally { await close(); }
});

it.each([
  ["Подтвердить email", { email_verified: true }],
  ["Подтвердить бизнес", { business_verified: true, note: "Реквизиты проверены администратором" }],
  ["Восстановить", { status: "active" }],
] as const)("keeps the existing %s mutation contract", async (label, body) => {
  const close = await mount();
  try {
    await wait(() => expect(document.body.textContent).toContain(person.email)); await openActions(person.email);
    await act(async () => menuItem(label).click());
    expect(requests).toEqual([{ path: "/api/admin/users/person", method: "PATCH", body }]);
  } finally { await close(); }
});

it("submits only the selected account action and prevents duplicate changes while pending", async () => {
  let finish!: (response: Response) => void; respondMutation = () => new Promise(resolve => { finish = resolve; });
  const close = await mount();
  try {
    await wait(() => expect(document.body.textContent).toContain(person.email)); await openActions(person.email);
    expect(requests).toHaveLength(0);
    await act(async () => menuItem("Сделать админом").click());
    expect(requests).toEqual([{ path: "/api/admin/users/person", method: "PATCH", body: { role: "admin" } }]);
    expect(document.querySelector<HTMLButtonElement>(`button[aria-label="Действия с аккаунтом ${person.email}"]`)!.disabled).toBe(true);
    await act(async () => finish(Response.json({ ...person, role: "admin", is_admin: true })));
  } finally { await close(); }
});

it("reveals organization review without changing it and preserves the explicit rejection payload", async () => {
  const close = await mount();
  try {
    await click("Организации");
    await wait(() => expect(document.querySelector('table[aria-label="Организации"]')).not.toBeNull());
    expect(document.querySelectorAll("tbody tr")).toHaveLength(1);
    await click("Рассмотреть"); expect(requests).toEqual([]);
    await fill('[aria-label="Комментарий к решению"]', "  Уточните реквизиты  ");
    await click("Отклонить");
    await wait(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toEqual({ path: "/api/max/account/business/7707083893/decision", method: "POST", body: { approved: false, note: "Уточните реквизиты" } });
  } finally { await close(); }
});

it("distinguishes an empty organization search from an empty review queue", async () => {
  const close = await mount();
  try {
    await click("Организации"); await wait(() => expect(document.body.textContent).toContain(org.legal_name));
    await fill('[aria-label="Поиск организаций"]', "missing"); expect(document.body.textContent).toContain("Ничего не найдено");
    await fill('[aria-label="Поиск организаций"]', ""); await click("Все заявки");
    expect(document.querySelectorAll("tbody tr")).toHaveLength(2); expect(requests).toEqual([]);
  } finally { await close(); }
});

it("offers retry for failed audit loading and renders real actor, target and change after retry", async () => {
  auditError = true; const close = await mount();
  try {
    await click("Журнал"); await wait(() => expect(document.body.textContent).toContain("Журнал не загрузился"));
    expect(button("Повторить")).toBeDefined(); expect(document.body.textContent).not.toContain("Изменений пока нет");
    auditError = false; await click("Повторить");
    await wait(() => expect(document.querySelector('table[aria-label="Журнал изменений"]')).not.toBeNull());
    expect(document.querySelector("tbody")!.textContent).toContain(person.email);
    expect(document.querySelector("tbody")!.textContent).toContain(self.email);
    expect(document.querySelector("tbody")!.textContent).toContain(event.details.note);
  } finally { await close(); }
});

it("supports keyboard tab navigation with one selected tab and its labelled panel", async () => {
  const close = await mount();
  try {
    const first = document.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]'); expect(first).not.toBeNull();
    await act(async () => { first!.focus(); first!.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })); });
    const selected = document.querySelector('[role="tab"][aria-selected="true"]')!;
    expect(selected.textContent).toBe("Организации"); expect(document.activeElement).toBe(selected);
    expect(document.querySelector('[role="tabpanel"]')!.getAttribute("aria-labelledby")).toBe(selected.id);
  } finally { await close(); }
});
