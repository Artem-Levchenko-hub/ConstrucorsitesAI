import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxWorkspaceShell } from "@/components/max/MaxWorkspaceShell";
import type { Project, Message } from "@/lib/api/types";
import type { RestoreOperation } from "@/lib/api/restorations";

const api = vi.hoisted(() => ({
  submit: vi.fn(), cancel: vi.fn(), list: vi.fn(), detail: vi.fn(), snapshots: vi.fn(),
  pendingPrompt: null as string | null,
}));
vi.mock("@/hooks/usePromptStream", () => ({ usePromptStream: () => ({
  submit: api.submit, cancel: vi.fn(), cancelPending: vi.fn(), pendingPrompt: api.pendingPrompt,
}) }));
vi.mock("@/lib/api/restorations", () => ({
  listRestorations: api.list, getRestoration: api.detail, cancelRestoration: api.cancel,
  prepareRestoration: vi.fn(), applyRestoration: vi.fn(),
}));
vi.mock("@/lib/api/projects", () => ({ listProjects: async () => [] }));
vi.mock("@/lib/api/snapshots", () => ({
  listSnapshots: api.snapshots, listProjectVersions: async () => ({ versions: [], next_cursor: null }), rollback: vi.fn(),
}));
vi.mock("@/lib/api/max-studio", () => ({
  getMaxReadiness: async () => ({ items: [] }), getMaxProjectConfig: async () => ({ config_version: 1 }),
}));
vi.mock("@/lib/api/messages", () => ({ listMessages: async () => [] }));
vi.mock("@/components/max/MaxEditorLayout", () => ({ MaxEditorLayout: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock("@/components/max/MaxLivePreview", () => ({ MaxLivePreview: () => null }));
vi.mock("@/components/max/MaxLaunchPanel", () => ({ MaxLaunchPanel: () => null }));
vi.mock("@/components/max/MaxAccountMenu", () => ({ MaxAccountMenu: () => null }));
vi.mock("@/components/max/MaxProjectNav", () => ({ MaxProjectNav: () => null }));
vi.mock("@/components/max/MaxUsageBreakdown", () => ({ MaxUsageBreakdown: () => null }));
vi.mock("@/components/workspace/DownloadButton", () => ({ DownloadButton: () => null }));
vi.mock("@/components/workspace/ChatMessage", () => ({ ChatMessage: () => null }));

const project = { id: "a", name: "App", slug: "app", template: "max_miniapp", current_snapshot_id: "head" } as Project;
const reference = { operation_id: "operation", expected_draft_snapshot_id: "head" };
const key = "omnia:max:adaptation:a";
const blocked: RestoreOperation = {
  id: "operation", project_id: "a", target: "draft", source_version_id: "old-version",
  source_snapshot_id: "old-snapshot", base_draft_snapshot_id: "head", state: "needs_changes",
  phase: "needs_changes", updated_at: "2026-09-12T00:00:00Z", revision: 1,
  candidate_id: null, can_apply: false, can_cancel: true, applied_version: null,
  applied_snapshot: null, error: null,
  report: { revision: 1, mode: "exact", database_state: "present", changes: [], retained_data: [],
    unavailable_features: [], warnings: [], blockers: ["Требуется совместимость новых полей"], next_actions: [] },
};
let currentOperation: RestoreOperation;
let root: Root, container: HTMLDivElement, client: QueryClient;
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); }
async function render(value = project) {
  await act(async () => root.render(<QueryClientProvider client={client}>
    <MaxWorkspaceShell project={value} email="" />
  </QueryClientProvider>));
  await settle();
}
function button(text: string) {
  const element = [...container.querySelectorAll("button")].find(item => item.textContent?.includes(text));
  expect(element).toBeDefined();
  return element!;
}
async function click(text: string) { await act(async () => button(text).click()); await settle(); }
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  localStorage.clear(); vi.clearAllMocks(); api.pendingPrompt = null;
  currentOperation = { ...blocked };
  api.list.mockImplementation(async () => ({ enabled: true, items: [currentOperation] }));
  api.detail.mockImplementation(async () => currentOperation);
  api.cancel.mockImplementation(async () => {
    currentOperation = { ...blocked, state: "cancelled", revision: 2, can_cancel: false };
    return currentOperation;
  });
  api.snapshots.mockResolvedValue([{ id: "head", project_id: "a" }]);
  api.submit.mockResolvedValue(true);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); });

it("one click cancels preparation then submits the historical reference without touching the typed draft", async () => {
  await render();
  const textarea = container.querySelector("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(textarea, "Мой следующий запрос");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(api.submit).not.toHaveBeenCalled();
  await click("Адаптировать и восстановить");
  expect(api.cancel).toHaveBeenCalledExactlyOnceWith("a", "operation");
  expect(api.submit).toHaveBeenCalledExactlyOnceWith(expect.stringContaining("Верни экраны"), "topmix-v1", [], {
    restorationAdaptation: reference, idempotencyKey: "restoration-adapt:operation", skipClarify: true,
  });
  expect(api.cancel.mock.invocationCallOrder[0]).toBeLessThan(api.submit.mock.invocationCallOrder[0]);
  expect(textarea.value).toBe("Мой следующий запрос");
  expect(localStorage.getItem(key)).toBeNull();
});

it("uncertain cancellation never sends a model request", async () => {
  api.cancel.mockResolvedValue({ ...blocked, state: "reconciling", can_cancel: false });
  await render(); await click("Адаптировать и восстановить");
  expect(api.submit).not.toHaveBeenCalled();
  expect(JSON.parse(localStorage.getItem(key)!).phase).toBe("cancelling");
});

it("saves intent before cancellation and recovers after F5 only when the user explicitly retries", async () => {
  const cancellation = deferred<RestoreOperation>(); api.cancel.mockReturnValue(cancellation.promise);
  await render(); await act(async () => button("Адаптировать и восстановить").click());
  const saved = JSON.parse(localStorage.getItem(key)!);
  expect(saved.phase).toBe("cancelling"); expect(saved.reference).toEqual(reference);
  await act(async () => root.unmount()); client.clear(); root = createRoot(container);
  currentOperation = { ...blocked, state: "cancelled", revision: 2, can_cancel: false };
  await render();
  await act(async () => cancellation.resolve(currentOperation));
  expect(api.submit).not.toHaveBeenCalled();
  await click("Повторить запуск адаптации");
  expect(api.submit).toHaveBeenCalledExactlyOnceWith(saved.prompt, "topmix-v1", [], {
    restorationAdaptation: reference, idempotencyKey: "restoration-adapt:operation", skipClarify: true,
  });
  expect(api.cancel).toHaveBeenCalledTimes(1);
  expect(localStorage.getItem(key)).toBeNull();
  expect(localStorage.getItem("omnia:restore:request:a")).toBeNull();
});

it.each(["reconciling", "other-operation"])("saved cancellation never submits when canonical state is %s", async state => {
  localStorage.setItem(key, JSON.stringify({ projectId: "a", prompt: "Верни экраны", reference, phase: "cancelling" }));
  currentOperation = { ...blocked, state: "reconciling", can_cancel: false,
    ...(state === "other-operation" ? { id: "another-operation" } : {}) };
  await render(); await click("Повторить запуск адаптации");
  expect(api.submit).not.toHaveBeenCalled(); expect(api.cancel).not.toHaveBeenCalled();
  expect(localStorage.getItem(key)).not.toBeNull();
});

it("saved intent explicitly retries an unfinished cancellable preparation before generation", async () => {
  localStorage.setItem(key, JSON.stringify({ projectId: "a", prompt: "Верни экраны", reference, phase: "cancelling" }));
  await render();
  expect(api.cancel).not.toHaveBeenCalled(); expect(api.submit).not.toHaveBeenCalled();
  await click("Повторить запуск адаптации");
  expect(api.cancel).toHaveBeenCalledExactlyOnceWith("a", "operation");
  expect(api.submit).toHaveBeenCalledTimes(1);
  expect(api.cancel.mock.invocationCallOrder[0]).toBeLessThan(api.submit.mock.invocationCallOrder[0]);
});

it("ordinary compatible restoration does not offer or start an AI generation", async () => {
  currentOperation = { ...blocked, state: "ready", can_apply: true,
    report: { ...blocked.report!, database_state: "empty", blockers: [] } };
  await render();
  expect(container.textContent).toContain("Подготовлен выбранный код без запуска ИИ");
  expect(container.textContent).toContain("В проверенной базе нет бизнес-записей");
  expect(container.querySelector('[data-testid="max-restoration-adapt"]')).toBeNull();
  expect(api.submit).not.toHaveBeenCalled();
});

it("unknown data inventory never promises an empty database", async () => {
  currentOperation = { ...blocked, report: { ...blocked.report!, database_state: "unknown" } };
  await render();
  expect(container.textContent).not.toContain("нет бизнес-записей");
  expect(api.submit).not.toHaveBeenCalled();
});

it("double click during cancellation and while submitting sends exactly one request", async () => {
  const cancellation = deferred<RestoreOperation>();
  api.cancel.mockReturnValue(cancellation.promise);
  const submission = deferred<boolean>(); api.submit.mockReturnValue(submission.promise);
  await render();
  await act(async () => { const action = button("Адаптировать и восстановить"); action.click(); action.click(); });
  expect(api.cancel).toHaveBeenCalledTimes(1); expect(api.submit).not.toHaveBeenCalled();
  await act(async () => {
    currentOperation = { ...blocked, state: "cancelled", revision: 2, can_cancel: false };
    cancellation.resolve(currentOperation);
  });
  await settle();
  expect(button("Повторить запуск адаптации").disabled).toBe(true);
  await act(async () => button("Повторить запуск адаптации").click());
  expect(api.submit).toHaveBeenCalledTimes(1);
  await act(async () => submission.resolve(true));
});

it("failed submission survives refresh without automatic replay; explicit retry uses the same request", async () => {
  api.submit.mockResolvedValueOnce(false).mockResolvedValueOnce(true);
  await render(); await click("Адаптировать и восстановить");
  expect(localStorage.getItem(key)).not.toBeNull();
  const first = api.submit.mock.calls[0];
  await act(async () => root.unmount()); client.clear(); root = createRoot(container);
  await render();
  expect(api.submit).toHaveBeenCalledTimes(1);
  await click("Повторить запуск адаптации");
  expect(api.submit.mock.calls[1]).toEqual(first);
  expect(localStorage.getItem(key)).toBeNull();
});

it.each(["head", "project"])("does not submit if %s changes while cancellation is pending", async kind => {
  const cancellation = deferred<RestoreOperation>(); api.cancel.mockReturnValue(cancellation.promise);
  await render(); await act(async () => button("Адаптировать и восстановить").click());
  if (kind === "head") {
    await act(async () => client.setQueryData(["snapshots", "a"], [{ id: "new-head", project_id: "a" }]));
    await render({ ...project, current_snapshot_id: "new-head" });
  } else {
    api.list.mockResolvedValue({ enabled: true, items: [] });
    api.snapshots.mockResolvedValue([{ id: "head-b", project_id: "b" }]);
    await render({ ...project, id: "b", current_snapshot_id: "head-b" });
  }
  await act(async () => cancellation.resolve({ ...blocked, state: "cancelled", revision: 2, can_cancel: false }));
  await settle();
  expect(api.submit).not.toHaveBeenCalled();
});

it("refuses to adapt a historical request after HEAD changed before clicking", async () => {
  api.snapshots.mockResolvedValue([{ id: "new-head", project_id: "a" }]);
  await render({ ...project, current_snapshot_id: "new-head" });
  expect(button("Адаптировать и восстановить").disabled).toBe(true);
  await click("Адаптировать и восстановить");
  expect(api.cancel).not.toHaveBeenCalled(); expect(api.submit).not.toHaveBeenCalled();
});

it.each(["generation", "queued", "publication", "stale-head"])("blocks explicit retry during %s without discarding the saved reference", async condition => {
  localStorage.setItem(key, JSON.stringify({ projectId: "a", prompt: "Верни экраны", reference }));
  currentOperation = { ...blocked, state: "cancelled", revision: 2, can_cancel: false };
  if (condition === "queued") api.pendingPrompt = "Ожидающий запрос";
  if (condition === "publication") localStorage.setItem("omnia:max:launch:a", JSON.stringify({
    version: 1, phase: "requesting", idempotencyKey: "publish", runId: null, paused: false, deadlineAt: Date.now() + 1000,
  }));
  if (condition === "stale-head") api.snapshots.mockResolvedValue([{ id: "new-head", project_id: "a" }]);
  await render();
  if (condition === "generation") await act(async () => client.setQueryData(["messages", "a"], [{
    id: "active", role: "assistant", content: "", generation_status: "running", tokens_out: null,
  } as unknown as Message]));
  await click("Повторить запуск адаптации");
  expect(api.submit).not.toHaveBeenCalled();
  expect(localStorage.getItem(key)).not.toBeNull();
});
