import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxLaunchPanel } from "@/components/max/MaxLaunchPanel";
import { MaxPostLaunchDashboard } from "@/components/max/MaxPostLaunchDashboard";
import type { DeployStatus, MaxIntegration, MaxReadiness, Project } from "@/lib/api/types";

const api = vi.hoisted(() => ({ readiness: vi.fn(), deploy: vi.fn(), history: vi.fn(), runtime: vi.fn(), integration: vi.fn() }));
vi.mock("@/components/max/MaxOwnerAnalytics", () => ({ MaxOwnerAnalytics: () => <section data-testid="owner-analytics" /> }));
vi.mock("@/lib/api/max-studio", async (original) => ({ ...await original<object>(), getMaxReadiness: api.readiness }));
vi.mock("@/lib/api/runtime", async (original) => ({ ...await original<object>(), getLastDeploy: api.deploy, getDeployHistory: api.history, getRuntime: api.runtime }));
vi.mock("@/lib/api/max-integration", async (original) => ({ ...await original<object>(), getMaxIntegration: api.integration }));
const project = { id: "project-surfaces", name: "Проверка", template: "max_miniapp" } as Project;
const release: DeployStatus = { phase: "done", run_id: "old-release", started_at: null, finished_at: "2026-09-01T10:00:00Z", prod_url: "https://app.example.com", image_tag: "app:v1", error: null, detail: null, target_label: "Yleum", target_id: null, can_cancel: false, logs: [] };
const connectedBot: MaxIntegration = { eligible: true, connected: true, status: "active", bot_id: "1", bot_name: "Example", bot_username: "Example_bot", app_url: "https://app.example.com", webhook_url: "https://app.example.com/api/max/webhook", deep_link: "https://max.ru/Example_bot", last_error: null, verified_at: "2026-09-01T09:00:00Z", published_at: "2026-09-01T10:00:00Z" };
function readiness(published = false): MaxReadiness {
  return { ready_to_launch: published, progress: published ? 100 : 67, max_url_bot_id: published ? connectedBot.bot_id : null, items: ["build", "legal", "bot", "publish", "max_url"].map(id => ({ id, label: id, done: published || !["publish", "max_url"].includes(id), blocking: true, action: null })) };
}
let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.clearAllMocks();
  localStorage.clear();
  api.readiness.mockResolvedValue(readiness());
  api.deploy.mockResolvedValue(release);
  api.history.mockResolvedValue([]);
  api.runtime.mockResolvedValue({ state: "running", container_name: "preview", port: 3000, dev_url: "https://preview.example.com", last_active_at: null, hibernate_after_seconds: 600, keep_alive: false });
  api.integration.mockResolvedValue({ eligible: true, connected: false, status: "not_connected", bot_id: null, bot_name: null, bot_username: null, app_url: null, webhook_url: null, deep_link: null, last_error: null, verified_at: null, published_at: null });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: Infinity } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); client.clear(); });
async function mount(node: ReactNode) {
  await act(async () => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>));
}
async function settle(check: () => void) { await act(async () => { await vi.waitFor(check); }); }
const dashboard = () => <MaxPostLaunchDashboard projectId={project.id} projectName={project.name} />;

it("keeps statistics focused on analytics while publication remains in project navigation", async () => {
  await mount(dashboard());
  expect(container.querySelector('[data-testid="owner-analytics"]')).not.toBeNull();
  expect(container.querySelector("h1")?.textContent).toBe("Статистика");
  expect(container.querySelector('a[href="/max/project-surfaces/publish"]')?.textContent).toContain("Публикация");
  expect(container.querySelector(".max-dashboard-operations")).toBeNull();
  expect(container.querySelector(".max-dashboard-inspect")).toBeNull();
  expect(container.textContent).not.toContain("Подготовить запуск");
  for (const request of Object.values(api)) expect(request).not.toHaveBeenCalled();
});

it("keeps optional services discoverable outside collapsed readiness details and offers no own-server hosting", async () => {
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(container.querySelector('[data-testid="max-one-click-launch"]')).not.toBeNull());
  expect(container.querySelector('a[href="/max/project-surfaces/integrations?tab=services"]')!.closest("details")).toBeNull();
  // MAX apps run on the platform; own-server hosting left with the site builder.
  expect(container.querySelector('a[href*="panel=hosting"]')).toBeNull();
  expect(container.textContent).not.toContain("Собственный сервер");
  expect(container.querySelector('[data-testid="max-one-click-launch"]')).not.toBeNull();
});
it("does not call an older release the current version in launch", async () => {
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(container.textContent).toContain("Изменения не опубликованы"));
  expect(container.textContent).not.toContain("Production URL готов");
});

it("shows publication blockers without expanding details and links each unfinished requirement to its editor", async () => {
  const state = readiness();
  state.items = state.items.map(item => ({ ...item, done: item.id === "build" }));
  api.readiness.mockResolvedValue(state);
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(container.querySelector('[data-requirement="build"][data-state="done"]')).not.toBeNull());
  const requirements = container.querySelector('[aria-label="Путь до публикации"]');
  expect(requirements).not.toBeNull();
  expect(requirements!.closest("details")).toBeNull();
  // Панель считает за владельца, а не оставляет ему список без итога.
  expect(requirements!.textContent).toContain("До публикации осталось 2 шага из 3");
  // Шаг после публикации живёт в том же маршруте, приглушённым: он не
  // требование, но владелец должен знать, что его ждёт.
  expect(requirements!.querySelectorAll('[data-requirement]')).toHaveLength(4);
  // Текущий шаг уже вынесен кнопкой выше — в маршруте он подписан «Делаем
  // сейчас», а не повторяет ту же кнопку второй раз.
  expect(requirements!.querySelector('[data-requirement="legal"][data-promoted]')).not.toBeNull();
  expect(requirements!.querySelector('[data-requirement="legal"] a')).toBeNull();
  expect(requirements!.textContent).toContain("Делаем сейчас");
  expect(requirements!.querySelector('[data-requirement="bot"] a')?.getAttribute("href")).toBe("/max/project-surfaces/integrations");
  expect(requirements!.querySelector('[data-requirement="max_url"]')?.getAttribute("data-state")).toBe("later");
  expect(requirements!.querySelector('[data-requirement="max_url"] a')?.getAttribute("href")).toBe("/max/project-surfaces/integrations");
});

it.each(["loading", "error"])("never marks stale requirements complete while readiness is %s", async state => {
  if (state === "error") {
    client.setQueryData(["max-readiness", project.id], readiness(true));
    client.setQueryDefaults(["max-readiness", project.id], { staleTime: 0 });
    api.readiness.mockRejectedValue(new Error("offline"));
  } else api.readiness.mockImplementation(() => new Promise(() => {}));
  await mount(<MaxLaunchPanel project={project} />);
  if (state === "error") await settle(() => expect(container.textContent).toContain("Не дозвонились"));
  const requirements = container.querySelector('[aria-label="Путь до публикации"]');
  expect(requirements).not.toBeNull();
  expect(requirements!.querySelectorAll('[data-state="done"]')).toHaveLength(0);
  expect(requirements!.querySelectorAll('[data-state="unknown"]')).toHaveLength(3);
});
it("keeps unknown launch readiness distinct from completed preparation", async () => {
  api.readiness.mockImplementation(() => new Promise(() => {}));
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(container.textContent).toContain("Проверяем готовность"));
  expect(container.textContent).not.toContain("Запуск завершён");
  expect(container.querySelector('a[href="/max/project-surfaces/dashboard"]')).toBeNull();
});
it("stops showing an in-progress readiness check after the request fails", async () => {
  api.readiness.mockRejectedValue(new Error("offline"));
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(container.textContent).toContain("Не дозвонились"));
  expect(container.textContent).not.toContain("Проверяем…");
  expect(container.querySelector("progress")).toBeNull();
  expect(container.querySelector('[data-testid="max-launch-current-step"]')?.getAttribute("role")).toBe("alert");
});
it("waits for deployment details before announcing the already-ready version as published", async () => {
  let resolve!: (status: DeployStatus) => void;
  client.setQueryData(["max-readiness", project.id], readiness(true));
  api.readiness.mockResolvedValue(readiness(true));
  api.deploy.mockImplementation(() => new Promise<DeployStatus>(done => { resolve = done; }));
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(container.textContent).toContain("Проверяем публикацию"));
  expect(container.textContent).not.toContain("Приложение опубликовано");
  expect(container.textContent).not.toContain("Полностью готово к запуску");
  expect(container.querySelector('[data-testid="max-launch-app-url"]')).toBeNull();
  await act(async () => resolve(release));
  await settle(() => expect(container.textContent).toContain("Приложение опубликовано"));
  expect(container.querySelector('[data-testid="max-launch-app-url"]')?.textContent).toBe("https://app.example.com");
  expect(container.querySelector('a[href="https://app.example.com"]')?.textContent).toContain("Открыть сайт");
});

it.each([
  ["a confirmed release with a verified bot and no webhook", release, { ...connectedBot, status: "verified", app_url: null, webhook_url: null, published_at: null }],
  ["a confirmed release with an older webhook address", release, { ...connectedBot, app_url: "https://old.example.com" }],
  ["a legacy release without a deploy URL", { ...release, prod_url: null }, { ...connectedBot, deep_link: null }],
] as const)("opens the mini app in MAX for %s and keeps its HTTPS address", async (_name, deploy, integration) => {
  api.readiness.mockResolvedValue(readiness(true));
  api.deploy.mockResolvedValue(deploy);
  api.integration.mockResolvedValue(integration);
  await mount(<MaxLaunchPanel project={project} standalone />);
  await settle(() => expect(container.querySelector('a[href="https://max.ru/Example_bot?startapp"]')?.textContent?.trim()).toBe("Открыть в MAX"));
  expect(container.querySelector('[data-testid="max-launch-app-url"]')?.textContent).toBe("https://app.example.com");
  expect(container.querySelector('button[aria-label="Скопировать адрес приложения"]')).not.toBeNull();
});

it("waits for fresh integration and address confirmation before using a cached MAX link", async () => {
  client.setQueryData(["max-integration", project.id], connectedBot);
  client.setQueryData(["max-readiness", project.id], readiness(true));
  api.integration.mockResolvedValue({ ...connectedBot, status: "error" });
  api.readiness.mockResolvedValue(readiness(true));
  await mount(<MaxLaunchPanel project={project} standalone />);
  await settle(() => expect(container.textContent).toContain("Открыть сайт"));
  expect(container.querySelector('a[href="https://max.ru/Example_bot?startapp"]')).toBeNull();
  expect(api.integration).toHaveBeenCalledWith(project.id);
});

it.each([
  ["the MAX address has not been confirmed", false, connectedBot],
  ["the confirmed address belongs to the previous bot", true, { ...connectedBot, bot_id: "2", deep_link: "https://max.ru/New_bot" }],
  ["the bot connection is invalid", true, { ...connectedBot, status: "error" }],
  ["the bot link points outside MAX", true, { ...connectedBot, deep_link: "https://max.ru@other.example/Example_bot" }],
  ["the legacy bot name is invalid", true, { ...connectedBot, deep_link: null, bot_username: "Example_bot?redirect=elsewhere" }],
] as const)("opens the public website if %s", async (_name, addressConfirmed, integration) => {
  const state = readiness(true);
  api.readiness.mockResolvedValue({ ...state, items: state.items.map(item => item.id === "max_url" ? { ...item, done: addressConfirmed } : item) });
  api.integration.mockResolvedValue(integration);
  await mount(<MaxLaunchPanel project={project} standalone />);
  await settle(() => expect(client.getQueryData(["max-integration", project.id])).toEqual(integration));
  await settle(() => expect(container.querySelector('a[href="https://app.example.com"]')?.textContent).toContain("Открыть сайт"));
  expect(container.textContent).not.toContain("Открыть в MAX");
  if (!addressConfirmed) expect(container.querySelector('[data-testid="max-open-business-cabinet"]')).not.toBeNull();
});
