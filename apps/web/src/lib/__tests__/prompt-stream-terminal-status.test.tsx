import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePromptStream } from "@/hooks/usePromptStream";
import { ApiError } from "@/lib/api/client";
import { cancelGeneration, getLatestGeneration, sendPrompt } from "@/lib/api/messages";
import type { GenerationRun, Message, WsEvent } from "@/lib/api/types";
import { isChatMessageStreaming } from "@/lib/chat-message-status";
import { toast } from "sonner";

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
  send = vi.fn<(data: string) => void>();
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
  it("hydrates durable failure history after the error event wins the commit race", async () => {
    vi.mocked(getLatestGeneration).mockResolvedValueOnce(run("running")).mockResolvedValue(run("failed"));
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await act(async () => TestSocket.instances.at(-1)!.emit({
      type: "llm.error", data: { message_id: "message-1", error: "deadline" },
    }));
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["messages", "project-1"] });
  });

  it("keeps observing a slow cleanup until durable failure is committed", async () => {
    vi.mocked(getLatestGeneration).mockResolvedValue(run("running"));
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await act(async () => TestSocket.instances.at(-1)!.emit({
      type: "llm.error", data: { message_id: "message-1", error: "deadline" },
    }));
    await act(async () => vi.advanceTimersByTimeAsync(31_000));
    invalidate.mockClear();
    vi.mocked(getLatestGeneration).mockResolvedValue(run("failed"));
    await act(async () => vi.advanceTimersByTimeAsync(15_000));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["messages", "project-1"] });
    expect(sendPrompt).not.toHaveBeenCalled();
  });

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

describe("message cache update contracts", () => {
  it("drops duplicate/gap chunks and resumes from the cumulative replay", async () => {
    const socket = TestSocket.instances.at(-1)!;
    const emit = async (event: WsEvent) => act(async () => socket.emit(event));
    await emit({ type: "stream.sync", data: { message_id: "message-1", content: "A", seq: 2 } });
    for (const seq of [2, 4, 3]) {
      await emit({ type: "llm.chunk", data: { message_id: "message-1", delta: "ignored", seq } });
    }
    expect(message().content).toBe("A");
    expect(socket.send).toHaveBeenCalledWith(JSON.stringify({ type: "resync" }));
    await emit({ type: "stream.sync", data: { message_id: "message-1", content: "AB", seq: 4 } });
    await emit({ type: "llm.chunk", data: { message_id: "message-1", delta: "C", seq: 5 } });
    expect(message().content).toBe("ABC");
  });

  it("updates duplicate IDs while preserving unmatched references and handles absent caches", async () => {
    const original = message();
    client.setQueryData(["messages", "project-1"], [original, { ...original, content: "second" }, { ...original, id: "unmatched" }]);
    const unmatched = client.getQueryData<Message[]>(["messages", "project-1"])![2];
    const socket = TestSocket.instances.at(-1)!;
    await act(async () => socket.emit({ type: "llm.chunk", data: { message_id: "message-1", delta: "!" } }));
    const rows = client.getQueryData<Message[]>(["messages", "project-1"])!;
    expect(rows.map((m) => m.content)).toEqual(["Проверяю каталог!", "second!", "Проверяю каталог"]);
    expect(rows[2]).toBe(unmatched);
    await act(async () => socket.emit({ type: "stream.sync", data: { message_id: "absent", content: "ignored", seq: 1 } }));
    expect(client.getQueryData<Message[]>(["messages", "project-1"])![0]).toBe(rows[0]);
    client.removeQueries({ queryKey: ["messages", "project-1"] });
    await act(async () => socket.emit({ type: "stream.sync", data: { message_id: "absent", content: "ignored", seq: 1 } }));
    expect(client.getQueryData(["messages", "project-1"])).toEqual([]);
  });

  it.each(["llm.done", "llm.error"] as const)("starts the queued prompt after %s and swaps its temporary ID", async (type) => {
    vi.mocked(sendPrompt).mockResolvedValue({ run_id: "run-2", message_id: "message-2", snapshot_id: null });
    await act(async () => { await stream.submit("Follow up", "model"); });
    expect(stream.pendingPrompt).toBe("Follow up");
    expect(sendPrompt).not.toHaveBeenCalled();
    await act(async () => TestSocket.instances.at(-1)!.emit(type === "llm.done"
      ? { type, data: { message_id: "message-1", tokens_in: 2, tokens_out: 4, cost_rub: 0 } }
      : { type, data: { message_id: "message-1", error: "failed" } }));
    expect(stream.pendingPrompt).toBeNull();
    await act(async () => vi.advanceTimersByTime(0));
    expect(sendPrompt).toHaveBeenCalledTimes(1);
    const rows = client.getQueryData<Message[]>(["messages", "project-1"])!;
    expect(rows.at(-2)).toMatchObject({ role: "user", content: "Follow up" });
    expect(rows.at(-1)).toMatchObject({ id: "message-2", role: "assistant", content: "", tokens_out: null });
  });

  it("marks rejected POST placeholder without inventing a generation status", async () => {
    vi.mocked(sendPrompt).mockRejectedValue(new Error("fixture rejected SECRET_VALUE"));
    await act(async () => { await stream.submit("Follow up", "model"); });
    await act(async () => TestSocket.instances.at(-1)!.emit({ type: "llm.done", data: { message_id: "message-1", tokens_in: 1, tokens_out: 2, cost_rub: 0 } }));
    await act(async () => vi.advanceTimersByTime(0));
    const last = client.getQueryData<Message[]>(["messages", "project-1"])!.at(-1)!;
    expect(last.id).toMatch(/^__opt_asst_/);
    expect(last).toMatchObject({ content: "[Ошибка: Не удалось отправить запрос. Проверьте соединение и попробуйте ещё раз.]", tokens_in: 0, tokens_out: 0 });
    expect(JSON.stringify(vi.mocked(toast.error).mock.calls)).not.toContain("SECRET_VALUE");
    expect(last.generation_status).toBeUndefined();
  });

  it("never copies an error event secret into partial text or toast", async () => {
    await act(async () => TestSocket.instances.at(-1)!.emit({
      type: "llm.error", data: { message_id: "message-1", error: "password=SECRET_VALUE" },
    }));
    expect(message().content).toBe("Проверяю каталог");
    expect(JSON.stringify(vi.mocked(toast.error).mock.calls)).not.toContain("SECRET_VALUE");
  });

  it.each([
    ["wallet_empty", "Пополните баланс"],
    ["generation_draining", "обновляется"],
  ] as const)("keeps actionable %s refusal without server detail", async (code, expected) => {
    vi.mocked(sendPrompt).mockRejectedValue(new ApiError(402, {
      code, message: "SECRET_VALUE provider detail",
    }));
    await act(async () => { await stream.submit("Follow up", "model"); });
    await act(async () => TestSocket.instances.at(-1)!.emit({ type: "llm.done", data: {
      message_id: "message-1", tokens_in: 1, tokens_out: 2, cost_rub: 0,
    } }));
    await act(async () => vi.advanceTimersByTime(0));
    const last = client.getQueryData<Message[]>(["messages", "project-1"])!.at(-1)!;
    expect(last.content).toContain(expected);
    expect(last.content).not.toContain("SECRET_VALUE");
    expect(last.generation_status).toBeUndefined();
  });

  it("clears queued work on cancellation and keeps user rows sharing the ID intact", async () => {
    const original = message();
    client.setQueryData(["messages", "project-1"], [{ ...original, role: "user" }, original]);
    const user = client.getQueryData<Message[]>(["messages", "project-1"])![0];
    await act(async () => { await stream.submit("Follow up", "model"); });
    await act(async () => stream.cancel());
    expect(stream.pendingPrompt).toBeNull();
    expect(sendPrompt).not.toHaveBeenCalled();
    const rows = client.getQueryData<Message[]>(["messages", "project-1"])!;
    expect(rows[0]).toBe(user);
    expect(rows[1]).toMatchObject({ generation_status: "cancelled", tokens_in: 0, tokens_out: 0 });
  });
});
