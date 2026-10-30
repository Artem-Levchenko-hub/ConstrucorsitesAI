import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AccountUsage } from "@/components/account/AccountUsage";
import type { BillingUsage } from "@/lib/api/account";

vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
const bucket = { calls: 2, cost_rub: "15.5", tokens_in: 5, tokens_out: 3 };
const fixture: BillingUsage = {
  period: { start: "2026-10-01T00:00:00Z", end: "2026-10-28T12:00:00Z", source: "calendar_month" },
  plan: null, subscription_status: null,
  generations: { ...bucket, total: 8, completed: 5, failed: 1, active: 1, cancelled: 1 },
  app_ai_answers: { ...bucket, cost_rub: "3" }, other_ai: { ...bucket, cost_rub: "1" },
  total_ai_cost_rub: "19.5", publications: { total: 3, projects: 2 },
  free_generations: { limit: 3, used: 2, left: 1, unlimited: false },
  wallet: { balance_rub: "100", debited_rub: "42", credited_rub: "200", charges: 7 },
  credits: { initial_generations: 2, edits: 3 },
  entitlements: [
    { key: "zero", label: "Нет мест", kind: "limit", used: 0, limit: 0, exceeded: false },
    { key: "unlimited", label: "Приложения", kind: "limit", used: 2, limit: null, exceeded: false },
    { key: "exceeded", label: "Публикации", kind: "limit", used: 3, limit: 2, exceeded: true },
    { key: "disabled", label: "Интеграции", kind: "flag", used: 0, enabled: false, exceeded: false },
  ],
};
let response: BillingUsage;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  response = structuredClone(fixture);
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(response)));
});
afterEach(() => vi.unstubAllGlobals());
async function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  await act(async () => root.render(<QueryClientProvider client={client}><AccountUsage /></QueryClientProvider>));
  await act(async () => { await vi.waitFor(() => expect(container.textContent).not.toContain("Считаем расход…")); });
  return { container, close: async () => { await act(async () => root.unmount()); client.clear(); container.remove(); } };
}
const tabs = () => [...document.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
const panel = () => document.querySelector<HTMLElement>('[role="tabpanel"]')!;
async function select(index: number) { await act(async () => tabs()[index].click()); }

it("switches one visible panel by keyboard and click without making a payment request", async () => {
  const app = await mount();
  try {
    expect(tabs().map(tab => tab.textContent)).toEqual(["Сборки", "Списания", "Лимиты"]);
    expect(panel().textContent).toContain("Сборки приложений");
    await act(async () => { tabs()[0].focus(); tabs()[0].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })); });
    expect(document.activeElement).toBe(tabs()[1]);
    expect(tabs()[1].getAttribute("aria-selected")).toBe("true");
    expect(panel().getAttribute("aria-labelledby")).toBe(tabs()[1].id);
    expect(document.getElementById(tabs()[1].getAttribute("aria-controls")!)).toBe(panel());
    expect(document.querySelectorAll('[role="tabpanel"]')).toHaveLength(1);
    expect([...panel().querySelectorAll(".usage-debit-grid dd")].map(e => e.textContent)).toEqual(["2", "3"]);
    expect(panel().textContent).not.toContain("₽");
    await act(async () => tabs()[1].dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true })));
    expect(document.activeElement).toBe(tabs()[2]);
    await act(async () => tabs()[2].dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true })));
    expect(document.activeElement).toBe(tabs()[0]);
    await select(1);
    expect(panel().textContent).toContain("Только подтверждённые списания");
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  } finally { await app.close(); }
});

it("distinguishes zero, unlimited, exceeded and disabled limits without invalid meters", async () => {
  const app = await mount();
  try {
    await select(2);
    const items = [...panel().querySelectorAll("li")];
    const values = (item: Element) => [...item.querySelectorAll("dl > div")].map(row => [row.querySelector("dt")?.textContent, row.querySelector("dd")?.textContent]);
    expect(values(items[0])).toEqual([["Используется сейчас", "0"], ["Лимит тарифа", "0"]]);
    expect(items[0].querySelector('[role="meter"]')).toBeNull();
    expect(values(items[1])).toEqual([["Используется сейчас", "2"], ["Лимит тарифа", "Без ограничений"]]);
    expect(items[1].querySelector('[role="meter"]')).toBeNull();
    expect(values(items[2])).toEqual([["Используется сейчас", "3"], ["Лимит тарифа", "2"]]);
    expect(items[2].textContent).toContain("сверх тарифа");
    const meter = items[2].querySelector('[role="meter"]')!;
    expect(meter.getAttribute("aria-valuenow")).toBe("2");
    expect(meter.getAttribute("aria-valuetext")).toContain("Используется сейчас: 3, лимит тарифа: 2");
    expect(items[3].textContent).toContain("не входит в тариф");
  } finally { await app.close(); }
});

it("identifies limits as a current snapshot instead of activity in the requested period", async () => {
  response.period = { start: "2026-09-01T00:00:00Z", end: "2026-09-02T00:00:00Z", source: "custom" };
  const app = await mount();
  try {
    expect(app.container.querySelector("h2")?.textContent).toBe("Расход за период");
    expect(app.container.querySelector(".usage-period summary")?.textContent).toBe("Выбранный период");
    await select(2);
    expect(app.container.querySelector("h2")?.textContent).toBe("Лимиты тарифа");
    expect(app.container.querySelector(".usage-period")).toBeNull();
    expect(panel().textContent).toContain("Текущее использование не зависит от выбранного периода");
    await select(1);
    expect(app.container.querySelector("h2")?.textContent).toBe("Расход за период");
    expect(app.container.querySelectorAll(".usage-period time")).toHaveLength(2);
  } finally { await app.close(); }
});

it("keeps zero activity meaningful without surfacing the obsolete free-build counter", async () => {
  response.generations = { ...bucket, calls: 0, cost_rub: "0", total: 0, completed: 0, failed: 0, cancelled: 0, active: 0 };
  response.free_generations = { limit: 0, used: 0, left: 0, unlimited: true };
  response.credits = { initial_generations: 0, edits: 0 };
  const app = await mount();
  try {
    expect(panel()).not.toBeNull();
    expect(panel().querySelector(".usage-hero-value")?.textContent).toBe("0");
    expect(panel().textContent).not.toContain("Бесплатные сборки");
    expect(panel().textContent).not.toContain("NaN");
    await select(1);
    expect([...panel().querySelectorAll(".usage-debit-grid dd")].map(e => e.textContent)).toEqual(["0", "0"]);
  } finally { await app.close(); }
});

it("does not present model cost estimates or historical wallet amounts as product-unit debits", async () => {
  response.generations.calls = 3;
  response.generations.cost_breakdown = {
    confirmed: { calls: 1, cost_rub: "10" },
    estimated: { calls: 1, cost_rub: "5" },
    unknown: { calls: 1, cost_rub: "0.5" },
  };
  const app = await mount();
  try {
    await select(1);
    expect(panel().textContent).not.toMatch(/₽|стоимост|баланса|Зачислено/);
    expect([...panel().querySelectorAll(".usage-debit-grid dd")].map(e => e.textContent)).toEqual(["2", "3"]);
    expect(document.querySelectorAll(".usage-period time")).toHaveLength(2);
  } finally { await app.close(); }
});

it("does not invent zero debits when an older API response lacks the unit journal", async () => {
  delete response.credits;
  const app = await mount();
  try {
    await select(1);
    expect(panel().querySelector('[role="status"]')?.textContent).toContain("Данные о списаниях временно недоступны");
    expect(panel().querySelector(".usage-debit-grid")).toBeNull();
    expect(panel().textContent).not.toContain("₽");
  } finally { await app.close(); }
});

it("keeps a failed usage request retryable without any billing mutation", async () => {
  vi.mocked(fetch).mockRejectedValueOnce(new Error("Usage unavailable"));
  const app = await mount();
  try {
    expect(app.container.querySelector('[role="alert"]')?.textContent).toContain("Usage unavailable");
    const retry = [...app.container.querySelectorAll("button")].find(button => button.textContent === "Повторить")!;
    await act(async () => retry.click());
    await act(async () => { await vi.waitFor(() => expect(tabs()).toHaveLength(3)); });
    expect(panel().querySelector(".usage-hero-value")?.textContent).toBe("8");
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  } finally { await app.close(); }
});
