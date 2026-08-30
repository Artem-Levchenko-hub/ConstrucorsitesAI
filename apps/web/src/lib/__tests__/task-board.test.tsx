import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { TaskBoard } from "@/components/task-board/TaskBoard";
import type {
  CreateTaskPayload,
  TaskBoardApi,
  TaskBoardAttachment,
  TaskBoardTask,
  UpdateTaskPayload,
} from "@/lib/api/task-board";

function task(overrides: Partial<TaskBoardTask> = {}): TaskBoardTask {
  return {
    id: "task-1",
    title: "Сверить релиз",
    description: "Проверить основной сценарий перед публикацией.",
    status: "backlog",
    assignee: "roman",
    priority: "high",
    position: 0,
    attachments: [],
    created_at: "2026-08-30T08:00:00Z",
    updated_at: "2026-08-30T08:00:00Z",
    ...overrides,
  };
}

function memoryApi(initial: TaskBoardTask[] = []): TaskBoardApi {
  let tasks = initial.map((item) => ({ ...item }));
  return {
    async listTasks() {
      return tasks.map((item) => ({ ...item }));
    },
    async createTask(payload: CreateTaskPayload) {
      const created = task({
        id: `task-${tasks.length + 1}`,
        ...payload,
        description: payload.description ?? "",
        status: payload.status ?? "backlog",
        priority: payload.priority ?? "medium",
        position: tasks.filter(
          (item) => item.status === (payload.status ?? "backlog"),
        ).length,
      });
      tasks = [...tasks, created];
      return { ...created };
    },
    async updateTask(id: string, payload: UpdateTaskPayload) {
      const current = tasks.find((item) => item.id === id);
      if (!current) throw new Error("missing task");
      const updated = {
        ...current,
        ...payload,
        updated_at: "2026-08-30T09:00:00Z",
      };
      tasks = tasks.map((item) => (item.id === id ? updated : item));
      return { ...updated };
    },
    async deleteTask(id: string) {
      tasks = tasks.filter((item) => item.id !== id);
    },
    async uploadAttachment(id: string, file: File) {
      const attachment: TaskBoardAttachment = {
        id: `attachment-${file.name}`,
        filename: file.name,
        content_type: file.type || "application/octet-stream",
        size: file.size,
        created_at: "2026-08-30T10:00:00Z",
      };
      tasks = tasks.map((item) =>
        item.id === id
          ? { ...item, attachments: [...item.attachments, attachment] }
          : item,
      );
      return attachment;
    },
    async deleteAttachment(taskId: string, attachmentId: string) {
      tasks = tasks.map((item) =>
        item.id === taskId
          ? {
              ...item,
              attachments: item.attachments.filter(
                (attachment) => attachment.id !== attachmentId,
              ),
            }
          : item,
      );
    },
    attachmentDownloadUrl(taskId: string, attachmentId: string) {
      return `/api/task-board/tasks/${taskId}/attachments/${attachmentId}`;
    },
  };
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("team task board", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (
      globalThis as typeof globalThis & {
        IS_REACT_ACT_ENVIRONMENT: boolean;
      }
    ).IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.clear();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("shows the four team profiles and four workflow columns", async () => {
    await act(async () => {
      root.render(<TaskBoard api={memoryApi()} refreshIntervalMs={0} />);
    });
    await settle();

    expect(container.querySelectorAll("[data-member-id]")).toHaveLength(4);
    expect(container.textContent).toContain("Алексей");
    expect(container.textContent).toContain("Алексей jr.");
    expect(container.textContent).toContain("Артем");
    expect(container.textContent).toContain("Роман");
    expect(container.querySelectorAll("[data-board-column]")).toHaveLength(4);
    expect(container.textContent).toContain("Новые");
    expect(container.textContent).toContain("В работе");
    expect(container.textContent).toContain("На проверке");
    expect(container.textContent).toContain("Готово");
  });

  it("creates a task for the selected team member", async () => {
    await act(async () => {
      root.render(<TaskBoard api={memoryApi()} refreshIntervalMs={0} />);
    });
    await settle();

    const artem = container.querySelector<HTMLButtonElement>(
      "[data-member-id='artem']",
    );
    const add = container.querySelector<HTMLButtonElement>(
      "[data-action='new-task']",
    );
    act(() => artem?.click());
    act(() => add?.click());

    const title = container.querySelector<HTMLInputElement>("input[name='title']");
    expect(title).not.toBeNull();
    act(() => {
      if (!title) return;
      const valueSetter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set;
      valueSetter?.call(title, "Собрать обратную связь");
      title.dispatchEvent(new InputEvent("input", { bubbles: true }));
    });
    const form = container.querySelector<HTMLFormElement>("form[data-task-form]");
    await act(async () => {
      form?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await settle();

    const card = container.querySelector<HTMLElement>("[data-task-id='task-1']");
    expect(card?.textContent).toContain("Собрать обратную связь");
    expect(card?.textContent).toContain("Артем");
  });

  it("moves a dropped task into the destination column", async () => {
    await act(async () => {
      root.render(<TaskBoard api={memoryApi([task()])} refreshIntervalMs={0} />);
    });
    await settle();

    const card = container.querySelector<HTMLElement>("[data-task-id='task-1']");
    const review = container.querySelector<HTMLElement>(
      "[data-board-column='review']",
    );
    const data = new Map<string, string>();
    const dataTransfer = {
      effectAllowed: "move",
      dropEffect: "move",
      setData(type: string, value: string) {
        data.set(type, value);
      },
      getData(type: string) {
        return data.get(type) ?? "";
      },
    };
    const dragStart = new Event("dragstart", { bubbles: true });
    Object.defineProperty(dragStart, "dataTransfer", { value: dataTransfer });
    act(() => card?.dispatchEvent(dragStart));

    const drop = new Event("drop", { bubbles: true, cancelable: true });
    Object.defineProperty(drop, "dataTransfer", { value: dataTransfer });
    await act(async () => review?.dispatchEvent(drop));
    await settle();

    expect(
      review?.querySelector<HTMLElement>("[data-task-id='task-1']")?.textContent,
    ).toContain("Сверить релиз");
    expect(
      container.querySelector("[data-board-column='backlog'] [data-task-id='task-1']"),
    ).toBeNull();
  });

  it("does not let a stale background refresh undo a task move", async () => {
    const baseApi = memoryApi([task()]);
    let listCalls = 0;
    let resolveRefresh: ((tasks: TaskBoardTask[]) => void) | undefined;
    const api: TaskBoardApi = {
      ...baseApi,
      async listTasks() {
        listCalls += 1;
        if (listCalls === 1) return [task()];
        return new Promise<TaskBoardTask[]>((resolve) => {
          resolveRefresh = resolve;
        });
      },
    };

    await act(async () => {
      root.render(<TaskBoard api={api} refreshIntervalMs={1000} />);
    });
    await settle();
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 1020));
    });
    expect(resolveRefresh).toBeTypeOf("function");

    const card = container.querySelector<HTMLElement>("[data-task-id='task-1']");
    const review = container.querySelector<HTMLElement>(
      "[data-board-column='review']",
    );
    const data = new Map<string, string>();
    const dataTransfer = {
      effectAllowed: "move",
      dropEffect: "move",
      setData(type: string, value: string) {
        data.set(type, value);
      },
      getData(type: string) {
        return data.get(type) ?? "";
      },
    };
    const dragStart = new Event("dragstart", { bubbles: true });
    Object.defineProperty(dragStart, "dataTransfer", { value: dataTransfer });
    act(() => card?.dispatchEvent(dragStart));
    const drop = new Event("drop", { bubbles: true, cancelable: true });
    Object.defineProperty(drop, "dataTransfer", { value: dataTransfer });
    await act(async () => review?.dispatchEvent(drop));
    await settle();

    await act(async () => resolveRefresh?.([task({ status: "backlog" })]));
    await settle();

    expect(
      review?.querySelector<HTMLElement>("[data-task-id='task-1']")?.textContent,
    ).toContain("Сверить релиз");
  });

  it("locks a card while its mutation is still pending", async () => {
    const baseApi = memoryApi([task()]);
    let resolveUpdate: ((task: TaskBoardTask) => void) | undefined;
    const api: TaskBoardApi = {
      ...baseApi,
      async updateTask() {
        return new Promise<TaskBoardTask>((resolve) => {
          resolveUpdate = resolve;
        });
      },
    };

    await act(async () => {
      root.render(<TaskBoard api={api} refreshIntervalMs={0} />);
    });
    await settle();

    const card = container.querySelector<HTMLElement>("[data-task-id='task-1']");
    const review = container.querySelector<HTMLElement>(
      "[data-board-column='review']",
    );
    const data = new Map<string, string>();
    const dataTransfer = {
      effectAllowed: "move",
      dropEffect: "move",
      setData(type: string, value: string) {
        data.set(type, value);
      },
      getData(type: string) {
        return data.get(type) ?? "";
      },
    };
    const dragStart = new Event("dragstart", { bubbles: true });
    Object.defineProperty(dragStart, "dataTransfer", { value: dataTransfer });
    act(() => card?.dispatchEvent(dragStart));
    const drop = new Event("drop", { bubbles: true, cancelable: true });
    Object.defineProperty(drop, "dataTransfer", { value: dataTransfer });
    await act(async () => {
      review?.dispatchEvent(drop);
      await Promise.resolve();
    });

    const movingCard = review?.querySelector<HTMLElement>("[data-task-id='task-1']");
    expect(movingCard?.getAttribute("draggable")).toBe("false");
    expect(
      movingCard?.querySelector<HTMLButtonElement>("button[aria-label^='Редактировать']")
        ?.disabled,
    ).toBe(true);

    await act(async () =>
      resolveUpdate?.(task({ status: "review", position: 0 })),
    );
    await settle();
  });

  it("shows an HTML attachment as a downloadable file", async () => {
    const attachment: TaskBoardAttachment = {
      id: "attachment-html",
      filename: "landing.html",
      content_type: "text/html",
      size: 1536,
      created_at: "2026-08-30T10:00:00Z",
    };
    await act(async () => {
      root.render(
        <TaskBoard
          api={memoryApi([task({ attachments: [attachment] })])}
          refreshIntervalMs={0}
        />,
      );
    });
    await settle();

    const link = container.querySelector<HTMLAnchorElement>(
      "[data-attachment-id='attachment-html']",
    );
    expect(link?.textContent).toContain("landing.html");
    expect(link?.textContent).toContain("1,5 КБ");
    expect(link?.getAttribute("download")).toBe("landing.html");
    expect(link?.getAttribute("href")).toContain("attachment-html");
  });

  it("uploads an HTML file from the task card", async () => {
    const api = memoryApi([task()]);
    await act(async () => {
      root.render(<TaskBoard api={api} refreshIntervalMs={0} />);
    });
    await settle();

    const input = container.querySelector<HTMLInputElement>(
      "input[data-attachment-input='task-1']",
    );
    const file = new File(["<!doctype html><title>Demo</title>"], "demo.html", {
      type: "text/html",
    });
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    await act(async () => {
      input?.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await settle();

    const attachment = container.querySelector<HTMLAnchorElement>(
      "[data-attachment-id='attachment-demo.html']",
    );
    expect(attachment?.textContent).toContain("demo.html");
  });
});
