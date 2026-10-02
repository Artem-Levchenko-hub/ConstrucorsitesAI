import { act, type ComponentProps, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxWorkspaceShell } from "@/components/max/MaxWorkspaceShell";
import type { Project } from "@/lib/api/types";

type PreviewProps = ComponentProps<typeof import("@/components/max/MaxLivePreview").MaxLivePreview>;
const api = vi.hoisted(() => ({
  versions: vi.fn(), restorations: vi.fn(), prepare: vi.fn(), rollback: vi.fn(),
  observed: null as PreviewProps | null,
}));
vi.mock("@/lib/api/projects", () => ({ listProjects: async () => [] }));
vi.mock("@/lib/api/snapshots", () => ({
  listSnapshots: async () => [{ id: "current" }],
  listProjectVersions: api.versions, rollback: api.rollback,
}));
vi.mock("@/lib/api/restorations", () => ({ listRestorations: api.restorations, prepareRestoration: api.prepare }));
vi.mock("@/lib/api/max-studio", () => ({ getMaxReadiness: async () => ({ items: [] }) }));
vi.mock("@/components/max/MaxEditorLayout", () => ({
  MaxEditorLayout: ({ children, preview, launch }: { children: ReactNode; preview: () => ReactNode; launch: ReactNode }) =>
    <>{children}{preview()}{launch}</>,
}));
vi.mock("@/components/max/MaxLivePreview", () => ({ MaxLivePreview: (props: PreviewProps) => {
  api.observed = props; return <div data-testid="current-preview" />;
} }));
vi.mock("@/components/workspace/ChatPanel", () => ({ ChatPanel: () => <textarea aria-label="Опишите приложение или правки" /> }));
vi.mock("@/components/max/MaxLaunchPanel", () => ({ MaxLaunchPanel: () => <button>Опубликовать приложение</button> }));
vi.mock("@/components/max/MaxAccountMenu", () => ({ MaxAccountMenu: () => null }));
vi.mock("@/components/max/MaxProjectNav", () => ({ MaxProjectNav: () => null }));
vi.mock("@/components/max/MaxUsageBreakdown", () => ({ MaxUsageBreakdown: () => null }));
vi.mock("@/components/workspace/DownloadButton", () => ({ DownloadButton: () => null }));

const project = { id: "mvp", slug: "mvp", template: "max_miniapp", current_snapshot_id: "current" } as Project;
let root: Root, container: HTMLDivElement, client: QueryClient;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.clearAllMocks();
  localStorage.clear();
  api.versions.mockResolvedValue({ versions: [], next_cursor: 20 });
  api.restorations.mockResolvedValue({ enabled: true, items: [] });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount()); client.clear(); container.remove(); localStorage.clear();
});
it("preserves generation/edit, current preview and publication without historical actions or requests", async () => {
  // Old storage must not silently resume a restoration after the MVP policy changes.
  localStorage.setItem("omnia:restore:request:mvp", JSON.stringify({ kind: "prepare", payload: {
    target_version_id: "historical", expected_draft_snapshot_id: "current", idempotency_key: "old-request",
  } }));
  localStorage.setItem("omnia:max:adaptation:mvp", JSON.stringify({ projectId: "mvp", prompt: "old adaptation", reference: {
    operation_id: "old-operation", expected_draft_snapshot_id: "current",
  } }));
  await act(async () => root.render(<QueryClientProvider client={client}><MaxWorkspaceShell project={project} email="qa@example.invalid" /></QueryClientProvider>));
  await act(async () => { await vi.waitFor(() => expect(api.versions).toHaveBeenCalled()); });
  expect(api.observed?.selectedVersionId).toBeNull();
  expect(api.observed?.onLoadOlder).toBeUndefined();
  expect(api.observed?.onPrepareRestoration).toBeUndefined();
  expect(api.observed?.restorationEnabled).toBe(false);
  expect(api.restorations).not.toHaveBeenCalled();
  expect(api.prepare).not.toHaveBeenCalled();
  await act(async () => api.observed?.onRestoreSnapshot("historical"));
  expect(api.rollback).not.toHaveBeenCalled();
  expect(container.querySelector("textarea")).not.toBeNull();
  expect(container.querySelector('[data-testid="current-preview"]')).not.toBeNull();
  expect(container.textContent).toContain("Опубликовать приложение");
  expect(container.textContent).not.toContain("адаптаци");
  expect(container.textContent).not.toContain("восстанов");
});
