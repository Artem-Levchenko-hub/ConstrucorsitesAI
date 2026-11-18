import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import type { AgentStep, Message } from "@/lib/api/types";

const api = vi.hoisted(() => ({ history: vi.fn(), latest: vi.fn(), submit: vi.fn() }));
vi.mock("@/lib/api/messages", () => ({ listMessages: api.history, getLatestGeneration: api.latest }));
vi.mock("@/lib/api/max-studio", () => ({ getMaxProjectConfig: async () => ({ config_version: 1 }) }));
vi.mock("@/hooks/useOwnerChatQuota", () => ({ useOwnerChatQuota: () => ({ exhausted: false, limited: false }) }));
vi.mock("@/hooks/usePromptStream", () => ({ usePromptStream: () => ({ submit: api.submit, cancel: vi.fn(), pendingPrompt: null }) }));
vi.mock("@/components/max/MaxChatComposer", () => ({ MaxChatComposer: () => <div>Поле запроса</div> }));

it("follows an AgentTranscript cache-only update while preserving history and the jump-to-latest action", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const frames = new Map<number, FrameRequestCallback>();
  let frameId = 0;
  const observers: { target?: Element; notify: () => void; disconnect: ReturnType<typeof vi.fn> }[] = [];
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.set(++frameId, callback); return frameId;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  vi.stubGlobal("ResizeObserver", class {
    notify: () => void;
    target?: Element;
    disconnect = vi.fn();
    constructor(callback: ResizeObserverCallback) {
      this.notify = () => callback([], this as unknown as ResizeObserver); observers.push(this);
    }
    observe(target: Element) { this.target = target; }
  });
  const flushFrame = () => act(() => {
    const pending = [...frames.values()]; frames.clear();
    pending.forEach(callback => callback(performance.now()));
  });
  const message: Message = {
    id: "assistant", project_id: "p", role: "assistant", content: "",
    model_id: "model", snapshot_id: null, tokens_in: null, tokens_out: null,
    generation_status: "running", created_at: new Date().toISOString(),
  };
  api.history.mockResolvedValue([message]);
  api.latest.mockResolvedValue({ id: "run", project_id: "p", assistant_message_id: message.id,
    status: "running", response_mode: "build", created_at: message.created_at, started_at: message.created_at });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><ChatPanel projectId="p" projectSlug="p" /></QueryClientProvider>));
    await act(async () => { await vi.waitFor(() => expect(client.getQueryData(["messages", "p"])).toEqual([message])); });
    const pane = container.querySelector<HTMLDivElement>(".overflow-y-auto")!;
    const contentObserver = observers.find(observer => observer.target === pane.firstElementChild)!;
    expect(contentObserver).toBeDefined();
    let height = 1000;
    Object.defineProperties(pane, { scrollHeight: { get: () => height }, clientHeight: { value: 400 } });
    flushFrame(); expect(pane.scrollTop).toBe(1000);
    const originalMessages = client.getQueryData(["messages", "p"]);
    const addStep = async (seq: number) => {
      await act(async () => {
        client.setQueryData<AgentStep[]>(["agent-steps", "p", message.id], steps => [
          ...(steps ?? []), { step: seq, seq, runId: "run", kind: "step", tool: "read_file", action: "Читаю файл", path: `src/page${seq}.tsx` },
        ]);
      });
      await act(async () => { await vi.waitFor(() => expect(container.querySelectorAll("[data-agent-transcript] li")).toHaveLength(seq)); });
    };
    await addStep(1);
    expect(client.getQueryData(["messages", "p"])).toBe(originalMessages);
    height = 1200; contentObserver.notify(); flushFrame();
    expect(pane.scrollTop).toBe(1200);
    act(() => { pane.scrollTop = 100; pane.dispatchEvent(new Event("scroll")); });
    await addStep(2); height = 1400; contentObserver.notify(); flushFrame();
    expect(pane.scrollTop).toBe(100);
    const jump = [...container.querySelectorAll("button")].find(button => button.textContent?.includes("К последнему сообщению"));
    expect(jump).toBeDefined(); act(() => jump!.click());
    expect(pane.scrollTop).toBe(1400);
    expect(api.history).toHaveBeenCalledOnce(); expect(api.submit).not.toHaveBeenCalled();
  } finally {
    await act(async () => root.unmount()); client.clear(); container.remove(); vi.unstubAllGlobals();
  }
});
