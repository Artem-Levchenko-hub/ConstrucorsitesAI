import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { AccountBalance } from "@/components/account/AccountBalance";
import type { GenerationCredits } from "@/lib/api/account";

const api = vi.hoisted(() => ({ credits: vi.fn(), wallet: vi.fn() }));
vi.mock("@/lib/api/account", () => ({ getGenerationCredits: api.credits }));
vi.mock("@/lib/api/wallet", () => ({ getWallet: api.wallet }));
const credits: GenerationCredits = {
  edit_only_units: 5, flexible_units: 15, available_units: 20, reserved_units: 7,
  initial_build_units: 5, edit_units: 1, included_generations_remaining: 2,
  included_edits_remaining: 24, included_generations_limit: 2,
  included_edits_limit: 25, period_end: "2026-11-28T17:31:00Z",
};
let cleanup: (() => Promise<void>) | undefined;
afterEach(async () => { await cleanup?.(); vi.resetAllMocks(); });
async function mount(value: GenerationCredits | Promise<GenerationCredits> | Error) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  api.credits.mockImplementation(() => value instanceof Error ? Promise.reject(value) : Promise.resolve(value));
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
  expect(container.textContent).toContain("7");
  expect(container.querySelector('a[href="#billing-packages"]')).not.toBeNull();
});

it("does not use edits-only packages for a new generation and shows the empty state", async () => {
  const container = await mount({ ...credits, flexible_units: 0, available_units: 5,
    included_generations_remaining: 0, included_edits_remaining: 0 });
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

it("renders exhausted zero limits without an invalid progress value", async () => {
  const container = await mount({ ...credits, flexible_units: 0, available_units: 0,
    edit_only_units: 0, included_generations_remaining: 0, included_edits_remaining: 0,
    included_generations_limit: 0, included_edits_limit: 0 });
  await wait(() => expect(container.textContent).toContain("Лимиты исчерпаны"));
  expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
  expect(container.querySelectorAll('[role="meter"]')).toHaveLength(0);
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
