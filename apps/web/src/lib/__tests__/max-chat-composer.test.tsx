import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MaxChatComposer } from "@/components/max/MaxChatComposer";

const response = {
  version: "2", project_id: "app-a", current_snapshot_id: "version-a",
  analysis_snapshot_id: "version-a", archetype: "fitness-health", source: "model",
  items: [{ id: "meal-repeat", kind: "improvement", title: "Повторить завтрак",
    benefit: "Не вводить состав привычного завтрака каждый день.",
    prompt: "Добавь повтор завтрака из дневника питания с редактированием порций." }],
};

describe("MAX chat suggestions and draft", () => {
  let root: Root;
  let container: HTMLDivElement;
  let client: QueryClient;
  let requests: string[];
  let sent: string[];
  let fail: boolean;
  const render = (snapshotId: string | null = "version-a", isStreaming = false) => {
    act(() => root.render(createElement(QueryClientProvider, { client },
      createElement(MaxChatComposer, {
        projectId: "app-a", snapshotId, isStreaming, pendingPrompt: null,
        onSubmit: async (text: string) => { sent.push(text); return true; },
        onCancel: () => {}, onCancelPending: () => {},
      }),
    )));
  };
  const button = (text: string) => Array.from(container.querySelectorAll("button"))
    .find((item) => item.textContent?.includes(text))!;
  const settleQueries = async () => {
    await vi.waitFor(async () => {
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
      expect(client.isFetching()).toBe(0);
      expect(container.querySelector(".max-chat-advice-loading")).toBeNull();
    }, { timeout: 2000 });
  };
  const click = async (element: HTMLElement) => {
    await act(async () => { element.click(); });
    await settleQueries();
  };
  beforeEach(() => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    container = document.createElement("div"); document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    requests = []; sent = []; fail = false;
    vi.stubGlobal("fetch", async (url: string) => {
      requests.push(url);
      return new Response(JSON.stringify(fail
        ? { error: { code: "advice_unavailable", message: "Unavailable" } }
        : response), { status: fail ? 503 : 200, headers: { "content-type": "application/json" } });
    });
  });
  afterEach(() => {
    act(() => root.unmount()); client.clear(); container.remove(); vi.unstubAllGlobals();
  });

  it("loads only on explicit open and inserts an editable draft without submitting", async () => {
    render();
    expect(button("Подсказки")).toBeDefined();
    expect(requests).toEqual([]);
    await click(button("Подсказки"));
    expect(container.textContent).toContain("Повторить завтрак");
    expect(requests).toHaveLength(1);
    await click(button("Вставить в чат"));
    const input = container.querySelector("textarea")!;
    expect(input.value).toBe(response.items[0].prompt);
    expect(document.activeElement).toBe(input);
    expect(sent).toEqual([]);
    expect(button("Подсказки").getAttribute("aria-expanded")).toBe("false");
    await click(button("Отправить"));
    expect(sent).toEqual([response.items[0].prompt]);
  });

  it("appends a suggestion without losing the user's existing draft", async () => {
    render();
    const input = container.querySelector("textarea")!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, "Сохрани мои цвета");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await click(button("Подсказки")); await click(button("Вставить в чат"));
    expect(input.value).toBe(`Сохрани мои цвета\n\n${response.items[0].prompt}`);
    expect(sent).toEqual([]);
  });

  it("shows a retry state without canned suggestions when AI is unavailable", async () => {
    fail = true; render(); await click(button("Подсказки"));
    expect(container.querySelector("[role=alert]")).not.toBeNull();
    expect(container.querySelectorAll("[data-advice-id]")).toHaveLength(0);
    fail = false; await click(button("Повторить"));
    expect(container.textContent).toContain("Повторить завтрак");
  });

  it("does not analyze an empty app or an active build", async () => {
    render(null); await click(button("Подсказки"));
    expect(container.textContent).toContain("первую версию");
    expect(requests).toEqual([]);
    render("version-a", true);
    expect(container.textContent).toContain("завершения");
    expect(requests).toEqual([]);
  });

  it("does not insert stale suggestions after a snapshot changes", async () => {
    render(); await click(button("Подсказки"));
    render("version-b");
    await settleQueries();
    expect(container.querySelectorAll("[data-advice-id]")).toHaveLength(0);
    expect(sent).toEqual([]);
  });

  it("uses the restored version after the workspace HEAD changes", async () => {
    render(); await click(button("Подсказки"));
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ ...response,
      current_snapshot_id: "restored-version", analysis_snapshot_id: "restored-version",
      items: [{ ...response.items[0], title: "Идея для восстановленной версии" }],
    }), { headers: { "content-type": "application/json" } }));
    render("restored-version");
    await settleQueries();
    expect(container.textContent).toContain("Идея для восстановленной версии");
    expect(container.textContent).not.toContain("Повторить завтрак");
  });

  it("closes with Escape and returns focus to the hints button", async () => {
    render(); await click(button("Подсказки"));
    act(() => { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
    expect(button("Подсказки").getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(button("Подсказки"));
  });
});
