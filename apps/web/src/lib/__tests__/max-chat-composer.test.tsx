import { act, createElement, createRef, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MaxChatComposer } from "@/components/max/MaxChatComposer";
import { PromptInput, type PromptInputHandle } from "@/components/workspace/PromptInput";

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
  const originalHref = window.location.href;
  const draftRef = createRef<PromptInputHandle>();
  const render = (snapshotId: string | null = "version-a", isStreaming = false, overrides: Partial<ComponentProps<typeof MaxChatComposer>> = {}) => {
    act(() => root.render(createElement(QueryClientProvider, { client },
      createElement(MaxChatComposer, {
        projectId: "app-a", snapshotId, isStreaming, pendingPrompt: null,
        draftRef,
        onSubmit: async (text: string) => { sent.push(text); return true; },
        onCancel: () => {}, onCancelPending: () => {},
        ...overrides,
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
  const typeDraft = (value: string) => {
    const input = container.querySelector("textarea")!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    return input;
  };
  const routeBlocked = (href: string, wrapperClass = "max-editor-header") => {
    const wrapper = document.createElement("div");
    wrapper.className = wrapperClass;
    const link = document.createElement("a"); link.href = href;
    wrapper.append(link); document.body.append(wrapper);
    let blocked = false;
    link.addEventListener("click", event => { blocked = event.defaultPrevented; event.preventDefault(); }, { once: true });
    act(() => link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })));
    wrapper.remove();
    return blocked;
  };
  beforeEach(() => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    window.history.replaceState(null, "", "/max/app-a");
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
    act(() => root.unmount()); client.clear(); container.remove();
    window.history.replaceState(null, "", originalHref);
    vi.unstubAllGlobals(); vi.restoreAllMocks();
  });

  it("keeps a cancelled draft when leaving the editor and lets same-page controls operate", () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render();
    expect(routeBlocked("/max/app-a/manage")).toBe(false);
    const input = typeDraft("Мои правки");
    expect(routeBlocked("/max/app-a?data=details")).toBe(false);
    expect(routeBlocked("/max/app-a/manage", "")).toBe(false);
    expect(confirm).not.toHaveBeenCalled();
    expect(routeBlocked("/max/app-a/manage")).toBe(true);
    expect(input.value).toBe("Мои правки");
    confirm.mockReturnValue(true);
    expect(routeBlocked("/max/app-a/integrations")).toBe(false);
  });

  it("guards the editor's portaled project navigation without guarding other dialogs", () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render();
    const input = typeDraft("Черновик для MAX");
    expect(routeBlocked("/max/app-a/publish", "max-editor-modal max-editor-modal-preview")).toBe(false);
    expect(confirm).not.toHaveBeenCalled();
    expect(routeBlocked("/max/app-a/publish", "max-editor-modal max-editor-modal-navigation")).toBe(true);
    expect(confirm).toHaveBeenCalledOnce();
    expect(input.value).toBe("Черновик для MAX");
    confirm.mockReturnValue(true);
    expect(routeBlocked("/max/app-a/integrations", "max-editor-modal max-editor-modal-navigation")).toBe(false);
  });

  it("protects a font-only draft and releases navigation after removal", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    vi.stubGlobal("fetch", async () => Response.json({ id: "font-1", family: "Onest", filename: "brand.woff2", sha256: "a".repeat(64), size_bytes: 4, weight: 400, style: "normal", source: "upload", license: null }));
    render();
    await click(container.querySelector<HTMLButtonElement>('[aria-label="Прикрепить шрифт"]')!);
    const picker = container.querySelector<HTMLInputElement>('[aria-label="Выбрать файл шрифта"]')!;
    Object.defineProperty(picker, "files", { configurable: true, value: [new File(["font"], "brand.woff2")] });
    await act(async () => picker.dispatchEvent(new Event("change", { bubbles: true })));
    expect(container.querySelector("textarea")?.value).toBe("");
    expect(container.querySelector('[aria-label="Шрифты к запросу"]')?.textContent).toContain("Onest");
    expect(routeBlocked("/max/app-a/publish")).toBe(true);
    expect(confirm).toHaveBeenCalledOnce();
    await click(container.querySelector<HTMLButtonElement>('[aria-label="Удалить шрифт Onest"]')!);
    expect(routeBlocked("/max/app-a/publish")).toBe(false);
  });

  it("holds route exits during submission or a queued prompt and releases after acceptance", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    let accept!: (value: boolean) => void;
    const onSubmit = vi.fn(() => new Promise<boolean>(resolve => { accept = resolve; }));
    render("version-a", false, { onSubmit });
    const input = typeDraft("Отправляю изменение");
    act(() => button("Отправить").click());
    expect(onSubmit).toHaveBeenCalledOnce();
    expect(routeBlocked("/max/app-a/manage")).toBe(true);
    expect(confirm).not.toHaveBeenCalled();
    await act(async () => accept(true));
    expect(input.value).toBe("");
    expect(routeBlocked("/max/app-a/manage")).toBe(false);
    render("version-a", true, { pendingPrompt: "Следующий запрос" });
    expect(routeBlocked("/max/app-a/publish")).toBe(true);
    expect(confirm).not.toHaveBeenCalled();
  });

  it("leaves ordinary PromptInput route behavior unchanged", () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    act(() => root.render(createElement(PromptInput, {
      onSubmit: () => true, onCancel: () => {}, onCancelPending: () => {}, isStreaming: false, pendingPrompt: null,
    })));
    typeDraft("Другой workspace");
    expect(routeBlocked("/max/app-a/manage")).toBe(false);
    expect(confirm).not.toHaveBeenCalled();
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
  it("accepts a restoration draft through the external handle without sending or losing edits", () => {
    render();
    const input = container.querySelector("textarea")!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, "Мои уточнения");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    act(() => { expect(draftRef.current?.insertDraft("Keep current business data.")).toBe(true); });
    expect(input.value).toBe("Мои уточнения\n\nKeep current business data.");
    expect(document.activeElement).toBe(input);
    expect(sent).toEqual([]);
    expect(requests).toEqual([]);
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
    expect(container.textContent).toContain("Соберите приложение");
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
