import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import PricingPage from "@/app/pricing/page";

vi.mock("@/lib/auth-mock", () => ({ getSession: async () => null }));
vi.mock("@/components/marketing/PublicPageShell", () => ({ PublicPageShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

const plans = [
  { id: "free-v3", code: "free", version: 3, name: "Free", price_rub: "0.00", included_credit_rub: "0", billing_interval: "month", entitlements: { max_projects: 1, static_publish_slots: 1, generation_messages_lifetime: 1, integrations: true } },
  { id: "pro-v2", code: "pro", version: 2, name: "Pro", price_rub: "2990.00", included_credit_rub: "500", billing_interval: "month", entitlements: { max_projects: 3, static_publish_slots: 3, integrations: true } },
  { id: "business-v2", code: "business", version: 2, name: "Business", price_rub: "6990.00", included_credit_rub: "1500", billing_interval: "month", entitlements: { max_projects: 10, static_publish_slots: 10, integrations: true } },
];

it("renders anonymous server catalog, lifetime allowance and monthly credit without caching", async () => {
  vi.stubEnv("INTERNAL_API_URL", "http://api.internal:8000/");
  const fetchMock = vi.fn().mockResolvedValue(Response.json(plans)); vi.stubGlobal("fetch", fetchMock);
  const html = renderToStaticMarkup(await PricingPage());
  expect(html).toMatch(/2\s990/); expect(html).toMatch(/6\s990/);
  expect(html).toContain("Сообщений для генерации за всё время");
  expect(html).toContain("после каждой успешной ежемесячной оплаты");
  expect(html).toContain("/login?next=/billing/plan");
  expect(fetchMock).toHaveBeenCalledWith("http://api.internal:8000/api/billing/plans", expect.objectContaining({ cache: "no-store" }));
  expect(fetchMock.mock.calls[0][1].headers).toBeUndefined();
});

it("uses changed prices from the API rather than a static marketing snapshot", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(plans.map(plan => plan.code === "pro" ? { ...plan, price_rub: "3210.00" } : plan))));
  expect(renderToStaticMarkup(await PricingPage())).toMatch(/3\s210/);
});

it.each([
  { code: "free", name: "Free", creations: 1, edits: 1, period: "lifetime", price: "0", label: "За всё время" },
  { code: "pro", name: "Pro", creations: 2, edits: 25, period: "paid_month", price: "2990", label: "За оплаченный месяц" },
  { code: "business", name: "Business", creations: 5, edits: 50, period: "paid_month", price: "6990", label: "За оплаченный месяц" },
  { code: "pro", name: "Pro", creations: 3, edits: 40, period: "paid_month", price: "3210", label: "За оплаченный месяц" },
])("renders $name split allowance from entitlements while keeping legacy Pro credit", async ({ code, name, creations, edits, period, price, label }) => {
  const legacy = { ...plans[1], id: "legacy-pro", name: "Pro прежний" };
  const included = { ...plans[1], id: "split-plan", code, name, version: 4, price_rub: price, included_credit_rub: "0", entitlements: { max_projects: 3, static_publish_slots: 3, included_creations: creations, included_edits: edits, generation_allowance_period: period } };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json([included, legacy])));
  const host = document.createElement("div");
  host.innerHTML = renderToStaticMarkup(await PricingPage());
  const cards = [...host.querySelectorAll("article")];
  const newPro = cards.find(card => card.querySelector("h2")?.textContent === name)!;
  const oldPro = cards.find(card => card.querySelector("h2")?.textContent === "Pro прежний")!;
  expect(newPro.textContent).toContain(`Созданий приложений${creations}`);
  expect(newPro.textContent).toContain(`Правок${edits}`);
  expect(newPro.textContent).toContain(label);
  expect(newPro.textContent).not.toMatch(/баланс|кредит/i);
  expect(oldPro.textContent).toMatch(/500.*на баланс после/);
  expect(oldPro.textContent).not.toContain("Созданий приложений");
});

it.each([new Response(null, { status: 503 }), Response.json([]), Response.json({ error: "bad" }), Response.json([{ code: "pro" }])])("shows honest unavailable state for failed or invalid catalog", async response => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
  const html = renderToStaticMarkup(await PricingPage());
  expect(html).toContain("Не удалось загрузить тарифы");
  expect(html).not.toMatch(/2\s990|6\s990/);
});

it("shows unavailable state on a network failure", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  expect(renderToStaticMarkup(await PricingPage())).toContain("Не удалось загрузить тарифы");
});
