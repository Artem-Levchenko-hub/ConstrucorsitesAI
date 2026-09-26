import { act, type ButtonHTMLAttributes, type ComponentProps, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MaxLivePreview } from "@/components/max/MaxLivePreview";
import { ApiError } from "@/lib/api/client";
import type {
  MaxProjectConfig,
  MaxPreviewSession,
  Project,
  Message,
  ProjectVersion,
  RuntimeStatus,
} from "@/lib/api/types";

const createMaxPreviewSession = vi.fn<
  (projectId: string) => Promise<MaxPreviewSession>
>();
const syncMaxManagedKit = vi.fn<
  (projectId: string) => Promise<MaxProjectConfig>
>();
const getRuntime = vi.fn<(projectId: string) => Promise<RuntimeStatus>>();
const startRuntime = vi.fn<(projectId: string) => Promise<RuntimeStatus>>();
const toastError = vi.fn();
const listMessages = vi.fn<(projectId: string) => Promise<Message[]>>();

vi.mock("@/lib/api/messages", () => ({
  listMessages: (projectId: string) => listMessages(projectId),
}));

vi.mock("@/lib/api/max-studio", () => ({
  createMaxPreviewSession: (projectId: string) =>
    createMaxPreviewSession(projectId),
  syncMaxManagedKit: (projectId: string) => syncMaxManagedKit(projectId),
}));

vi.mock("@/lib/api/runtime", () => ({
  getRuntime: (projectId: string) => getRuntime(projectId),
  startRuntime: (projectId: string) => startRuntime(projectId),
}));

vi.mock("@/components/max/MaxVersionRail", () => ({
  MaxVersionRail: () => <div data-testid="max-version-rail" />,
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({
    children,
    ...props
  }: ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
}));

vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ children }: { children: ReactNode }) => <>{children}</>,
  DialogContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogDescription: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogFooter: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogHeader: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogTitle: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
}));

vi.mock("sonner", () => ({
  toast: {
    error: (...args: unknown[]) => toastError(...args),
  },
}));

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function runtime(state: RuntimeStatus["state"] = "running"): RuntimeStatus {
  return {
    state,
    container_name: "omnia-cell-preview",
    port: 3000,
    dev_url: "https://preview.dev.local",
    last_active_at: "2026-09-02T20:00:00Z",
    hibernate_after_seconds: 600,
    keep_alive: false,
  };
}

function managedKit(snapshotId: string | null): MaxProjectConfig {
  return {
    project_id: PROJECT.id,
    config_version: 1,
    updated_at: "2026-09-02T20:00:00Z",
    synced_snapshot_id: snapshotId,
    config: {
      app_name: "MAX Planner",
      app_type: "custom",
      summary: "Personal planner",
      audience: "Owner",
      primary_action: "Plan",
      features: [],
      style: "clean",
      brand_colors: "#111111",
      content: [],
      operator: {
        legal_name: "Owner",
      },
      support: {
        email: null,
        response_time: "24h",
      },
      legal: {
        age_rating: "0+",
        has_sales: false,
        has_user_content: false,
        marketing_notifications: false,
        personal_data_consent: true,
        terms_accepted: true, policy_url: "" },
      max_url_attached: true,
    },
  };
}

function session(url: string): MaxPreviewSession {
  return {
    url,
    expires_at: "2026-09-02T20:10:00Z",
  };
}

const PROJECT: Project = {
  id: "00000000-0000-0000-0000-000000000001",
  owner_id: "00000000-0000-0000-0000-000000000002",
  name: "MAX Planner",
  slug: "max-planner",
  template: "max_miniapp",
  current_snapshot_id: "snapshot-1",
  created_at: "2026-09-02T20:00:00Z",
  updated_at: "2026-09-02T20:00:00Z",
};

function buildMessage(status: Message["generation_status"], id = "build-1"): Message {
  return {
    id, project_id: PROJECT.id, snapshot_id: null, role: "assistant", content: "",
    model_id: null, tokens_in: null, tokens_out: null, selected_elements: null,
    created_at: "2026-09-26T14:39:22Z", generation_status: status,
  };
}

function buildVersion(status: ProjectVersion["status"] = "failed"): ProjectVersion {
  return {
    id: "version-1", number: 1, project_id: PROJECT.id,
    source_message_id: "build-1", generation_run_id: "run-1",
    snapshot_id: "seed-snapshot", commit_sha: "a".repeat(40),
    prompt_text: "Synthetic QA", model_id: null, created_at: "2026-09-26T14:39:22Z",
    status, preview_status: "missing", previews: [], is_current: status === "ready",
    can_restore: status === "ready",
  };
}

async function flushPromises(rounds: number = 4): Promise<void> {
  for (let index = 0; index < rounds; index += 1) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

async function waitForValue<T>(
  read: () => T | null | undefined,
  {
    intervalMs = 10,
    timeoutMs = 2_000,
    debug = "Condition was not met",
  }: {
    intervalMs?: number;
    timeoutMs?: number;
    debug?: string;
  } = {},
): Promise<T> {
  let result: T | null | undefined;
  await act(async () => {
    result = await vi.waitFor(
      () => {
        const value = read();
        if (!value) throw new Error(debug);
        return value;
      },
      {
        interval: intervalMs,
        timeout: timeoutMs,
      },
    );
  });
  return result as T;
}

describe("MAX live preview recovery", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;
  let originalResizeObserver: typeof globalThis.ResizeObserver | undefined;

  beforeEach(() => {
    (
      globalThis as typeof globalThis & {
        IS_REACT_ACT_ENVIRONMENT: boolean;
      }
    ).IS_REACT_ACT_ENVIRONMENT = true;
    class ResizeObserverMock {
      observe() {}
      disconnect() {}
      unobserve() {}
    }
    originalResizeObserver = globalThis.ResizeObserver;
    globalThis.ResizeObserver =
      ResizeObserverMock as typeof globalThis.ResizeObserver;
    getRuntime.mockResolvedValue(runtime());
    startRuntime.mockResolvedValue(runtime());
    listMessages.mockResolvedValue([]);
    queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          gcTime: 0,
        },
      },
    });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
    if (originalResizeObserver === undefined) {
      delete (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
    } else {
      globalThis.ResizeObserver = originalResizeObserver;
    }
    vi.useRealTimers();
    vi.resetAllMocks();
  });

  function renderPreview(
    currentSnapshotId: string | null,
    overrides: Partial<ComponentProps<typeof MaxLivePreview>> = {},
  ) {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MaxLivePreview
            project={PROJECT}
            versions={[]}
            snapshotsLoading={false}
            currentSnapshotId={currentSnapshotId}
            selectedVersionId={null}
            onSelectVersion={vi.fn()}
            onRestoreSnapshot={vi.fn().mockResolvedValue(undefined)}
            restoringSnapshot={false}
            {...overrides}
          />
        </QueryClientProvider>,
      );
    });
  }

  it.each(["failed", "cancelled"] as const)(
    "stops first-build preparation after durable %s even with a seed snapshot",
    async (status) => {
      queryClient.setQueryData(["messages", PROJECT.id], [buildMessage(status)]);
      listMessages.mockResolvedValue([buildMessage(status)]);
      getRuntime.mockResolvedValue(runtime("provisioning"));
      renderPreview("seed-snapshot", { versions: [buildVersion(status)] });
      await flushPromises();
      expect(container.textContent).toContain(status === "failed"
        ? "Первая сборка не завершена" : "Первая сборка отменена");
      expect(container.textContent).not.toContain("от 15 до 60 секунд");
      expect(container.querySelector(".animate-spin")).toBeNull();
      expect(startRuntime).not.toHaveBeenCalled();
      expect(syncMaxManagedKit).not.toHaveBeenCalled();
      expect(createMaxPreviewSession).not.toHaveBeenCalled();
      expect(container.querySelector<HTMLButtonElement>("[data-testid='max-refresh-preview']")?.disabled)
        .toBe(true);
    },
  );

  it("restores the failed-first-build state from history after a reload", async () => {
    listMessages.mockResolvedValue([buildMessage("failed")]);
    getRuntime.mockResolvedValue(runtime("provisioning"));
    renderPreview("seed-snapshot", { versions: [buildVersion()] });
    await waitForValue(() => container.textContent?.includes("Первая сборка не завершена"));
    expect(container.textContent).not.toContain("от 15 до 60 секунд");
    expect(startRuntime).not.toHaveBeenCalled();
  });

  it("releases the terminal state immediately for a new optimistic request and opens its ready build", async () => {
    const failed = buildMessage("failed");
    listMessages.mockResolvedValue([failed]);
    queryClient.setQueryData(["messages", PROJECT.id], [failed]);
    getRuntime.mockResolvedValue(runtime("stopped"));
    startRuntime.mockResolvedValue(runtime("provisioning"));
    syncMaxManagedKit.mockResolvedValue(managedKit("built-snapshot"));
    createMaxPreviewSession.mockResolvedValue(session("https://new-build.example"));
    renderPreview("seed-snapshot", { versions: [buildVersion()] });
    await flushPromises();
    expect(container.textContent).toContain("Первая сборка не завершена");

    const optimistic = buildMessage(null, "optimistic-next");
    listMessages.mockResolvedValue([failed, optimistic]);
    act(() => queryClient.setQueryData(["messages", PROJECT.id], [failed, optimistic]));
    await waitForValue(() => startRuntime.mock.calls.length === 1);
    expect(container.textContent).not.toContain("Первая сборка не завершена");

    act(() => queryClient.setQueryData(["messages", PROJECT.id], [failed, buildMessage("running", "next")]));
    renderPreview("seed-snapshot", { versions: [buildVersion("running")] });
    expect(container.textContent).not.toContain("Первая сборка не завершена");
    act(() => {
      queryClient.setQueryData(["messages", PROJECT.id], [failed, buildMessage("completed", "next")]);
      queryClient.setQueryData(["runtime", PROJECT.id], runtime());
    });
    renderPreview("built-snapshot", { versions: [{ ...buildVersion("ready"), snapshot_id: "built-snapshot" }] });
    const frame = await waitForValue(() => container.querySelector<HTMLIFrameElement>("iframe"));
    expect(frame.src).toBe("https://new-build.example/");
  });

  it("stops a scheduled start retry when the first generation fails", async () => {
    vi.useFakeTimers();
    listMessages.mockResolvedValue([buildMessage("running")]);
    queryClient.setQueryData(["messages", PROJECT.id], [buildMessage("running")]);
    getRuntime.mockResolvedValue(runtime("stopped"));
    startRuntime.mockRejectedValue(new ApiError(503, { code: "orchestrator_unavailable", message: "busy" }));
    renderPreview("seed-snapshot", { versions: [buildVersion("running")] });
    await act(async () => { await vi.advanceTimersByTimeAsync(20); });
    expect(startRuntime).toHaveBeenCalledTimes(1);
    listMessages.mockResolvedValue([buildMessage("failed")]);
    act(() => queryClient.setQueryData(["messages", PROJECT.id], [buildMessage("failed")]));
    renderPreview("seed-snapshot", { versions: [buildVersion()] });
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(startRuntime).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("Первая сборка не завершена");
  });

  it.each(["sync", "session"] as const)("cancels scheduled %s retries after a first-build failure", async (stage) => {
    vi.useFakeTimers();
    const running = buildMessage("running");
    queryClient.setQueryData(["messages", PROJECT.id], [running]);
    listMessages.mockResolvedValue([running]);
    syncMaxManagedKit.mockResolvedValue(managedKit("seed-snapshot"));
    const target = stage === "sync" ? syncMaxManagedKit : createMaxPreviewSession;
    target.mockRejectedValue(new ApiError(503, { code: "orchestrator_unavailable", message: "busy" }));
    renderPreview("seed-snapshot", { versions: [buildVersion("running")] });
    await act(async () => { await vi.advanceTimersByTimeAsync(30); });
    expect(target).toHaveBeenCalledTimes(1);
    const failed = buildMessage("failed");
    listMessages.mockResolvedValue([failed]);
    act(() => queryClient.setQueryData(["messages", PROJECT.id], [failed]));
    renderPreview("seed-snapshot", { versions: [buildVersion()] });
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(target).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("Первая сборка не завершена");
  });

  it("does not revive the previous scheduled start when a new request arrives before its retry", async () => {
    vi.useFakeTimers();
    const running = buildMessage("running");
    queryClient.setQueryData(["messages", PROJECT.id], [running]);
    listMessages.mockResolvedValue([running]);
    getRuntime.mockResolvedValue(runtime("stopped"));
    startRuntime.mockRejectedValueOnce(new ApiError(503, { code: "orchestrator_unavailable", message: "busy" }))
      .mockResolvedValue(runtime("provisioning"));
    renderPreview("seed-snapshot", { versions: [buildVersion("running")] });
    await act(async () => { await vi.advanceTimersByTimeAsync(20); });
    expect(startRuntime).toHaveBeenCalledTimes(1);
    const failed = buildMessage("failed");
    listMessages.mockResolvedValue([failed]);
    act(() => queryClient.setQueryData(["messages", PROJECT.id], [failed]));
    renderPreview("seed-snapshot", { versions: [buildVersion()] });
    await act(async () => { await vi.advanceTimersByTimeAsync(20); });
    const next = buildMessage(null, "new-request");
    listMessages.mockResolvedValue([failed, next]);
    act(() => queryClient.setQueryData(["messages", PROJECT.id], [failed, next]));
    await act(async () => { await vi.advanceTimersByTimeAsync(20); });
    expect(startRuntime).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1_600); });
    expect(startRuntime).toHaveBeenCalledTimes(2);
    expect(container.textContent).not.toContain("Первая сборка не завершена");
  });

  it("shows the first-build invitation before any generation", async () => {
    getRuntime.mockResolvedValue(runtime("provisioning"));
    renderPreview(null);
    await flushPromises();
    expect(container.textContent).toContain("Превью появится после первой сборки");
    expect(container.textContent).not.toContain("от 15 до 60 секунд");
  });

  it.each([
    { snapshotsLoading: true }, { historyError: true },
    { historyCurrent: false }, { hasOlder: true },
  ])("does not infer terminal first-build failure from incomplete history %j", async (state) => {
    queryClient.setQueryData(["messages", PROJECT.id], [buildMessage("failed")]);
    listMessages.mockResolvedValue([buildMessage("failed")]);
    getRuntime.mockResolvedValue(runtime("provisioning"));
    renderPreview("seed-snapshot", { versions: [buildVersion()], ...state });
    await flushPromises();
    expect(container.textContent).not.toContain("Первая сборка не завершена");
  });

  it.each(["queued", "running"] as const)("does not let a stale failed message block a new %s version", async (status) => {
    queryClient.setQueryData(["messages", PROJECT.id], [buildMessage("failed")]);
    listMessages.mockResolvedValue([buildMessage("failed")]);
    getRuntime.mockResolvedValue(runtime("provisioning"));
    renderPreview("seed-snapshot", { versions: [buildVersion(status)] });
    await flushPromises();
    expect(container.textContent).not.toContain("Первая сборка не завершена");
    expect(getRuntime).toHaveBeenCalledTimes(1);
  });

  it("does not infer generation failure from a failed message-history request", async () => {
    listMessages.mockRejectedValue(new Error("offline"));
    getRuntime.mockResolvedValue(runtime("provisioning"));
    renderPreview("seed-snapshot", { versions: [buildVersion()] });
    await flushPromises();
    expect(container.textContent).not.toContain("Первая сборка не завершена");
  });

  it("does not treat iframe load during the first running build as proof of a successful version", async () => {
    const running = buildMessage("running");
    queryClient.setQueryData(["messages", PROJECT.id], [running]);
    listMessages.mockResolvedValue([running]);
    syncMaxManagedKit.mockResolvedValue(managedKit("seed-snapshot"));
    // Browsers emit load for cross-origin HTTP error pages as well as app pages.
    createMaxPreviewSession.mockResolvedValue(session("https://error-page.example"));
    renderPreview("seed-snapshot", { versions: [buildVersion("running")] });
    const frame = await waitForValue(() => container.querySelector<HTMLIFrameElement>("iframe"));
    act(() => frame.dispatchEvent(new Event("load")));
    const failed = buildMessage("failed");
    listMessages.mockResolvedValue([failed]);
    act(() => queryClient.setQueryData(["messages", PROJECT.id], [failed]));
    renderPreview("seed-snapshot", { versions: [buildVersion()] });
    await waitForValue(() => container.textContent?.includes("Первая сборка не завершена"));
    expect(container.querySelector("iframe")).toBeNull();
    expect(container.querySelector(".animate-spin")).toBeNull();
    expect(container.textContent).not.toContain("Подключено");
    expect(container.querySelector<HTMLButtonElement>("[data-testid='max-refresh-preview']")?.disabled).toBe(true);
    expect(container.querySelector<HTMLButtonElement>("[data-testid='max-open-preview-separate']")?.disabled).toBe(true);
  });

  it("keeps a working prior version when a later generation fails", async () => {
    syncMaxManagedKit.mockResolvedValue(managedKit("snapshot-1"));
    createMaxPreviewSession.mockResolvedValue(session("https://working.example"));
    renderPreview("snapshot-1", { versions: [buildVersion("ready")] });
    const frame = await waitForValue(() => container.querySelector<HTMLIFrameElement>("iframe"));
    act(() => frame.dispatchEvent(new Event("load")));
    act(() => queryClient.setQueryData(["messages", PROJECT.id], [buildMessage("failed", "next")]));
    renderPreview("snapshot-1", { versions: [{ ...buildVersion(), id: "failed-next", number: 2 }, buildVersion("ready")] });
    await flushPromises();
    expect(container.querySelector("iframe")?.src).toBe("https://working.example/");
    expect(container.textContent).not.toContain("Первая сборка не завершена");
  });

  it("keeps historical inspection separate from the failed live first build", async () => {
    queryClient.setQueryData(["messages", PROJECT.id], [buildMessage("failed")]);
    listMessages.mockResolvedValue([buildMessage("failed")]);
    renderPreview("seed-snapshot", { versions: [buildVersion()], selectedVersionId: "version-1" });
    await flushPromises();
    expect(container.querySelector("[data-testid='max-history-unavailable']")).not.toBeNull();
    expect(container.textContent).not.toContain("Первая сборка не завершена");
    expect(startRuntime).not.toHaveBeenCalled();
  });

  it("does not carry a working iframe into another project's failed first build", async () => {
    syncMaxManagedKit.mockResolvedValue(managedKit("snapshot-1"));
    createMaxPreviewSession.mockResolvedValue(session("https://working.example"));
    renderPreview("snapshot-1", { versions: [buildVersion("ready")] });
    const frame = await waitForValue(() => container.querySelector<HTMLIFrameElement>("iframe"));
    act(() => frame.dispatchEvent(new Event("load")));
    const other = { ...PROJECT, id: "other-project" };
    const failure = { ...buildMessage("failed"), project_id: other.id };
    queryClient.setQueryData(["messages", other.id], [failure]);
    listMessages.mockResolvedValue([failure]);
    getRuntime.mockResolvedValue(runtime("provisioning"));
    renderPreview("other-seed", { project: other, versions: [{ ...buildVersion(), project_id: other.id }] });
    await flushPromises();
    expect(container.querySelector("iframe")).toBeNull();
    expect(container.textContent).toContain("Первая сборка не завершена");
  });

  it("keeps the last working iframe while a new snapshot sync is still preparing", async () => {
    const nextManagedKit = deferred<MaxProjectConfig>();
    const nextSession = deferred<MaxPreviewSession>();
    syncMaxManagedKit
      .mockResolvedValueOnce(managedKit("snapshot-1"))
      .mockImplementationOnce(() => nextManagedKit.promise);
    createMaxPreviewSession
      .mockResolvedValueOnce(session("https://preview-1.example"))
      .mockImplementationOnce(() => nextSession.promise);

    renderPreview("snapshot-1");
    const initialFrame = await waitForValue(
      () =>
        container.querySelector<HTMLIFrameElement>(
          "[data-testid='max-live-iframe']",
        ),
      {
        debug: `Initial preview did not render: ${container.innerHTML}`,
      },
    );
    expect(initialFrame?.getAttribute("src")).toBe("https://preview-1.example");
    act(() => {
      initialFrame?.dispatchEvent(new Event("load"));
    });

    renderPreview("snapshot-2");
    const fallbackFrame = await waitForValue(
      () =>
        container.querySelector<HTMLIFrameElement>(
          "[data-testid='max-live-iframe']",
        ),
      {
        debug: `Fallback preview disappeared: ${container.innerHTML}`,
      },
    );
    expect(fallbackFrame?.getAttribute("src")).toBe("https://preview-1.example");
    expect(container.textContent).toContain(
      "Пока показываем последнюю рабочую версию.",
    );
    expect(syncMaxManagedKit).toHaveBeenCalledTimes(2);

    nextManagedKit.resolve(managedKit("snapshot-2"));
    await flushPromises();
    nextSession.resolve(session("https://preview-2.example"));
    const updatedFrame = await waitForValue(
      () => {
        const frame = container.querySelector<HTMLIFrameElement>(
          "[data-testid='max-live-iframe']",
        );
        return frame?.getAttribute("src") === "https://preview-2.example"
          ? frame
          : null;
      },
      {
        debug: `Updated preview did not render: ${container.innerHTML}`,
      },
    );
    expect(updatedFrame?.getAttribute("src")).toBe("https://preview-2.example");
    expect(createMaxPreviewSession).toHaveBeenCalledTimes(2);
  });

  it("refreshes preview from the header and restarts a retained draft without dropping the iframe", async () => {
    const nextSession = deferred<MaxPreviewSession>();
    getRuntime
      .mockResolvedValueOnce(runtime())
      .mockResolvedValueOnce(runtime("stopped"));
    startRuntime.mockResolvedValueOnce(runtime());
    syncMaxManagedKit
      .mockResolvedValueOnce(managedKit("snapshot-1"))
      .mockResolvedValueOnce(managedKit("snapshot-1"));
    createMaxPreviewSession
      .mockResolvedValueOnce(session("https://preview-1.example"))
      .mockImplementationOnce(() => nextSession.promise);

    renderPreview("snapshot-1");
    const initialFrame = await waitForValue(
      () =>
        container.querySelector<HTMLIFrameElement>(
          "[data-testid='max-live-iframe']",
        ),
      {
        debug: `Initial preview did not render: ${container.innerHTML}`,
      },
    );
    act(() => {
      initialFrame?.dispatchEvent(new Event("load"));
    });

    const refreshButton = container.querySelector<HTMLButtonElement>(
      "[data-testid='max-refresh-preview']",
    );
    expect(refreshButton?.getAttribute("aria-label")).toBe("Обновить превью");

    await act(async () => {
      refreshButton?.click();
      await Promise.resolve();
    });
    await flushPromises();

    expect(startRuntime).toHaveBeenCalledTimes(1);
    expect(syncMaxManagedKit).toHaveBeenCalledTimes(2);
    const pendingFrame = container.querySelector<HTMLIFrameElement>(
      "[data-testid='max-live-iframe']",
    );
    expect(pendingFrame?.getAttribute("src")).toBe("https://preview-1.example");

    nextSession.resolve(session("https://preview-2.example"));
    const recoveredFrame = await waitForValue(
      () => {
        const frame = container.querySelector<HTMLIFrameElement>(
          "[data-testid='max-live-iframe']",
        );
        return frame?.getAttribute("src") === "https://preview-2.example"
          ? frame
          : null;
      },
      {
        debug: `Recovered preview did not render: ${container.innerHTML}`,
      },
    );
    expect(recoveredFrame?.getAttribute("src")).toBe("https://preview-2.example");
    expect(createMaxPreviewSession).toHaveBeenCalledTimes(2);
  });

  it("does not auto-retry permanent preview auth errors", async () => {
    syncMaxManagedKit.mockResolvedValueOnce(managedKit("snapshot-1"));
    createMaxPreviewSession.mockRejectedValueOnce(
      new ApiError(403, {
        code: "orchestrator_rejected",
        message: "forbidden",
      }),
    );

    renderPreview("snapshot-1");
    await waitForValue(
      () =>
        container.textContent?.includes("Превью пока недоступно")
          ? true
          : null,
      {
        debug: `Preview error state did not render: ${container.innerHTML}`,
      },
    );

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1_700));
    });

    expect(createMaxPreviewSession).toHaveBeenCalledTimes(1);
  });

  it("automatically recovers a busy cold start without user clicks", async () => {
    getRuntime.mockResolvedValue(runtime("stopped"));
    startRuntime.mockRejectedValueOnce(new ApiError(503, {
      code: "orchestrator_unavailable", message: "preparing",
    })).mockResolvedValueOnce(runtime());
    syncMaxManagedKit.mockResolvedValue(managedKit("snapshot-1"));
    createMaxPreviewSession.mockResolvedValue(session("https://recovered.example"));
    renderPreview("snapshot-1");
    const frame = await waitForValue(() => container.querySelector<HTMLIFrameElement>(
      "[data-testid='max-live-iframe']",
    ), { timeoutMs: 4000 });
    expect(frame.getAttribute("src")).toBe("https://recovered.example");
    expect(startRuntime).toHaveBeenCalledTimes(2);
    expect(toastError).not.toHaveBeenCalled();
  });

  it("does not show the previous start error once runtime polling sees recovery", async () => {
    getRuntime.mockResolvedValue(runtime("stopped"));
    startRuntime.mockRejectedValueOnce(new ApiError(403, { code: "forbidden", message: "unavailable" }));
    const pendingSync = deferred<MaxProjectConfig>();
    syncMaxManagedKit.mockImplementationOnce(() => pendingSync.promise);
    renderPreview("snapshot-1", { versions: [buildVersion("ready")] });
    await waitForValue(() => container.textContent?.includes("Превью пока недоступно"));
    act(() => queryClient.setQueryData(["runtime", PROJECT.id], runtime()));
    await waitForValue(() => container.textContent?.includes("Синхронизируем последнюю версию"));
    expect(container.textContent).not.toContain("Превью пока недоступно");
    expect(container.textContent).toContain("Синхронизируем последнюю версию");
  });

  it("shows a toast when manual preview refresh fails", async () => {
    createMaxPreviewSession
      .mockResolvedValueOnce(session("https://preview-1.example"))
      .mockRejectedValueOnce(
        new ApiError(403, {
          code: "forbidden",
          message: "preview forbidden",
        }),
      );
    syncMaxManagedKit
      .mockResolvedValueOnce(managedKit("snapshot-1"))
      .mockResolvedValueOnce(managedKit("snapshot-1"));

    renderPreview("snapshot-1");
    const initialFrame = await waitForValue(
      () =>
        container.querySelector<HTMLIFrameElement>(
          "[data-testid='max-live-iframe']",
        ),
      {
        debug: `Initial preview did not render: ${container.innerHTML}`,
      },
    );
    act(() => {
      initialFrame?.dispatchEvent(new Event("load"));
    });

    const refreshButton = container.querySelector<HTMLButtonElement>(
      "[data-testid='max-refresh-preview']",
    );
    await act(async () => {
      refreshButton?.click();
      await Promise.resolve();
    });

    await waitForValue(
      () => (toastError.mock.calls.length > 0 ? true : null),
      {
        debug: "Refresh failure toast did not appear",
      },
    );

    expect(toastError).toHaveBeenCalledWith("Не удалось обновить превью", {
      description: "preview forbidden",
    });
    expect(
      container.querySelector<HTMLIFrameElement>("[data-testid='max-live-iframe']")?.getAttribute("src"),
    ).toBe("https://preview-1.example");
  });
});
