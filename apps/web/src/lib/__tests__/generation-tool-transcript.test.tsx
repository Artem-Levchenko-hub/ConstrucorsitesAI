import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentTranscript } from "@/components/workspace/AgentTranscript";
import { usePromptStream } from "@/hooks/usePromptStream";
import { getLatestGeneration } from "@/lib/api/messages";
import type { AgentStep, GenerationRun, Message, WsEvent } from "@/lib/api/types";

vi.mock("@/lib/api/messages", () => ({
  cancelGeneration: vi.fn(), getLatestGeneration: vi.fn(), sendPrompt: vi.fn(),
}));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn(), dismiss: vi.fn() } }));

class TestSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static instances: TestSocket[] = [];
  readyState = TestSocket.OPEN;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onopen: (() => void) | null = null;
  constructor() { TestSocket.instances.push(this); }
  send = vi.fn();
  close() { this.readyState = 3; }
  emit(event: WsEvent) { this.onmessage?.({ data: JSON.stringify(event) }); }
}

let client: QueryClient;
let root: Root;
let container: HTMLDivElement;
let seq: number;

function Harness({ completed = false }: { completed?: boolean }) {
  usePromptStream("project-1", "coffee");
  return <AgentTranscript projectId="project-1" messageId="message-1"
    streaming={!completed} generationStatus={completed ? "completed" : "running"} />;
}

async function emit(type: string, data: Record<string, unknown>) {
  seq += 1;
  await act(async () => {
    TestSocket.instances[0].emit({
      type: "generation.event",
      data: { event_id: `event-${seq}`, run_id: "run-1", seq, type,
        data: { message_id: "message-1", ...data } },
    });
    await vi.advanceTimersByTimeAsync(1);
  });
}

function steps() {
  return client.getQueryData<AgentStep[]>(["agent-steps", "project-1", "message-1"]) ?? [];
}

beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", TestSocket);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  TestSocket.instances = [];
  seq = 0;
  vi.mocked(getLatestGeneration).mockResolvedValue({
    id: "run-1", project_id: "project-1", assistant_message_id: "message-1",
    status: "running", response_mode: "build", created_at: "2026-10-12T12:47:25Z",
    started_at: "2026-10-12T12:47:27Z", finished_at: null,
  } as GenerationRun);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  client.setQueryData<Message[]>(["messages", "project-1"], [{
    id: "message-1", project_id: "project-1", role: "assistant", content: "Проверяю каталог",
    model_id: "model", snapshot_id: null, tokens_in: null, tokens_out: null,
    generation_status: "running", created_at: "2026-10-12T12:47:25Z",
  }]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>));
});

afterEach(async () => {
  await act(async () => root.unmount());
  client.clear(); container.remove();
  vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.clearAllMocks();
});

describe("tool liveness in the actual stream and transcript", () => {
  it("updates a call_id tool instead of appending heartbeat actions", async () => {
    await emit("agent.step", { step: 1, kind: "step", action: "done", tool: "done", ok: true });
    await emit("generation.phase", { phase: "final_build" });
    await emit("tool.started", { call_id: "build-1", phase: "final_build" });
    await emit("tool.heartbeat", { call_id: "build-1", phase: "final_build", log_bytes: 120 });
    await emit("tool.heartbeat", { call_id: "build-1", phase: "final_build", log_bytes: 240 });
    expect(steps().filter(step => step.operationId)).toHaveLength(1);
    expect(container.querySelectorAll("ol > li")).toHaveLength(3);
    expect(container.textContent).toContain("Собираю приложение");
    expect(container.textContent).not.toContain("Работаю над задачей");
    expect(container.textContent).toContain("1 шаг.");
    expect(container.textContent).toContain("Правки подготовлены");
    expect(container.textContent).not.toContain("Готово");
  });

  it("keeps an operation row in place and a failed result despite a late heartbeat", async () => {
    await emit("tool.started", { operation_id: "operation-1", phase: "final_build" });
    await emit("agent.step", { step: 2, action: "Читаю код", path: "src/app/page.tsx" });
    await emit("tool.finished", { operation_id: "operation-1", phase: "final_build", state: "failed" });
    await emit("tool.heartbeat", { operation_id: "operation-1", phase: "final_build", log_bytes: 250 });
    expect(steps()[0].operationId).toBe("operation-1");
    expect(steps()[0].ok).toBe(false);
    expect(container.textContent).toContain("Не получилось");
    expect(container.querySelectorAll("ol > li")).toHaveLength(2);
  });

  it("does not collapse distinct tool calls or count phase heartbeats as actions", async () => {
    await emit("tool.started", { operation_id: "op-a", phase: "runtime_probe" });
    await emit("tool.finished", { operation_id: "op-a", phase: "runtime_probe", state: "completed" });
    await emit("tool.started", { operation_id: "op-b", phase: "runtime_probe" });
    await emit("tool.finished", { operation_id: "op-b", phase: "runtime_probe", state: "completed" });
    await emit("generation.phase", { phase: "snapshot" });
    await emit("generation.phase", { phase: "snapshot" });
    expect(container.querySelectorAll("ol > li")).toHaveLength(3);
    expect(container.textContent).not.toContain("шаг.");
    expect(container.textContent).toContain("Сохраняю версию");
  });

  it("keeps a running tool visibly live even with later agent actions", async () => {
    await emit("tool.started", { operation_id: "op-a", phase: "final_build" });
    await emit("agent.step", { step: 1, action: "Читаю код", path: "src/app/page.tsx" });
    await emit("tool.heartbeat", { operation_id: "op-a", phase: "final_build" });
    expect(container.querySelector("ol > li > button")?.getAttribute("data-live")).toBe("true");
  });

  it("labels done as ready only after the durable generation completed", async () => {
    await emit("agent.step", { step: 1, action: "done", tool: "done", ok: true });
    expect(container.textContent).toContain("Правки подготовлены");
    expect(container.querySelector("ol > li")?.getAttribute("title")).toBe("Правки подготовлены");
    await act(async () => {
      client.setQueryData<GenerationRun>(["generation", "project-1"], previous => ({
        ...previous!, status: "completed", finished_at: "2026-10-12T12:56:19Z",
      }));
      root.render(<QueryClientProvider client={client}><Harness completed /></QueryClientProvider>);
      await vi.advanceTimersByTimeAsync(1);
    });
    const button = container.querySelector("button")!;
    await act(async () => button.click());
    expect(container.textContent).toContain("Проверка результата не подтверждена");
    expect(container.querySelector("ol > li")?.getAttribute("title")).toBe("Правки подготовлены");
    expect(container.textContent).not.toContain("Изменения готовы");
    await act(async () => {
      client.setQueryData<GenerationRun>(["generation", "project-1"], previous => ({
        ...previous!, acceptance: { run_id: "run-1", assistant_message_id: "message-1",
          snapshot_id: "accepted-snapshot", accepted_at: null, current_request_complete: true },
      }));
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(container.textContent).toContain("Готово");
    expect(container.textContent).toContain("Изменения готовы");
    expect(container.querySelector("ol > li")?.getAttribute("title")).toBe("Готово");
  });

  it("renders the real complete phase in Russian without claiming durable acceptance", async () => {
    await emit("generation.phase", { phase: "complete" });
    expect(container.textContent).toContain("Завершаю проверку результата");
    expect(container.textContent).not.toContain("complete");
    expect(container.textContent).not.toContain("Изменения готовы");
    expect(container.textContent).not.toContain("Готово");
  });
});
