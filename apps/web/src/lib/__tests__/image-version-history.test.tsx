import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePromptStream } from "@/hooks/usePromptStream";
import { PreviewFrame } from "@/components/workspace/PreviewFrame";
import { useWorkspaceStore } from "@/store/workspace";
import { MaxLivePreview } from "@/components/max/MaxLivePreview";
import { MaxWorkspaceShell } from "@/components/max/MaxWorkspaceShell";
import type { Project, ProjectVersion } from "@/lib/api/types";

const api = vi.hoisted(() => ({ runtime: vi.fn(), start: vi.fn(), sync: vi.fn(), session: vi.fn(), versions: vi.fn(), snapshots: vi.fn(), rollback: vi.fn(), send: vi.fn() }));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
vi.mock("@/lib/api/runtime", () => ({ getRuntime: api.runtime, startRuntime: api.start }));
vi.mock("@/lib/api/max-studio", () => ({ syncMaxManagedKit: api.sync, createMaxPreviewSession: api.session, getMaxReadiness: async () => ({ items: [] }) }));
vi.mock("@/lib/api/snapshots", () => ({ listSnapshots: api.snapshots, listProjectVersions: api.versions, rollback: api.rollback }));
vi.mock("@/lib/api/projects", () => ({ listProjects: async () => [], getProject: async () => ({ ...project, template: "fullstack" }) }));
vi.mock("@/lib/api/messages", () => ({ listMessages: async () => [], reportClientError: vi.fn(), getLatestGeneration: async () => ({ id: "run", status: "running", assistant_message_id: "a" }), sendPrompt: api.send, cancelGeneration: vi.fn() }));
vi.mock("@/components/workspace/HeroMediaPanel", () => ({ HeroMediaPanel: () => null }));
vi.mock("@/components/workspace/StylePanel", () => ({ StylePanel: () => null }));
vi.mock("@/components/workspace/ChatPanel", () => ({ ChatPanel: () => null }));
vi.mock("@/components/workspace/DownloadButton", () => ({ DownloadButton: () => null }));
vi.mock("@/components/max/MaxLaunchPanel", () => ({ MaxLaunchPanel: () => null }));
vi.mock("@/components/max/MaxAccountMenu", () => ({ MaxAccountMenu: () => null }));
vi.mock("@/components/max/MaxProjectNav", () => ({ MaxProjectNav: () => null }));
vi.mock("@/components/max/MaxUsageBreakdown", () => ({ MaxUsageBreakdown: () => null }));
vi.mock("@/components/marketing/BrandMark", () => ({ BrandMark: () => null }));

const project = { id: "p", name: "App", slug: "app", template: "max_miniapp", current_snapshot_id: "s32" } as Project;
const version = (number: number, extra: Partial<ProjectVersion> = {}): ProjectVersion => ({ id: `v${number}`, number, project_id: "p", snapshot_id: `s${number}`, commit_sha: `sha${number}`, prompt_text: "Добавил каталог", model_id: null, created_at: "2026-09-06T10:00:00Z", status: "ready", preview_status: "ready", previews: [{ url: `/images/v${number}.png`, width: 390, height: 2400, route: "/" }], is_current: number === 32, can_restore: true, ...extra });
let root: Root, container: HTMLDivElement, client: QueryClient;
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 25)); }); }
// Flush React/Query notifications on each poll; finish as soon as the actual
// DOM condition holds rather than assuming one short timer drains the request chain.
async function waitForDom(assertion: () => void) {
  const deadline = Date.now() + 1_000;
  for (;;) {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
    try { assertion(); return; } catch (error) {
      if (Date.now() >= deadline) throw error;
    }
  }
}
function click(selector: string) { const button = container.querySelector<HTMLButtonElement>(selector); expect(button).not.toBeNull(); act(() => button!.click()); }
function image() { return container.querySelector<HTMLImageElement>("[data-testid='history-image']"); }
function Preview({ versions, selected = "v31", head = "s32" }: { versions: ProjectVersion[]; selected?: string | null; head?: string }) {
  const [id, setId] = useState<string | null>(selected);
  return <MaxLivePreview project={project} versions={versions} snapshotsLoading={false} currentSnapshotId={head} selectedVersionId={id} onSelectVersion={setId} onRestoreSnapshot={api.rollback} restoringSnapshot={false} />;
}
function render(node: React.ReactNode) { act(() => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)); }

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  api.runtime.mockResolvedValue({ state: "running", container_name: "head", dev_url: "https://live.example" });
  api.start.mockResolvedValue({ state: "running" });
  api.sync.mockResolvedValue({ synced_snapshot_id: "s32" });
  api.session.mockResolvedValue({ url: "https://live.example", expires_at: "2099-01-01" });
  api.snapshots.mockResolvedValue([{ id: "s32" }]);
  api.versions.mockResolvedValue({ versions: [version(32), version(31)], next_cursor: null });
  useWorkspaceStore.setState({ selectedSnapshotId: null, viewMode: "preview" });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); client.clear(); container.remove(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

describe("image version history", () => {
  it("shows a full-height image with no runtime/session/restore calls, even for current version", async () => {
    render(<Preview versions={[version(32), version(31)]} selected="v32" />); await settle();
    expect(image()?.getAttribute("src")).toBe("/images/v32.png");
    expect(image()?.style.height).toBe("auto");
    expect(container.querySelector("[data-testid='history-image-scroll']")?.getAttribute("style")).toContain("overflow-y: auto");
    expect(container.querySelector("iframe")).toBeNull();
    for (const fn of [api.runtime, api.start, api.sync, api.session, api.rollback]) expect(fn).not.toHaveBeenCalled();
  });
  it("switches quickly using arrows, keyboard and horizontal swipe while preserving vertical scrolling", async () => {
    render(<Preview versions={[version(32), version(31), version(30)]} />);
    click("[aria-label='Предыдущая версия']"); expect(image()?.getAttribute("src")).toBe("/images/v30.png");
    const region = () => container.querySelector<HTMLElement>("[data-testid='history-image-viewer']")!;
    act(() => region().dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })));
    expect(image()?.getAttribute("src")).toBe("/images/v31.png");
    function touch(type: string, x: number, y: number) { const e = new Event(type, { bubbles: true }); Object.defineProperty(e, type === "touchstart" ? "touches" : "changedTouches", { value: [{ clientX: x, clientY: y }] }); act(() => region().dispatchEvent(e)); }
    touch("touchstart", 100, 100); touch("touchend", 110, 300); expect(image()?.getAttribute("src")).toBe("/images/v31.png");
    touch("touchstart", 200, 100); touch("touchend", 40, 105); expect(image()?.getAttribute("src")).toBe("/images/v32.png");
    await settle(); expect(api.start).not.toHaveBeenCalled();
  });
  it("shows missing, pending and broken images honestly and ignores late old image events", () => {
    const versions = [version(32), version(31), version(30, { previews: [], preview_status: "failed", status: "failed", can_restore: false })];
    render(<Preview versions={versions} />); const old = image()!;
    click("[data-testid='max-version-32']"); act(() => old.dispatchEvent(new Event("error")));
    expect(image()?.getAttribute("src")).toBe("/images/v32.png");
    act(() => image()!.dispatchEvent(new Event("error"))); expect(container.textContent).toContain("Не удалось загрузить изображение");
    click("[data-testid='max-version-30']"); expect(container.textContent).toContain("Изображение не сохранилось");
    expect(container.querySelector("iframe")).toBeNull(); expect(container.querySelector<HTMLButtonElement>("[data-testid='max-restore-version']")?.disabled).toBe(true);
  });
  it("loads older permanent version numbers beyond 30 and preserves selection when HEAD/technical snapshots change", async () => {
    const first = Array.from({ length: 30 }, (_, i) => version(32 - i));
    api.versions.mockImplementation(async (_id: string, before?: number) => before ? { versions: [version(2), version(1)], next_cursor: null } : { versions: first, next_cursor: 3 });
    render(<MaxWorkspaceShell project={project} email="a@example.com" />); await settle(); await settle();
    click("[data-testid='max-version-31']"); expect(image()?.getAttribute("src")).toBe("/images/v31.png");
    click("[data-testid='max-history-load-older']"); await settle();
    expect(container.querySelector("[data-testid='max-version-1']")).not.toBeNull();
    const counts = [api.runtime, api.start, api.sync, api.session].map((fn) => fn.mock.calls.length);
    act(() => { client.setQueryData(["snapshots", "p"], [{ id: "technical", preview_url: null }]); });
    await settle(); expect(image()?.getAttribute("src")).toBe("/images/v31.png");
    expect([api.runtime, api.start, api.sync, api.session].map((fn) => fn.mock.calls.length)).toEqual(counts);
    expect(container.querySelector("[data-testid='max-version-32']")?.textContent).toContain("v32");
    api.session.mockImplementationOnce(async () => {
      await new Promise((resolve) => setTimeout(resolve, 80));
      return { url: "https://live.example", expires_at: "2099-01-01" };
    });
    click("[data-testid='max-return-current-version']");
    await waitForDom(() => expect(container.querySelector("iframe")).not.toBeNull());
  });
  it("keeps queued, failed and unchanged versions with their own number", () => {
    render(<Preview versions={[version(34, { status: "queued", previews: [], preview_status: "pending" }), version(33, { status: "failed", previews: [], preview_status: "missing" }), version(32, { status: "unchanged", snapshot_id: "s31" }), version(31)]} selected="v34" />);
    expect(container.textContent).toContain("В очереди"); expect(container.textContent).toContain("Ошибка"); expect(container.textContent).toContain("Без изменений");
    click("[data-testid='max-version-32']"); expect(image()?.getAttribute("src")).toBe("/images/v32.png");
  });
  it("generic fullstack history never starts or shows HEAD, including pending and missing IDs", async () => {
    api.snapshots.mockImplementation(() => new Promise(() => {}));
    useWorkspaceStore.setState({ selectedSnapshotId: "s31" });
    render(<PreviewFrame project={{ ...project, template: "fullstack" }} />); await settle();
    expect(api.runtime).not.toHaveBeenCalled(); expect(api.start).not.toHaveBeenCalled();
    expect(container.querySelector("iframe")).toBeNull();
    act(() => client.setQueryData(["snapshots", "p"], [{ id: "s32", commit_sha: "new", prompt_text: "New", preview_url: "/current.png" }])); await settle();
    expect(container.querySelector("iframe")).toBeNull(); expect(container.textContent).toContain("нет изображения");
    act(() => client.setQueryData(["snapshots", "p"], [{ id: "s32", commit_sha: "new" }, { id: "s31", commit_sha: "old", created_at: "2026-09-06T10:00:00Z", preview_url: "/old.png" }])); await settle();
    expect(image()?.getAttribute("src")).toBe("/old.png");
    act(() => useWorkspaceStore.getState().selectSnapshot(null));
    await waitForDom(() => expect(container.querySelector("iframe")?.getAttribute("src")).toContain("live.example"));
  });

  it("does not clear generic historical selection on a real snapshot.created stream event", async () => {
    let socket: { onmessage?: (event: { data: string }) => void } | undefined;
    vi.stubGlobal("WebSocket", class { static OPEN = 1; static CONNECTING = 0; readyState = 1; onmessage?: (event: { data: string }) => void; onclose = null; constructor() { socket = this; } send() {} close() {} });
    client.setQueryData(["messages", "p"], [{ id: "a", role: "assistant", tokens_out: null }]);
    useWorkspaceStore.setState({ selectedSnapshotId: "s31" });
    function Stream() { usePromptStream("p", "app"); return null; }
    render(<Stream />); await settle();
    expect(socket?.onmessage).toBeTypeOf("function");
    act(() => socket!.onmessage!({ data: JSON.stringify({ type: "snapshot.created", data: { snapshot: { id: "new-head" } } }) }));
    expect(useWorkspaceStore.getState().selectedSnapshotId).toBe("s31");
    expect(client.getQueryData<Array<{ id: string }>>(["snapshots", "p"])?.[0].id).toBe("new-head");
  });

  it("prefetches neighboring immutable images without calling runtime endpoints", () => {
    const preloads: HTMLImageElement[] = [];
    vi.stubGlobal("Image", function ImageMock() { const img = document.createElement("img"); preloads.push(img); return img; });
    render(<Preview versions={[version(32), version(31), version(30)]} />);
    expect(preloads.map((img) => img.getAttribute("src"))).toEqual(["/images/v30.png", "/images/v32.png"]);
    expect(api.session).not.toHaveBeenCalled(); expect(api.start).not.toHaveBeenCalled();
  });
  it("restores the result snapshot only after explicit confirmation", async () => {
    api.rollback.mockResolvedValue(undefined);
    render(<Preview versions={[version(32), version(31)]} />);
    click("[data-testid='max-restore-version']"); expect(api.rollback).not.toHaveBeenCalled();
    const confirm = [...document.querySelectorAll<HTMLButtonElement>("[role='dialog'] button")].find((button) => button.textContent === "Вернуться к версии");
    expect(confirm).toBeDefined(); await act(async () => confirm!.click());
    expect(api.rollback).toHaveBeenCalledWith("s31");
  });
  it("clears history and cached live frame on project change without resurrecting selection on return", async () => {
    render(<MaxWorkspaceShell project={project} email="a@example.com" />); await settle(); await settle();
    click("[data-testid='max-version-31']"); expect(image()).not.toBeNull();
    render(<MaxWorkspaceShell project={{ ...project, id: "other" }} email="a@example.com" />); expect(image()).toBeNull();
    render(<MaxWorkspaceShell project={project} email="a@example.com" />); await settle();
    expect(image()).toBeNull();
    await waitForDom(() => expect(container.querySelector("iframe")).not.toBeNull());
  });
  it("keeps missing version selection isolated, with an explicit return to live preview", async () => {
    render(<Preview versions={[version(32)]} selected="unknown" />); await settle();
    expect(container.textContent).toContain("нет изображения"); expect(container.querySelector("iframe")).toBeNull();
    expect(container.querySelector<HTMLButtonElement>("[aria-label='Предыдущая версия']")?.disabled).toBe(true);
    expect(api.runtime).not.toHaveBeenCalled();
    click("[data-testid='max-return-current-version']");
    await waitForDom(() => expect(container.querySelector("iframe")).not.toBeNull());
  });

  it("stops scheduled live connection retries after selecting image history", async () => {
    api.sync.mockRejectedValue(new Error("temporarily unavailable"));
    render(<Preview versions={[version(32), version(31)]} selected={null} />); await settle(); await settle();
    expect(api.sync).toHaveBeenCalledTimes(1);
    click("[data-testid='max-version-31']");
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 1700)); });
    expect(api.sync).toHaveBeenCalledTimes(1); expect(api.session).not.toHaveBeenCalled();
    expect(image()?.getAttribute("src")).toBe("/images/v31.png");
  });

  it("shows an accepted queued version immediately without waiting for a snapshot or polling", async () => {
    vi.stubGlobal("WebSocket", class { static OPEN = 1; static CONNECTING = 0; readyState = 1; onclose = null; send() {} close() {} });
    api.send.mockImplementation(async () => {
      api.versions.mockResolvedValue({ versions: [version(33, { status: "queued", preview_status: "pending", previews: [], snapshot_id: null }), version(32)], next_cursor: null });
      return { message_id: "a", run_id: "run", run_status: "queued_for_capacity", mode: "build" };
    });
    function Submit() { const { submit } = usePromptStream("p", "app"); return <button data-testid="submit" onClick={() => void submit("Добавь каталог", "model")}>Отправить</button>; }
    render(<><MaxWorkspaceShell project={project} email="a@example.com" /><Submit /></>); await settle(); await settle();
    click("[data-testid='submit']"); await settle(); await settle();
    expect(container.querySelector("[data-testid='max-version-33']")?.textContent).toContain("В очереди");
  });

  it("distinguishes captured viewports on the same route and switches the image", () => {
    render(<Preview versions={[version(31, { previews: [{ url: "/mobile.png", width: 390, height: 2400, route: "/" }, { url: "/desktop.png", width: 1280, height: 900, route: "/" }] })]} />);
    const select = container.querySelector<HTMLSelectElement>("[aria-label='Экран версии']")!;
    expect([...select.options].map((option) => option.textContent)).toEqual(["/ · 390px", "/ · 1280px"]);
    act(() => { select.value = "1"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(image()?.getAttribute("src")).toBe("/desktop.png");
  });

  it("retries the failed older page instead of refetching only the already loaded page", async () => {
    let olderAttempts = 0;
    api.versions.mockImplementation(async (_id: string, before?: number) => {
      if (!before) return { versions: Array.from({ length: 30 }, (_, i) => version(32 - i)), next_cursor: 3 };
      if (++olderAttempts === 1) throw new Error("offline");
      return { versions: [version(2), version(1)], next_cursor: null };
    });
    render(<MaxWorkspaceShell project={project} email="a@example.com" />); await settle(); await settle();
    click("[data-testid='max-history-load-older']"); await settle();
    expect(container.querySelector("[data-testid='max-history-load-older']")?.textContent).toBe("Повторить");
    click("[data-testid='max-history-load-older']"); await settle();
    expect(container.querySelector("[data-testid='max-version-1']")).not.toBeNull();
  });
  it.each(["llm.error", "generation.cancelled"])("refreshes the queued version immediately on %s", async (type) => {
    let socket: { onmessage?: (event: { data: string }) => void } | undefined;
    vi.stubGlobal("WebSocket", class { static OPEN = 1; static CONNECTING = 0; readyState = 1; onmessage?: (event: { data: string }) => void; onclose = null; constructor() { socket = this; } send() {} close() {} });
    client.setQueryData(["messages", "p"], [{ id: "a", role: "assistant", tokens_out: null }]);
    api.versions.mockResolvedValue({ versions: [version(33, { status: "queued", previews: [], preview_status: "pending" })], next_cursor: null });
    function Stream() { usePromptStream("p", "app"); return null; }
    render(<><MaxWorkspaceShell project={project} email="a@example.com" /><Stream /></>); await settle(); await settle();
    expect(container.querySelector("[data-testid='max-version-33']")?.textContent).toContain("В очереди");
    api.versions.mockResolvedValue({ versions: [version(33, { status: type === "llm.error" ? "failed" : "cancelled", previews: [], preview_status: "missing" })], next_cursor: null });
    act(() => socket!.onmessage!({ data: JSON.stringify({ type, data: { message_id: "a", error: "failure" } }) })); await settle(); await settle();
    expect(container.querySelector("[data-testid='max-version-33']")?.textContent).toContain(type === "llm.error" ? "Ошибка" : "Отменена");
  });
  it("resolves API-relative authenticated images against the configured API origin", () => {
    render(<Preview versions={[version(31, { previews: [{ url: "/api/projects/p/snapshots/s31/previews/0", width: 390, height: 1000, route: "/" }] })]} />);
    expect(image()?.getAttribute("src")).toBe("http://localhost:8000/api/projects/p/snapshots/s31/previews/0");
    expect(container.querySelector<HTMLImageElement>("[data-testid='max-version-31'] img")?.getAttribute("src")).toBe("http://localhost:8000/api/projects/p/snapshots/s31/previews/0");
  });
  it("labels reconstructed capture with preview data and clears that label on original capture", () => {
    render(<Preview versions={[version(32), version(31, { previews: [{ url: "/rebuilt.png", width: 390, height: 1200, route: "/", reconstructed: true }] })]} />);
    expect(container.textContent).toContain("Восстановлено из кода · данные для предпросмотра");
    click("[data-testid='max-version-32']"); expect(container.textContent).not.toContain("Восстановлено из кода · данные для предпросмотра");
  });

});
