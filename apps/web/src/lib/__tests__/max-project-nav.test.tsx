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

async function render(projectId: string, active: "editor" | "dashboard" | "integrations" | "publish", done: string[]) {
  client.setQueryData<MaxReadiness>(["max-readiness", projectId], {
    ready_to_launch: done.length === 5, progress: done.length * 20, items: items(done),
  });
  await act(async () => root.render(
    <QueryClientProvider client={client}>
      <MaxProjectNav projectId={projectId} active={active} />
    </QueryClientProvider>,
  ));
  return [...container.querySelectorAll("nav a")];
}

it("называет разделы делом и умещает проект в четыре пункта", async () => {
  // «MAX» — название мессенджера, «После запуска» — момент времени: владелец не
  // понимал, где искать адрес приложения и куда вставлять токен бота.
  const links = await render("p2", "editor", ["build", "legal"]);
  expect(links.map(link => link.textContent?.trim())).toEqual([
    "Сборка", "Данные приложения", "Бот и подключения", "Запуск и адрес",
  ]);
  expect(links.map(link => link.getAttribute("href"))).toEqual([
    "/max/p2", "/max/p2?data=details", "/max/p2?panel=max", "/max/p2?panel=publish",
  ]);
});

it("после публикации «Запуск и адрес» ведёт туда, где адрес и история", async () => {
  const links = await render("p3", "dashboard", ["build", "legal", "bot", "publish", "max_url"]);
  const launch = links.at(-1)!;
  expect(launch.getAttribute("href")).toBe("/max/p3/dashboard");
  expect(launch.getAttribute("aria-current")).toBe("page");
});

it("старые разделы продолжают подсвечивать свой пункт", async () => {
  // Страницы /integrations и /publish никуда не делись: их ключи прежние,
  // и ссылки на них из писем и закладок продолжают работать.
  const services = await render("p4", "integrations", ["build"]);
  expect(services[2].getAttribute("aria-current")).toBe("page");
  await act(async () => root.unmount());
  root = createRoot(container);
  const publish = await render("p4", "publish", ["build"]);
  expect(publish[3].getAttribute("aria-current")).toBe("page");
});
