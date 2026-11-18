import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, expect, it, vi } from "vitest";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import { buildMaxProjectPrompt } from "@/lib/max-brief";
import type { AgentStep, Message } from "@/lib/api/types";

const api = vi.hoisted(() => ({ history: vi.fn(), latest: vi.fn(), submit: vi.fn(), ordinaryAction: vi.fn() }));
vi.mock("@/lib/api/messages", () => ({ listMessages: api.history, getLatestGeneration: api.latest }));
vi.mock("@/lib/api/max-studio", () => ({ getMaxProjectConfig: async () => ({ config_version: 1 }) }));
vi.mock("@/hooks/useOwnerChatQuota", () => ({ useOwnerChatQuota: () => ({ exhausted: false, limited: false }) }));
vi.mock("@/hooks/usePromptStream", () => ({ usePromptStream: () => ({ submit: api.submit, cancel: vi.fn(), pendingPrompt: null }) }));
vi.mock("@/components/max/MaxChatComposer", () => ({ MaxChatComposer: () => <button type="button" onClick={api.ordinaryAction}>Обычное действие</button> }));

beforeEach(() => { vi.clearAllMocks(); });

async function mountChat(initialHeight = 1000) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const frames = new Map<number, FrameRequestCallback>();
  let frameId = 0;
  const observers: { target?: Element; notify: () => void }[] = [];
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.set(++frameId, callback); return frameId;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  vi.stubGlobal("ResizeObserver", class {
    notify: () => void;
    target?: Element;
    constructor(callback: ResizeObserverCallback) {
      this.notify = () => callback([], this as unknown as ResizeObserver); observers.push(this);
    }
    observe(target: Element) { this.target = target; }
    disconnect() {}
  });
  const createdAt = new Date().toISOString();
  const owner: Message = {
    id: "owner-message", project_id: "p", role: "user",
    content: buildMaxProjectPrompt({ name: "Читаю описание", appType: "custom", idea: "Один понятный экран.", audience: "", primaryAction: "", features: [], brandColors: "" }),
    model_id: null, snapshot_id: null, tokens_in: null, tokens_out: null, created_at: createdAt,
  };
  const assistant: Message = { ...owner, id: "assistant", role: "assistant", content: "", generation_status: "running" };
  api.history.mockResolvedValue([owner, assistant]);
  api.latest.mockResolvedValue({ id: "run", project_id: "p", assistant_message_id: assistant.id,
    status: "running", response_mode: "build", created_at: createdAt, started_at: createdAt });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  await act(async () => root.render(<QueryClientProvider client={client}><ChatPanel projectId="p" projectSlug="p" /></QueryClientProvider>));
  await act(async () => { await vi.waitFor(() => expect(container.querySelector("summary")).not.toBeNull()); });
  const pane = container.querySelector<HTMLDivElement>(".overflow-y-auto")!;
  const observer = observers.find(item => item.target === pane.firstElementChild)!;
  let height = initialHeight;
  let top = 0;
  Object.defineProperties(pane, {
    scrollHeight: { get: () => height }, clientHeight: { value: 400 },
    scrollTop: { get: () => top, set: (value: number) => { top = Math.max(0, Math.min(value, Math.max(0, height - 400))); } },
  });
  const flushFrame = () => act(() => {
    const pending = [...frames.values()]; frames.clear();
    pending.forEach(callback => callback(performance.now()));
  });
  flushFrame();
  return {
    pane, container, client, frames, flushFrame,
    resize(value: number) { height = value; observer.notify(); },
    async addAgentStep(seq: number) {
      await act(async () => {
        client.setQueryData<AgentStep[]>(["agent-steps", "p", assistant.id], steps => [
          ...(steps ?? []), { step: seq, seq, runId: "run", kind: "step", tool: "read_file", action: "Читаю файл", path: `src/page${seq}.tsx` },
        ]);
      });
      await act(async () => { await vi.waitFor(() => expect(container.querySelectorAll("[data-agent-transcript] li")).toHaveLength(seq)); });
    },
    jump: () => [...container.querySelectorAll<HTMLButtonElement>("button")].find(button => button.textContent?.includes("К последнему сообщению")),
    async dispose() { await act(async () => root.unmount()); client.clear(); container.remove(); vi.unstubAllGlobals(); },
  };
}

it.each([
  { activation: "pointer", height: 1000 },
  { activation: "Enter", height: 1000 },
  { activation: "Space", height: 300 },
])("retains native disclosure reading with $activation, even with a queued follow frame (height=$height)", async ({ activation, height }) => {
  const ui = await mountChat(height);
  try {
    const initialTop = Math.max(0, height - 400);
    expect(ui.pane.scrollTop).toBe(initialTop);
    const messages = ui.client.getQueryData(["messages", "p"]);
    ui.resize(height); // Already coalesced before the explicit reading intent.
    const summary = [...ui.container.querySelectorAll("summary")].find(item => item.textContent === "Полный исходный запрос")!;
    act(() => {
      summary.focus();
      if (activation === "pointer") summary.click();
      else {
        // Native Enter/Space activation dispatches a detail=0 click. jsdom does
        // not synthesize that default keyboard action, so dispatch it explicitly.
        summary.dispatchEvent(new KeyboardEvent("keydown", { key: activation === "Space" ? " " : "Enter", bubbles: true }));
        summary.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 0 }));
      }
    });
    expect(summary.closest("details")!.open).toBe(true);
    expect(document.activeElement).toBe(summary);
    ui.resize(1800); ui.flushFrame();
    expect(ui.pane.scrollTop).toBe(initialTop);
    expect(ui.jump()).toBeDefined();
    await ui.addAgentStep(1); ui.resize(2100); ui.flushFrame();
    expect(ui.pane.scrollTop).toBe(initialTop);
    expect(ui.client.getQueryData(["messages", "p"])).toBe(messages);
    act(() => ui.jump()!.click()); expect(ui.pane.scrollTop).toBe(1700);
    await ui.addAgentStep(2); ui.resize(2300); ui.flushFrame();
    expect(ui.pane.scrollTop).toBe(1900);
    expect(api.submit).not.toHaveBeenCalled();
  } finally { await ui.dispose(); }
});

it("leaves unrelated chat actions and ordinary growth following alone", async () => {
  const ui = await mountChat();
  try {
    const button = [...ui.container.querySelectorAll("button")].find(item => item.textContent === "Обычное действие")!;
    act(() => button.click()); expect(api.ordinaryAction).toHaveBeenCalledOnce();
    await ui.addAgentStep(1); ui.resize(1200); ui.flushFrame();
    expect(ui.pane.scrollTop).toBe(800); expect(ui.jump()).toBeUndefined();
    expect(api.submit).not.toHaveBeenCalled();
  } finally { await ui.dispose(); }
});

it.each(["pointer", "Enter", "Space"])("keeps native generation details reading with %s while real cache updates continue", async activation => {
  const ui = await mountChat();
  try {
    await act(async () => { await vi.waitFor(() => expect(ui.container.querySelector("[data-agent-transcript] summary")).not.toBeNull()); });
    const initialTop = ui.pane.scrollTop;
    const summary = ui.container.querySelector<HTMLElement>("[data-agent-transcript] summary")!;
    expect(summary.textContent).toBe("Подробности работы");
    expect(summary.closest("details")!.open).toBe(false);
    ui.resize(1000); // A queued follow must not win the user's disclosure click.
    await act(async () => {
      summary.focus();
      if (activation === "pointer") summary.click();
      else {
        summary.dispatchEvent(new KeyboardEvent("keydown", { key: activation === "Space" ? " " : "Enter", bubbles: true }));
        summary.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 0 }));
      }
    });
    expect(summary.closest("details")!.open).toBe(true);
    expect(document.activeElement).toBe(summary);
    ui.resize(1800); ui.flushFrame();
    expect(ui.pane.scrollTop).toBe(initialTop);
    await ui.addAgentStep(1); ui.resize(2100); ui.flushFrame();
    expect(ui.pane.scrollTop).toBe(initialTop);
    expect(ui.jump()).toBeDefined();
    expect(api.submit).not.toHaveBeenCalled();
  } finally { await ui.dispose(); }
});
