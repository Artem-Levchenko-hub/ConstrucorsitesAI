import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ComponentProps, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MaxWorkspaceShell } from "@/components/max/MaxWorkspaceShell";
import { usePromptStream } from "@/hooks/usePromptStream";
import { listProjectVersions, listSnapshots, rollback } from "@/lib/api/snapshots";
import type { Message, Project, ProjectVersionPage, Snapshot, WsEvent } from "@/lib/api/types";

type PreviewProps = ComponentProps<typeof import("@/components/max/MaxLivePreview").MaxLivePreview>;
type ChatProps = ComponentProps<typeof import("@/components/workspace/ChatPanel").ChatPanel>;
const observed = vi.hoisted(() => ({ preview: null as PreviewProps | null }));
function preview(): PreviewProps {
  if (!observed.preview) throw new Error("Preview has not rendered");
  return observed.preview;
}

vi.mock("@/lib/api/messages", () => ({
  cancelGeneration: vi.fn(), sendPrompt: vi.fn(),
  getLatestGeneration: vi.fn(async () => ({
    id: "run-1", assistant_message_id: "message-1", status: "running",
  })),
}));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
vi.mock("@/lib/api/projects", () => ({ listProjects: vi.fn(async () => []) }));
vi.mock("@/lib/api/snapshots", () => ({
  listProjectVersions: vi.fn(), listSnapshots: vi.fn(), rollback: vi.fn(),
}));
vi.mock("@/lib/api/max-studio", () => ({
  getMaxReadiness: vi.fn(async () => ({ items: [] })),
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() } }));
vi.mock("@/components/max/MaxEditorLayout", () => ({
  MaxEditorLayout: ({ children, preview }: { children: ReactNode; preview: ReactNode }) =>
    <>{children}{preview}</>,
}));
vi.mock("@/components/workspace/ChatPanel", () => ({
  ChatPanel: ({ projectId, projectSlug }: ChatProps) => {
    usePromptStream(projectId, projectSlug);
    return null;
  },
}));
vi.mock("@/components/max/MaxLivePreview", () => ({
  MaxLivePreview: (props: PreviewProps) => {
    observed.preview = props;
    return <output data-current={String(props.historyCurrent)}
      data-project={props.project.id} data-selected={props.selectedVersionId ?? "live"}
      data-versions={props.versions.map((v) => v.id).join(",")} />;
  },
}));
vi.mock("@/components/max/MaxLaunchPanel", () => ({ MaxLaunchPanel: () => null }));
vi.mock("@/components/max/MaxAccountMenu", () => ({ MaxAccountMenu: () => null }));
vi.mock("@/components/max/MaxProjectNav", () => ({ MaxProjectNav: () => null }));
vi.mock("@/components/max/MaxUsageBreakdown", () => ({ MaxUsageBreakdown: () => null }));
vi.mock("@/components/workspace/DownloadButton", () => ({ DownloadButton: () => null }));

class TestSocket {
  static OPEN = 1;
  static instances: TestSocket[] = [];
  readyState = 1;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose = null;
  onopen = null;
  constructor() { TestSocket.instances.push(this); }
  send() {}
  close() { this.readyState = 3; }
  emit(event: WsEvent) { this.onmessage?.({ data: JSON.stringify(event) }); }
}

let client: QueryClient;
let root: Root;
let container: HTMLDivElement;
let deferred = false;
let ignoreAbort = false;
let requests: {
  projectId: string; cursor?: number; aborted: boolean;
  resolve: (page?: ProjectVersionPage) => void;
}[];
const snapshot = (id: string): Snapshot => ({
  id, project_id: "project-1", commit_sha: `commit-${id}`, prompt_text: "Build",
  model_id: "fixture-model", parent_id: null, preview_url: null,
  is_rollback_target: false, created_at: "2026-09-08T00:00:00Z",
});
const project: Project = {
  id: "project-1", slug: "coffee", template: "max_miniapp", current_snapshot_id: "head-1",
  name: "Fixture", owner_id: "owner-1", created_at: "2026-09-08T00:00:00Z",
  updated_at: "2026-09-08T00:00:00Z",
};
const counts = () => ({ starts: requests.length, aborts: requests.filter((r) => r.aborted).length });
const page = (id: string, next: number | null = null): ProjectVersionPage => ({
  versions: [{
    id, number: next ? 21 : 20, project_id: project.id, snapshot_id: "head-1",
    commit_sha: "commit-head-1", prompt_text: "Build", model_id: "fixture-model",
    created_at: "2026-09-08T00:00:00Z", status: "ready", preview_status: "ready",
    previews: [], is_current: false, can_restore: false,
  }], next_cursor: next,
});
const output = (attribute: string) => container.querySelector("output")?.getAttribute(attribute);
async function renderProject(value: Project) {
  await act(async () => root.render(
    <QueryClientProvider client={client}>
      <MaxWorkspaceShell project={value} email="fixture@example.invalid" />
    </QueryClientProvider>,
  ));
  await flush();
}
async function loadTwoPages() {
  await act(async () => { void client.invalidateQueries({ queryKey: ["project-versions", project.id] }); });
  await act(async () => requests.at(-1)!.resolve(page("version-new", 20)));
  await flush();
  expect(preview().hasOlder).toBe(true);
  await act(async () => preview().onLoadOlder?.());
  expect(requests.at(-1)!.cursor).toBe(20);
  await act(async () => requests.at(-1)!.resolve(page("version-old")));
  await flush();
  expect(output("data-versions")).toBe("version-new,version-old");
  expect(output("data-current")).toBe("true");
  requests = [];
}
async function flush() {
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
}
async function emit(event: WsEvent) {
  await act(async () => TestSocket.instances.at(-1)!.emit(event));
  await flush();
}
beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", TestSocket);
  TestSocket.instances = [];
  requests = [];
  deferred = false;
  ignoreAbort = false;
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 300_000 } } });
  client.setQueryData<Message[]>(["messages", project.id], [{
    id: "message-1", role: "assistant", content: "Building", tokens_out: null,
    generation_status: "running", project_id: project.id, model_id: "fixture-model",
    tokens_in: null, snapshot_id: null, created_at: "2026-09-08T00:00:00Z",
  }]);
  client.setQueryData(["snapshots", project.id], [snapshot("head-1")]);
  vi.mocked(listSnapshots).mockImplementation(async (id) =>
    client.getQueryData<Snapshot[]>(["snapshots", id]) ?? []);
  vi.mocked(listProjectVersions).mockImplementation((id, cursor, signal) =>
    new Promise((resolve, reject) => {
      const request = {
        projectId: id, cursor, aborted: false,
        resolve: (value: ProjectVersionPage = { versions: [], next_cursor: null }) => resolve(value),
      };
      requests.push(request);
      signal?.addEventListener("abort", () => {
        request.aborted = true;
        if (!ignoreAbort) reject(new DOMException("Aborted", "AbortError"));
      });
      if (!deferred) request.resolve();
    }));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(
    <QueryClientProvider client={client}>
      <MaxWorkspaceShell project={project} email="fixture@example.invalid" />
    </QueryClientProvider>,
  ));
  await flush();
  expect(TestSocket.instances.length).toBe(1);
  expect(client.getQueryState(["project-versions", project.id])?.fetchStatus).toBe("idle");
  requests = [];
  deferred = true;
});
afterEach(async () => {
  await act(async () => root.unmount());
  client.clear();
  container.remove();
  vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.clearAllMocks();
});
describe("project version refetch ownership", () => {
  it.each(["head-2", "head-1"])("snapshot.created %s", async (id) => {
    await emit({ type: "snapshot.created", data: {
      snapshot: { ...snapshot(id), preview_url: "/updated-preview.png" },
    } });
    expect(counts()).toEqual({ starts: 1, aborts: 0 });
    await act(async () => requests.at(-1)!.resolve());
    await flush();
    expect(container.querySelector("output")?.getAttribute("data-current")).toBe("true");
  });
  it("terminal alone", async () => {
    await emit({ type: "llm.done", data: { message_id: "message-1", tokens_in: 2, tokens_out: 4, cost_rub: 0 } });
    expect(counts()).toEqual({ starts: 1, aborts: 0 });
  });
  it("terminal during changed-HEAD refetch", async () => {
    await emit({ type: "snapshot.created", data: { snapshot: snapshot("head-2") } });
    await emit({ type: "llm.done", data: { message_id: "message-1", tokens_in: 2, tokens_out: 4, cost_rub: 0 } });
    expect(counts()).toEqual({ starts: 2, aborts: 1 });
  });
  it("new HEAD refetches two loaded pages, preserves selection and waits for all pages", async () => {
    await loadTwoPages();
    await act(async () => preview().onSelectVersion("version-old"));
    await emit({ type: "snapshot.created", data: { snapshot: snapshot("head-2") } });
    expect(counts()).toEqual({ starts: 1, aborts: 0 });
    expect(output("data-selected")).toBe("version-old");
    expect(output("data-current")).toBe("false");
    await act(async () => requests.at(-1)!.resolve(page("version-newer", 20)));
    await flush();
    expect(requests.at(-1)!.cursor).toBe(20);
    expect(output("data-current")).toBe("false");
    expect(output("data-selected")).toBe("version-old");
    await act(async () => requests.at(-1)!.resolve(page("version-old")));
    await flush();
    expect(output("data-current")).toBe("true");
    expect(output("data-versions")).toBe("version-newer,version-old");
    expect(output("data-selected")).toBe("version-old");
    expect(counts()).toEqual({ starts: 2, aborts: 0 });
  });
  it("rollback completion refreshes two pages and returns selection to live", async () => {
    await loadTwoPages();
    await act(async () => preview().onSelectVersion("version-old"));
    vi.mocked(rollback).mockResolvedValue(snapshot("head-restored"));
    await act(async () => preview().onRestoreSnapshot("snapshot-old"));
    await flush();
    expect(rollback).toHaveBeenCalledWith(project.id, "snapshot-old");
    expect(output("data-selected")).toBe("live");
    expect(output("data-current")).toBe("false");
    await act(async () => requests.at(-1)!.resolve(page("version-restored", 20)));
    await flush();
    expect(output("data-current")).toBe("false");
    await act(async () => requests.at(-1)!.resolve(page("version-old")));
    await flush();
    expect(output("data-current")).toBe("true");
    expect(output("data-versions")).toBe("version-restored,version-old");
    expect(counts()).toEqual({ starts: 2, aborts: 0 });
  });
  it("A to B to A ignores late pages and does not resurrect historical selection", async () => {
    await loadTwoPages();
    await act(async () => preview().onSelectVersion("version-old"));
    ignoreAbort = true;
    await emit({ type: "snapshot.created", data: { snapshot: snapshot("head-2") } });
    const lateA = requests.at(-1)!;
    const projectB = { ...project, id: "project-2", current_snapshot_id: "head-b" };
    client.setQueryData(["snapshots", projectB.id], [{ id: "head-b", project_id: projectB.id }]);
    await renderProject(projectB);
    const lateB = requests.at(-1)!;
    expect(lateB.projectId).toBe(projectB.id);
    expect(lateA.aborted).toBe(true);
    expect(output("data-project")).toBe(projectB.id);
    await act(async () => lateA.resolve(page("stale-project-a")));
    await flush();
    expect(output("data-versions")).not.toContain("stale-project-a");
    await renderProject(project);
    const currentA = requests.at(-1)!;
    expect(currentA.projectId).toBe(project.id);
    expect(output("data-selected")).toBe("live");
    expect(lateB.aborted).toBe(true);
    await act(async () => lateB.resolve(page("stale-project-b")));
    await flush();
    expect(output("data-project")).toBe(project.id);
    expect(output("data-versions")).not.toContain("stale-project-b");
    await act(async () => currentA.resolve(page("current-project-a")));
    await flush();
    expect(output("data-current")).toBe("true");
    expect(output("data-versions")).toBe("current-project-a");
  });
  it.each(["head-1", "head-2"])("unknown cached HEAD keeps active invalidation for %s", async (id) => {
    await act(async () => client.setQueryData(["snapshots", project.id], []));
    await flush();
    // The project prop supplies head-1 even though the snapshot cache is empty.
    expect(preview().currentSnapshotId).toBe("head-1");
    await emit({ type: "snapshot.created", data: { snapshot: snapshot(id) } });
    expect(counts()).toEqual(id === "head-1"
      ? { starts: 1, aborts: 0 } : { starts: 2, aborts: 1 });
    await act(async () => requests.at(-1)!.resolve(page("fresh-metadata")));
    await flush();
    expect(output("data-current")).toBe("true");
    expect(output("data-versions")).toBe("fresh-metadata");
  });
  it.each([false, true])("rollback to same rendered HEAD refreshes metadata, empty cache=%s", async (empty) => {
    if (empty) {
      await act(async () => client.setQueryData(["snapshots", project.id], []));
      await flush();
    }
    vi.mocked(rollback).mockResolvedValue(snapshot("head-1"));
    await act(async () => preview().onRestoreSnapshot("snapshot-old"));
    await flush();
    expect(counts()).toEqual({ starts: 1, aborts: 0 });
    await act(async () => requests.at(-1)!.resolve(page("fresh-rollback-metadata")));
    await flush();
    expect(output("data-versions")).toBe("fresh-rollback-metadata");
    expect(output("data-current")).toBe("true");
  });
  it("late earlier HEAD cannot replace the refreshed history in the same project", async () => {
    ignoreAbort = true;
    await act(async () => { void client.invalidateQueries({ queryKey: ["project-versions", project.id] }); });
    const earlier = requests.at(-1)!;
    await emit({ type: "snapshot.created", data: { snapshot: snapshot("head-2") } });
    expect(counts()).toEqual({ starts: 2, aborts: 1 });
    const current = requests.at(-1)!;
    expect(output("data-current")).toBe("false");
    await act(async () => current.resolve(page("current-head-history")));
    await flush();
    await act(async () => earlier.resolve(page("stale-head-history")));
    await flush();
    expect(output("data-versions")).toBe("current-head-history");
    expect(output("data-current")).toBe("true");
  });
  it("cold in-flight history stays guarded until the unchanged five-second poll catches up", async () => {
    await act(async () => root.unmount());
    client.removeQueries({ queryKey: ["project-versions", project.id] });
    root = createRoot(container);
    requests = [];
    await renderProject(project);
    const initial = requests.at(-1)!;
    expect(counts()).toEqual({ starts: 1, aborts: 0 });
    await emit({ type: "snapshot.created", data: { snapshot: snapshot("head-2") } });
    // Query deduplicates initial fetches without cached data; it must not authorize old HEAD.
    expect(counts()).toEqual({ starts: 1, aborts: 0 });
    await act(async () => initial.resolve(page("initial-old-head")));
    await flush();
    expect(output("data-current")).toBe("false");
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(counts()).toEqual({ starts: 2, aborts: 0 });
    await act(async () => requests.at(-1)!.resolve(page("polled-new-head")));
    await flush();
    expect(output("data-current")).toBe("true");
    expect(output("data-versions")).toBe("polled-new-head");
  });
});
