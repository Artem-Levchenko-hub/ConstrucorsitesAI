import { QueryClient, QueryClientProvider, QueryObserver } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DeleteProjectDialog } from "@/components/projects/DeleteProjectDialog";
import type { ProjectDeletionResult } from "@/lib/api/projects";
import type { Project } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({ request: vi.fn(), success: vi.fn(), error: vi.fn() }));
vi.mock("@/lib/api/projects", () => ({ deleteProject: mocks.request }));
vi.mock("sonner", () => ({ toast: { success: mocks.success, error: mocks.error } }));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const project = { id: "own", name: "Своя проверка" } as Project;
const other = { id: "other", name: "Другое приложение" } as Project;
let cleanup: (() => Promise<void>) | null = null;
afterEach(async () => {
  await cleanup?.();
  cleanup = null;
  vi.clearAllMocks();
  document.body.replaceChildren();
});

async function mountDialog() {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  const close = vi.fn();
  client.setQueryData(["projects"], [project, other]);
  client.setQueryData(["projects", "owner"], [other, project]);
  cleanup = async () => { await act(async () => root.unmount()); client.clear(); };
  await act(async () => root.render(<QueryClientProvider client={client}>
    <DeleteProjectDialog project={project} open onOpenChange={close} />
  </QueryClientProvider>));
  return { client, close };
}

const button = (label: string) => [...document.querySelectorAll<HTMLButtonElement>("button")]
  .find((element) => element.textContent?.trim() === label)!;

describe("one-click project deletion", () => {
  it("removes the card on durable acceptance without waiting for physical cleanup", async () => {
    let accept!: (result: ProjectDeletionResult) => void;
    mocks.request.mockReturnValue(new Promise<ProjectDeletionResult>((resolve) => { accept = resolve; }));
    const { client, close } = await mountDialog();
    let replyWithOldList!: (list: Project[]) => void;
    const staleRead = client.fetchQuery({
      queryKey: ["projects", "owner"],
      queryFn: () => new Promise<Project[]>((resolve) => { replyWithOldList = resolve; }),
    }).catch(() => undefined);
    await act(async () => button("Удалить навсегда").click());
    await vi.waitFor(() => {
      expect(button("Удаление…").disabled).toBe(true);
      expect(button("Удаление…").querySelector("svg")).not.toBeNull();
    });
    expect(client.getQueryData(["projects"])).toEqual([project, other]);
    await act(async () => accept({ state: "deleting" }));
    await vi.waitFor(() => expect(close).toHaveBeenCalledWith(false));
    await act(async () => { replyWithOldList([project, other]); await staleRead; });

    expect(client.getQueryData(["projects"])).toEqual([other]);
    expect(client.getQueryData(["projects", "owner"])).toEqual([other]);
    expect(mocks.request).toHaveBeenCalledTimes(1);
    expect(mocks.success).toHaveBeenCalledWith("Проект «Своя проверка» убран из списка", {
      description: "Очистка продолжится в фоне.",
    });
  });

  it("keeps the project and lets the owner retry after a refused deletion", async () => {
    mocks.request.mockRejectedValue(new Error("Перед удалением проекта остановите активную сборку"));
    const { client, close } = await mountDialog();
    await act(async () => button("Удалить навсегда").click());
    await vi.waitFor(() => expect(button("Удалить навсегда").disabled).toBe(false));

    expect(client.getQueryData(["projects"])).toEqual([project, other]);
    expect(close).not.toHaveBeenCalled();
    expect(mocks.error).toHaveBeenCalledWith("Перед удалением проекта остановите активную сборку");
    expect(mocks.success).not.toHaveBeenCalled();
  });

  it("restarts an active list's first read if it was cancelled on acceptance", async () => {
    mocks.request.mockResolvedValue({ state: "deleting" });
    const { client } = await mountDialog();
    const fetchList = vi.fn<() => Promise<Project[]>>()
      .mockImplementationOnce(() => new Promise(() => {}))
      .mockResolvedValue([other]);
    const observer = new QueryObserver(client, {
      queryKey: ["projects", "another-surface"], queryFn: fetchList,
    });
    const unsubscribe = observer.subscribe(() => {});
    try {
      expect(observer.getCurrentResult().isPending).toBe(true);
      await act(async () => button("Удалить навсегда").click());
      await vi.waitFor(() => expect(observer.getCurrentResult().data).toEqual([other]));
      expect(fetchList).toHaveBeenCalledTimes(2);
    } finally {
      unsubscribe();
    }
  });
});
