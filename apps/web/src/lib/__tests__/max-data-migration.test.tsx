import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxDataMigration } from "@/components/max/MaxDataMigration";

const api = vi.hoisted(() => ({ status: vi.fn(), prepare: vi.fn(), confirm: vi.fn() }));
vi.mock("@/lib/api/max-studio", () => ({ getPublicationMigration: api.status,
  preparePublicationMigration: api.prepare, confirmPublicationMigration: api.confirm }));
vi.mock("@/components/max/MaxLaunchButton", () => ({ MaxLaunchButton: () => <button>Публикация после проверки</button> }));
const prepared = { operation_id: "op", project_id: "p", snapshot_id: "s", state: "prepared",
  plan_sha256: "a".repeat(64), backup_verified: true, effect_possible: false,
  fields: [{ table: "orders", name: "pickup_at", type: "pg_catalog.timestamptz" }],
  error_code: null, expires_at: "2099-01-01T00:00:00Z" };
let client: QueryClient, root: Root, container: HTMLDivElement;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.clearAllMocks(); api.status.mockResolvedValue(null); api.prepare.mockResolvedValue(prepared);
  api.confirm.mockResolvedValue({ ...prepared, state: "applied" });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); client.clear(); vi.useRealTimers(); });
async function mount() { await act(async () => root.render(<QueryClientProvider client={client}><MaxDataMigration projectId="p" snapshotId="s" /></QueryClientProvider>)); }
async function click(label: string) {
  await act(async () => {
    const button = [...container.querySelectorAll("button")].find(b => b.textContent === label);
    expect(button).toBeDefined(); button!.click();
  });
}
async function settle(check: () => void) { await act(async () => { await vi.waitFor(check); }); }

it("requires preparation and a separate explicit confirmation of the exact plan", async () => {
  await mount(); await settle(() => expect(container.textContent).toContain("Проверить и создать копию"));
  expect(api.prepare).not.toHaveBeenCalled();
  await click("Проверить и создать копию");
  await settle(() => expect(container.textContent).toContain("Копия восстановлена и проверена"));
  expect(api.confirm).not.toHaveBeenCalled();
  await click("Применить изменения данных");
  expect(api.confirm).toHaveBeenCalledWith("p", "op", "a".repeat(64));
  await settle(() => expect(container.textContent).toContain("Публикация после проверки"));
});

it.each([
  { snapshot_id: "older" }, { project_id: "other" }, { backup_verified: false },
  { effect_possible: true }, { expires_at: "2000-01-01T00:00:00Z" },
])("never enables confirmation for an unproven or stale plan %o", async changes => {
  api.status.mockResolvedValue({ ...prepared, ...changes });
  await mount(); await settle(() => expect(api.status).toHaveBeenCalled());
  expect([...container.querySelectorAll("button")].some(b => b.textContent === "Применить изменения данных" && !b.disabled)).toBe(false);
  expect(api.confirm).not.toHaveBeenCalled();
});

it("does not replay an uncertain confirmation and asks to observe its status", async () => {
  api.status.mockResolvedValue(prepared); api.confirm.mockRejectedValue(new Error("lost response"));
  await mount(); await settle(() => expect(container.textContent).toContain("Применить изменения данных"));
  await click("Применить изменения данных");
  await settle(() => expect(container.textContent).toContain("Уточнить статус"));
  expect(api.confirm).toHaveBeenCalledTimes(1);
  expect(container.textContent).not.toContain("Публикация после проверки");
});

it("expires an open confirmation without starting any change", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-27T18:00:00Z"));
  api.status.mockResolvedValue({ ...prepared, expires_at: "2026-10-27T18:00:02Z" });
  await mount();
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(container.textContent).toContain("Применить изменения данных");
  await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
  expect(container.textContent).not.toContain("Применить изменения данных");
  expect(api.confirm).not.toHaveBeenCalled();
});

it("retains the preparation key after a lost reply and an early empty status read", async () => {
  api.prepare.mockRejectedValueOnce(new Error("lost reply"));
  await mount();
  await settle(() => expect(container.textContent).toContain("Проверить и создать копию"));
  await click("Проверить и создать копию");
  await settle(() => expect(container.textContent).toContain("Уточнить статус"));
  await click("Уточнить статус");
  await settle(() => expect(container.textContent).toContain("Проверить и создать копию"));
  await click("Проверить и создать копию");
  expect(api.prepare).toHaveBeenCalledTimes(2);
  expect(api.prepare.mock.calls[0][1]).toBe(api.prepare.mock.calls[1][1]);
});

it("retains the new request key while status still shows an older failed operation", async () => {
  api.status.mockResolvedValue({ ...prepared, operation_id: "older-failed", state: "failed", backup_verified: false });
  api.prepare.mockRejectedValueOnce(new Error("lost reply"));
  await mount();
  await settle(() => expect(container.textContent).toContain("Проверить и создать копию"));
  await click("Проверить и создать копию");
  await settle(() => expect(container.textContent).toContain("Уточнить статус"));
  await click("Уточнить статус");
  await settle(() => expect(container.textContent).toContain("Проверить и создать копию"));
  await click("Проверить и создать копию");
  expect(api.prepare).toHaveBeenCalledTimes(2);
  expect(api.prepare.mock.calls[0][1]).toBe(api.prepare.mock.calls[1][1]);
});
