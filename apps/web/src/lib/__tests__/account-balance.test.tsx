import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { AccountBalance } from "@/components/account/AccountBalance";
import type { GenerationCredits } from "@/lib/api/account";

const api = vi.hoisted(() => ({ credits: vi.fn(), usage: vi.fn(), wallet: vi.fn() }));
vi.mock("@/lib/api/account", () => ({ getGenerationCredits: api.credits, getBillingUsage: api.usage }));
vi.mock("@/lib/api/wallet", () => ({ getWallet: api.wallet }));
const credits: GenerationCredits = {
  edit_only_units: 5, flexible_units: 15, available_units: 20, reserved_units: 7,
  initial_build_units: 5, edit_units: 1, included_generations_remaining: 2,
  included_edits_remaining: 24, included_generations_limit: 2,
  included_edits_limit: 25, period_end: "2026-11-28T17:31:00Z",
};
let cleanup: (() => Promise<void>) | undefined;
afterEach(async () => { await cleanup?.(); vi.resetAllMocks(); });
async function mount(value: GenerationCredits | Promise<GenerationCredits> | Error, unlimited?: boolean) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  api.credits.mockImplementation(() => value instanceof Error ? Promise.reject(value) : Promise.resolve(value));
  api.usage.mockResolvedValue({ free_generations: unlimited === undefined ? {} : { unlimited } });
  api.wallet.mockResolvedValue({ balance_rub: "65.042", recent_charges: [] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  await act(async () => root.render(<QueryClientProvider client={client}><AccountBalance /></QueryClientProvider>));
  cleanup = async () => { await act(async () => root.unmount()); client.clear(); container.remove(); };
  return container;
}
const wait = async (check: () => void) => act(async () => { await vi.waitFor(check); });

it("shows separate available generations and edits without counting held units twice", async () => {
  const container = await mount(credits);
  await wait(() => expect(container.querySelector('[aria-label="Доступно генераций"]')?.textContent).toBe("5"));
  expect(container.querySelector('[aria-label="Доступно правок"]')?.textContent).toBe("44");
  expect(container.textContent).not.toContain("₽");
  expect(api.wallet).not.toHaveBeenCalled();
  expect(container.querySelector(".balance-reserve")?.textContent).toContain("Учтено в остатке");
  expect(container.querySelector('a[href="#billing-packages"]')).not.toBeNull();
});

it.each([undefined, false])("keeps edits-only packages finite when unlimited is %s", async unlimited => {
  const container = await mount({ ...credits, flexible_units: 0, available_units: 5,
    included_generations_remaining: 0, included_edits_remaining: 0 }, unlimited);
  await wait(() => expect(container.querySelector('[aria-label="Доступно генераций"]')?.textContent).toBe("0"));
  expect(container.querySelector('[aria-label="Доступно правок"]')?.textContent).toBe("5");
  expect(container.textContent).toContain("Доступны правки");
  expect(container.textContent).not.toContain("Можно создавать");
  expect(container.textContent).not.toContain("Лимиты исчерпаны");
});

it("uses API unit costs and floors incomplete packages", async () => {
  const container = await mount({ ...credits, initial_build_units: 6, edit_units: 2 });
  await wait(() => expect(container.querySelector('[aria-label="Доступно генераций"]')?.textContent).toBe("4"));
  expect(container.querySelector('[aria-label="Доступно правок"]')?.textContent).toBe("34");
});

it.each([undefined, false])("renders exhausted zero limits when unlimited is %s", async unlimited => {
  const container = await mount({ ...credits, flexible_units: 0, available_units: 0,
    edit_only_units: 0, included_generations_remaining: 0, included_edits_remaining: 0,
    included_generations_limit: 0, included_edits_limit: 0 }, unlimited);
  await wait(() => expect(container.textContent).toContain("Лимиты исчерпаны"));
  expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
  expect(container.querySelectorAll('[role="meter"]')).toHaveLength(0);
});

it("shows confirmed unlimited generations and edits despite zero finite inventory", async () => {
  const container = await mount({ ...credits, flexible_units: 0, available_units: 0,
    edit_only_units: 0, reserved_units: 0, included_generations_remaining: 0,
    included_edits_remaining: 0, included_generations_limit: 0,
    included_edits_limit: 0, period_end: null }, true);
  await wait(() => expect(container.querySelector('[aria-label="Доступно генераций"]')?.textContent).toBe("∞"));
  expect(container.querySelector('[aria-label="Доступно правок"]')?.textContent).toBe("∞");
  expect(container.querySelector(".balance-state")?.textContent).toBe("Без ограничений");
  expect(container.textContent).not.toContain("Лимиты исчерпаны");
  expect(container.querySelectorAll(".balance-plan, [role=\"meter\"]")).toHaveLength(0);
  expect(container.textContent).not.toContain("По тарифу");
  expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
  expect(container.querySelector(".balance-package-empty")?.textContent).toContain("В пакетах пока нет правок");
});

it("keeps finite package and reserve details under an unlimited allowance", async () => {
  const container = await mount(credits, true);
  await wait(() => expect(container.querySelector('[aria-label="Доступно генераций"]')?.textContent).toBe("∞"));
  expect(container.querySelector('[aria-label="Доступно правок"]')?.textContent).toBe("∞");
  expect([...container.querySelectorAll('[aria-label="Лимиты из пакетов"] dd')].map(item => item.textContent)).toEqual(["3", "20"]);
  expect(container.querySelector(".balance-reserve")?.textContent).toContain("Учтено в остатке");
  expect(container.querySelector("time")?.getAttribute("datetime")).toBe(credits.period_end);
  expect(container.querySelectorAll(".balance-plan, [role=\"meter\"]")).toHaveLength(0);
});

it("keeps unknown loading and failed credits distinct from zero", async () => {
  const container = await mount(new Error("Сервис остатков недоступен"));
  await wait(() => expect(container.querySelector('[role="alert"]')?.textContent).toContain("Сервис остатков недоступен"));
  expect(container.textContent).not.toContain("Лимиты исчерпаны");
  expect(container.querySelector('[aria-label="Доступно генераций"]')?.textContent).toBe("—");
  api.credits.mockResolvedValue(credits);
  await act(async () => container.querySelector<HTMLButtonElement>('[role="alert"] button')!.click());
  await wait(() => expect(container.querySelector('[aria-label="Доступно генераций"]')?.textContent).toBe("5"));
});

it("shows placeholders while credits are loading", async () => {
  const container = await mount(new Promise<GenerationCredits>(() => {}));
  expect(container.querySelector('[role="status"]')?.textContent).toContain("Загружаем остатки");
  expect(container.querySelector('[aria-label="Доступно генераций"]')?.textContent).toBe("—");
  expect(container.querySelector('[aria-label="Доступно правок"]')?.textContent).toBe("—");
  expect(container.textContent).not.toContain("Лимиты исчерпаны");
});

it("opens a package breakdown in generation/edit units with an explicit alternative budget", async () => {
  const container = await mount(credits);
  await wait(() => expect(container.querySelector(".balance-details")).not.toBeNull());
  const details = container.querySelector<HTMLDetailsElement>(".balance-details")!;
  expect(details.open).toBe(false);
  await act(async () => details.querySelector("summary")!.click());
  expect(details.open).toBe(true);
  const breakdown = details.querySelector('[aria-label="Лимиты из пакетов"]')!;
  expect([...breakdown.querySelectorAll("dd")].map(e => e.textContent)).toEqual(["3", "20"]);
  expect(details.textContent).toContain("Из них 5 — только для правок");
  expect(details.querySelector(".balance-conversion")?.textContent).toContain("1 генерация");
  expect(details.querySelector(".balance-conversion")?.textContent).toContain("5 правок");
  expect(details.textContent).toContain("Общий остаток: генерации или правки");
  expect(details.querySelector("time")?.getAttribute("datetime")).toBe(credits.period_end);
  expect(details.textContent).not.toMatch(/единиц|Денежный|₽/);
  await act(async () => details.querySelector("summary")!.click());
  expect(details.open).toBe(false);
});

it("shows a concise empty package state without an imaginary reserve or renewal date", async () => {
  const container = await mount({ ...credits, available_units: 0, flexible_units: 0,
    edit_only_units: 0, reserved_units: 0, period_end: null });
  await wait(() => expect(container.querySelector(".balance-package-empty")).not.toBeNull());
  const details = container.querySelector(".balance-details")!;
  expect(details.textContent).toContain("В пакетах пока нет правок");
  expect(details.querySelector('[aria-label="Лимиты из пакетов"]')).toBeNull();
  expect(details.querySelector(".balance-reserve")).toBeNull();
  expect(details.querySelector("time")).toBeNull();
});

it("derives the conversion from API costs and avoids invalid or fractional equivalents", async () => {
  let container = await mount({ ...credits, initial_build_units: 6, edit_units: 2 });
  await wait(() => expect(container.querySelector(".balance-conversion")?.textContent).toContain("3 правки"));
  await cleanup?.(); cleanup = undefined;
  container = await mount({ ...credits, initial_build_units: 5, edit_units: 3 });
  await wait(() => expect(container.querySelector(".balance-details")).not.toBeNull());
  expect(container.querySelector(".balance-conversion")).toBeNull();
  expect(container.textContent).not.toMatch(/NaN|Infinity/);
});
