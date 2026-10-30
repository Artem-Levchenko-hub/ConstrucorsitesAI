import { act, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AccountControlCenter } from "@/components/account/AccountControlCenter";
import { createSubscriptionCheckout, manageSubscription } from "@/lib/api/account";

const mocks = vi.hoisted(() => ({ replace: vi.fn(), search: "" }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: mocks.replace }), useSearchParams: () => new URLSearchParams(mocks.search) }));
vi.mock("@/lib/api/wallet", () => ({ getWallet: async () => ({ balance_rub: 1234, recent_charges: [] }) }));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
const payment = { id: "pay-1", purpose: "wallet_topup", subscription_id: null, package_code: "start", amount_rub: "777", credit_rub: "888", edit_units: 5, edits_only: true, package_version: 2, status: "pending", confirmation_url: null, created_at: "2026-09-07T00:00:00Z" };
const plan = { id: "pro-1", code: "pro", version: 1, name: "Pro", price_rub: "999", billing_interval: "month", included_credit_rub: "456", entitlements: { max_projects: 7, team_seats: 3, static_publish_slots: 5, always_on_slots: 2, integrations: false } };
const freePlan = { id: "free-3", code: "free", version: 3, name: "Free", price_rub: "0.00", billing_interval: "month", included_credit_rub: "0", entitlements: { max_projects: 1, team_seats: 1, static_publish_slots: 1, always_on_slots: 0, integrations: true, generation_messages_lifetime: 1 } };
let currentPlan = freePlan;
const usage = {
  period: { start: "2026-09-01T00:00:00Z", end: "2026-09-23T12:00:00Z", source: "calendar_month" },
  plan: freePlan, subscription_status: "active",
  generations: { calls: 12, cost_rub: "48.5000", tokens_in: 100, tokens_out: 20, total: 5, completed: 4, failed: 1, cancelled: 0, active: 0 },
  app_ai_answers: { calls: 30, cost_rub: "3.2000", tokens_in: 0, tokens_out: 0 },
  other_ai: { calls: 0, cost_rub: "0", tokens_in: 0, tokens_out: 0 },
  publications: { total: 3, projects: 2 },
  free_generations: { limit: 3, used: 3, left: 0, unlimited: false },
  wallet: { balance_rub: "100.0000", debited_rub: "51.7000", credited_rub: "50.0000", charges: 7 },
  credits: { initial_generations: 2, edits: 3 },
  entitlements: [
    { key: "max_projects", label: "Приложений", kind: "limit", limit: null, used: 2, exceeded: false },
    { key: "static_publish_slots", label: "Опубликованных приложений", kind: "limit", limit: 1, used: 2, exceeded: true },
    { key: "integrations", label: "Интеграции", kind: "flag", enabled: true, used: 1, exceeded: false },
  ],
  total_ai_cost_rub: "51.7000",
};
let config: unknown, payments: unknown, post: (body: Record<string, unknown>) => Promise<Response>, requests: Record<string, unknown>[];
let sessionError = false;
let usageResponse: unknown = usage;
let publicPlans: unknown = [freePlan, plan];
let personalOffer: unknown = null;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  localStorage.clear(); mocks.search = ""; mocks.replace.mockReset(); sessionError = false; requests = [];
  config = { enabled: true, reason: null, packages: [{ code: "start", title: "Серверный пакет", price_rub: "777", credit_rub: "888", edit_units: 5, edits_only: true, package_version: 2 }] };
  payments = []; post = async () => Response.json(payment);
  usageResponse = usage; currentPlan = freePlan; publicPlans = [freePlan, plan]; personalOffer = null;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    // Same-origin requests are relative ("/api/..."), so resolve like a browser.
    const path = new URL(url, window.location.origin).pathname;
    if (path === "/api/max/account/access") return Response.json({ authenticated: true, email_verified: true, email_delivery_configured: true, can_create_project: true, reason: null, legal_document_version: "2026-10-27", payments_configured: true });
    if (init?.method === "POST") { const body = JSON.parse(String(init.body)); requests.push(body); return post(body); }
    if (path === "/api/payments/config") return Response.json(config);
    if (path === "/api/payments") return payments instanceof Error ? Promise.reject(payments) : Response.json(payments);
    if (path === "/api/billing/plans") return Response.json(publicPlans);
    if (path === "/api/billing/purchase-offer") return Response.json(personalOffer);
    if (path === "/api/billing/usage") return Response.json(usageResponse);
    if (path === "/api/billing/credits") return Response.json({ edit_only_units: 5, flexible_units: 15, available_units: 20, reserved_units: 1, initial_build_units: 5, edit_units: 1, included_generations_remaining: 2, included_edits_remaining: 24, included_generations_limit: 2, included_edits_limit: 25, period_end: "2026-11-24T00:00:00Z" });
    if (path === "/api/billing/subscription") return Response.json({ id: "sub", plan: currentPlan, status: "active", auto_renew: false, cancel_at_period_end: false });
    if (path === "/api/auth/sessions") return sessionError ? Promise.reject(new Error("Sessions offline")) : Response.json([{ id: "current", current: true, user_agent: "Macintosh", ip_address: "127.0.0.1", created_at: "2026-09-06", last_seen_at: "2026-09-07" }]);
    return new Response(null, { status: 204 });
  }));
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
async function mount(view: React.ComponentProps<typeof AccountControlCenter>["view"], email = "qa@example.test", strict = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  const content = (address: string) => {
    const surface = <AccountControlCenter email={address} view={view} />;
    return <QueryClientProvider client={client}>{strict ? <StrictMode>{surface}</StrictMode> : surface}</QueryClientProvider>;
  };
  await act(async () => root.render(content(email)));
  return { client, invalidate, container,
    rerender: async (nextEmail: string) => { await act(async () => root.render(content(nextEmail))); },
    close: async () => { await act(async () => root.unmount()); client.clear(); container.remove(); } };
}
const wait = async (check: () => void) => { await act(async () => { await vi.waitFor(check); }); };
const button = (text: string) => [...document.querySelectorAll<HTMLButtonElement>("button")].find(b => b.textContent?.includes(text))!;
const click = async (text: string) => { await act(async () => button(text).click()); };

it("quotes an independent personal payment test and preserves its offer on uncertain retry", async () => {
  currentPlan = { ...freePlan, ...plan, entitlements: { ...freePlan.entitlements, ...plan.entitlements } };
  personalOffer = { id: "personal-offer", price_rub: "1.00", currency: "RUB", expires_at: "2026-11-03T00:00:00Z", reserved_payment_id: null };
  post = async () => { throw new Error("Lost response"); };
  let app = await mount("plan");
  try {
    await wait(() => expect(button("Проверить оплату за 1")).toBeDefined());
    expect(document.querySelector(".account-plans")?.textContent).toContain("999");
    await click("Проверить оплату за 1");
    expect(document.querySelector('[role="dialog"] input[type="checkbox"]')).toBeNull();
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Действующая подписка, её срок, автопродление и пакеты не изменятся");
    expect(document.querySelector('[role="dialog"]')?.textContent).not.toContain("1 месяц");
    await click("Перейти к оплате в ЮKassa");
    await wait(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toEqual({ offer_id: "personal-offer", idempotency_key: expect.any(String) });
  } finally { await app.close(); }
  app = await mount("plan");
  try {
    await wait(() => expect(button("Продолжить незавершённую оплату")).toBeDefined());
    await click("Продолжить незавершённую оплату");
    await click("Перейти к оплате в ЮKassa");
    await wait(() => expect(requests).toHaveLength(2));
    expect(requests[1]).toEqual(requests[0]);
  } finally { await app.close(); }
});

it("never sends subscription parameters from a stored payment test attempt", async () => {
  localStorage.setItem("max-checkout:qa@example.test", JSON.stringify({
    key: "personal-retry", autoRenew: true,
    selection: { kind: "test", code: "payment_test", title: "Проверочная оплата", price: "1.00", credit: "0", offerId: "personal-offer" },
  }));
  const app = await mount("plan");
  try {
    await wait(() => expect(button("Продолжить незавершённую оплату")).toBeDefined());
    await click("Продолжить незавершённую оплату");
    expect(document.querySelector('[role="dialog"] input[type="checkbox"]')).toBeNull();
    await click("Перейти к оплате в ЮKassa");
    await wait(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toEqual({ offer_id: "personal-offer", idempotency_key: "personal-retry" });
  } finally { await app.close(); }
});

it("releases a stale test intent only when the server proves no payment was created", async () => {
  personalOffer = { id: "expired-offer", price_rub: "1.00", currency: "RUB", expires_at: "2026-11-03T00:00:00Z", reserved_payment_id: null };
  post = async () => Response.json({ error: { code: "conflict", message: "Предложение истекло", details: { payment_test_not_created: true } } }, { status: 409 });
  const app = await mount("plan");
  try {
    await wait(() => expect(button("Проверить оплату за 1")).toBeDefined());
    await click("Проверить оплату за 1");
    await click("Перейти к оплате в ЮKassa");
    await wait(() => expect(document.body.textContent).toContain("Предложение истекло"));
    expect(localStorage.getItem("max-checkout:qa@example.test")).toBeNull();
    expect(button("Продолжить незавершённую оплату")).toBeUndefined();
  } finally { await app.close(); }
});

it("clears a personal checkout and cached quote when switching account in place", async () => {
  personalOffer = { id: "only-account-a", price_rub: "1.00", currency: "RUB", expires_at: "2026-11-03T00:00:00Z", reserved_payment_id: null };
  const app = await mount("plan", "account-a@example.test");
  try {
    await wait(() => expect(button("Проверить оплату за 1")).toBeDefined());
    await click("Проверить оплату за 1");
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    personalOffer = null;
    await app.rerender("account-b@example.test");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(button("Проверить оплату за 1")).toBeUndefined();
    await wait(() => expect(app.client.getQueryData(["billing-purchase-offer", "account-b@example.test"])).toBeNull());
    expect(document.body.textContent).not.toContain("Проверочная оплата");
    expect(requests).toHaveLength(0);
  } finally { await app.close(); }
});

it("labels a successful test payment without claiming activation or credits", async () => {
  payments = [{ ...payment, purpose: "payment_test", package_code: "payment_test", status: "succeeded", amount_rub: "1.00", credit_rub: "0", edit_units: 0 }];
  const app = await mount("transactions");
  try {
    await wait(() => expect(document.body.textContent).toContain("Проверочная оплата"));
    expect(document.body.textContent).toContain("Подписка и пакеты без изменений");
    expect(document.body.textContent).not.toContain("Тариф активирован");
    expect(document.body.textContent).not.toContain("Покупка тарифа");
  } finally { await app.close(); }
});

it("confirms a test receipt without refreshing product allowances or claiming plan changes", async () => {
  mocks.search = "payment=pay-1";
  payments = [{ ...payment, purpose: "payment_test", status: "succeeded", amount_rub: "1.00", credit_rub: "0", edit_units: 0 }];
  const app = await mount("plan");
  try {
    await wait(() => expect(document.body.textContent).toContain("Подписка и пакеты не изменены"));
    expect(document.body.textContent).not.toContain("Баланс и тариф обновляются");
    const keys = app.invalidate.mock.calls.map(([arg]) => arg?.queryKey?.[0]);
    expect(keys).toContain("billing-purchase-offer");
    expect(keys).not.toContain("wallet");
    expect(keys).not.toContain("billing-subscription");
    expect(keys).not.toContain("generation-credits");
  } finally { await app.close(); }
});

it("does not redirect another account when a previous checkout response arrives late", async () => {
  let resolve!: (response: Response) => void;
  post = () => new Promise(done => { resolve = done; });
  const originalUrl = window.location.href;
  const app = await mount("plan", "account-a@example.test");
  try {
    await wait(() => expect(button("Выбрать Pro")).toBeDefined());
    await click("Выбрать Pro");
    await click("Перейти к оплате в ЮKassa");
    const saved = localStorage.getItem("max-checkout:account-a@example.test");
    expect(saved).not.toBeNull();
    await app.rerender("account-b@example.test");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    // Same-origin hash navigation exercises location.assign without a real
    // provider/navigation: jsdom supports this observable navigation boundary.
    await act(async () => {
      resolve(Response.json({ ...payment, confirmation_url: originalUrl.split("#")[0] + "#old-account-payment" }));
      await new Promise(done => setTimeout(done, 30));
    });
    expect(window.location.href).toBe(originalUrl);
    expect(localStorage.getItem("max-checkout:account-a@example.test")).toBe(saved);
    expect(localStorage.getItem("max-checkout:account-b@example.test")).toBeNull();
    expect(requests).toHaveLength(1);
  } finally { window.history.replaceState(null, "", originalUrl); await app.close(); }
});

it("accepts its own checkout response after StrictMode effect replay", async () => {
  const app = await mount("plan", "qa@example.test", true);
  try {
    await wait(() => expect(button("Выбрать Pro")).toBeDefined());
    await click("Выбрать Pro");
    await click("Перейти к оплате в ЮKassa");
    await wait(() => expect(JSON.parse(localStorage.getItem("max-checkout:qa@example.test")!).paymentId).toBe(payment.id));
    expect(requests).toHaveLength(1);
  } finally { await app.close(); }
});

it("shows actual package units and edit-only restriction without promising ruble credit", async () => {
  const app = await mount("billing");
  try {
    await wait(() => expect(button("Выбрать")).toBeDefined());
    const pack = document.querySelector(".account-package")!;
    expect(pack.textContent).toContain("5 правок");
    expect(pack.textContent).toContain("существующих приложений");
    expect(pack.textContent).not.toContain("888");
    expect(document.body.textContent).not.toContain("Зачислим");
    await click("Выбрать");
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Новые проекты не включены");
  } finally { await app.close(); }
});

it("shows generation and edit allowances from the plan and no monetary included credit", async () => {
  publicPlans = [
    { ...freePlan, version: 4, entitlements: { ...freePlan.entitlements, generation_messages_lifetime: undefined, initial_generations_lifetime: 1, edits_lifetime: 1 } },
    { ...plan, version: 3, price_rub: "1.00", included_credit_rub: "0", entitlements: { ...plan.entitlements, initial_generations_monthly: 2, edits_monthly: 25, always_on_slots: 1 } },
  ];
  const app = await mount("plan");
  try {
    await wait(() => expect(button("Выбрать Pro")).toBeDefined());
    const entries = [...document.querySelectorAll("article dl > div")].map(row => row.textContent);
    expect(entries).toContain("Генераций за всё время1");
    expect(entries).toContain("Правок за всё время1");
    expect(entries).toContain("Генераций в месяц2");
    expect(entries).toContain("Правок в месяц25");
    expect(entries).toContain("Постоянно работающих приложений1");
    expect(document.body.textContent).not.toContain("Включённый кредит");
    await click("Выбрать Pro");
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog.textContent).toContain("2 генерации");
    expect(dialog.textContent).toContain("25 правок");
    expect(dialog.textContent).not.toContain("Зачисление на баланс");
  } finally { await app.close(); }
});

it("preserves the original monetary benefit when reviewing a saved legacy order", async () => {
  localStorage.setItem("max-checkout:qa@example.test", JSON.stringify({
    key: "legacy-key", autoRenew: false,
    selection: { kind: "topup", code: "start", title: "Прежний заказ", price: "123", credit: "321" },
  }));
  const app = await mount("billing");
  try {
    await wait(() => expect(button("Продолжить незавершённую оплату")).toBeDefined());
    await click("Продолжить незавершённую оплату");
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog.textContent).toContain("123");
    expect(dialog.textContent).toContain("По прежним условиям: 321");
    expect(dialog.textContent).not.toContain("5 правок");
    expect(dialog.textContent).not.toContain("888");
    expect(requests).toHaveLength(0);
  } finally { await app.close(); }
});

it("reviews server amounts before POST and prevents double submission while uncertain", async () => {
  let resolve!: (value: Response) => void; post = () => new Promise(r => { resolve = r; });
  const app = await mount("billing");
  try {
    await wait(() => expect(button("Выбрать")).toBeDefined()); await click("Выбрать");
    expect(requests).toHaveLength(0);
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog.textContent).toContain("777"); expect(dialog.textContent).toContain("5 правок"); expect(dialog.textContent).not.toContain("888"); expect(dialog.textContent).not.toContain("Зачисление на баланс"); expect(dialog.textContent).toContain("Разовое");
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
it("shows disabled provider reason and unit balances without offering checkout", async () => {
  config = { ...(config as object), enabled: false, reason: "Магазин не подключён" }; const app = await mount("billing");
  try { await wait(() => expect(document.body.textContent).toContain("Магазин не подключён")); expect(button("Выбрать").disabled).toBe(true); expect(document.querySelector('[aria-label="Доступно правок"]')?.textContent).toBe("44"); expect(document.querySelector(".balance-card")?.textContent).not.toContain("₽"); } finally { await app.close(); }
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

it("does not trust a terminal checkout response before a confirming GET", async () => {
  post = async () => { payments = [payment]; return Response.json({ ...payment, status: "succeeded" }); };
  const app = await mount("billing");
  try { await wait(() => expect(button("Выбрать")).toBeDefined()); await click("Выбрать"); await click("Перейти к оплате в ЮKassa"); await wait(() => expect(document.body.textContent).toContain("Ожидает оплаты")); expect(document.body.textContent).not.toContain("Оплата подтверждена"); } finally { await app.close(); }
});
it("includes current legal consent only after explicit renewal opt-in", async () => {
  vi.stubEnv("NEXT_PUBLIC_LEGAL_DOCUMENT_VERSION", "");
  const app = await mount("plan");
  try {
    await wait(() => expect(button("Выбрать Pro")).toBeDefined()); await click("Выбрать Pro");
    await act(async () => document.querySelector<HTMLInputElement>('[role="dialog"] input[type="checkbox"]')!.click());
    await click("Перейти к оплате в ЮKassa"); await wait(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({ plan_code: "pro", auto_renew: true, consent_version: "2026-10-27" });
  } finally { await app.close(); }
});
it.each([
  [undefined, "2026-10-27"],
  ["", "2026-10-27"],
  ["2026-11-01", "2026-11-01"],
])("sends the renewal consent version for checkout and restore (%s)", async (configured, expected) => {
  vi.stubEnv("NEXT_PUBLIC_LEGAL_DOCUMENT_VERSION", configured);
  const sent: Array<{ path: string; method: string | undefined; body: unknown }> = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    sent.push({ path, method: init?.method, body: JSON.parse(String(init?.body)) });
    return Response.json({});
  }));
  await createSubscriptionCheckout("pro", true, "renewal-test");
  await manageSubscription("restore");
  await createSubscriptionCheckout("pro", false, "manual-test");
  await manageSubscription("cancel");
  expect(sent).toEqual([
    { path: "/api/payments/subscription", method: "POST", body: { plan_code: "pro", idempotency_key: "renewal-test", auto_renew: true, consent_version: expected } },
    { path: "/api/billing/subscription", method: "PATCH", body: { action: "restore", consent_version: expected } },
    { path: "/api/payments/subscription", method: "POST", body: { plan_code: "pro", idempotency_key: "manual-test", auto_renew: false, consent_version: null } },
    { path: "/api/billing/subscription", method: "PATCH", body: { action: "cancel", consent_version: null } },
  ]);
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
  try {
    await wait(() => expect(button("Выбрать Pro")).toBeDefined());
    const entries = [...document.querySelectorAll("article dl > div")].map(row => row.textContent);
    expect(entries).toContain("Публикаций5"); expect(entries).toContain("Постоянно работающих приложений2"); expect(entries).toContain("ИнтеграцииНет");
    expect(entries).toContain("Проектов1"); expect(entries).toContain("Публикаций1"); expect(entries).toContain("ИнтеграцииДа");
    expect(entries).toContain("Сообщений для генерации за всё время1");
    expect(document.body.textContent).toContain("после каждой успешной ежемесячной оплаты");
    expect(document.body.textContent).not.toContain("null");
  } finally { await app.close(); }
});

it("explains preserved historical paid subscription terms without hiding its price", async () => {
  currentPlan = { ...freePlan, id: "historical-pro", code: "pro", name: "Pro", version: 1, price_rub: "1490.00" };
  const app = await mount("plan");
  try {
    await wait(() => expect(button("Выбрать Pro")).toBeDefined());
    expect(document.querySelector(".account-plan-current")?.textContent).toMatch(/1\s490/);
    expect(document.body.textContent).toContain("У вас сохранены условия прежней версии тарифа");
  } finally { await app.close(); }
});

it.each([
  ["qa_internal", "РўРµСЃС‚РѕРІС‹Р№ Р±РµР·Р»РёРјРёС‚", "Тестовый безлимит"],
  ["pro", "Pro - персональные условия", "Pro - персональные условия"],
])("shows the current %s plan name without changing billing values", async (code, name, expected) => {
  currentPlan = { ...freePlan, id: "current-plan", code, name, price_rub: "1490.00" };
  const app = await mount("plan");
  try {
    await wait(() => expect(document.querySelector(".account-plan-current h2")?.textContent).toBe(expected));
    expect(document.querySelector(".account-plan-current strong")?.textContent).toMatch(/1\s490/);
    expect(requests).toHaveLength(0);
  } finally { await app.close(); }
});

it.each([
  ["qa_internal", "РўРµСЃС‚РѕРІС‹Р№ Р±РµР·Р»РёРјРёС‚", "Тестовый безлимит"],
  ["pro", "РўРµ — персональные условия", "РўРµ — персональные условия"],
])("uses the known %s display name consistently in usage without decoding arbitrary catalog names", async (code, name, expected) => {
  usageResponse = { ...usage, plan: { ...freePlan, code, name } };
  const app = await mount("billing");
  try {
    await wait(() => expect(document.querySelector(".usage-period")?.textContent).toContain(`тариф ${expected}`));
    await click("Лимиты");
    expect(document.querySelector(".usage-limits-heading > strong")?.textContent).toBe(expected);
    expect(requests).toHaveLength(0);
  } finally { await app.close(); }
});

it("shows the period's spend and the plan's use next to each limit", async () => {
  const app = await mount("billing");
  try {
    const panel = () => document.querySelector(".usage-dashboard")!;
    await wait(() => expect(panel().textContent).toContain("Сборки приложений"));
    const text = panel().textContent!;
    expect(text).toContain("Текущий месяц");
    expect(text).toContain("тариф Free");
    expect(text).toContain("Завершено4");
    expect(text).toContain("С ошибкой1");
    expect(text).toContain("Опубликовано приложений2");
    expect(text).not.toContain("Бесплатные сборки");
    await click("Лимиты");
    const rows = [...panel().querySelectorAll("li")];
    const values = (row: Element) => [...row.querySelectorAll("dd")].map(value => value.textContent);
    expect(values(rows[0])).toEqual(["2", "Без ограничений"]);
    expect(values(rows[1])).toEqual(["2", "1"]);
    expect(rows[1].textContent).toContain("сверх тарифа");
    expect(values(rows[2])).toEqual(["1", "входит в тариф"]);
  } finally { await app.close(); }
});

it.each([
  ["Подтверждено провайдером", 1, 0, 0, "0"],
  ["Оценочная стоимость", 0, 1, 0, "5"],
  ["Смешанная стоимость", 1, 1, 1, "10"],
  ["Источник стоимости неизвестен", 0, 0, 1, "5"],
  ["Нет вызовов", 0, 0, 0, "0"],
] as const)("keeps internal %s costs out of the product-unit dashboard", async (_label, confirmed, estimated, unknown, amount) => {
  const breakdown = { confirmed: { calls: confirmed, cost_rub: "0" },
    estimated: { calls: estimated, cost_rub: estimated ? "5" : "0" },
    unknown: { calls: unknown, cost_rub: unknown ? "5" : "0" } };
  usageResponse = { ...usage, total_ai_cost_rub: amount, total_ai_cost_breakdown: breakdown,
    app_ai_answers: { ...usage.app_ai_answers, calls: 0, cost_rub: "0" },
    generations: { ...usage.generations, calls: confirmed + estimated + unknown, cost_rub: amount, cost_breakdown: breakdown } };
  const app = await mount("billing");
  try {
    await wait(() => expect(button("Списания")).toBeDefined());
    await click("Списания");
    const panel = app.container.querySelector('[role="tabpanel"]')!;
    expect([...panel.querySelectorAll(".usage-debit-grid dd")].map(e => e.textContent)).toEqual(["2", "3"]);
    expect(panel.textContent).not.toContain("₽");
    expect(app.container.querySelector(".balance-card")?.textContent).not.toContain("₽");
  } finally { await app.close(); }
});

it.each([undefined, null])("keeps unit debits available independently of model cost breakdown %s", async breakdown => {
  usageResponse = { ...usage, total_ai_cost_breakdown: breakdown,
    generations: { ...usage.generations, cost_breakdown: breakdown } };
  const app = await mount("billing");
  try {
    await wait(() => expect(button("Списания")).toBeDefined());
    await click("Списания");
    const panel = app.container.querySelector('[role="tabpanel"]')!;
    expect([...panel.querySelectorAll(".usage-debit-grid dd")].map(e => e.textContent)).toEqual(["2", "3"]);
    expect(panel.textContent).not.toContain("₽");
  } finally { await app.close(); }
});
it("keeps every operation labelled and semantically tabular when mobile rows reflow", async () => {
  payments = [payment];
  const app = await mount("transactions");
  try {
    await wait(() => expect(document.querySelector("tbody tr")).not.toBeNull());
    const table = document.querySelector('table[role="table"]');
    expect(table?.getAttribute("aria-label")).toBe("Платежи аккаунта");
    const row = document.querySelector('tbody [role="row"]')!;
    expect([...row.querySelectorAll('[role="cell"] > .account-operation-label')].map(label => label.textContent)).toEqual(["Дата", "Операция", "Сумма", "Статус"]);
    expect(row.querySelector('[headers="payment-amount"]')?.textContent).toContain("777");
    expect(row.querySelector('[headers="payment-status"]')?.textContent).toContain("Ожидает оплаты");
  } finally { await app.close(); }
});

it.each(["subscription_already_active", "subscription_plan_not_purchasable", "subscription_consent_required"])("releases a fresh definitively rejected plan intent for correction: %s", async code => {
  post = async () => Response.json({ error: { code, message: "Заказ отклонён до создания" } }, { status: 409 });
  const app = await mount("all");
  try {
    await wait(() => expect(button("Выбрать Pro")).toBeDefined()); await click("Выбрать Pro");
    await act(async () => document.querySelector<HTMLInputElement>('[role="dialog"] input[type="checkbox"]')!.click());
    await click("Перейти к оплате в ЮKassa");
    await wait(() => expect(document.body.textContent).toContain("Заказ отклонён до создания"));
    expect(localStorage.length).toBe(0);
    expect(button("Продолжить незавершённую оплату")).toBeUndefined();
    expect(document.querySelector<HTMLInputElement>('[role="dialog"] input[type="checkbox"]')!.disabled).toBe(false);
    const key = requests[0].idempotency_key;
    post = async () => Response.json(payment);
    if (code === "subscription_consent_required") {
      await act(async () => document.querySelector<HTMLInputElement>('[role="dialog"] input[type="checkbox"]')!.click());
    } else {
      await click("Закрыть"); await click("Выбрать");
    }
    await click("Перейти к оплате в ЮKassa"); await wait(() => expect(requests).toHaveLength(2));
    expect(requests[1].idempotency_key).not.toBe(key);
    expect(requests[1]).toMatchObject(code === "subscription_consent_required" ? { plan_code: "pro", auto_renew: false, consent_version: null } : { package_code: "start" });
  } finally { await app.close(); }
});

it.each([["subscription_checkout_in_progress", 409], ["conflict", 409], ["unrecognized_error", 409], ["payment_provider_unavailable", 503]] as const)("retains the same plan intent after uncertain/pending rejection %s", async (code, status) => {
  post = async () => Response.json({ error: { code, message: "Статус пока неизвестен" } }, { status });
  const app = await mount("plan");
  try {
    await wait(() => expect(button("Выбрать Pro")).toBeDefined()); await click("Выбрать Pro"); await click("Перейти к оплате в ЮKassa");
    await wait(() => expect(document.body.textContent).toContain("Статус пока неизвестен"));
    expect(button("Продолжить незавершённую оплату")).toBeDefined();
    expect(document.querySelector<HTMLInputElement>('[role="dialog"] input[type="checkbox"]')!.disabled).toBe(true);
    await click("Перейти к оплате в ЮKassa"); await wait(() => expect(requests).toHaveLength(2));
    expect(requests[1]).toEqual(requests[0]);
  } finally { await app.close(); }
});

it("does not clear an uncertain earlier payment when a later consent check rejects before idempotency lookup", async () => {
  post = async () => { throw new Error("Connection lost"); };
  let app = await mount("plan");
  try {
    await wait(() => expect(button("Выбрать Pro")).toBeDefined()); await click("Выбрать Pro");
    await act(async () => document.querySelector<HTMLInputElement>('[role="dialog"] input[type="checkbox"]')!.click());
    await click("Перейти к оплате в ЮKassa"); await wait(() => expect(document.body.textContent).toContain("Connection lost"));
  } finally { await app.close(); }
  const first = requests[0];
  post = async () => Response.json({ error: { code: "subscription_consent_required", message: "Обновились условия" } }, { status: 409 });
  app = await mount("plan");
  try {
    await wait(() => expect(button("Продолжить незавершённую оплату")).toBeDefined()); await click("Продолжить незавершённую оплату"); await click("Перейти к оплате в ЮKassa");
    await wait(() => expect(document.body.textContent).toContain("Обновились условия"));
    expect(requests[1]).toEqual(first);
    expect(localStorage.length).toBe(1);
    expect(button("Продолжить незавершённую оплату")).toBeDefined();
  } finally { await app.close(); }
});
