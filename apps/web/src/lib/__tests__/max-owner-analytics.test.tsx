import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxOwnerAnalytics } from "@/components/max/MaxOwnerAnalytics";

let root: Root;
let node: HTMLDivElement;
let client: QueryClient;
let response: "ok" | "empty" | "error" | "pending";
let urls: string[];
beforeEach(() => {
  vi.useFakeTimers();
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  response = "ok"; urls = [];
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    urls.push(url);
    if (response === "pending") return new Promise<Response>(() => {});
    if (response === "error") return Response.json({ error: { message: "Unavailable", code: "internal_error" } }, { status: 503 });
    const empty = response === "empty";
    return Response.json({ days: 30, from_date: "2026-09-05", to_date: "2026-10-04", timezone: "Europe/Moscow",
      measured_since: empty ? null : "2026-10-03T00:00:00Z", users: empty ? 0 : 2,
      opens: empty ? 0 : 3, actions: empty ? 0 : 5, events: empty ? 0 : 8,
      daily: [{ date: "2026-10-04", users: empty ? 0 : 2, opens: empty ? 0 : 3, actions: empty ? 0 : 5, events: empty ? 0 : 8 }],
    });
  }));
});
afterEach(async () => {
  await act(async () => root.unmount()); client.clear(); node.remove();
  vi.unstubAllGlobals(); vi.useRealTimers();
});
async function mount(project = "owned-project") {
  await act(async () => root.render(<QueryClientProvider client={client}><MaxOwnerAnalytics projectId={project} /></QueryClientProvider>));
  await act(async () => { await vi.advanceTimersByTimeAsync(20); });
}
it("shows real counts, Moscow range and requests the selected owner's project", async () => {
  await mount();
  expect(urls[0]).toContain("/api/projects/owned-project/max/analytics?days=30");
  expect(node.textContent).toContain("Пользователи");
  expect(node.querySelector('[data-metric="users"]')?.textContent).toContain("2");
  expect(node.querySelector('[data-metric="opens"]')?.textContent).toContain("3");
  expect(node.textContent).toContain("Московское время");
  expect(node.querySelector("table")?.textContent).toContain("2026-10-04");
  await act(async () => {
    const select = node.querySelector("select")!; select.value = "7";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(20); });
  expect(urls.some(url => url.includes("days=7"))).toBe(true);
  await mount("another-project");
  expect(urls.some(url => url.includes("another-project"))).toBe(true);
});
it("shows honest empty state without invented activity", async () => {
  response = "empty"; await mount();
  expect(node.textContent).toContain("Нет полученных событий");
  expect(node.querySelector('[data-metric="users"]')?.textContent).toContain("0");
});
it("error state hides stale totals and offers retry", async () => {
  response = "error"; await mount();
  expect(node.querySelector('[role="alert"]')?.textContent).toContain("Статистика временно недоступна");
  expect(node.querySelector('[data-metric="users"]')).toBeNull();
  response = "ok";
  await act(async () => node.querySelector("button")!.click());
  await act(async () => { await vi.advanceTimersByTimeAsync(20); });
  expect(node.querySelector('[data-metric="users"]')?.textContent).toContain("2");
});
it("shows loading without placeholder totals", async () => {
  response = "pending"; await mount();
  expect(node.textContent).toContain("Загружаем статистику");
  expect(node.querySelector('[data-metric="users"]')).toBeNull();
});
