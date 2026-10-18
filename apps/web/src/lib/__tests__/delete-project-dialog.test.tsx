import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DeleteProjectDialog } from "@/components/projects/DeleteProjectDialog";
import type { Project } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({ request: vi.fn(), wait: vi.fn(), success: vi.fn(), error: vi.fn() }));
vi.mock("@/lib/api/projects", () => ({ deleteProject: mocks.request, waitForProjectDeletion: mocks.wait }));
vi.mock("sonner", () => ({ toast: { success: mocks.success, error: mocks.error } }));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const project = { id: "own", name: "Своя проверка" } as Project;
let cleanup: (() => Promise<void>) | null = null;
afterEach(async () => {
  await cleanup?.();
  cleanup = null;
  vi.clearAllMocks();
  document.body.replaceChildren();
});

describe("one-click project deletion", () => {
  it("shows durable pending honestly and checks again without resending DELETE", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const close = vi.fn();
    cleanup = async () => { await act(async () => root.unmount()); client.clear(); };
    mocks.request.mockResolvedValue({ state: "deleting" });
    mocks.wait.mockResolvedValueOnce({ state: "deleting" }).mockResolvedValueOnce({ state: "deleted" });
    await act(async () => root.render(<QueryClientProvider client={client}>
      <DeleteProjectDialog project={project} open onOpenChange={close} />
    </QueryClientProvider>));
    const button = (label: string) => [...document.querySelectorAll<HTMLButtonElement>("button")]
      .find((element) => element.textContent === label)!;
    await act(async () => button("Удалить навсегда").click());
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)); });
    expect(document.body.textContent).toContain("процесс продолжится");
    expect(button("Проверить удаление")).toBeDefined();
    expect(mocks.success).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
    await act(async () => button("Проверить удаление").click());
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)); });
    expect(mocks.request).toHaveBeenCalledTimes(1);
    expect(mocks.wait).toHaveBeenCalledTimes(2);
    expect(mocks.success).toHaveBeenCalledWith("Проект «Своя проверка» удалён");
    expect(close).toHaveBeenCalledWith(false);
  });
});
