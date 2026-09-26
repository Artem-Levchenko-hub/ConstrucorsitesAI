import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MaxStudioProjectCard } from "@/components/max/MaxStudioProjectCard";
import type { MaxReadiness, Project } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({ readiness: vi.fn() }));

vi.mock("@/lib/api/max-studio", () => ({
  getMaxReadiness: mocks.readiness,
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const studioCss = readFileSync(
  resolve(process.cwd(), "src/components/max/max-studio.css"),
  "utf8",
);

const baseProject: Project = {
  id: "coffee",
  owner_id: "owner",
  name: "Кофе рядом",
  slug: "coffee",
  template: "max_miniapp",
  current_snapshot_id: "snapshot",
  created_at: "2026-09-01T12:00:00Z",
  updated_at: "2026-09-01T12:00:00Z",
};

function readiness(doneIds: string[]): MaxReadiness {
  const ids = ["build", "legal", "bot", "publish", "max_url"];
  return {
    ready_to_launch: doneIds.length === ids.length,
    progress: Math.round((doneIds.length / ids.length) * 100),
    items: ids.map((id) => ({
      id,
      label: id,
      done: doneIds.includes(id),
      blocking: true,
      action: null,
    })),
  };
}

describe("Yleum project card", () => {
  let client: QueryClient;
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    document.head.replaceChildren();
    document.body.replaceChildren();
    const style = document.createElement("style");
    style.textContent = studioCss;
    document.head.append(style);
    container = document.createElement("div");
    container.dataset.maxStudio = "";
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    client.clear();
  });

  async function renderProject(project: Project) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <MaxStudioProjectCard project={project} />
        </QueryClientProvider>,
      );
    });
  }

  async function waitForStatus(kind: string) {
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.querySelector(`[data-project-status="${kind}"]`)).not.toBeNull();
      });
    });
    return container.querySelector<HTMLElement>(`[data-project-status="${kind}"]`)!;
  }

  it("says what is happening with the app instead of naming a stage", async () => {
    // Владелец читал имя этапа («Сборка приложения») и не понимал, идёт она,
    // её надо начать или она сломалась. В строке стоит законченная фраза о его
    // приложении, полоса пройденного пути и подсказка «почему это важно».
    const states: Array<{
      id: string;
      result: MaxReadiness | Error | Promise<never>;
      tone: string;
      title: string;
      filled: number | null;
    }> = [
      { id: "setup", result: readiness([]), tone: "build", title: "Приложение ещё не собрано", filled: 1 },
      { id: "input", result: readiness(["build"]), tone: "connect", title: "Собрано, остались документы", filled: 2 },
      { id: "ready", result: readiness(["build", "legal", "bot", "publish", "max_url"]), tone: "live", title: "Работает у пользователей", filled: 6 },
      { id: "failed", result: new Error("offline"), tone: "offline", title: "Не дозвонились до сервера", filled: null },
      { id: "pending", result: new Promise<never>(() => {}), tone: "checking", title: "Проверяем состояние", filled: null },
    ];

    mocks.readiness.mockImplementation((id: string) => {
      const state = states.find((candidate) => candidate.id === id)!;
      return state.result instanceof Error ? Promise.reject(state.result) : Promise.resolve(state.result);
    });

    for (const state of states) {
      await renderProject({ ...baseProject, id: state.id });
      const status = await waitForStatus(state.tone);
      expect(status.querySelector("strong")?.textContent, state.tone).toBe(state.title);
      expect(status.querySelector("small")?.textContent?.length ?? 0).toBeGreaterThan(20);
      const filled = status.querySelectorAll('.max-project-progress > span[data-done="true"]');
      if (state.filled === null) {
        // Пока состояние неизвестно, полосы нет: пустая полоса читалась бы
        // как «ничего не сделано».
        expect(status.querySelector(".max-project-progress"), state.tone).toBeNull();
      } else {
        expect(filled.length, state.tone).toBe(state.filled);
      }
    }
  });

  it("uses the current app preview with a stable fallback for absent, unsafe and broken images", async () => {
    mocks.readiness.mockResolvedValue(readiness([]));

    await renderProject({ ...baseProject, preview_url: "https://cdn.example.test/coffee.png" });
    const image = container.querySelector<HTMLImageElement>(".max-project-thumbnail")!;
    expect(image.getAttribute("src")).toBe("https://cdn.example.test/coffee.png");
    expect(image.getAttribute("alt")).toBe("Превью приложения «Кофе рядом»");
    expect(getComputedStyle(image).width).toBe("40px");
    expect(getComputedStyle(image).height).toBe("40px");

    await act(async () => image.dispatchEvent(new Event("error")));
    const brokenFallback = container.querySelector<HTMLElement>(".max-project-monogram")!;
    expect(brokenFallback.textContent).toBe("К");
    expect(getComputedStyle(brokenFallback).width).toBe("40px");
    expect(getComputedStyle(brokenFallback).height).toBe("40px");

    await renderProject({ ...baseProject, id: "unsafe", preview_url: "javascript:alert(1)" });
    expect(container.querySelector(".max-project-thumbnail")).toBeNull();
    expect(container.querySelector(".max-project-monogram")?.textContent).toBe("К");

    await renderProject({ ...baseProject, id: "absent", preview_url: null });
    expect(container.querySelector(".max-project-thumbnail")).toBeNull();
    expect(container.querySelector(".max-project-monogram")?.textContent).toBe("К");
  });
});
