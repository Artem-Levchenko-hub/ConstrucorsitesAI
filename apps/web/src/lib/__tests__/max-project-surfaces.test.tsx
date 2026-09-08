import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxLaunchPanel } from "@/components/max/MaxLaunchPanel";
import { MaxPostLaunchDashboard } from "@/components/max/MaxPostLaunchDashboard";
import type { DeployStatus, MaxReadiness, Project } from "@/lib/api/types";

const api = vi.hoisted(() => ({ readiness: vi.fn(), deploy: vi.fn(), history: vi.fn(), runtime: vi.fn(), integration: vi.fn() }));
vi.mock("@/lib/api/max-studio", async (original) => ({ ...await original<object>(), getMaxReadiness: api.readiness }));
vi.mock("@/lib/api/runtime", async (original) => ({ ...await original<object>(), getLastDeploy: api.deploy, getDeployHistory: api.history, getRuntime: api.runtime }));
vi.mock("@/lib/api/max-integration", async (original) => ({ ...await original<object>(), getMaxIntegration: api.integration }));
const project = { id: "project-surfaces", name: "Проверка", template: "max_miniapp" } as Project;
const release: DeployStatus = { phase: "done", run_id: "old-release", started_at: null, finished_at: "2026-09-01T10:00:00Z", prod_url: "https://app.example.com", image_tag: "app:v1", error: null, detail: null, target_label: "Omnia", target_id: null, can_cancel: false, logs: [] };
function readiness(published = false): MaxReadiness {
  return { ready_to_launch: published, progress: published ? 100 : 67, items: ["build", "business", "legal", "bot", "publish", "max_url"].map(id => ({ id, label: id, done: published || !["publish", "max_url"].includes(id), blocking: true, action: null })) };
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

it("keeps optional services and server actions discoverable outside collapsed readiness details", async () => {
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(container.querySelector('[data-testid="max-one-click-launch"]')).not.toBeNull());
  const server = container.querySelector('a[href="/max/project-surfaces?panel=hosting"]');
  expect(server).not.toBeNull();
  expect(server!.closest("details")).toBeNull();
  expect(container.querySelector('a[href="/max/project-surfaces?panel=services"]')!.closest("details")).toBeNull();
  expect(container.querySelector('[data-testid="max-one-click-launch"]')).not.toBeNull();
});
it("does not call an older release the current version in launch", async () => {
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(container.textContent).toContain("Текущая версия не опубликована"));
  expect(container.textContent).not.toContain("Production URL готов");
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
  await settle(() => expect(container.textContent).toContain("Статус недоступен"));
  expect(container.textContent).not.toContain("Проверяем…");
  expect(container.querySelector("progress")).toBeNull();
  expect(container.querySelector('[data-testid="max-launch-current-step"]')?.getAttribute("role")).toBe("alert");
});
it("waits for deployment details before announcing the already-ready version as published", async () => {
  let resolve!: (status: DeployStatus) => void;
  client.setQueryData(["max-readiness", project.id], readiness(true));
  api.deploy.mockImplementation(() => new Promise<DeployStatus>(done => { resolve = done; }));
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(container.textContent).toContain("Проверяем публикацию"));
  expect(container.textContent).not.toContain("Текущая версия опубликована");
  expect(container.textContent).not.toContain("Полностью готово к запуску");
  expect(container.querySelector('[data-testid="max-launch-app-url"]')).toBeNull();
  await act(async () => resolve(release));
  await settle(() => expect(container.textContent).toContain("Текущая версия опубликована"));
  expect(container.querySelector('[data-testid="max-launch-app-url"]')?.getAttribute("href")).toBe("https://app.example.com");
});
it.each([true, false])("reports publication evidence without claiming continuous uptime (published=%s)", async published => {
  api.readiness.mockResolvedValue(readiness(published));
  await mount(dashboard());
  await settle(() => expect(container.textContent).toContain(published ? "Текущая версия опубликована" : "Текущая версия не опубликована"));
  expect(container.textContent).toContain("Среда разработки");
  expect(container.textContent).toContain("Постоянный мониторинг доступности не подключён");
  expect(container.textContent).not.toContain("Отвечает");
});
it("offers a retry for failed status queries without showing stale success", async () => {
  client.setQueryData(["max-readiness", project.id], readiness(true));
  client.setQueryDefaults(["max-readiness", project.id], { staleTime: 0 });
  api.readiness.mockRejectedValue(new Error("offline"));
  await mount(dashboard());
  await settle(() => expect(container.textContent).toContain("Не удалось проверить публикацию"));
  expect(container.textContent).not.toContain("Текущая версия опубликована");
  api.readiness.mockResolvedValue(readiness(false));
  const retry = [...container.querySelectorAll("button")].find(button => button.textContent?.includes("Повторить проверку"));
  expect(retry).toBeDefined();
  await act(async () => retry!.click());
  await settle(() => expect(container.textContent).toContain("Текущая версия не опубликована"));
});
it("distinguishes history loading and failure from an empty history", async () => {
  let reject!: (error: Error) => void;
  api.history.mockImplementation(() => new Promise((_, fail) => { reject = fail; }));
  await mount(dashboard());
  await settle(() => expect(container.textContent).toContain("Загружаем историю"));
  expect(container.textContent).not.toContain("после первой публикации");
  await act(async () => { reject(new Error("offline")); });
  await settle(() => expect(container.textContent).toContain("Не удалось загрузить историю"));
  expect(container.textContent).toContain("Не удалось загрузить историю");
  expect(container.textContent).not.toContain("после первой публикации");
});
it("shows an explicit empty history after a successful empty response", async () => {
  await mount(dashboard());
  await settle(() => expect(container.textContent).toContain("История появится после первой публикации"));
});
it("reports a failed deployment separately from an unpublished draft", async () => {
  api.deploy.mockResolvedValue({ ...release, phase: "failed", error: "Сборка не завершена" });
  await mount(dashboard());
  await settle(() => expect(container.textContent).toContain("Последняя публикация не завершилась"));
  expect(container.textContent).toContain("Сборка не завершена");
});
