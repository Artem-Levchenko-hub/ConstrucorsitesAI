import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePromptStream } from "@/hooks/usePromptStream";
import { ApiError } from "@/lib/api/client";
import { cancelGeneration, getLatestGeneration } from "@/lib/api/messages";
import type { GenerationRun, Message, WsEvent } from "@/lib/api/types";
import { isChatMessageStreaming } from "@/lib/chat-message-status";

vi.mock("@/lib/api/messages", () => ({
  cancelGeneration: vi.fn(),
  getLatestGeneration: vi.fn(),
  sendPrompt: vi.fn(),
}));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn() } }));

function run(status: GenerationRun["status"]): GenerationRun {
  return {
    id: "run-1", project_id: "project-1", assistant_message_id: "message-1",
    status, response_mode: "build", created_at: "2026-09-08T00:00:00Z",
    started_at: null, finished_at: null,
  };
}

class TestSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static instances: TestSocket[] = [];
  readyState = TestSocket.OPEN;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onopen: (() => void) | null = null;
  constructor() { TestSocket.instances.push(this); }
  send() {}
  close() { this.readyState = 3; }
  emit(event: WsEvent) { this.onmessage?.({ data: JSON.stringify(event) }); }
}

let client: QueryClient;
let root: Root;
let container: HTMLDivElement;
let stream: ReturnType<typeof usePromptStream>;

function Harness() {
  const currentStream = usePromptStream("project-1", "coffee");
  useEffect(() => { stream = currentStream; }, [currentStream]);
  return null;
}

function message() {
  return client.getQueryData<Message[]>(["messages", "project-1"])![0];
}

beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", TestSocket);
  TestSocket.instances = [];
  vi.mocked(getLatestGeneration).mockResolvedValue(run("running"));
  vi.mocked(cancelGeneration).mockResolvedValue(run("cancelled"));
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  client.setQueryData<Message[]>(["messages", "project-1"], [{
    id: "message-1", project_id: "project-1", role: "assistant", content: "Проверяю каталог",
    model_id: "model", snapshot_id: null, tokens_in: null, tokens_out: null,
    generation_status: "running", created_at: "2026-09-08T00:00:00Z",
  }]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(
    <QueryClientProvider client={client}><Harness /></QueryClientProvider>,
  ));
  expect(isChatMessageStreaming(message())).toBe(true);
});

afterEach(async () => {
  await act(async () => root.unmount());
  client.clear();
  container.remove();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("prompt stream terminal reconciliation", () => {
  it.each([
    [{ type: "generation.cancelled", data: { run_id: "run-1", message_id: "message-1" } }, "cancelled"],
    [{ type: "llm.error", data: { message_id: "message-1", error: "Build failed" } }, "failed"],
    [{ type: "llm.done", data: { message_id: "message-1", tokens_in: 2, tokens_out: 4, cost_rub: 0 } }, "completed"],
  ] as const)("reconciles running cache after %s", async (event, status) => {
    await act(async () => TestSocket.instances.at(-1)!.emit(event));
    expect(message().generation_status).toBe(status);
    expect(isChatMessageStreaming(message())).toBe(false);
  });

  it("reconciles confirmed local cancellation", async () => {
    await act(async () => stream.cancel());
    expect(message().generation_status).toBe("cancelled");
    expect(isChatMessageStreaming(message())).toBe(false);
    expect(message().content).toContain("[Отменено пользователем]");
  });

  it("keeps cancellation pending until the server confirms it", async () => {
    vi.mocked(cancelGeneration).mockResolvedValue(run("cancel_requested"));
    await act(async () => stream.cancel());
    expect(message().generation_status).toBe("cancel_requested");
    expect(message().content).not.toContain("[Отменено пользователем]");
    expect(isChatMessageStreaming(message())).toBe(true);
    expect(TestSocket.instances.at(-1)!.readyState).toBe(TestSocket.OPEN);
    await act(async () => TestSocket.instances.at(-1)!.emit({
      type: "generation.cancelled", data: { run_id: "run-1", message_id: "message-1" },
    }));
    expect(isChatMessageStreaming(message())).toBe(false);
  });

  it("preserves completion when completion wins the stop race", async () => {
    vi.mocked(cancelGeneration).mockRejectedValue(new ApiError(409, {
      code: "conflict", message: "already finished",
    }));
    vi.mocked(getLatestGeneration).mockResolvedValue(run("completed"));
    await act(async () => stream.cancel());
    expect(message().generation_status).toBe("completed");
    expect(message().content).toBe("Проверяю каталог");
    expect(isChatMessageStreaming(message())).toBe(false);
  });

  it("does not downgrade a terminal event when a pending cancel response arrives later", async () => {
    let resolveCancel!: (value: GenerationRun) => void;
    vi.mocked(cancelGeneration).mockImplementation(() => new Promise((resolve) => {
      resolveCancel = resolve;
    }));
    let cancellation!: Promise<void>;
    await act(async () => { cancellation = stream.cancel(); });
    await act(async () => TestSocket.instances.at(-1)!.emit({
      type: "llm.done", data: { message_id: "message-1", tokens_in: 2, tokens_out: 4, cost_rub: 0 },
    }));
    await act(async () => {
      resolveCancel(run("cancel_requested"));
      await cancellation;
    });
    expect(message().generation_status).toBe("completed");
    expect(isChatMessageStreaming(message())).toBe(false);
    expect(message().content).not.toContain("[Отменено пользователем]");
  });
});
