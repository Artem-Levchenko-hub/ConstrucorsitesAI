import { StrictMode, act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxStaffInvite } from "@/components/max/MaxStaffInvite";
import { MaxStaffQueue } from "@/components/max/MaxStaffQueue";
import type { StaffLead, StaffOrder } from "@/lib/api/max-staff";

const router = vi.hoisted(() => ({ replace: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const project = "00000000-0000-4000-8000-000000000001";
const acceptedPath = `/max/staff/${project}`;
const proof = "test-proof-".padEnd(43, "a");
const base: StaffOrder = {
  id: "current", resource: "orders", created_at: "2026-11-05T10:00:00Z", revision: "v1", status: "new",
  comment: "Заберу у стойки", assignee_id: null, assignee_name: null, outcome: null,
  items: [{ title: "Срочный заказ", quantity: 2, option: "Без сахара" }],
  scheduled_for: "2026-11-05T10:15:00Z", prepare_at: "2026-11-05T10:08:00Z", payment: "paid",
};
function order(id: string, title: string, fields: Partial<StaffOrder> = {}): StaffOrder {
  return { ...base, id, items: [{ title, quantity: 1, option: null }], ...fields };
}

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
const originalHref = location.href;
const join = (authenticated: boolean) => <MaxStaffInvite authenticated={authenticated}
  email={authenticated ? "staff@example.test" : null} verified={authenticated} emailDeliveryAvailable={true} />;

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  router.replace.mockReset(); router.refresh.mockReset(); sessionStorage.clear();
});
afterEach(() => {
  act(() => root.unmount()); container.remove(); client.clear();
  sessionStorage.clear(); history.replaceState(null, "", originalHref); vi.unstubAllGlobals(); vi.useRealTimers();
});

async function render(children: React.ReactNode) {
  await act(async () => root.render(<QueryClientProvider client={client}>{children}</QueryClientProvider>));
}
async function waitForText(value: string) {
  await vi.waitFor(async () => {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    expect(container.textContent).toContain(value);
  });
}
function button(label: string) {
  return Array.from(container.querySelectorAll("button")).find(element => element.textContent?.trim() === label);
}
async function click(label: string) {
  const target = button(label);
  expect(target, label).toBeTruthy();
  await act(async () => target!.click());
}
async function openRow(title: string) {
  const target = Array.from(container.querySelectorAll<HTMLButtonElement>('[aria-label="Записи очереди"] button'))
    .find(element => element.textContent?.includes(title));
  expect(target, title).toBeTruthy();
  await act(async () => target!.click());
}
function error(code: string, status: number) {
  return Response.json({ error: { code, message: "Fixture" } }, { status });
}

it("scrubs the invite, preserves it across auth, and accepts only after an explicit click", async () => {
  const fetch = vi.fn(async (_url: string, _init: RequestInit) => Response.json({ project_id: project }));
  vi.stubGlobal("fetch", fetch);
  history.replaceState(null, "", `/max/staff/join#token=${proof}`);
  await render(<StrictMode>{join(false)}</StrictMode>);
  expect(location.hash).toBe("");
  expect(sessionStorage.length).toBe(1);
  const links = Array.from(container.querySelectorAll("a")).map(link => link.getAttribute("href"));
  expect(links).toEqual(["/login?next=%2Fmax%2Fstaff%2Fjoin", "/max/register?next=%2Fmax%2Fstaff%2Fjoin"]);
  expect(fetch).not.toHaveBeenCalled();
  await render(<StrictMode key="after-auth">{join(true)}</StrictMode>);
  expect(fetch).not.toHaveBeenCalled();
  await click("Принять приглашение");
  expect(fetch).toHaveBeenCalledOnce();
  const [url, init] = fetch.mock.calls[0];
  expect(url).toBe("/api/max/staff/invitations/accept");
  expect(JSON.parse(String(init.body))).toEqual({ token: proof });
  expect(sessionStorage.length).toBe(0);
  expect(router.replace).toHaveBeenCalledWith(acceptedPath);
});

it("clears an expired invite without returning its token to the login page", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => error("staff_invite_invalid", 410)));
  history.replaceState(null, "", `/max/staff/join#token=${proof}`);
  await render(join(true));
  await click("Принять приглашение");
  await waitForText("Срок действия ссылки истёк");
  expect(button("Принять приглашение")).toBeUndefined();
  expect(sessionStorage.length).toBe(0);
  expect(router.replace).not.toHaveBeenCalled();
});

it("separates due, ready, later and test orders, then hides cached records after access is revoked", async () => {
  let revoked = false;
  const fetch = vi.fn(async (url: string) => {
    if (!url.includes("/records")) return Response.json({ project_id: project, project_name: "Тестовая кофейня",
      actor_id: "operator", is_owner: false, permissions: ["orders"] });
    if (revoked) return error("staff_access_denied", 403);
    return Response.json({ server_time: "2026-11-05T10:10:00Z", next_cursor: null, records: [
      order("next", "Поздний срочный", { scheduled_for: "2026-11-05T10:20:00Z" }), base,
      order("later", "Отложенный заказ", { prepare_at: "2026-11-05T10:35:00Z" }),
      order("ready", "Готовый заказ", { status: "ready" }), order("test", "Пробный заказ", { payment: "test" }),
    ] });
  });
  vi.stubGlobal("fetch", fetch);
  await render(<MaxStaffQueue projectId={project} userId="operator" />);
  await waitForText("Срочный заказ");
  expect(container.textContent?.indexOf("Срочный заказ")).toBeLessThan(container.textContent?.indexOf("Поздний срочный") ?? 0);
  expect(container.textContent).not.toContain("Пробный заказ");
  expect(button("Заявки")).toBeUndefined();
  await click("На потом"); await openRow("Отложенный заказ");
  expect(button("Взять в работу")).toBeUndefined();
  await click("Проверка"); await openRow("Пробный заказ");
  expect(container.textContent).toContain("Тестовая оплата");
  expect(button("Взять в работу")).toBeUndefined();
  await click("Готовы"); await openRow("Готовый заказ");
  revoked = true;
  await click("Обновить"); await waitForText("Доступ к этой очереди закрыт");
  expect(container.textContent).not.toContain("Готовый заказ");
  expect(container.textContent).not.toContain("Заберу у стойки");
  expect(fetch.mock.calls.some(([url]) => url.includes("resource=leads"))).toBe(false);
});

it("automatically finds the earliest due order behind pages with closed, test and ineligible records", async () => {
  const cursors: Array<string | null> = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (!url.includes("/records")) return Response.json({ project_id: project, project_name: "Тестовая кофейня",
      actor_id: "operator", is_owner: false, permissions: ["orders"] });
    const cursor = new URL(url, "https://yleum.test").searchParams.get("cursor");
    cursors.push(cursor);
    const records = cursor === "third" ? [base] : cursor === "second" ? [] : [
      order("closed", "Завершённый", { status: "completed" }), order("test", "Тестовый", { payment: "test" }),
      order("next", "Следующий срочный", { scheduled_for: "2026-11-05T10:20:00Z" }),
    ];
    return Response.json({ records, next_cursor: cursor === "third" ? null : cursor === "second" ? "third" : "second", server_time: "2026-11-05T10:10:00Z" });
  }));
  await render(<MaxStaffQueue projectId={project} userId="operator" />);
  await waitForText("Срочный заказ");
  expect(cursors).toEqual([null, "second", "third"]);
  expect(container.textContent?.indexOf("Срочный заказ")).toBeLessThan(container.textContent?.indexOf("Следующий срочный") ?? 0);
  expect(container.textContent).not.toContain("Тестовый");
});

it("keeps an incomplete queue explicit, retries only the failed cursor and bounds periodic paging", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  let failed = false;
  const cursors: Array<string | null> = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (!url.includes("/records")) return Response.json({ project_id: project, project_name: "Тестовая кофейня",
      actor_id: "operator", is_owner: false, permissions: ["orders"] });
    const cursor = new URL(url, "https://yleum.test").searchParams.get("cursor");
    cursors.push(cursor);
    if (cursor === "second" && !failed) { failed = true; return error("unavailable", 503); }
    const next_cursor = cursor === "fourth" ? null : cursor === "third" ? "fourth" : cursor === "second" ? "third" : "second";
    return Response.json({ records: cursor === "fourth" ? [base] : [], next_cursor, server_time: "2026-11-05T10:10:00Z" });
  }));
  await render(<MaxStaffQueue projectId={project} userId="operator" />);
  await waitForText("Следующая страница не загрузилась");
  expect(container.textContent).not.toContain("Сейчас очередь пуста");
  await click("Повторить загрузку страницы");
  await waitForText("Очередь проверена не полностью");
  expect(cursors).toEqual([null, "second", "second", "third"]);
  await click("Загрузить ещё");
  await waitForText("Срочный заказ");
  expect(cursors.at(-1)).toBe("fourth");
  const count = cursors.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(16_000); });
  expect(cursors).toHaveLength(count);
});

it("moves an order into the due queue between polls using time elapsed since the server response", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "performance"] });
  let reads = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (!url.includes("/records")) return Response.json({ project_id: project, project_name: "Тестовая кофейня",
      actor_id: "operator", is_owner: false, permissions: ["orders"] });
    reads += 1;
    return Response.json({ records: [{ ...base, prepare_at: "2026-11-05T10:10:01Z" }], next_cursor: null, server_time: "2026-11-05T10:10:00Z" });
  }));
  await render(<MaxStaffQueue projectId={project} userId="operator" />);
  await waitForText("Сейчас очередь пуста");
  expect(container.textContent).not.toContain("Срочный заказ");
  await act(async () => { await vi.advanceTimersByTimeAsync(1_050); });
  await waitForText("Срочный заказ");
  expect(reads).toBe(1);
});

it("sends a revisioned claim and shows the real assignee when a colleague won the race", async () => {
  let taken = false;
  const claims: Array<Record<string, unknown>> = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    if (init.method === "PATCH") {
      claims.push(JSON.parse(String(init.body))); taken = true;
      return error("revision_conflict", 409);
    }
    if (url.includes("/records")) return Response.json({ server_time: "2026-11-05T10:10:00Z", next_cursor: null,
      records: [taken ? { ...base, status: "in_progress", revision: "v2", assignee_id: "another", assignee_name: "Другой сотрудник" } : base] });
    return Response.json({ project_id: project, project_name: "Тестовая кофейня", actor_id: "operator", is_owner: false, permissions: ["orders"] });
  }));
  await render(<MaxStaffQueue projectId={project} userId="operator" />);
  await waitForText("Срочный заказ"); await openRow("Срочный заказ"); await click("Взять в работу");
  await waitForText("Другой сотрудник");
  expect(claims).toEqual([{ resource: "orders", action: "claim", expected_revision: "v1", operation_key: expect.any(String) }]);
  expect(container.textContent).toContain("Данные изменились");
  expect(button("Отметить готовым")).toBeUndefined();
  expect(button("Взять в работу")).toBeUndefined();
});

it("retries a lost response with the same action key and only shows the confirmed state", async () => {
  const writes: Array<Record<string, unknown>> = [];
  const accepted: StaffOrder = { ...base, revision: "v2", status: "in_progress", assignee_id: "operator", assignee_name: "Бариста" };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    if (init.method === "PATCH") {
      writes.push(JSON.parse(String(init.body)));
      if (writes.length === 1) throw new TypeError("Connection lost");
      return Response.json(accepted);
    }
    if (url.includes("/records")) return Response.json({
      server_time: "2026-11-05T10:10:00Z", next_cursor: null, records: [writes.length > 1 ? accepted : base],
    });
    return Response.json({ project_id: project, project_name: "Тестовая кофейня", actor_id: "operator", is_owner: false, permissions: ["orders"] });
  }));
  await render(<MaxStaffQueue projectId={project} userId="operator" />);
  await waitForText("Срочный заказ"); await openRow("Срочный заказ"); await click("Взять в работу");
  await waitForText("Сервер не подтвердил результат");
  expect(button("Отметить готовым")).toBeUndefined();
  await click("Повторить действие");
  await waitForText("Отметить готовым");
  expect(writes).toHaveLength(2);
  expect(writes[0]).toEqual(writes[1]);
});

it("requires an outcome before closing an assigned lead", async () => {
  const changes: Array<Record<string, unknown>> = [];
  let lead: StaffLead = {
    id: "lead", resource: "leads", created_at: "2026-11-05T10:00:00Z", revision: "v1", status: "in_progress",
    comment: null, assignee_id: "operator", assignee_name: "Оператор", outcome: null,
    customer_name: "Клиент", contact_phone: null, contact_email: "customer@example.test",
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    if (init.method === "PATCH") {
      const change = JSON.parse(String(init.body));
      changes.push(change);
      lead = { ...lead, revision: "v2", status: "completed", outcome: change.outcome };
      return Response.json(lead);
    }
    if (url.includes("/records")) return Response.json({
      server_time: "2026-11-05T10:10:00Z", next_cursor: null, records: [lead],
    });
    return Response.json({ project_id: project, project_name: "Тестовая кофейня", actor_id: "operator", is_owner: false, permissions: ["leads"] });
  }));
  await render(<MaxStaffQueue projectId={project} userId="operator" />);
  await waitForText("Клиент"); await openRow("Клиент");
  expect(button("Закрыть заявку")?.disabled).toBe(true);
  expect(container.querySelector('a[href^="mailto:"]')?.textContent).toBe("customer@example.test");
  await act(async () => {
    const textarea = container.querySelector("textarea")!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(textarea, "  Клиенту ответили  ");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await click("Закрыть заявку"); await waitForText("Клиенту ответили");
  expect(changes).toEqual([{ resource: "leads", action: "close", expected_revision: "v1", operation_key: expect.any(String), outcome: "Клиенту ответили" }]);
  expect(button("Закрыть заявку")).toBeUndefined();
  await click("К очереди");
  await vi.waitFor(() => expect(document.activeElement).toBe(button("Открытые")));
});
