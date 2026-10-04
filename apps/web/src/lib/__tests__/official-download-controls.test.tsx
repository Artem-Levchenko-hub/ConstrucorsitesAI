import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AccountProfile } from "@/components/account/AccountProfile";
import { AccountShell } from "@/components/account/AccountShell";
import { DownloadButton } from "@/components/workspace/DownloadButton";
import { MaxWorkspaceShell } from "@/components/max/MaxWorkspaceShell";
import type { Project } from "@/lib/api/types";

type LayoutProps = ComponentProps<typeof import("@/components/max/MaxEditorLayout").MaxEditorLayout>;
type PreviewProps = ComponentProps<typeof import("@/components/max/MaxLivePreview").MaxLivePreview>;
const state = vi.hoisted(() => ({ admin: false, exportAccount: vi.fn(), downloadProjectFiles: vi.fn(), remove: vi.fn() }));
vi.mock("@/lib/auth-mock", () => ({ getMaxAdminAccessServer: async () => state.admin }));
vi.mock("@/app/(auth)/actions", () => ({ logoutAction: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }) }));
vi.mock("@/lib/api/account", () => ({ deleteAccount: state.remove, exportAccount: state.exportAccount }));
vi.mock("@/lib/api/projects", () => ({ listProjects: async () => [], downloadProjectFiles: state.downloadProjectFiles }));
vi.mock("@/lib/api/snapshots", () => ({
  listSnapshots: async () => [{ id: "snapshot-ready" }], rollback: vi.fn(),
  listProjectVersions: async () => ({ versions: [{ id: "version-ready", status: "ready", snapshot_id: "snapshot-ready", prompt_text: "Saved version metadata" }], next_cursor: null }),
}));
vi.mock("@/lib/api/max-studio", () => ({ getMaxReadiness: async () => ({ items: [] }) }));
vi.mock("@/lib/use-max-restoration", () => ({ useMaxRestoration: () => ({ enabled: false }) }));
vi.mock("@/lib/use-max-adaptation", () => ({ useMaxAdaptation: () => ({ attachment: null }) }));
vi.mock("@/components/max/MaxEditorLayout", () => ({
  MaxEditorLayout: ({ children, tools, navigation, launch, preview }: LayoutProps) =>
    <>{navigation}{tools}{launch}{children}{typeof preview === "function" ? preview() : preview}</>,
}));
vi.mock("@/components/workspace/ChatPanel", () => ({ ChatPanel: () => <p>Editor chat</p> }));
vi.mock("@/components/max/MaxLivePreview", () => ({
  MaxLivePreview: ({ versions }: PreviewProps) => <output>{versions.map(version => version.prompt_text).join(", ")}</output>,
}));
vi.mock("@/components/max/MaxLaunchPanel", () => ({ MaxLaunchPanel: () => <button>Опубликовать приложение</button> }));
vi.mock("@/components/max/MaxAccountMenu", () => ({ MaxAccountMenu: () => null }));
vi.mock("@/components/max/MaxProjectNav", () => ({ MaxProjectNav: () => null }));
vi.mock("@/components/max/MaxUsageBreakdown", () => ({ MaxUsageBreakdown: () => null }));
const project: Project = { id: "test-project", name: "Disposable", slug: "test-project", template: "max_miniapp", owner_id: "owner", current_snapshot_id: "snapshot-ready", created_at: "2026-10-04", updated_at: "2026-10-04" };
let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount()); container.remove(); client.clear();
});

// These controls receive no tier entitlement. Removal must be unconditional,
// including the admin shell, rather than enabling export for a paid/admin branch.
it.each([false, true])("removes export controls while keeping profile, publication and saved metadata (admin=%s)", async admin => {
  state.admin = admin;
  const shell = await AccountShell({ email: "fixture@example.invalid", active: "profile", children: <AccountProfile email="fixture@example.invalid" /> });
  await act(async () => root.render(<QueryClientProvider client={client}>
    {shell}<MaxWorkspaceShell project={project} email="fixture@example.invalid" />
  </QueryClientProvider>));
  await act(async () => { await vi.waitFor(() => expect(container.querySelector("output")?.textContent).toContain("Saved version metadata")); });
  expect(container.textContent).not.toMatch(/Скачать|Экспорт|скачайте/i);
  expect(container.querySelector("a[download], a[href*='/download'], a[href*='/account/export']")).toBeNull();
  expect(container.querySelector("#account-email")?.getAttribute("value")).toBe("fixture@example.invalid");
  expect(container.textContent).toContain("Удалить аккаунт");
  expect(container.textContent).toContain("Опубликовать приложение");
  expect(container.querySelector("a[href='/max/test-project?panel=services']")).not.toBeNull();
  expect(!!container.querySelector("a[href='/admin/max']")).toBe(admin);
  expect(state.exportAccount).not.toHaveBeenCalled();
  expect(state.downloadProjectFiles).not.toHaveBeenCalled();
  expect(state.remove).not.toHaveBeenCalled();
});
it("legacy DownloadButton mounts no hidden actionable control or export request", async () => {
  await act(async () => root.render(<DownloadButton projectId="test-project" projectSlug="test-project" />));
  expect(container.childElementCount).toBe(0);
  expect(state.downloadProjectFiles).not.toHaveBeenCalled();
});
