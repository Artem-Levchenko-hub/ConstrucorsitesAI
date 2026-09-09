import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PreviewFrame } from "@/components/workspace/PreviewFrame";
import { useWorkspaceStore } from "@/store/workspace";
import { useInspectorStore } from "@/store/inspector";
import { useStyleEditStore } from "@/store/styleEdit";
import type { Project, RuntimeStatus, Snapshot } from "@/lib/api/types";

// Network boundaries only: the real PreviewFrame, Query observers, stores,
// startup panel, historical image viewer and iframe/load handlers run together.
const api = vi.hoisted(() => ({ runtime: vi.fn(), start: vi.fn() }));
vi.mock("@/lib/api/runtime", () => ({ getRuntime: api.runtime, startRuntime: api.start }));
vi.mock("@/lib/api/snapshots", () => ({ listSnapshots: async () => snapshots }));
vi.mock("@/lib/api/projects", () => ({ getProject: async () => project }));
vi.mock("@/lib/api/messages", () => ({ listMessages: async () => [], reportClientError: vi.fn() }));
// Exit animations use a browser frame clock independent of these polling
// timers. Keep motion elements/ref/load events real; omit only exit retention.
vi.mock("framer-motion", async (importOriginal) => ({
  ...await importOriginal<typeof import("framer-motion")>(),
  AnimatePresence: ({ children }: { children: ReactNode }) => children,
}));

const project: Project = {
  id: "preview-project", owner_id: "owner", name: "Preview", slug: "preview",
  template: "fullstack", current_snapshot_id: "head",
  created_at: "2026-09-09T10:00:00Z", updated_at: "2026-09-09T10:00:00Z",
};
const snapshots: Snapshot[] = ["head", "old"].map((id) => ({
  id, project_id: project.id, commit_sha: id === "head" ? "abcdef12" : "12345678",
  prompt_text: "Build app", model_id: null, parent_id: null,
  preview_url: `https://images.example/${id}.png`, is_rollback_target: false,
  created_at: "2026-09-09T10:00:00Z",
}));
function runtime(state: RuntimeStatus["state"]): RuntimeStatus {
  return { state, container_name: "preview", port: 3000,
    dev_url: state === "running" ? "https://live.example" : null,
    last_active_at: null, hibernate_after_seconds: 600, keep_alive: false };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
let root: Root, container: HTMLDivElement, client: QueryClient;
let mounted: boolean;
async function tick(ms = 1) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}
async function settle() { for (let i = 0; i < 6; i++) await tick(); }
function render(value = project) {
  // Seed unrelated API reads, while runtime is always exercised as a request.
  client.setQueryData(["project", value.id], value);
  client.setQueryData(["snapshots", value.id], snapshots);
  client.setQueryData(["messages", value.id], []);
  act(() => root.render(<QueryClientProvider client={client}><PreviewFrame project={value} /></QueryClientProvider>));
}
function unmount() { act(() => root.unmount()); mounted = false; }
function frame() { return container.querySelector<HTMLIFrameElement>("iframe"); }
function startButton() {
  return [...container.querySelectorAll("button")].find((button) => /^(Повторить|Запустить приложение)$/.test(button.textContent ?? ""));
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  useWorkspaceStore.setState({ selectedSnapshotId: null, viewMode: "preview" });
  useInspectorStore.setState({ inspectMode: false, selections: [] });
  useStyleEditStore.setState({ styleMode: false, selected: null });
  api.runtime.mockResolvedValue(runtime("running"));
  api.start.mockResolvedValue(runtime("provisioning"));
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: Infinity } } });
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container); mounted = true;
});
afterEach(() => {
  if (mounted) unmount();
  client.clear(); container.remove();
  vi.useRealTimers(); vi.unstubAllGlobals(); vi.resetAllMocks();
});

describe("PreviewFrame runtime lifecycle", () => {
  it("waits for the initial read before auto-starting, then publishes the start result into the shared cache", async () => {
    const read = deferred<RuntimeStatus>(), start = deferred<RuntimeStatus>();
    api.runtime.mockReturnValue(read.promise); api.start.mockReturnValue(start.promise);
    render(); await settle();
    expect(api.runtime).toHaveBeenCalledExactlyOnceWith(project.id);
    expect(api.start).not.toHaveBeenCalled();
    read.resolve(runtime("stopped")); await settle();
    expect(api.start).toHaveBeenCalledExactlyOnceWith(project.id);
    expect(container.textContent).toContain("Приложение запускается…");
    expect(frame()).toBeNull();
    start.resolve(runtime("running")); await settle(); await tick(300);
    expect(client.getQueryData(["runtime", project.id])).toEqual(runtime("running"));
    expect(frame()?.src).toBe("https://live.example/?inspect=1#k=0&omniaApi=http%3A%2F%2Flocalhost%3A3000");
    expect(container.querySelector('[aria-label="Превью загружается"]')).not.toBeNull();
    act(() => frame()!.dispatchEvent(new Event("load"))); await tick(300);
    expect(container.querySelector('[aria-label="Превью загружается"]')).toBeNull();
    await tick(6_000);
    expect(api.runtime).toHaveBeenCalledTimes(1);
    expect(api.start).toHaveBeenCalledTimes(1);
  });

  it("polls provisioning every two seconds and stops after the running response", async () => {
    const reads: number[] = [];
    api.runtime.mockImplementation(() => { reads.push(Date.now()); return Promise.resolve(runtime(reads.length < 3 ? "provisioning" : "running")); });
    render(); await settle();
    expect(reads).toHaveLength(1); expect(api.start).not.toHaveBeenCalled();
    await tick(1_990); expect(reads).toHaveLength(1);
    await tick(10); await settle(); expect(reads).toHaveLength(2);
    expect(reads[1] - reads[0]).toBe(2_000);
    await tick(2_000); await settle(); await tick(300);
    expect(reads).toHaveLength(3); expect(reads[2] - reads[1]).toBe(2_000);
    expect(frame()?.title).toBe("Preview (live dev container)");
    await tick(6_000); expect(reads).toHaveLength(3);
  });

  it("offers a manual retry after an initial read and auto-start failure without repeating the automatic mutation", async () => {
    api.runtime.mockRejectedValue(new Error("offline"));
    api.start.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(runtime("running"));
    render(); await settle();
    expect(api.start).toHaveBeenCalledExactlyOnceWith(project.id);
    expect(startButton()).toBeDefined();
    await tick(4_000); await settle();
    expect(api.runtime).toHaveBeenCalledTimes(3);
    expect(api.start).toHaveBeenCalledTimes(1);
    act(() => startButton()!.click()); await settle(); await tick(300);
    expect(api.start).toHaveBeenCalledTimes(2);
    expect(client.getQueryData(["runtime", project.id])).toEqual(runtime("running"));
    expect(frame()?.title).toBe("Preview (live dev container)");
  });

  it("does not poll a failed runtime again after its one automatic start fails", async () => {
    api.runtime.mockResolvedValue(runtime("failed")); api.start.mockRejectedValue(new Error("offline"));
    render(); await settle(); await tick(6_000);
    expect(api.runtime).toHaveBeenCalledTimes(1); expect(api.start).toHaveBeenCalledTimes(1);
    expect(startButton()?.textContent).toBe("Повторить");
  });

  it("keeps historical selection image-only and resumes the runtime observer on return to HEAD", async () => {
    useWorkspaceStore.setState({ selectedSnapshotId: "old" });
    api.runtime.mockResolvedValue(runtime("provisioning"));
    render(); await settle(); await tick(4_000);
    expect(api.runtime).not.toHaveBeenCalled(); expect(api.start).not.toHaveBeenCalled();
    expect(frame()).toBeNull();
    expect(container.querySelector('img[src="https://images.example/old.png"]')).not.toBeNull();
    act(() => useWorkspaceStore.getState().selectSnapshot(null)); await settle();
    expect(api.runtime).toHaveBeenCalledTimes(1);
    act(() => useWorkspaceStore.getState().selectSnapshot("old")); await settle(); await tick(4_000);
    expect(api.runtime).toHaveBeenCalledTimes(1); expect(api.start).not.toHaveBeenCalled();
  });

  it("enables runtime after the shared project cache changes a static template to fullstack", async () => {
    render({ ...project, template: "landing" }); await settle();
    expect(api.runtime).not.toHaveBeenCalled(); expect(api.start).not.toHaveBeenCalled();
    expect(frame()?.src).toContain("/p/preview?inspect=1");
    act(() => client.setQueryData(["project", project.id], project)); await settle(); await tick(300);
    expect(api.runtime).toHaveBeenCalledExactlyOnceWith(project.id);
    expect(frame()?.title).toBe("Preview (live dev container)");
  });

  it("scopes reads to the changed project and ignores a late response from the previous project in the visible iframe", async () => {
    const oldRead = deferred<RuntimeStatus>();
    api.runtime.mockImplementation((id: string) => id === project.id ? oldRead.promise : Promise.resolve({ ...runtime("running"), dev_url: "https://second.example" }));
    render(); await settle();
    render({ ...project, id: "second" }); await settle(); await tick(300);
    oldRead.resolve(runtime("stopped")); await settle();
    expect(api.runtime.mock.calls).toEqual([[project.id], ["second"]]);
    expect(api.start).not.toHaveBeenCalled();
    expect(frame()?.src).toContain("https://second.example/");
  });

  it("removes the polling observer on unmount and does not auto-start from a late initial read", async () => {
    api.runtime.mockResolvedValue(runtime("provisioning"));
    render(); await settle(); unmount(); await tick(6_000);
    expect(api.runtime).toHaveBeenCalledTimes(1); expect(api.start).not.toHaveBeenCalled();
    root = createRoot(container); mounted = true; client.removeQueries({ queryKey: ["runtime", project.id] });
    const read = deferred<RuntimeStatus>(); api.runtime.mockReturnValue(read.promise);
    render(); await settle(); unmount(); read.resolve(runtime("stopped")); await settle(); await tick(4_000);
    expect(api.runtime).toHaveBeenCalledTimes(2); expect(api.start).not.toHaveBeenCalled();
  });
});
