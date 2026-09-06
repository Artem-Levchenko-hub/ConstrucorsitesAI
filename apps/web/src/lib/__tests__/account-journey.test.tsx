import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AccountControlCenter } from "@/components/account/AccountControlCenter";

const mocks = vi.hoisted(() => ({ replace: vi.fn(), search: "" }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: mocks.replace }), useSearchParams: () => new URLSearchParams(mocks.search) }));
vi.mock("@/lib/api/wallet", () => ({ getWallet: async () => ({ balance_rub: 1234, recent_charges: [] }) }));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
const payment = { id: "pay-1", purpose: "wallet_topup", subscription_id: null, package_code: "start", amount_rub: "777", credit_rub: "888", status: "pending", confirmation_url: null, created_at: "2026-09-07T00:00:00Z" };
const plan = { id: "pro-1", code: "pro", version: 1, name: "Pro", price_rub: "999", billing_interval: "month", included_credit_rub: "456", entitlements: { max_projects: 7, team_seats: 3, static_publish_slots: 5, always_on_slots: 2, integrations: false } };
let config: unknown, payments: unknown, post: (body: Record<string, unknown>) => Promise<Response>, requests: Record<string, unknown>[];
let sessionError = false;
let business: Record<string, unknown> | null = null;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  localStorage.clear(); mocks.search = ""; mocks.replace.mockReset(); sessionError = false; requests = [];
  business = null;
  config = { enabled: true, reason: null, packages: [{ code: "start", title: "Серверный пакет", price_rub: "777", credit_rub: "888" }] };
  payments = []; post = async () => Response.json(payment);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname;
    if (path === "/api/max/account/access") return Response.json({ authenticated: true, email_verified: true, email_delivery_configured: true, business, can_create_project: true, reason: null, legal_document_version: "2026-07-30", payments_configured: true });
    if (path === "/api/max/account/business" && init?.method === "PUT") { const body = JSON.parse(String(init.body)); requests.push(body); business = { ...business, ...body, status: "pending" }; return Response.json(business); }
    if (init?.method === "POST") { const body = JSON.parse(String(init.body)); requests.push(body); return post(body); }
    if (path === "/api/payments/config") return Response.json(config);
    if (path === "/api/payments") return payments instanceof Error ? Promise.reject(payments) : Response.json(payments);
    if (path === "/api/billing/plans") return Response.json([plan]);
    if (path === "/api/billing/subscription") return Response.json({ id: "sub", plan: { ...plan, code: "free", id: "free", name: "Free" }, status: "active", auto_renew: false, cancel_at_period_end: false });
    if (path === "/api/auth/sessions") return sessionError ? Promise.reject(new Error("Sessions offline")) : Response.json([{ id: "current", current: true, user_agent: "Macintosh", ip_address: "127.0.0.1", created_at: "2026-09-06", last_seen_at: "2026-09-07" }]);
    return new Response(null, { status: 204 });
  }));
});
afterEach(() => vi.unstubAllGlobals());
async function mount(view: React.ComponentProps<typeof AccountControlCenter>["view"], email = "qa@example.test") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  await act(async () => root.render(<QueryClientProvider client={client}><AccountControlCenter email={email} view={view} /></QueryClientProvider>));
  return { client, invalidate, container, close: async () => { await act(async () => root.unmount()); client.clear(); container.remove(); } };
}
const wait = async (check: () => void) => { await act(async () => { await vi.waitFor(check); }); };
const button = (text: string) => [...document.querySelectorAll<HTMLButtonElement>("button")].find(b => b.textContent?.includes(text))!;
const click = async (text: string) => { await act(async () => button(text).click()); };

it("reviews server amounts before POST and prevents double submission while uncertain", async () => {
  let resolve!: (value: Response) => void; post = () => new Promise(r => { resolve = r; });
  const app = await mount("billing");
  try {
    await wait(() => expect(button("Выбрать")).toBeDefined()); await click("Выбрать");
    expect(requests).toHaveLength(0);
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog.textContent).toContain("777"); expect(dialog.textContent).toContain("888"); expect(dialog.textContent).toContain("Разовое");
    expect(dialog.hasAttribute("data-max-studio")).toBe(true);
    await click("Перейти к оплате в ЮKassa");
    expect(button("Перейти к оплате в ЮKassa").disabled).toBe(true);
    await click("Перейти к оплате в ЮKassa"); expect(requests).toHaveLength(1);
    await act(async () => resolve(Response.json(payment)));
    await wait(() => expect(document.body.textContent).toContain("не вернул ссылку"));
    expect(document.body.textContent).not.toContain("Оплата подтверждена");
  } finally { await app.close(); }
});

it("reopens an uncertain request with the same per-user key and selection", async () => {
  post = async () => { throw new Error("Network lost"); };
  let app = await mount("billing");
  try { await wait(() => expect(button("Выбрать")).toBeDefined()); await click("Выбрать"); await click("Перейти к оплате в ЮKassa"); await wait(() => expect(document.body.textContent).toContain("Network lost")); } finally { await app.close(); }
  const key = requests[0].idempotency_key;
  app = await mount("billing");
  try {
    await wait(() => expect(button("Продолжить незавершённую оплату")).toBeDefined()); await click("Продолжить незавершённую оплату"); await click("Перейти к оплате в ЮKassa");
    await wait(() => expect(requests).toHaveLength(2)); expect(requests[1]).toEqual({ package_code: "start", idempotency_key: key });
  } finally { await app.close(); }
  app = await mount("billing", "other@example.test");
  try { await wait(() => expect(button("Выбрать")).toBeDefined()); expect(button("Продолжить незавершённую оплату")).toBeUndefined(); } finally { await app.close(); }
});

it.each([["pending", "Ожидает оплаты"], ["cancelled", "Оплата отменена"], ["succeeded", "Оплата подтверждена"], ["alien", "Статус неизвестен"]])("uses server return state %s, invalidates only confirmed success", async (status, label) => {
  mocks.search = "payment=pay-1"; payments = [{ ...payment, status }]; const app = await mount("profile");
  const invalidate = app.invalidate;
  try { await wait(() => expect(document.body.textContent).toContain(label)); expect(invalidate.mock.calls.some(([a]) => a?.queryKey?.[0] === "wallet")).toBe(status === "succeeded"); } finally { await app.close(); }
});
it.each([[], new Error("Payments offline")])("does not infer success from return query if lookup is missing or fails", async (value) => {
  mocks.search = "payment=pay-1"; payments = value; const app = await mount("profile");
  try { await wait(() => expect(document.body.textContent).toContain(value instanceof Error ? "Не удалось проверить" : "Платёж не найден")); expect(document.body.textContent).not.toContain("Оплата подтверждена"); } finally { await app.close(); }
});
it("shows disabled provider reason and real wallet without offering checkout", async () => {
  config = { ...(config as object), enabled: false, reason: "Магазин не подключён" }; const app = await mount("billing");
  try { await wait(() => expect(document.body.textContent).toContain("Магазин не подключён")); expect(button("Выбрать").disabled).toBe(true); expect(document.body.textContent).toMatch(/1\s234/); } finally { await app.close(); }
});
it("reviews monthly server plan with auto-renew explicitly off by default", async () => {
  const app = await mount("plan");
  try { await wait(() => expect(button("Выбрать Pro")).toBeDefined()); await click("Выбрать Pro"); expect(document.querySelector<HTMLInputElement>('[role="dialog"] input[type="checkbox"]')!.checked).toBe(false); await click("Перейти к оплате в ЮKassa"); await wait(() => expect(requests).toHaveLength(1)); expect(requests[0]).toMatchObject({ plan_code: "pro", auto_renew: false, consent_version: null }); } finally { await app.close(); }
});
it("labels current device and exposes session query errors", async () => {
  let app = await mount("security"); try { await wait(() => expect(document.body.textContent).toContain("Текущая сессия")); expect(document.body.textContent).toContain("Mac"); } finally { await app.close(); }
  sessionError = true; app = await mount("security"); try { await wait(() => expect(document.body.textContent).toContain("Sessions offline")); expect(button("Повторить")).toBeDefined(); } finally { await app.close(); }
});
it("requires opening destructive review and typing the exact email before deletion", async () => {
  const app = await mount("profile"); try { expect(document.querySelector('[role="dialog"]')).toBeNull(); await click("Удалить аккаунт"); expect(button("Подтвердить удаление").disabled).toBe(true); expect(document.body.textContent).toContain("30-дневного"); } finally { await app.close(); }
});

it("retains a server-confirmed result when an on-page checkout finishes", async () => {
  post = async () => { payments = [{ ...payment, status: "succeeded" }]; return Response.json({ ...payment, status: "succeeded" }); };
  const app = await mount("billing");
  try {
    await wait(() => expect(button("Выбрать")).toBeDefined()); await click("Выбрать"); await click("Перейти к оплате в ЮKassa");
    await wait(() => expect(document.body.textContent).toContain("Оплата подтверждена"));
    expect(button("Продолжить незавершённую оплату")).toBeUndefined();
    expect(localStorage.length).toBe(0);
  } finally { await app.close(); }
});

it("respects verified organization locking instead of offering an impossible edit", async () => {
  business = { id: "org", kind: "legal_entity", inn: "7707083893", ogrn: "1027700132195", legal_name: "Verified org", status: "verified", verification_source: "manual", verification_note: null, verified_at: "2026-09-07", created_at: "2026-09-07" };
  const app = await mount("organization");
  try {
    await wait(() => expect(document.body.textContent).toContain("Verified org"));
    expect(document.body.textContent).toContain("через поддержку");
    expect(button("Сохранить реквизиты")).toBeUndefined();
    expect((document.querySelector("#org-name") as HTMLInputElement)?.readOnly).toBe(true);
  } finally { await app.close(); }
});
it("requires registration ID for a legal entity and saves pending business using the actual PUT contract", async () => {
  business = { id: "org", kind: "legal_entity", inn: "7707083893", ogrn: "1027700132195", legal_name: "Pending org", status: "pending", verification_source: "manual", verification_note: "Проверяем реквизиты", verified_at: null, created_at: "2026-09-07" };
  const app = await mount("organization");
  try {
    await wait(() => expect(button("Сохранить реквизиты")).toBeDefined());
    expect(document.querySelector<HTMLInputElement>("#org-ogrn")!.required).toBe(true);
    await act(async () => {
      const input = document.querySelector<HTMLInputElement>("#org-name")!;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "Updated org");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await click("Сохранить реквизиты");
    await wait(() => expect(document.body.textContent).toContain("Реквизиты сохранены"));
    expect(requests).toEqual([{ kind: "legal_entity", legal_name: "Updated org", inn: "7707083893", ogrn: "1027700132195" }]);
  } finally { await app.close(); }
});

it("does not trust a terminal checkout response before a confirming GET", async () => {
  post = async () => { payments = [payment]; return Response.json({ ...payment, status: "succeeded" }); };
  const app = await mount("billing");
  try { await wait(() => expect(button("Выбрать")).toBeDefined()); await click("Выбрать"); await click("Перейти к оплате в ЮKassa"); await wait(() => expect(document.body.textContent).toContain("Ожидает оплаты")); expect(document.body.textContent).not.toContain("Оплата подтверждена"); } finally { await app.close(); }
});
it("includes current legal consent only after explicit renewal opt-in", async () => {
  const app = await mount("plan");
  try {
    await wait(() => expect(button("Выбрать Pro")).toBeDefined()); await click("Выбрать Pro");
    await act(async () => document.querySelector<HTMLInputElement>('[role="dialog"] input[type="checkbox"]')!.click());
    await click("Перейти к оплате в ЮKassa"); await wait(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({ plan_code: "pro", auto_renew: true, consent_version: process.env.NEXT_PUBLIC_LEGAL_DOCUMENT_VERSION ?? "2026-07-30" });
  } finally { await app.close(); }
});
it("filters real ledger statuses without treating pending payments as credited", async () => {
  payments = [payment, { ...payment, id: "paid", status: "succeeded", amount_rub: "555" }];
  const app = await mount("transactions");
  try {
    await wait(() => expect(document.querySelectorAll("tbody tr")).toHaveLength(2));
    await act(async () => { const select = document.querySelector("select")!; select.value = "pending"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(document.querySelectorAll("tbody tr")).toHaveLength(1); expect(document.querySelector("tbody")!.textContent).toContain("Не зачислено"); expect(document.querySelector("tbody")!.textContent).not.toContain("555");
  } finally { await app.close(); }
});
it("keeps failed deletion in the dialog and preserves exact-email confirmation", async () => {
  const app = await mount("profile");
  try {
    await click("Удалить аккаунт");
    await act(async () => { const input = document.querySelector<HTMLInputElement>("#delete-email")!; Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "qa@example.test"); input.dispatchEvent(new Event("input", { bubbles: true })); });
    expect(button("Подтвердить удаление").disabled).toBe(false);
    vi.mocked(fetch).mockRejectedValueOnce(new Error("Deletion failed"));
    await click("Подтвердить удаление"); await wait(() => expect(document.querySelector('[role="dialog"]')!.textContent).toContain("Deletion failed")); expect(mocks.replace).not.toHaveBeenCalled();
  } finally { await app.close(); }
});
it("compares publication capacity and integrations from actual entitlements", async () => {
  const app = await mount("plan");
  try { await wait(() => expect(button("Выбрать Pro")).toBeDefined()); const entries = [...document.querySelectorAll("article dl > div")].map(row => row.textContent); expect(entries).toContain("Публикаций5"); expect(entries).toContain("Постоянно работающих приложений2"); expect(entries).toContain("ИнтеграцииНет"); } finally { await app.close(); }
});
