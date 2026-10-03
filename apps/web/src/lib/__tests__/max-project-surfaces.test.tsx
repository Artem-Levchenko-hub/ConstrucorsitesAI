import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxLaunchPanel } from "@/components/max/MaxLaunchPanel";
import { MaxPostLaunchDashboard } from "@/components/max/MaxPostLaunchDashboard";
import type { DeployStatus, MaxReadiness, Project } from "@/lib/api/types";
import { relativeDateLabel } from "@/lib/relative-date";

const api = vi.hoisted(() => ({ readiness: vi.fn(), deploy: vi.fn(), history: vi.fn(), runtime: vi.fn(), integration: vi.fn() }));
vi.mock("@/lib/api/max-studio", async (original) => ({ ...await original<object>(), getMaxReadiness: api.readiness }));
vi.mock("@/lib/api/runtime", async (original) => ({ ...await original<object>(), getLastDeploy: api.deploy, getDeployHistory: api.history, getRuntime: api.runtime }));
vi.mock("@/lib/api/max-integration", async (original) => ({ ...await original<object>(), getMaxIntegration: api.integration }));
const project = { id: "project-surfaces", name: "Проверка", template: "max_miniapp" } as Project;
const release: DeployStatus = { phase: "done", run_id: "old-release", started_at: null, finished_at: "2026-09-01T10:00:00Z", prod_url: "https://app.example.com", image_tag: "app:v1", error: null, detail: null, target_label: "Yleum", target_id: null, can_cancel: false, logs: [] };
const activePublication = { release_id: "old-release", snapshot_id: "old-snapshot", commit_sha: "old-commit", prod_url: release.prod_url!, finished_at: release.finished_at };
function readiness(published = false): MaxReadiness {
  return { ready_to_launch: published, progress: published ? 100 : 67, items: ["build", "legal", "bot", "publish", "max_url"].map(id => ({ id, label: id, done: published || !["publish", "max_url"].includes(id), blocking: true, action: null })) };
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

it("keeps optional services discoverable outside collapsed readiness details and offers no own-server hosting", async () => {
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(container.querySelector('[data-testid="max-one-click-launch"]')).not.toBeNull());
  expect(container.querySelector('a[href="/max/project-surfaces?panel=services"]')!.closest("details")).toBeNull();
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
  expect(requirements!.querySelector('[data-requirement="bot"] a')?.getAttribute("href")).toBe("/max/project-surfaces?panel=max");
  expect(requirements!.querySelector('[data-requirement="max_url"]')?.getAttribute("data-state")).toBe("later");
  expect(requirements!.querySelector('[data-requirement="max_url"] a')?.getAttribute("href")).toBe("/max/project-surfaces?panel=max");
  // Длинный дисклеймер больше не самый заметный текст на экране.
  expect(container.querySelector(".max-publication-optional")?.tagName).toBe("DETAILS");
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
  api.deploy.mockImplementation(() => new Promise<DeployStatus>(done => { resolve = done; }));
  await mount(<MaxLaunchPanel project={project} />);
  await settle(() => expect(container.textContent).toContain("Проверяем публикацию"));
  expect(container.textContent).not.toContain("Приложение опубликовано");
  expect(container.textContent).not.toContain("Полностью готово к запуску");
  expect(container.querySelector('[data-testid="max-launch-app-url"]')).toBeNull();
  await act(async () => resolve(release));
  await settle(() => expect(container.textContent).toContain("Приложение опубликовано"));
  expect(container.querySelector('[data-testid="max-launch-app-url"]')?.getAttribute("href")).toBe("https://app.example.com");
});
it.each([true, false])("reports publication evidence without claiming continuous uptime (published=%s)", async published => {
  api.readiness.mockResolvedValue(readiness(published));
  await mount(dashboard());
  await settle(() => expect(container.textContent).toContain(published ? "Приложение опубликовано" : "Изменения не опубликованы"));
  expect(container.textContent).toContain("Рабочая среда редактора");
  // Про наблюдение говорим прямо: не «мониторинг не подключён», а что это
  // значит для владельца, если приложение перестанет открываться.
  expect(container.textContent).toContain("Постоянного наблюдения за доступностью пока нет");
  expect(container.textContent).toContain("мы не узнаем об этом сами");
  expect(container.textContent).not.toContain("Отвечает");
});
it("offers a retry for failed status queries without showing stale success", async () => {
  client.setQueryData(["max-readiness", project.id], readiness(true));
  client.setQueryDefaults(["max-readiness", project.id], { staleTime: 0 });
  api.readiness.mockRejectedValue(new Error("offline"));
  await mount(dashboard());
  // Одно событие — одна формулировка на весь кабинет: что случилось, опасно ли
  // это и что нажать. Раньше то же самое называлось тремя разными фразами.
  await settle(() => expect(container.textContent).toContain("Не дозвонились до сервера"));
  expect(container.textContent).toContain("продолжает работать");
  expect(container.textContent).not.toContain("Приложение опубликовано");
  api.readiness.mockResolvedValue(readiness(false));
  const retry = [...container.querySelectorAll("button")].find(button => button.textContent?.includes("Проверить ещё раз"));
  expect(retry).toBeDefined();
  await act(async () => retry!.click());
  await settle(() => expect(container.textContent).toContain("Изменения не опубликованы"));
});
it("does not expose publication history on the MVP dashboard", async () => {
  await mount(dashboard());
  await settle(() => expect(container.querySelector(".max-dashboard-release")).not.toBeNull());
  expect(container.querySelector(".max-dashboard-history")).toBeNull();
  expect(api.history).not.toHaveBeenCalled();
});
it("reports a failed deployment separately from an unpublished draft", async () => {
  api.deploy.mockResolvedValue({ ...release, phase: "failed", error: "Сборка не завершена" });
  await mount(dashboard());
  await settle(() => expect(container.textContent).toContain("Последняя публикация не завершилась"));
  expect(container.textContent).toContain("Сборка не завершена");
});

it.each(["done", "queued", "failed"] as const)("keeps the actual published address beside unpublished changes and latest %s attempt", async phase => {
  api.deploy.mockResolvedValue({ ...release, phase, run_id: "latest-attempt", active_publication: activePublication, error: phase === "failed" ? "Сборка не завершена" : null });
  await mount(dashboard());
  await settle(() => expect(container.querySelector('a[href="https://app.example.com"]')).not.toBeNull());
  expect(container.textContent).toContain("Изменения не опубликованы");
  expect(container.textContent).toContain("После последней публикации появились изменения");
  expect(container.textContent).not.toContain("Приложение опубликовано");
  if (phase === "failed") expect(container.textContent).toContain("Последняя публикация не завершилась");
  expect(api.history).not.toHaveBeenCalled();
});

it.each(["failed", "queued", "config_only", "already_current"] as const)("summarizes the actual release independently of later %s attempt", async attempt => {
  const phase = attempt === "failed" || attempt === "queued" ? attempt : "done";
  api.deploy.mockResolvedValue({ ...release, phase, run_id: "latest-attempt", active_publication: activePublication, started_at: "2026-10-03T09:00:00Z", finished_at: phase === "queued" ? null : "2026-10-03T10:00:00Z", detail: phase === "done" ? attempt : null, error: phase === "failed" ? "Сборка не завершена" : null });
  await mount(dashboard());
  await settle(() => expect(container.querySelector(".max-dashboard-release dl")).not.toBeNull());
  const fields = [...container.querySelectorAll(".max-dashboard-release dd")];
  expect(fields[0].textContent).toBe("Ваше приложение");
  expect(fields[1].querySelector("a")?.getAttribute("href")).toBe(activePublication.prod_url);
  expect(fields[2].textContent).toBe(relativeDateLabel(activePublication.finished_at));
  if (phase === "failed") expect(container.textContent).toContain("Последняя публикация не завершилась");
  if (phase === "queued") expect(container.textContent).toContain("В очереди — публикация выполняется на сервере");
  expect(api.history).not.toHaveBeenCalled();
});

it.each(["queued", "failed"] as const)("does not summarize a %s attempt as an accepted publication when active projection is null", async phase => {
  api.deploy.mockResolvedValue({ ...release, phase, run_id: "latest-attempt", active_publication: null, error: phase === "failed" ? "Сборка не завершена" : null });
  await mount(dashboard());
  await settle(() => expect(container.textContent).toContain("Изменения не опубликованы"));
  expect(container.querySelector(".max-dashboard-release dl")).toBeNull();
  expect(container.querySelector(".max-dashboard-empty")).not.toBeNull();
  if (phase === "failed") expect(container.textContent).toContain("Последняя публикация не завершилась");
  if (phase === "queued") expect(container.textContent).toContain("В очереди — публикация выполняется на сервере");
  expect(api.history).not.toHaveBeenCalled();
});

it("does not invent an active address when controller explicitly reports none", async () => {
  api.readiness.mockResolvedValue(readiness(true));
  api.deploy.mockResolvedValue({ ...release, active_publication: null });
  api.integration.mockResolvedValue({ app_url: "https://stale-integration.example.com" });
  await mount(dashboard());
  await settle(() => expect(container.textContent).toContain("Изменения не опубликованы"));
  expect(container.querySelector('a[href="https://app.example.com"]')).toBeNull();
  expect(container.querySelector('a[href="https://stale-integration.example.com"]')).toBeNull();
  expect(api.history).not.toHaveBeenCalled();
});

it.each(["readiness", "deploy"] as const)("hides stale active-publication success after %s status fails", async source => {
  api.readiness.mockResolvedValue(readiness(true));
  api.deploy.mockResolvedValue({ ...release, active_publication: activePublication });
  client.setQueryData([source === "readiness" ? "max-readiness" : "deploy", project.id], source === "readiness" ? readiness(true) : { ...release, active_publication: activePublication });
  client.setQueryDefaults([source === "readiness" ? "max-readiness" : "deploy", project.id], { staleTime: 0 });
  api[source].mockRejectedValue(new Error("offline"));
  await mount(dashboard());
  await settle(() => expect(container.textContent).toContain("Не дозвонились до сервера"));
  expect(container.querySelector('a[href="https://app.example.com"]')).toBeNull();
  expect(container.textContent).not.toContain("Приложение опубликовано");
});

it("вместо прочерков говорит, что публикаций ещё не было", async () => {
  // Три поля из четырёх стояли прочерками: пустой прочерк читается как
  // поломка. Пока публикации не было, честная строка понятнее таблицы.
  api.deploy.mockResolvedValue({ ...release, phase: "idle", run_id: null, finished_at: null, prod_url: null, image_tag: null });
  await mount(dashboard());
  await settle(() => expect(container.textContent).toContain("Публикаций ещё не было"));
  expect(container.querySelector(".max-dashboard-release dl")).toBeNull();
  expect(container.textContent).not.toContain("Версия сборки");
});

it("после публикации отвечает, что опубликовано, где открывается и когда", async () => {
  api.readiness.mockResolvedValue(readiness(true));
  api.deploy.mockResolvedValue(release);
  await mount(dashboard());
  await settle(() => expect(container.querySelector(".max-dashboard-release dl")).not.toBeNull());
  const fields = [...container.querySelectorAll(".max-dashboard-release dt")].map(node => node.textContent);
  expect(fields).toEqual(["Что опубликовано", "Где открывается", "Когда"]);
  expect(container.querySelector(".max-dashboard-release dd")?.textContent).toBe("Ваше приложение");
  // Про наблюдение за доступностью говорим прямо, а не техническим отрицанием.
  expect(container.textContent).toContain("мы не узнаем об этом сами");
});

it("keeps current application management without querying publication history", async () => {
  api.readiness.mockResolvedValue(readiness(true));
  api.deploy.mockResolvedValue(release);
  await mount(dashboard());
  await settle(() => expect(container.querySelector(".max-dashboard-release dl")).not.toBeNull());
  expect(container.querySelector(".max-dashboard-history")).toBeNull();
  expect(container.textContent).not.toContain("Версия v1");
  expect(api.history).not.toHaveBeenCalled();
  expect(container.querySelector('a[href="/max/project-surfaces"]')).not.toBeNull();
});
