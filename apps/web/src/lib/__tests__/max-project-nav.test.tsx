import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { MaxProjectNav } from "@/components/max/MaxProjectNav";
import type { MaxReadiness } from "@/lib/api/types";

vi.mock("@/lib/api/max-studio", async original => ({
  ...(await original<object>()),
  getMaxReadiness: vi.fn(() => new Promise(() => {})),
}));

const items = (done: string[]): MaxReadiness["items"] =>
  ["build", "legal", "bot", "publish", "max_url"].map(id => ({
    id, label: id, done: done.includes(id), blocking: true, action: null,
  }));

let root: Root;
let container: HTMLDivElement;
let client: QueryClient;

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: Infinity } } });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); client.clear(); });

async function render(projectId: string, active: "editor" | "dashboard" | "integrations" | "publish", done: string[], variant: "sidebar" | "mobile" = "sidebar") {
  client.setQueryData<MaxReadiness>(["max-readiness", projectId], {
    ready_to_launch: done.length === 5, progress: done.length * 20, items: items(done),
  });
  await act(async () => root.render(
    <QueryClientProvider client={client}>
      <MaxProjectNav projectId={projectId} active={active} variant={variant} />
    </QueryClientProvider>,
  ));
  return [...container.querySelectorAll("nav a")];
}

it("даёт статистике постоянное место рядом с запуском", async () => {
  // «MAX» — название мессенджера, «После запуска» — момент времени: владелец не
  // понимал, где искать адрес приложения и куда вставлять токен бота.
  const links = await render("p2", "editor", ["build", "legal"]);
  expect(links.map(link => link.textContent?.trim())).toEqual([
    "Управление", "Сборка", "Данные приложения", "Каталог", "Бот и подключения", "Запуск и адрес", "Статистика",
  ]);
  expect(links.map(link => link.getAttribute("href"))).toEqual([
    "/max/p2/manage", "/max/p2", "/max/p2?data=details", "/max/p2/catalog", "/max/p2?panel=max", "/max/p2?panel=publish", "/max/p2/dashboard",
  ]);
});

it.each(["sidebar", "mobile"] as const)("не подменяет запуск статистикой после публикации: %s", async variant => {
  const links = await render("p3", "dashboard", ["build", "legal", "bot", "publish", "max_url"], variant);
  const publish = links.find(link => link.getAttribute("href") === "/max/p3?panel=publish")!;
  const dashboard = links.find(link => link.getAttribute("href") === "/max/p3/dashboard")!;
  expect(publish).toBeTruthy(); expect(publish.hasAttribute("aria-current")).toBe(false);
  expect(dashboard).toBeTruthy(); expect(dashboard.getAttribute("aria-current")).toBe("page");
});

it("старые разделы продолжают подсвечивать свой пункт", async () => {
  // Страницы /integrations и /publish никуда не делись: их ключи прежние,
  // и ссылки на них из писем и закладок продолжают работать.
  const services = await render("p4", "integrations", ["build"]);
  expect(services.find(link => link.getAttribute("href") === "/max/p4?panel=max")?.getAttribute("aria-current")).toBe("page");
  await act(async () => root.unmount());
  root = createRoot(container);
  const publish = await render("p4", "publish", ["build"]);
  expect(publish.find(link => link.getAttribute("href") === "/max/p4?panel=publish")?.getAttribute("aria-current")).toBe("page");
});
