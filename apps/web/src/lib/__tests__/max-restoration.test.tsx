import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useMaxRestoration } from "@/lib/use-max-restoration";
import { MaxRestorationPanel } from "@/components/max/MaxRestorationPanel";
import * as api from "@/lib/api/restorations";
import type { ProjectVersion, Snapshot } from "@/lib/api/types";
import { ApiError } from "@/lib/api/client";

vi.mock("@/lib/api/restorations", () => ({
  listRestorations: vi.fn(), getRestoration: vi.fn(), prepareRestoration: vi.fn(),
  applyRestoration: vi.fn(), cancelRestoration: vi.fn(),
}));
const oldVersion: ProjectVersion = {
  id: "v3", number: 3, project_id: "a", snapshot_id: "s3", commit_sha: "sha3",
  prompt_text: "Список клиентов", model_id: null, created_at: "2026-09-09T00:00:00Z",
  status: "ready", preview_status: "missing", previews: [], is_current: false, can_restore: false,
};
const applied: Snapshot = {
  id: "s12", project_id: "a", commit_sha: "sha12", prompt_text: "Восстановление",
  model_id: null, parent_id: "s11", preview_url: null, is_rollback_target: false,
  created_at: "2026-09-09T01:00:00Z",
};
function operation(state: api.RestoreOperation["state"] = "ready"): api.RestoreOperation {
  return {
    id: "op-a", project_id: "a", target: "draft", source_version_id: "v3",
    source_snapshot_id: "s3", base_draft_snapshot_id: "s11", state, phase: state,
    updated_at: "2026-09-09T01:00:00Z", revision: 2, candidate_id: "candidate",
    report: {
      revision: 2, mode: "exact", changes: ["Вернётся список клиентов"],
      retained_data: ["Фамилии сохранятся"], unavailable_features: ["Ввод фамилии"],
      warnings: [], blockers: [], next_actions: [],
    },
    can_apply: state === "ready", can_cancel: state === "ready",
    applied_version: null, applied_snapshot: state === "completed" ? applied : null, error: null,
  };
}
let client: QueryClient;
let root: Root;
let container: HTMLDivElement;
let controller: ReturnType<typeof useMaxRestoration>;
const completed = vi.fn();
const adapt = vi.fn();
function Harness({ project = "a", head = "s11" }: { project?: string; head?: string }) {
  const value = useMaxRestoration({ projectId: project, currentSnapshotId: head, onCompleted: completed });
  useEffect(() => { controller = value; }, [value]);
  return <MaxRestorationPanel restoration={value} onAdapt={adapt} />;
}
async function render(project = "a", head = "s11") {
  await act(async () => root.render(<QueryClientProvider client={client}>
    <Harness project={project} head={head} />
  </QueryClientProvider>));
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); });
}
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  localStorage.clear(); vi.clearAllMocks();
  vi.mocked(api.listRestorations).mockResolvedValue({ enabled: true, items: [] });
  vi.mocked(api.getRestoration).mockResolvedValue(operation());
  vi.mocked(api.prepareRestoration).mockResolvedValue(operation("checking"));
  vi.mocked(api.applyRestoration).mockResolvedValue(operation("applying"));
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); });

it("discovers the server operation on F5 without creating another preparation", async () => {
  vi.mocked(api.listRestorations).mockResolvedValue({ enabled: true, items: [operation()] });
  await render();
  expect(controller.operation?.id).toBe("op-a");
  expect(container.textContent).toContain("Сделать текущей в редакторе");
  expect(api.prepareRestoration).not.toHaveBeenCalled(); expect(completed).not.toHaveBeenCalled();
});
it.each(["cancelled", "reconciling"] as const)("starts adaptation only after confirmed cancellation: %s", async (state) => {
  const blocked = { ...operation("needs_changes"), can_cancel: true,
    report: { ...operation().report!, blockers: ["new_required_column:contacts.surname"] } };
  vi.mocked(api.listRestorations).mockResolvedValue({ enabled: true, items: [blocked] });
  vi.mocked(api.getRestoration).mockResolvedValue(blocked);
  vi.mocked(api.cancelRestoration).mockResolvedValue(operation(state));
  await render();
  const button = container.querySelector<HTMLButtonElement>('[data-testid="max-restoration-adapt"]');
  expect(button).not.toBeNull();
  await act(async () => button!.click());
  expect(api.cancelRestoration).toHaveBeenCalledWith("a", "op-a");
  if (state === "cancelled") {
    expect(adapt).toHaveBeenCalledOnce();
    expect(adapt.mock.calls[0][0]).toContain("Сохрани все текущие пользовательские данные");
    expect(adapt.mock.calls[0][0]).not.toContain("op-a");
    expect(adapt.mock.calls[0][0]).toContain("Не публикуй приложение");
    expect(adapt.mock.calls[0][1]).toEqual({
      operation_id: "op-a", expected_draft_snapshot_id: "s11",
    });
  } else expect(adapt).not.toHaveBeenCalled();
  expect(api.prepareRestoration).not.toHaveBeenCalled();
  expect(api.applyRestoration).not.toHaveBeenCalled();
});
it("does not start a late adaptation request in another project's editor", async () => {
  const blocked = { ...operation("needs_changes"), can_cancel: true };
  vi.mocked(api.listRestorations).mockResolvedValue({ enabled: true, items: [blocked] });
  vi.mocked(api.getRestoration).mockResolvedValue(blocked);
  let finish!: (value: api.RestoreOperation) => void;
  vi.mocked(api.cancelRestoration).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  await render();
  await act(async () => container.querySelector<HTMLButtonElement>('[data-testid="max-restoration-adapt"]')!.click());
  vi.mocked(api.listRestorations).mockResolvedValue({ enabled: true, items: [] });
  await render("b", "b-head");
  await act(async () => finish(operation("cancelled")));
  expect(adapt).not.toHaveBeenCalled();
});
it("refuses preparation while this browser has an unfinished publication", async () => {
  localStorage.setItem("omnia:max:launch:a", JSON.stringify({ version: 1, phase: "requesting",
    idempotencyKey: "publish-key", runId: null, paused: false, deadlineAt: Date.now() + 60_000 }));
  await render();
  await act(async () => controller.prepare(oldVersion));
  expect(api.prepareRestoration).not.toHaveBeenCalled();
  await render();
  expect(container.textContent).toContain("публикации");
});
it("prepares retained source without a screenshot or legacy can_restore and deduplicates double clicks", async () => {
  let resolve!: (value: api.RestoreOperation) => void;
  vi.mocked(api.prepareRestoration).mockReturnValue(new Promise(done => { resolve = done; }));
  await render();
  let first!: Promise<void>;
  await act(async () => { first = controller.prepare(oldVersion); void controller.prepare(oldVersion); });
  expect(api.prepareRestoration).toHaveBeenCalledTimes(1);
  expect(api.prepareRestoration).toHaveBeenCalledWith("a", expect.objectContaining({
    target_version_id: "v3", expected_draft_snapshot_id: "s11", idempotency_key: expect.any(String),
  }));
  await act(async () => { resolve(operation("checking")); await first; });
  expect(completed).not.toHaveBeenCalled();
});
it("keeps the logical prepare key after a lost POST response", async () => {
  vi.mocked(api.prepareRestoration).mockRejectedValueOnce(new Error("Соединение прервано"));
  await render(); await act(async () => controller.prepare(oldVersion));
  const payload = vi.mocked(api.prepareRestoration).mock.calls[0][1];
  expect(container.textContent).toContain("Повторить проверку");
  await act(async () => controller.retry());
  expect(vi.mocked(api.prepareRestoration).mock.calls[1][1]).toEqual(payload);
});
it("applies the reviewed revision, then updates HEAD only after completed and only once", async () => {
  vi.mocked(api.listRestorations).mockResolvedValue({ enabled: true, items: [operation()] });
  await render(); await act(async () => controller.apply());
  expect(api.applyRestoration).toHaveBeenCalledWith("a", "op-a", expect.objectContaining({
    report_revision: 2, expected_draft_snapshot_id: "s11", idempotency_key: expect.any(String),
  }));
  expect(completed).not.toHaveBeenCalled();
  await act(async () => { client.setQueryData(["restoration", "a", "op-a"], operation("completed")); });
  await render();
  expect(completed).toHaveBeenCalledExactlyOnceWith(applied);
  await render(); expect(completed).toHaveBeenCalledTimes(1);
});
it("does not apply an old completed operation over a newer draft discovered after F5", async () => {
  vi.mocked(api.listRestorations).mockResolvedValue({ enabled: true, items: [operation("completed")] });
  vi.mocked(api.getRestoration).mockResolvedValue(operation("completed"));
  await render("a", "s99"); expect(completed).not.toHaveBeenCalled();
});
it("ignores a late preparation response after switching to another project", async () => {
  let resolve!: (value: api.RestoreOperation) => void;
  vi.mocked(api.prepareRestoration).mockReturnValue(new Promise(done => { resolve = done; }));
  await render(); let pending!: Promise<void>;
  await act(async () => { pending = controller.prepare(oldVersion); });
  await render("b");
  await act(async () => { resolve(operation("completed")); await pending; });
  expect(controller.operation).toBeNull(); expect(completed).not.toHaveBeenCalled();
});
it("shows reconciliation honestly without an apply button or promises before a report", async () => {
  const pending = { ...operation("reconciling"), report: null, can_apply: false, can_cancel: false };
  vi.mocked(api.listRestorations).mockResolvedValue({ enabled: true, items: [pending] });
  vi.mocked(api.getRestoration).mockResolvedValue(pending);
  await render();
  expect(container.textContent).toContain("Уточняем результат");
  expect(container.textContent).not.toContain("Фамилии сохранятся");
  expect(container.querySelector("[data-testid='max-restoration-apply']")).toBeNull();
});
it("does not prepare when the server disabled restoration or a version has no source", async () => {
  vi.mocked(api.listRestorations).mockResolvedValue({ enabled: false, items: [] });
  await render(); await act(async () => controller.prepare(oldVersion));
  expect(api.prepareRestoration).not.toHaveBeenCalled();
  vi.mocked(api.listRestorations).mockResolvedValue({ enabled: true, items: [] });
  await act(async () => { await client.invalidateQueries({ queryKey: ["restorations", "a"] }); });
  await act(async () => controller.prepare({ ...oldVersion, snapshot_id: null }));
  expect(api.prepareRestoration).not.toHaveBeenCalled();
});

it("rediscovers a newer server operation after a completed selected operation", async () => {
  vi.mocked(api.prepareRestoration).mockResolvedValue(operation("completed"));
  vi.mocked(api.getRestoration).mockResolvedValue(operation("completed"));
  await render(); await act(async () => controller.prepare(oldVersion));
  const next = { ...operation(), id: "op-new", updated_at: "2026-09-09T02:00:00Z" };
  vi.mocked(api.getRestoration).mockResolvedValue(next);
  await act(async () => { client.setQueryData(["restorations", "a"], { enabled: true, items: [next, operation("completed")] }); });
  await render(); expect(controller.operation?.id).toBe("op-new");
});

it("restores a lost-response key after an actual hook remount and does not auto-submit", async () => {
  vi.mocked(api.prepareRestoration).mockRejectedValueOnce(new Error("Нет ответа"));
  await render(); await act(async () => controller.prepare(oldVersion));
  const payload = vi.mocked(api.prepareRestoration).mock.calls[0][1];
  await act(async () => root.unmount()); client.clear();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); root = createRoot(container);
  await render(); expect(api.prepareRestoration).toHaveBeenCalledTimes(1);
  await act(async () => controller.retry());
  expect(vi.mocked(api.prepareRestoration).mock.calls[1][1]).toEqual(payload);
});

it("does not submit reviewed code after the draft HEAD changes", async () => {
  vi.mocked(api.listRestorations).mockResolvedValue({ enabled: true, items: [operation()] });
  await render("a", "s99"); await act(async () => controller.apply());
  expect(api.applyRestoration).not.toHaveBeenCalled();
  expect(container.textContent).toContain("Черновик изменился");
});

it("refreshes after a definitive admission conflict instead of replaying stale preparation forever", async () => {
  vi.mocked(api.prepareRestoration).mockRejectedValueOnce(new ApiError(409, { code: "conflict", message: "Черновик изменился" }));
  await render(); await act(async () => controller.prepare(oldVersion));
  const rejected = vi.mocked(api.prepareRestoration).mock.calls[0][1];
  await act(async () => controller.retry());
  expect(api.prepareRestoration).toHaveBeenCalledTimes(1);
  await act(async () => controller.prepare(oldVersion));
  expect(vi.mocked(api.prepareRestoration).mock.calls[1][1].idempotency_key).not.toBe(rejected.idempotency_key);
});

it("cancels only a server-cancellable operation without changing HEAD", async () => {
  vi.mocked(api.listRestorations).mockResolvedValue({ enabled: true, items: [operation()] });
  vi.mocked(api.cancelRestoration).mockResolvedValue(operation("cancelled"));
  await render(); await act(async () => controller.cancel());
  expect(api.cancelRestoration).toHaveBeenCalledExactlyOnceWith("a", "op-a");
  expect(completed).not.toHaveBeenCalled();
  await act(async () => controller.cancel());
  expect(api.cancelRestoration).toHaveBeenCalledTimes(1);
});

it("does not present a retained earlier report as a guarantee during rechecking", async () => {
  const checking = operation("checking");
  vi.mocked(api.listRestorations).mockResolvedValue({ enabled: true, items: [checking] });
  vi.mocked(api.getRestoration).mockResolvedValue(checking);
  await render();
  expect(container.textContent).toContain("Проверяем сохранность данных");
  expect(container.textContent).not.toContain("Фамилии сохранятся");
});
