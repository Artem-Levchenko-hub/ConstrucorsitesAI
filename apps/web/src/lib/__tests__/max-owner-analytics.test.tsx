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
let feedError: boolean;
let reportedEvent: boolean;
beforeEach(() => {
  vi.useFakeTimers();
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
  response = "ok"; urls = []; feedError = false; reportedEvent = false;
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    urls.push(url);
    if (response === "pending") return new Promise<Response>(() => {});
    if (response === "error" || (feedError && url.includes("/events?"))) return Response.json({ error: { message: "Unavailable", code: "internal_error" } }, { status: 503 });
    const empty = response === "empty";
    if (url.includes("/events?")) {
      const older = url.includes("cursor=");
      const filtered = url.includes("kind=action");
      return Response.json({ days: 30, from_date: "2026-09-05", to_date: "2026-10-04", timezone: "Europe/Moscow",
        observed_at: "2026-10-04T12:00:00Z", next_cursor: !empty && !older && !filtered ? "opaque+/=" : null,
        items: empty ? [] : older ? [{ id: "old", kind: "action", event_type: null, occurred_at: "2026-10-03T19:00:00Z" }]
          : filtered ? [{ id: "action", kind: "action", event_type: "order_created", occurred_at: "2026-10-04T10:00:00Z" }]
            : [...(reportedEvent ? [{ id: "reported", kind: "event", event_type: "order_created", occurred_at: "2026-10-04T11:00:00Z" }] : []), { id: "open", kind: "open", event_type: "app_open", occurred_at: "2026-10-04T09:00:00Z" },
              { id: "action", kind: "action", event_type: "order_created", occurred_at: "2026-10-04T10:00:00Z" }],
      });
    }
    return Response.json({ days: 30, from_date: "2026-09-05", to_date: "2026-10-04", timezone: "Europe/Moscow",
      measured_since: empty ? null : "2026-10-03T00:00:00Z", users: empty ? 0 : 2,
      opens: empty ? 0 : 3, actions: empty ? 0 : 5, events: empty ? 0 : 8,
      daily: ["2026-10-03", "2026-10-04"].map(date => ({ date, users: empty ? 0 : 2, opens: empty ? 0 : 3, actions: empty ? 0 : 5, events: empty ? 0 : 8 })),
    });
  }));
});
afterEach(async () => {
  await act(async () => root.unmount()); client.clear(); node.remove();
  vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers();
});
async function settle() { await act(async () => { await vi.advanceTimersByTimeAsync(30); }); }
async function mount(project = "owned-project") {
  await act(async () => root.render(<QueryClientProvider client={client}><MaxOwnerAnalytics projectId={project} /></QueryClientProvider>));
  await settle();
}
async function click(selector: string) {
  await act(async () => node.querySelector<HTMLButtonElement>(selector)!.click());
  await settle();
}
it("keeps period unique users, switches real chart metric and requests owner/period", async () => {
  await mount();
  expect(urls[0]).toContain("/api/projects/owned-project/max/analytics?days=30");
  expect(node.querySelector('[data-metric="users"] strong')?.textContent).toBe("2");
  expect(node.querySelector('[data-metric="opens"] strong')?.textContent).toBe("3");
  expect(node.querySelector('svg[role="img"] title')?.textContent).toContain("Пользователи");
  expect(node.querySelector("table")?.textContent).toContain("2026-10-04");
  await click('[data-metric="actions"]');
  expect(node.querySelector('svg[role="img"] title')?.textContent).toContain("Действия");
  expect(node.querySelector('[data-metric="actions"]')?.getAttribute("aria-pressed")).toBe("true");
  expect(node.querySelector("table td")?.textContent).toBe("5");
  await act(async () => {
    const select = node.querySelector("select")!; select.value = "7";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await settle();
  expect(urls.some(url => url.includes("days=7"))).toBe(true);
  await mount("another-project");
  expect(urls.some(url => url.includes("another-project"))).toBe(true);
});
it("shows honest empty state without invented activity", async () => {
  response = "empty"; await mount();
  expect(node.textContent).toContain("Нет полученных событий");
  expect(node.querySelector('[data-metric="users"] strong')?.textContent).toBe("0");
  expect(node.querySelectorAll(".max-analytics-events li")).toHaveLength(0);
});
it("distinguishes a client report from a confirmed durable order", async () => {
  reportedEvent = true;
  await mount();
  const entries = [...node.querySelectorAll(".max-analytics-events li")];
  expect(entries.some(entry => entry.textContent?.includes("Сообщение о заказе"))).toBe(true);
  expect(entries.some(entry => entry.textContent?.includes("Заказ создан"))).toBe(true);
  expect(node.querySelector(".max-analytics-method")?.textContent).toContain("не подтверждает сохранение заказа");
});
it("error state hides stale totals and offers retry", async () => {
  response = "error"; await mount();
  expect(node.querySelector('[role="alert"]')?.textContent).toContain("Статистика временно недоступна");
  expect(node.querySelector('[data-metric="users"]')).toBeNull();
  response = "ok";
  await click('button[aria-label="Обновить статистику"]');
  expect(node.querySelector('[data-metric="users"] strong')?.textContent).toBe("2");
});
it("hides previous owner and period data while loading and on failure", async () => {
  await mount();
  response = "pending";
  await mount("another-project");
  expect(node.textContent).toContain("Загружаем статистику");
  expect(node.querySelector('[data-metric="users"]')).toBeNull();
  expect(node.querySelectorAll(".max-analytics-events li")).toHaveLength(0);
  response = "error";
  await act(async () => {
    const select = node.querySelector("select")!; select.value = "90";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await settle();
  expect(node.querySelector('[data-metric="users"]')).toBeNull();
  expect(node.textContent).not.toContain("Обновлено в");
});
it("sorts feed newest first, uses Moscow time, pages with opaque cursor and labels legacy generically", async () => {
  await mount();
  const rows = node.querySelectorAll(".max-analytics-events li");
  expect(rows[0].textContent).toContain("Заказ создан");
  expect(rows[0].textContent).toContain("13:00:00");
  await click(".max-analytics-more");
  expect(urls.some(url => url.includes("cursor=opaque%2B%2F%3D"))).toBe(true);
  const last = node.querySelectorAll(".max-analytics-events li")[2];
  expect(last.textContent).toContain("Действие");
  expect(last.textContent).not.toContain("Заказ");
  expect(node.querySelectorAll(".max-analytics-events li")).toHaveLength(3);
});
it("filters on backend and clears previous category while pending", async () => {
  await mount();
  await click('.max-analytics-filters button:nth-child(3)');
  expect(urls.some(url => url.includes("kind=action"))).toBe(true);
  expect(node.querySelectorAll(".max-analytics-events li")).toHaveLength(1);
  response = "pending";
  await click('.max-analytics-filters button:nth-child(2)');
  expect(node.querySelectorAll(".max-analytics-events li")).toHaveLength(0);
});
it("polls only while visible and online and reports refresh failure honestly", async () => {
  await mount();
  const initial = urls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(10_050); });
  expect(urls.length).toBeGreaterThan(initial);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  const hidden = urls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(20_050); });
  expect(urls.length).toBe(hidden);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); window.dispatchEvent(new Event("offline")); });
  const offline = urls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(20_050); });
  expect(urls.length).toBe(offline);
  expect(node.textContent).toContain("Автообновление на паузе");
  vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
  feedError = true;
  await act(async () => window.dispatchEvent(new Event("online")));
  await act(async () => { await vi.advanceTimersByTimeAsync(10_050); });
  expect(node.textContent).toContain("Не удалось обновить");
  expect(node.textContent).not.toContain("Обновлено в");
  expect(node.querySelectorAll(".max-analytics-events li")).toHaveLength(0);
});
