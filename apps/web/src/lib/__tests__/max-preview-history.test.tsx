import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useMaxPreviewHistory } from "@/lib/use-max-preview-history";
import type { Project, ProjectVersion, ProjectVersionPage, Snapshot } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({ list: vi.fn(), snapshots: vi.fn() }));
vi.mock("@/lib/api/snapshots", () => ({ listProjectVersions: mocks.list, listSnapshots: mocks.snapshots }));
const project = { id: "p1", current_snapshot_id: "head-1" } as Project;
let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
let observed: ReturnType<typeof useMaxPreviewHistory>;
function Probe() {
  const history = useMaxPreviewHistory(project);
  useEffect(() => { observed = history; }, [history]);
  return <output data-current={String(history.historyCurrent)}>{history.versions.map(v => v.id).join(",")}</output>;
}
const page = (ids: string[]): ProjectVersionPage => ({ versions: ids.map(id => ({ id } as ProjectVersion)), next_cursor: 20 });
const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(10); });
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.useFakeTimers();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  client.setQueryData(["snapshots", project.id], [{ id: "head-1" } as Snapshot]);
  mocks.snapshots.mockImplementation(async () => client.getQueryData(["snapshots", project.id]));
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); client.clear(); container.remove(); vi.useRealTimers(); vi.resetAllMocks(); });
async function render() {
  await act(async () => root.render(<QueryClientProvider client={client}><Probe /></QueryClientProvider>));
  await flush();
}

it("uses the current-build policy key and deduplicates history without losing request provenance", async () => {
  let resolve!: (value: ProjectVersionPage) => void;
  mocks.list.mockImplementationOnce(() => new Promise<ProjectVersionPage>(done => { resolve = done; }));
  await render();
  expect(client.getQueryState(["project-versions", project.id, "current-build"])).toBeDefined();
  await act(async () => client.setQueryData(["snapshots", project.id], [{ id: "head-2" } as Snapshot]));
  await flush();
  await act(async () => resolve(page(["v1", "v1", "v2"])));
  await flush();
  expect(observed.currentSnapshotId).toBe("head-2");
  expect(observed.versions.map(v => v.id)).toEqual(["v1", "v2"]);
  expect(observed.history.data?.pages[0].snapshotIdAtRequest).toBe("head-1");
  expect(observed.historyCurrent).toBe(false);
  mocks.list.mockResolvedValue(page(["fresh"]));
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  expect(observed.historyCurrent).toBe(true);
  expect(observed.history.data?.pages).toHaveLength(1);
  expect(observed.history.data?.pages[0].snapshotIdAtRequest).toBe("head-2");
});

it("invalidates history when the cached HEAD changes and preserves the single-page policy", async () => {
  mocks.list.mockResolvedValue(page(["current"]));
  await render();
  const calls = mocks.list.mock.calls.length;
  await act(async () => client.setQueryData(["snapshots", project.id], [{ id: "head-2" } as Snapshot]));
  await flush();
  expect(mocks.list).toHaveBeenCalledTimes(calls + 1);
  expect(observed.historyCurrent).toBe(true);
  await act(async () => { await observed.history.fetchNextPage(); });
  await flush();
  expect(mocks.list.mock.calls.at(-1)?.[1]).toBe(20);
  expect(observed.history.data?.pages).toHaveLength(1);
});
