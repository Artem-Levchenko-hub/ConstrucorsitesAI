import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePromptStream } from "@/hooks/usePromptStream";
import { PreviewFrame } from "@/components/workspace/PreviewFrame";
import { VersionImagePreview } from "@/components/workspace/VersionImagePreview";
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
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

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
  it("keeps the visual rail compact and uses each version's own mobile image", () => {
    render(<Preview versions={[version(32), version(31, { previews: [
      { url: "/desktop.png", width: 1280, height: 900, route: "/" },
      { url: "/mobile.png", width: 390, height: 1200, route: "/" },
    ] })]} />);
    const entry = container.querySelector("[data-testid='max-version-31']")!;
    expect(entry.textContent?.trim()).toBe("v31");
    expect(entry.querySelector("img")?.getAttribute("src")).toBe("/mobile.png");
    expect(entry.querySelector("time")).toBeNull();
    expect(entry.getAttribute("aria-label")).toContain("Версия 31");
  });
  it("opens failed attempts separately without replacing the selected version image", () => {
    render(<Preview versions={[version(32), version(31), version(30, { status: "failed", previews: [], preview_status: "missing" })]} />);
    expect(container.querySelector("[data-testid='max-version-30']")).toBeNull();
    click("[data-testid='max-history-activity-open']");
    const row = document.querySelector("[data-testid='max-history-event-30']")!;
    expect(row.textContent).toContain("Не завершилась");
    expect(row.querySelector("[data-testid='max-version-30']")).toBeNull();
    expect(image()?.getAttribute("src")).toBe("/images/v31.png");
    for (const fn of [api.runtime, api.start, api.sync, api.session, api.rollback]) expect(fn).not.toHaveBeenCalled();
  });
  it.each(["pending", "missing", "failed"] as const)("separates ready versions with %s captures without inventing an image", (preview_status) => {
    render(<Preview versions={[version(32), version(31, { previews: [], preview_status })]} selected="v31" />);
    expect(container.querySelector("[data-testid='max-version-31']")).toBeNull();
    expect(container.querySelector("[data-testid='max-live-device']")).toBeNull();
    expect(container.querySelector("iframe")).toBeNull();
    expect(image()).toBeNull();
    expect(container.querySelector("[data-testid='max-history-unavailable']")).not.toBeNull();
    expect(container.textContent).not.toContain("Для этой версии нет изображения");
    if (preview_status === "pending") expect(container.textContent).toContain("Снимок готовится");
    else expect(container.textContent).not.toContain("Снимок готовится");
  });
  it("skips failed, cancelled and image-less entries when navigating historical images", () => {
    render(<Preview versions={[version(32), version(31), version(30, { status: "failed", previews: [] }), version(29, { status: "cancelled", previews: [] }), version(28, { previews: [], preview_status: "pending" }), version(27)]} />);
    act(() => container.querySelector("[data-testid='history-image-viewer']")!.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true })));
    expect(image()?.getAttribute("src")).toBe("/images/v27.png");
    expect(api.runtime).not.toHaveBeenCalled();
  });
  it("moves a failed thumbnail out of the rail and offers a retry without a runtime mutation", () => {
    render(<Preview versions={[version(32), version(31), version(30)]} />);
    const thumbnail = container.querySelector("[data-testid='max-version-30'] img")!;
    act(() => thumbnail.dispatchEvent(new Event("error")));
    expect(container.querySelector("[data-testid='max-version-30']")).toBeNull();
    expect(image()?.getAttribute("src")).toBe("/images/v31.png");
    click("[data-testid='max-history-activity-open']");
    const row = document.querySelector("[data-testid='max-history-event-30']")!;
    expect(row.textContent).toContain("Не удалось загрузить снимок");
    act(() => row.querySelector<HTMLButtonElement>("button[data-retry-image]")!.click());
    expect(container.querySelector("[data-testid='max-version-30'] img")?.getAttribute("src")).toBe("/images/v30.png");
    expect(api.start).not.toHaveBeenCalled(); expect(api.sync).not.toHaveBeenCalled();
  });
  it("makes a completed capture selectable when metadata arrives, without renumbering", () => {
    render(<Preview versions={[version(32), version(31, { previews: [], preview_status: "pending" })]} selected="v31" />);
    expect(image()).toBeNull();
    render(<Preview versions={[version(32), version(31)]} selected="v31" />);
    expect(image()?.getAttribute("src")).toBe("/images/v31.png");
    expect(container.querySelector("[data-testid='max-version-31']")).not.toBeNull();
    expect(container.querySelector("[data-testid='max-history-unavailable']")).toBeNull();
  });
  it("returns focus to the rail when the final pending event becomes a ready capture while its dialog is open", async () => {
    render(<Preview versions={[version(32), version(31, { previews: [], preview_status: "pending" })]} selected="v32" />);
    click("[data-testid='max-history-activity-open']");
    render(<Preview versions={[version(32), version(31)]} selected="v32" />);
    const close = document.querySelector<HTMLButtonElement>("[data-testid='max-history-activity'] button[aria-label='Закрыть']")
      ?? [...document.querySelectorAll<HTMLButtonElement>("[data-testid='max-history-activity'] button")].find((button) => button.textContent === "Закрыть");
    expect(close).toBeDefined();
    act(() => close!.click());
    await settle();
    expect(document.activeElement).not.toBe(document.body);
    expect(container.querySelector("[data-testid='max-version-rail']")?.contains(document.activeElement)).toBe(true);
  });
  it("mounts only one preview on mobile and closes it with Escape", async () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    render(<MaxWorkspaceShell project={project} email="a@example.com" />);
    await settle();
    expect(document.querySelectorAll("[data-testid='max-live-preview']")).toHaveLength(0);
    click("[data-testid='max-mobile-preview-open']");
    await settle();
    expect(document.querySelectorAll("[data-testid='max-live-preview']")).toHaveLength(1);
    act(() => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    await settle();
    expect(document.querySelectorAll("[data-testid='max-live-preview']")).toHaveLength(0);
    expect(document.activeElement).toBe(container.querySelector("[data-testid='max-mobile-preview-open']"));
  });
  it("does not distract a new project with an empty version rail", async () => {
    render(<Preview versions={[]} selected={null} head="" />);
    await settle();
    expect(container.querySelector("[data-testid='max-version-rail']")).toBeNull();
  });
  it("keeps a failed history request visible and retryable even without versions", async () => {
    const retry = vi.fn();
    render(<MaxLivePreview project={project} versions={[]} historyError snapshotsLoading={false}
      currentSnapshotId="s32" selectedVersionId={null} onSelectVersion={vi.fn()}
      onRestoreSnapshot={api.rollback} restoringSnapshot={false} onLoadOlder={retry} />);
    await settle();
    click("[data-testid='max-history-load-older']");
    expect(retry).toHaveBeenCalledOnce();
  });
  it("has one launch entry in the editor header and keeps project navigation behind a button", async () => {
    render(<MaxWorkspaceShell project={project} email="a@example.com" />);
    await settle();
    const launch = container.querySelector<HTMLButtonElement>("header [data-testid='max-launch-open']");
    expect(launch?.textContent).toContain("Опубликовать");
    expect(container.querySelector("[data-testid='max-next-action-bar']")).toBeNull();
    expect(container.querySelector("[data-testid='max-navigation-scroll']")).toBeNull();
    expect(container.querySelector("header a[href='/max/p/settings?tab=app']")).not.toBeNull();
    click("[data-testid='max-navigation-open']");
    await settle();
    expect(document.querySelector("[role='dialog'] [data-testid='max-navigation-scroll']")).not.toBeNull();
  });
  it("shows a full-height historical image with no runtime/session/restore calls", async () => {
    render(<Preview versions={[version(32), version(31)]} selected="v31" />); await settle();
    expect(image()?.getAttribute("src")).toBe("/images/v31.png");
    expect(image()?.style.height).toBe("auto");
    expect(container.querySelector("[data-testid='history-image-scroll']")?.getAttribute("style")).toContain("overflow-y: auto");
    expect(container.querySelector("iframe")).toBeNull();
    for (const fn of [api.runtime, api.start, api.sync, api.session, api.rollback]) expect(fn).not.toHaveBeenCalled();
  });
  it("opens the applied current version as a live iframe, including its rail entry", async () => {
    render(<Preview versions={[version(32), version(31)]} selected="v32" />);
    await waitForDom(() => expect(container.querySelector("iframe")?.getAttribute("src")).toBe("https://live.example"));
    expect(image()).toBeNull();
    expect(api.session).toHaveBeenCalledWith("p");
    click("[data-testid='max-version-31']");
    expect(image()?.getAttribute("src")).toBe("/images/v31.png");
    expect(container.querySelector("iframe")).toBeNull();
    click("[data-testid='max-version-32']");
    await waitForDom(() => expect(container.querySelector("iframe")?.getAttribute("src")).toBe("https://live.example"));
    expect(image()).toBeNull();
    expect(api.rollback).not.toHaveBeenCalled();
  });
  it("keeps an explicitly selected current version as an image when a newer HEAD is applied", async () => {
    render(<Preview versions={[version(32), version(31)]} selected="v32" />);
    await waitForDom(() => expect(container.querySelector("iframe")).not.toBeNull());
    const counts = [api.runtime, api.start, api.sync, api.session].map((fn) => fn.mock.calls.length);
    render(<Preview versions={[version(33, { is_current: true }), version(32, { is_current: false }), version(31)]} selected="v32" head="s33" />);
    await settle();
    expect(image()?.getAttribute("src")).toBe("/images/v32.png");
    expect(container.querySelector("iframe")).toBeNull();
    expect([api.runtime, api.start, api.sync, api.session].map((fn) => fn.mock.calls.length)).toEqual(counts);
  });
  it("preserves a historical selection across a newly applied HEAD", async () => {
    render(<Preview versions={[version(32), version(31)]} selected="v31" />);
    render(<Preview versions={[version(33, { is_current: true }), version(32, { is_current: false }), version(31)]} selected="v31" head="s33" />);
    await settle();
    expect(image()?.getAttribute("src")).toBe("/images/v31.png");
    expect(container.querySelector("iframe")).toBeNull();
    for (const fn of [api.runtime, api.start, api.sync, api.session]) expect(fn).not.toHaveBeenCalled();
  });
  it.each([
    { name: "a newer user version", head: "s33", remainsCurrent: false },
    { name: "a technical snapshot of the same version", head: "technical-s32", remainsCurrent: true },
  ])("waits for matching version metadata after HEAD changes to $name, ignoring an old in-flight response", async ({ head, remainsCurrent }) => {
    const previousPage = { versions: [version(32), version(31)], next_cursor: null };
    const staleRequest = deferred<typeof previousPage>();
    const freshRequest = deferred<typeof previousPage>();
    api.versions.mockReset().mockResolvedValueOnce(previousPage)
      .mockImplementationOnce(() => staleRequest.promise)
      .mockImplementationOnce(() => freshRequest.promise);
    render(<MaxWorkspaceShell project={project} email="a@example.com" />);
    await waitForDom(() => {
      expect(container.querySelector("iframe")).not.toBeNull();
      expect(container.querySelector("[data-testid='max-version-32']")).not.toBeNull();
    });
    click("[data-testid='max-version-32']");

    // An ordinary history poll starts before the snapshot-created notification.
    act(() => { void client.invalidateQueries({ queryKey: ["project-versions", "p"] }); });
    await waitForDom(() => expect(api.versions).toHaveBeenCalledTimes(2));
    const runtimeCounts = [api.runtime, api.start, api.sync, api.session].map((fn) => fn.mock.calls.length);
    act(() => { client.setQueryData(["snapshots", "p"], [{ id: head }]); });
    await waitForDom(() => {
      expect(container.querySelector("iframe")).toBeNull();
      expect(image()?.getAttribute("src")).toBe("/images/v32.png");
    });
    expect(api.versions).toHaveBeenCalledTimes(3);
    expect([api.runtime, api.start, api.sync, api.session].map((fn) => fn.mock.calls.length)).toEqual(runtimeCounts);

    // A late response describing the previous HEAD must not reopen the live app.
    await act(async () => { staleRequest.resolve(previousPage); });
    await settle();
    expect(container.querySelector("iframe")).toBeNull();
    expect(image()?.getAttribute("src")).toBe("/images/v32.png");
    expect([api.runtime, api.start, api.sync, api.session].map((fn) => fn.mock.calls.length)).toEqual(runtimeCounts);

    await act(async () => { freshRequest.resolve({
      versions: remainsCurrent ? previousPage.versions : [version(33, { is_current: true }), version(32, { is_current: false }), version(31)],
      next_cursor: null,
    }); });
    if (remainsCurrent) {
      await waitForDom(() => expect(container.querySelector("iframe")?.getAttribute("src")).toBe("https://live.example"));
      expect(image()).toBeNull();
    } else {
      await waitForDom(() => expect(container.querySelector("[data-testid='max-version-33']")).not.toBeNull());
      expect(container.querySelector("iframe")).toBeNull();
      expect(image()?.getAttribute("src")).toBe("/images/v32.png");
      expect([api.runtime, api.start, api.sync, api.session].map((fn) => fn.mock.calls.length)).toEqual(runtimeCounts);
    }
    expect(container.querySelector("[data-testid='max-version-32']")?.getAttribute("aria-pressed")).toBe("true");
  });
  it("retains a selected older page while history refreshes for a new HEAD", async () => {
    const nextFirstPage = deferred<{ versions: ProjectVersion[]; next_cursor: number }>();
    let headChanged = false;
    api.versions.mockReset().mockImplementation(async (_id: string, before?: number) => {
      if (before) return { versions: (headChanged ? [3, 2, 1] : [2, 1]).map((number) => version(number)), next_cursor: null };
      if (headChanged) return nextFirstPage.promise;
      return { versions: Array.from({ length: 30 }, (_, index) => version(32 - index)), next_cursor: 3 };
    });
    render(<MaxWorkspaceShell project={project} email="a@example.com" />);
    await waitForDom(() => expect(container.querySelector("[data-testid='max-history-load-older']")).not.toBeNull());
    click("[data-testid='max-history-load-older']");
    await waitForDom(() => expect(container.querySelector("[data-testid='max-version-1']")).not.toBeNull());
    click("[data-testid='max-version-1']");
    const runtimeCounts = [api.runtime, api.start, api.sync, api.session].map((fn) => fn.mock.calls.length);
    headChanged = true;
    act(() => { client.setQueryData(["snapshots", "p"], [{ id: "s33" }]); });
    await waitForDom(() => expect(api.versions).toHaveBeenCalledTimes(3));
    expect(image()?.getAttribute("src")).toBe("/images/v1.png");
    await act(async () => { nextFirstPage.resolve({
      versions: Array.from({ length: 30 }, (_, index) => version(33 - index, { is_current: index === 0 })),
      next_cursor: 4,
    }); });
    await waitForDom(() => expect(container.querySelector("[data-testid='max-version-33']")).not.toBeNull());
    expect(container.querySelector("[data-testid='max-version-1']")?.getAttribute("aria-pressed")).toBe("true");
    expect(image()?.getAttribute("src")).toBe("/images/v1.png");
    expect(container.querySelector("iframe")).toBeNull();
    expect([api.runtime, api.start, api.sync, api.session].map((fn) => fn.mock.calls.length)).toEqual(runtimeCounts);
  });
  it.each([false, true])("does not eagerly sync the previous live selection on snapshot.created (same current version=%s)", async (remainsCurrent) => {
    let socket: { onmessage?: (event: { data: string }) => void } | undefined;
    vi.stubGlobal("WebSocket", class { static OPEN = 1; static CONNECTING = 0; readyState = 1; onmessage?: (event: { data: string }) => void; onclose = null; constructor() { socket = this; } send() {} close() {} });
    client.setQueryData(["messages", "p"], [{ id: "a", role: "assistant", tokens_out: null }]);
    const previousPage = { versions: [version(32), version(31)], next_cursor: null };
    const freshRequest = deferred<typeof previousPage>();
    api.versions.mockReset().mockResolvedValue(previousPage);
    function Stream() { usePromptStream("p", "app"); return null; }
    render(<><MaxWorkspaceShell project={project} email="a@example.com" /><Stream /></>);
    await waitForDom(() => {
      expect(socket?.onmessage).toBeTypeOf("function");
      expect(container.querySelector("iframe")).not.toBeNull();
      expect(container.querySelector("[data-testid='max-version-32']")).not.toBeNull();
    });
    click("[data-testid='max-version-32']");
    const runtimeCounts = [api.runtime, api.start, api.sync, api.session].map((fn) => fn.mock.calls.length);
    api.versions.mockImplementation(() => freshRequest.promise);
    const head = remainsCurrent ? "technical-s32" : "s33";
    act(() => { socket!.onmessage!({ data: JSON.stringify({ type: "snapshot.created", data: { snapshot: { id: head } } }) }); });
    await waitForDom(() => {
      expect(container.querySelector("iframe")).toBeNull();
      expect(image()?.getAttribute("src")).toBe("/images/v32.png");
    });
    expect([api.runtime, api.start, api.sync, api.session].map((fn) => fn.mock.calls.length)).toEqual(runtimeCounts);
    await act(async () => { freshRequest.resolve({
      versions: remainsCurrent ? previousPage.versions : [version(33, { is_current: true }), version(32, { is_current: false }), version(31)],
      next_cursor: null,
    }); });
    if (remainsCurrent) {
      await waitForDom(() => expect(container.querySelector("iframe")).not.toBeNull());
      expect(api.sync.mock.calls.length).toBeGreaterThan(runtimeCounts[2]);
      expect(image()).toBeNull();
    } else {
      await waitForDom(() => expect(container.querySelector("[data-testid='max-version-33']")).not.toBeNull());
      expect(image()?.getAttribute("src")).toBe("/images/v32.png");
      expect(container.querySelector("iframe")).toBeNull();
      expect([api.runtime, api.start, api.sync, api.session].map((fn) => fn.mock.calls.length)).toEqual(runtimeCounts);
    }
  });
  it("highlights the applied current version when live preview has no explicit selection", async () => {
    render(<Preview versions={[version(33, { status: "failed", previews: [], preview_status: "missing" }), version(32), version(31)]} selected={null} />);
    await waitForDom(() => expect(container.querySelector("iframe")).not.toBeNull());
    expect(container.querySelector("[data-testid='max-version-32']")?.getAttribute("aria-pressed")).toBe("true");
    expect(container.querySelector("[data-testid='max-version-33']")).toBeNull();
  });
  it("collapses a long prompt behind a short title and discloses its exact original text", () => {
    const prompt = "Измени заголовок приложения.\n\nСохрани список задач, SDK и авторизацию без изменений. ".repeat(8);
    render(<Preview versions={[version(32), version(31, { prompt_text: prompt })]} />);
    const toggle = container.querySelector<HTMLButtonElement>("[data-testid='max-version-prompt-toggle']")!;
    expect(toggle).not.toBeNull();
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.textContent!.length).toBeLessThan(110);
    expect(toggle.textContent).toContain("Подробнее");
    expect(container.querySelector("[data-testid='max-preview-header']")?.textContent).not.toContain("Измени заголовок приложения.");
    const controls = toggle.getAttribute("aria-controls");
    expect(controls).toBeTruthy();
    click("[data-testid='max-version-prompt-toggle']");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const panel = document.getElementById(controls!);
    expect(panel).not.toBeNull();
    expect([...panel!.querySelectorAll("*")].some((node) => node.textContent === prompt)).toBe(true);
    expect(panel!.textContent).toContain("06.09.2026");
    expect(image()?.getAttribute("src")).toBe("/images/v31.png");
    click("[data-testid='max-version-prompt-toggle']");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(api.session).not.toHaveBeenCalled();
  });
  it("keeps a newer queued version isolated from the currently applied live runtime", async () => {
    render(<Preview versions={[version(33, { status: "queued", preview_status: "pending", previews: [], snapshot_id: null }), version(32)]} selected="v33" />);
    await settle();
    expect(container.querySelector("iframe")).toBeNull();
    for (const fn of [api.runtime, api.start, api.sync, api.session]) expect(fn).not.toHaveBeenCalled();
  });
  it("switches quickly using the rail, keyboard and horizontal swipe while preserving vertical scrolling", async () => {
    render(<Preview versions={[version(32), version(31), version(30)]} />);
    click("[data-testid='max-version-30']"); expect(image()?.getAttribute("src")).toBe("/images/v30.png");
    const region = () => container.querySelector<HTMLElement>("[data-testid='history-image-viewer']")!;
    act(() => region().dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })));
    expect(image()?.getAttribute("src")).toBe("/images/v31.png");
    function touch(type: string, x: number, y: number) { const e = new Event(type, { bubbles: true }); Object.defineProperty(e, type === "touchstart" ? "touches" : "changedTouches", { value: [{ clientX: x, clientY: y }] }); act(() => region().dispatchEvent(e)); }
    touch("touchstart", 100, 100); touch("touchend", 110, 300); expect(image()?.getAttribute("src")).toBe("/images/v31.png");
    touch("touchstart", 200, 100); touch("touchend", 40, 105);
    await waitForDom(() => expect(container.querySelector("iframe")).not.toBeNull());
    expect(image()).toBeNull(); expect(api.start).not.toHaveBeenCalled();
  });
  it("shows missing, pending and broken images honestly and ignores late old image events", () => {
    const versions = [version(32), version(31), version(30, { previews: [], preview_status: "failed", status: "failed", can_restore: false }), version(29)];
    render(<Preview versions={versions} />); const old = image()!;
    click("[data-testid='max-version-29']"); act(() => old.dispatchEvent(new Event("error")));
    expect(image()?.getAttribute("src")).toBe("/images/v29.png");
    act(() => image()!.dispatchEvent(new Event("error"))); expect(container.textContent).toContain("Не удалось загрузить снимок");
    expect(container.querySelector("[data-testid='max-version-30']")).toBeNull();
    expect(container.querySelector("iframe")).toBeNull();
    expect(container.querySelector("[data-testid='max-live-device']")).toBeNull();
    click("[data-testid='max-history-retry-image']");
    expect(image()?.getAttribute("src")).toBe("/images/v29.png");
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
    render(<Preview versions={[version(34, { status: "queued", previews: [], preview_status: "pending" }), version(33, { status: "failed", previews: [], preview_status: "missing" }), version(32, { status: "unchanged", snapshot_id: "s31", is_current: false }), version(31)]} selected="v34" />);
    click("[data-testid='max-history-activity-open']");
    const activity = document.querySelector("[data-testid='max-history-activity']")!;
    expect(activity.textContent).toContain("В очереди"); expect(activity.textContent).toContain("Не завершилась");
    expect(activity.querySelector("[data-testid='max-history-event-34']")).not.toBeNull();
    expect(activity.querySelector("[data-testid='max-history-event-33']")).not.toBeNull();
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
    expect(container.textContent).toContain("Версия недоступна"); expect(container.querySelector("iframe")).toBeNull();
    expect(container.querySelector("[data-testid='max-live-device']")).toBeNull();
    expect(container.querySelector("[aria-label='Предыдущая версия']")).toBeNull();
    act(() => container.querySelector("[data-testid='max-history-unavailable']")!.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })));
    expect(container.querySelector("iframe")).toBeNull();
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
    click("[data-testid='max-history-activity-open']");
    expect(document.querySelector("[data-testid='max-history-event-33']")?.textContent).toContain("В очереди");
  });

  it("keeps viewport and version controls in generic image history", () => {
    const previous = vi.fn();
    render(<VersionImagePreview identity="generic-v31" label="Версия v31" images={[{ url: "/mobile.png", width: 390, height: 2400, route: "/", reconstructed: true }, { url: "/desktop.png", width: 1280, height: 900, route: "/" }]} previewStatus="ready" status="ready" onPrevious={previous} />);
    const select = container.querySelector<HTMLSelectElement>("[aria-label='Экран версии']")!;
    expect([...select.options].map((option) => option.textContent)).toEqual(["/ · 390px", "/ · 1280px"]);
    expect(container.textContent).toContain("Восстановлено из кода · данные для предпросмотра");
    act(() => { select.value = "1"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(image()?.getAttribute("src")).toBe("/desktop.png");
    click("[aria-label='Предыдущая версия']");
    expect(previous).toHaveBeenCalledOnce();
    expect(container.textContent).toContain("Версия v31 · только просмотр");
  });
  it("chooses a mobile capture for MAX even when desktop is first and keeps the phone free of history controls", () => {
    render(<Preview versions={[version(32), version(31, { previews: [
      { url: "/desktop.png", width: 1280, height: 900, route: "/" },
      { url: "/tablet.png", width: 768, height: 1200, route: "/" },
      { url: "/mobile.png", width: 390, height: 2400, route: "/", reconstructed: true },
    ] })]} />);
    expect(image()?.getAttribute("src")).toBe("/mobile.png");
    act(() => image()!.dispatchEvent(new Event("load")));
    const phone = container.querySelector("[data-testid='max-historical-snapshot']")!;
    expect(phone.querySelector("select")).toBeNull();
    expect(phone.querySelector("button")).toBeNull();
    expect(phone.textContent).toBe("");
    expect(container.querySelector("[data-testid='max-version-31']")).not.toBeNull();
    expect(api.runtime).not.toHaveBeenCalled();
    expect(api.session).not.toHaveBeenCalled();
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
    click("[data-testid='max-history-activity-open']");
    expect(document.querySelector("[data-testid='max-history-event-33']")?.textContent).toContain("В очереди");
    api.versions.mockResolvedValue({ versions: [version(33, { status: type === "llm.error" ? "failed" : "cancelled", previews: [], preview_status: "missing" })], next_cursor: null });
    act(() => socket!.onmessage!({ data: JSON.stringify({ type, data: { message_id: "a", error: "failure" } }) })); await settle(); await settle();
    expect(document.querySelector("[data-testid='max-history-event-33']")?.textContent).toContain(type === "llm.error" ? "Не завершилась" : "Отменена");
  });
  it("resolves API-relative authenticated images against the configured API origin", () => {
    render(<Preview versions={[version(31, { previews: [{ url: "/api/projects/p/snapshots/s31/previews/0", width: 390, height: 1000, route: "/" }] })]} />);
    expect(image()?.getAttribute("src")).toBe("http://localhost:8000/api/projects/p/snapshots/s31/previews/0");
    expect(container.querySelector<HTMLImageElement>("[data-testid='max-version-31'] img")?.getAttribute("src")).toBe("http://localhost:8000/api/projects/p/snapshots/s31/previews/0");
  });
  it("shows reconstructed capture provenance in the prompt dialog outside the phone", () => {
    render(<Preview versions={[version(32), version(31, { previews: [{ url: "/rebuilt.png", width: 390, height: 1200, route: "/", reconstructed: true }] })]} />);
    const provenance = "Восстановлено из кода · данные для предпросмотра";
    expect(container.querySelector("[data-testid='max-historical-snapshot']")?.textContent).not.toContain(provenance);
    click("[data-testid='max-version-prompt-toggle']");
    expect(document.querySelector("[data-testid='max-version-prompt']")?.textContent).toContain(provenance);
    const close = [...document.querySelectorAll<HTMLButtonElement>("[data-testid='max-version-prompt'] button")].find((button) => button.textContent === "Закрыть");
    expect(close).toBeDefined();
    act(() => close!.click());
    click("[data-testid='max-version-32']");
    click("[data-testid='max-version-prompt-toggle']");
    expect(document.querySelector("[data-testid='max-version-prompt']")?.textContent).not.toContain(provenance);
  });

});
