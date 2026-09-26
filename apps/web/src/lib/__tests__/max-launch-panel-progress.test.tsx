import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxLaunchPanel } from "@/components/max/MaxLaunchPanel";
import type { DeployStatus, MaxReadiness, Project } from "@/lib/api/types";
import { formatElapsed, heartbeatStale, observeHeartbeat, publicationBytesLabel, publicationElapsedMs, publicationFailureText, publicationStageLabel } from "@/lib/max-publication-progress";

const api = vi.hoisted(() => ({ readiness: vi.fn(), deploy: vi.fn(), history: vi.fn(), runtime: vi.fn(), integration: vi.fn(), launch: vi.fn() }));
vi.mock("@/lib/api/max-studio", async (original) => ({ ...await original<object>(), getMaxReadiness: api.readiness }));
vi.mock("@/lib/api/runtime", async (original) => ({ ...await original<object>(), getLastDeploy: api.deploy, getDeployHistory: api.history, getRuntime: api.runtime }));
vi.mock("@/lib/api/max-integration", async (original) => ({ ...await original<object>(), getMaxIntegration: api.integration }));
vi.mock("@/lib/max-launch-runner", async (original) => ({ ...await original<object>(), launchMaxProject: api.launch }));

const project = { id: "project-progress", name: "Прогресс", template: "max_miniapp" } as Project;
const base: DeployStatus = { phase: "building", run_id: "run-1", started_at: "2026-09-18T15:15:03Z", finished_at: null, prod_url: null, image_tag: null, error: null, detail: null, target_label: "Yleum", target_id: null, can_cancel: false, logs: [] };
function readiness(): MaxReadiness {
  return { ready_to_launch: true, progress: 67, items: ["build", "legal", "bot", "publish", "max_url"].map(id => ({ id, label: id, done: !["publish", "max_url"].includes(id), blocking: true, action: null })) };
}
let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.clearAllMocks();
  vi.useRealTimers();
  localStorage.clear();
  api.readiness.mockResolvedValue(readiness());
  api.history.mockResolvedValue([]);
  api.runtime.mockResolvedValue({ state: "running", container_name: "preview", port: 3000, dev_url: "https://preview.example.com", last_active_at: null, hibernate_after_seconds: 600, keep_alive: false });
  api.integration.mockResolvedValue({ eligible: true, connected: true, status: "verified", bot_id: "1", bot_name: "bot", bot_username: "bot", app_url: null, webhook_url: null, deep_link: null, last_error: null, verified_at: "2026-09-18T00:00:00Z", published_at: null });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: Infinity } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); client.clear(); vi.useRealTimers(); });
async function mount(node: ReactNode) {
  await act(async () => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>));
}
async function settle(check: () => void) { await act(async () => { await vi.waitFor(check); }); }
const progressBlock = () => container.querySelector('[data-testid="max-launch-publication-progress"]');

it("shows the current substage, server elapsed and transferred bytes without inventing a percentage", async () => {
  api.deploy.mockResolvedValue({ ...base, format_version: 2, stage: "capture_volumes", stage_started_at: "2026-09-18T15:15:13Z", heartbeat_at: "2026-09-18T15:15:29Z", progress: { bytes_done: 1_326_277_632, bytes_total: null, files_done: null }, stages: [], metrics: {} });
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(progressBlock()).not.toBeNull());
  const text = progressBlock()!.textContent ?? "";
  expect(text).toContain("Упаковываем код и зависимости");
  expect(text).toContain("26 с");
  expect(text).toContain("передано 1,2 ГБ");
  expect(text).not.toContain("%");
  // Готовность считается один раз — в маршруте до публикации. Второй счётчик
  // с собственной полосой наверху панели убран: одно число, один индикатор.
  expect(container.querySelector('[data-testid="max-launch-progress"]')).toBeNull();
  expect(container.textContent).toContain("Всё готово к публикации");
  expect(api.launch).not.toHaveBeenCalled();
});

it("reports verification against a known total and falls back to the phase label for an older controller", async () => {
  api.deploy.mockResolvedValue({ ...base, phase: "swapping", format_version: 2, stage: "verify_artifacts", heartbeat_at: "2026-09-18T15:15:40Z", progress: { bytes_done: 716_800_000, bytes_total: 1_432_274_432, files_done: null }, stages: [], metrics: {} });
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(progressBlock()?.textContent).toContain("Проверяем целостность пакета"));
  expect(progressBlock()!.textContent).toContain("проверено 684 МБ из 1,3 ГБ");
  await act(async () => root.unmount());
  container.remove(); container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  api.deploy.mockResolvedValue({ ...base, phase: "swapping" });
  client.clear();
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(progressBlock()?.textContent).toContain("Проверяем и переключаем версию"));
  expect(progressBlock()!.textContent).not.toContain("передано");
});

it("says it is checking when the heartbeat stopped advancing and never starts another publication", async () => {
  vi.setSystemTime(new Date("2026-09-18T15:16:00Z"));
  const frozen = { ...base, format_version: 2, stage: "seed_data", heartbeat_at: "2026-09-18T15:15:46Z", progress: { bytes_done: 0, bytes_total: null, files_done: 2 }, stages: [], metrics: {} };
  api.deploy.mockResolvedValue(frozen);
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(progressBlock()?.textContent).toContain("Переносим пакет на площадку"));
  expect(container.textContent).not.toContain("проверяем состояние");
  vi.setSystemTime(new Date("2026-09-18T15:17:00Z"));
  await act(async () => { await client.invalidateQueries({ queryKey: ["deploy", project.id] }); });
  await settle(() => expect(container.textContent).toContain("проверяем состояние"));
  expect(api.launch).not.toHaveBeenCalled();
  expect(api.deploy.mock.calls.length).toBeGreaterThanOrEqual(2);
});

it("explains a failure by its reason code and stage, keeping the technical text secondary", async () => {
  api.deploy.mockResolvedValue({ ...base, phase: "failed", finished_at: "2026-09-18T15:17:00Z", error: "publication failed (CellResourceError); retained data were not restored", format_version: 2, error_stage: "tls", reason_code: "tls_failed", stages: [], metrics: { total_ms: 90000 } });
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(container.textContent).toContain("Не удалось включить HTTPS для адреса приложения."));
  expect(container.textContent).toContain("Стадия: настраиваем https");
  expect(container.textContent).toContain("publication failed (CellResourceError)");
  expect(progressBlock()).toBeNull();
});

it("removes the publication progress once the run is done", async () => {
  api.deploy.mockResolvedValue({ ...base, phase: "done", finished_at: "2026-09-18T15:16:54Z", prod_url: "https://app.example.com", format_version: 2, stages: [], metrics: { total_ms: 110718 } });
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(container.textContent).toContain("Текущая версия не опубликована"));
  expect(progressBlock()).toBeNull();
});

it("tells the owner when nothing had to be rebuilt", async () => {
  api.readiness.mockResolvedValue({ ...readiness(), items: readiness().items.map(item => ({ ...item, done: true })) });
  api.deploy.mockResolvedValue({ ...base, phase: "done", detail: "already_current", finished_at: "2026-09-18T15:16:54Z", prod_url: "https://app.example.com", format_version: 2, stages: [], metrics: { total_ms: 812, already_current: 1 } });
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(container.querySelector('[data-testid="max-launch-noop"]')?.textContent).toContain("повторная сборка не потребовалась"));
  expect(progressBlock()).toBeNull();
});

it("helpers never turn missing data into progress", () => {
  expect(publicationStageLabel(undefined)).toBe("");
  expect(publicationStageLabel({ phase: "building", stage: null })).toBe("Собираем приложение");
  expect(publicationStageLabel({ phase: "building", stage: "unknown_stage" })).toBe("Собираем приложение");
  expect(publicationBytesLabel(null)).toBeNull();
  expect(publicationBytesLabel({ bytes_done: 0, bytes_total: null, files_done: null })).toBeNull();
  expect(publicationElapsedMs({ started_at: null, heartbeat_at: null, finished_at: null })).toBeNull();
  expect(publicationElapsedMs({ started_at: "2026-09-18T15:15:03Z", heartbeat_at: "2026-09-18T15:16:54Z", finished_at: null })).toBe(111_000);
  expect(formatElapsed(111_000)).toBe("1 мин 51 с");
  expect(formatElapsed(59_400)).toBe("59 с");
  expect(publicationFailureText({ error: null, error_stage: null, reason_code: "unknown_code" }).title).toContain("Публикация не завершилась");
  const first = observeHeartbeat(null, "a", 1_000);
  expect(observeHeartbeat(first, "a", 50_000)).toBe(first);
  expect(heartbeatStale(first, 46_001)).toBe(true);
  expect(heartbeatStale(observeHeartbeat(first, "b", 46_001), 46_002)).toBe(false);
  expect(heartbeatStale(observeHeartbeat(null, null, 0), 999_999)).toBe(false);
});
