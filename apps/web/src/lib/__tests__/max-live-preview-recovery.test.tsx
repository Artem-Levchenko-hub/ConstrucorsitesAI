import { act, type ButtonHTMLAttributes, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MaxLivePreview } from "@/components/max/MaxLivePreview";
import { ApiError } from "@/lib/api/client";
import type {
  MaxProjectConfig,
  MaxPreviewSession,
  Project,
  RuntimeStatus,
  Snapshot,
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
        inn: "1234567890",
        ogrn: "1234567890123",
        address: "Moscow",
      },
      support: {
        email: null,
        phone: "+79990000000",
        response_time: "24h",
      },
      legal: {
        age_rating: "0+",
        has_sales: false,
        has_user_content: false,
        marketing_notifications: false,
        personal_data_consent: true,
        terms_accepted: true,
      },
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

const SNAPSHOTS: Snapshot[] = [
  {
    id: "snapshot-1",
    project_id: PROJECT.id,
    commit_sha: "1111111111111111111111111111111111111111",
    prompt_text: null,
    model_id: null,
    parent_id: null,
    preview_url: null,
    is_rollback_target: true,
    created_at: "2026-09-02T20:00:00Z",
  },
];

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
    vi.clearAllMocks();
  });

  function renderPreview(currentSnapshotId: string | null) {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MaxLivePreview
            project={PROJECT}
            snapshots={SNAPSHOTS}
            snapshotsLoading={false}
            currentSnapshotId={currentSnapshotId}
            selectedSnapshotId={null}
            onSelectSnapshot={vi.fn()}
            onRestoreSnapshot={vi.fn().mockResolvedValue(undefined)}
            restoringSnapshot={false}
          />
        </QueryClientProvider>,
      );
    });
  }

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
