import { act, type ComponentProps, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { MaxOwnerPreviewPage } from "@/components/max/MaxOwnerPreviewPage";
import type { Project } from "@/lib/api/types";

type PreviewProps = ComponentProps<typeof import("@/components/max/MaxLivePreview").MaxLivePreview>;
const observed = vi.hoisted(() => ({ props: null as PreviewProps | null, mounts: 0, history: vi.fn() }));
vi.mock("@/lib/use-max-preview-history", () => ({ useMaxPreviewHistory: observed.history }));
vi.mock("@/components/max/MaxLivePreview", () => ({ MaxLivePreview: (props: PreviewProps) => {
  observed.props = props;
  useEffect(() => { observed.mounts++; }, []);
  return <input defaultValue="Сохранённая форма" data-testid="preview-frame-probe" />;
} }));

it("keeps the live parent shell mounted across history updates, with a return action and no unsupported actions", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const project = { id: "p1", name: "Приложение" } as Project;
  observed.history.mockReturnValue({ currentSnapshotId: "head-1", versions: [], historyCurrent: true,
    history: { isError: false, hasNextPage: false, isFetchingNextPage: false, isPending: false } });
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  try {
    await act(async () => root.render(<MaxOwnerPreviewPage project={project} />));
    const frame = container.querySelector("input");
    expect(container.querySelector('a[href="/max/p1"]')?.textContent).toContain("редактор");
    expect(observed.history).toHaveBeenCalledWith(project);
    expect(observed.props).toMatchObject({ project, currentSnapshotId: "head-1", historyCurrent: true,
      selectedVersionId: null, showOpenSeparately: false });
    expect(observed.props?.onRestoreSnapshot).toBeUndefined();
    expect(observed.props?.onPrepareRestoration).toBeUndefined();
    observed.history.mockReturnValue({ ...observed.history.mock.results.at(-1)!.value,
      currentSnapshotId: "head-2", historyCurrent: false });
    await act(async () => root.render(<MaxOwnerPreviewPage project={project} />));
    expect(container.querySelector("input")).toBe(frame);
    expect(observed.mounts).toBe(1);
    expect(observed.props?.currentSnapshotId).toBe("head-2");
  } finally { await act(async () => root.unmount()); container.remove(); vi.resetAllMocks(); }
});
