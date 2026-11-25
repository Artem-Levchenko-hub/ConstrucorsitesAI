import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentTranscript } from "@/components/workspace/AgentTranscript";
import { usePromptStream } from "@/hooks/usePromptStream";
import { getLatestGeneration } from "@/lib/api/messages";
import type { AgentStep, GenerationRun, Message, WsEvent } from "@/lib/api/types";
import { restorePersistedAgentSteps } from "@/lib/agent-steps";

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
  constructor(readonly url: string) { TestSocket.instances.push(this); }
  send = vi.fn();
  close() { this.readyState = 3; this.onclose?.(); }
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
    TestSocket.instances.at(-1)!.emit({
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
  it("replaces a started command with its result after reconnect and discloses safe timings on click", async () => {
    const call_id = "53cbbb7d-1ebe-46ea-83ed-296a240a1373";
    await emit("agent.tool_started", { step: 1, action: "bash",
      tool_diagnostic: { call_id, state: "started" } });
    expect(container.querySelectorAll("ol > li")).toHaveLength(1);
    expect(container.textContent).toContain("Вызов команды начат");
    expect(container.textContent).not.toContain("1 шаг.");

    await act(async () => TestSocket.instances[0].close());
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(TestSocket.instances.at(-1)?.url).toContain("after_seq=1");
    await act(async () => TestSocket.instances.at(-1)!.emit({ type: "generation.event", data: {
      event_id: "event-1", run_id: "run-1", seq: 1, type: "agent.tool_started",
      data: { message_id: "message-1", step: 1, action: "bash",
        tool_diagnostic: { call_id, state: "started" } },
    } }));
    const recordedAt = "2026-10-12T12:48:00.541388+00:00";
    await emit("agent.step", { step: 1, kind: "step", action: "Проверяю каталог", tool: "bash", ok: false,
      recordedAt, path: "hidden-path", detail: "hidden-stdout",
      tool_diagnostic: { call_id, state: "failed", reason_code: "nonzero_exit", command_family: "pnpm",
        elapsed_ms: 1530, exit_code: 2, timings_ms: { dispatch: 2, workspace_sync: 78, reader_wait: 0, total: 1530 },
        command: "pnpm --token hidden-value", stdout: "hidden-output" } });
    expect(container.querySelectorAll("ol > li")).toHaveLength(1);
    expect(container.textContent).toContain("Не получилось: проверяю каталог");
    expect(container.textContent).toContain("1 шаг.");
    expect(container.textContent).not.toContain("Код возврата");

    await act(async () => {
      container.querySelector("details")!.open = true;
      container.querySelector("details")!.dispatchEvent(new Event("toggle"));
      container.querySelector<HTMLButtonElement>("ol > li button")!.click();
    });
    expect(container.textContent).toContain("Команда вернула ненулевой код");
    expect(container.textContent).toContain("Семейство командыpnpm");
    expect(container.textContent).toContain("Код возврата2");
    expect(container.textContent).toContain("Ожидание готовности reader0 мс");
    expect(container.textContent).toContain("Синхронизация файлов78 мс");
    expect(container.textContent).toContain("Длительность1530 мс");
    expect(container.textContent).toContain("Время события" + new Intl.DateTimeFormat("ru-RU", {
      dateStyle: "short", timeStyle: "medium",
    }).format(Date.parse(recordedAt)));
    expect(container.textContent).not.toMatch(/hidden-|nonzero_exit|53cbbb7d/);
  });

  it("restores distinct calls for the same step and a technical terminal result from saved history", async () => {
    const base = { step: 2, action: "Проверяю каталог", tool: "bash", path: "", kind: "step" as const,
      runId: "history-run", ok: true };
    const history: AgentStep[] = [
      { ...base, eventId: "history-3", seq: 3, tool_diagnostic: {
        call_id: "53cbbb7d-1ebe-46ea-83ed-296a240a1373", state: "succeeded", elapsed_ms: 0,
      } },
      { ...base, eventId: "history-4", seq: 4, tool_diagnostic: {
        call_id: "ec96f845-ac9a-4193-a2b4-c4d2547471e2", state: "succeeded", elapsed_ms: 7,
      } },
      { ...base, eventId: "history-5", seq: 5, kind: "heartbeat", action: "unexpected-raw-action",
        recordedAt: "unexpected-raw-time",
        tool_diagnostic: { call_id: "9d2b4136-7bbc-449b-b26a-48cd4bb43e47", state: "unknown",
          reason_code: "unexpected-raw-reason", command_family: "pnpm --token secret" } },
    ];
    const historicalMessageId = "history-message";
    await act(async () => root.render(<QueryClientProvider client={client}>
      <AgentTranscript projectId="project-1" messageId={historicalMessageId} generationStatus="failed"
        initialSteps={history} />
    </QueryClientProvider>));

    expect(container.querySelectorAll("ol > li")).toHaveLength(3);
    expect(container.textContent).toContain("2 шаг.");
    expect(container.textContent).toContain("Результат вызова не подтверждён");
    await act(async () => container.querySelectorAll<HTMLButtonElement>("ol > li button")[2].click());
    expect(container.textContent).toContain("Причина не подтверждена");
    expect(container.textContent).not.toContain("Время события");
    expect(container.textContent).not.toMatch(/unexpected-raw-|--token/);
  });

  it.each([
    ["rg", "Оболочка не нашла команду rg"],
    ["private-program", "Оболочка сообщила, что команда не найдена"],
  ])("shows a confirmed missing command from a shell invocation: %s", async (missing, label) => {
    await emit("agent.step", {
      kind: "step", step: 1, action: "bash", tool: "bash", ok: false,
      tool_diagnostic: {
        call_id: "53cbbb7d-1ebe-46ea-83ed-296a240a1373", state: "failed",
        reason_code: "executable_not_found", command_family: "sh", missing_executable: missing,
      },
    });
    await act(async () => container.querySelector<HTMLButtonElement>("ol > li button")!.click());
    expect(container.textContent).toContain(label);
    expect(container.textContent).not.toContain("private-program");
  });

  it("keeps a rejected reply as a stalled observation rather than a tool action", async () => {
    await emit("agent.step", {
      step: 2, action: "думаю, как лучше — меняю подход", kind: "stalled", tool: "",
      tool_diagnostic: { call_id: "53cbbb7d-1ebe-46ea-83ed-296a240a1373", state: "skipped",
        reason_code: "missing_action" },
    });
    expect(container.textContent).toContain("Меняю подход");
    expect(container.textContent).not.toContain("1 шаг.");
    await act(async () => container.querySelector<HTMLButtonElement>("ol > li button")!.click());
    expect(container.textContent).toContain("В ответе модели не указано действие");
  });

  it("shows native tool outcomes from their diagnostics and keeps deferred checks neutral", async () => {
    await emit("agent.step", { step: 1, action: "Выполняю команду", tool: "bash", ok: false,
      tool_diagnostic: { call_id: "53cbbb7d-1ebe-46ea-83ed-296a240a1373", state: "skipped",
        reason_code: "dispatch_rejected" } });
    await emit("agent.step", { step: 2, action: "Выполняю команду", tool: "bash", ok: false,
      tool_diagnostic: { call_id: "ec96f845-ac9a-4193-a2b4-c4d2547471e2", state: "unknown" } });
    await emit("agent.step", { step: 3, action: "Читаю код", tool: "read_file", ok: true,
      tool_diagnostic: { call_id: "9d2b4136-7bbc-449b-b26a-48cd4bb43e47", state: "skipped",
        reason_code: "cached_result" } });
    await emit("agent.step", { step: 4, action: "Проверяю приложение", tool: "build", ok: null,
      tool_diagnostic: { call_id: "c4a0975b-2efa-459f-b8e0-7793b050ada4", state: "skipped",
        reason_code: "verification_deferred" } });
    expect([...container.querySelectorAll("ol > li")].map(row => row.getAttribute("title"))).toEqual([
      "Шаг пропущен", "Результат вызова не подтверждён", "Использован результат из кэша", "Проверяю приложение",
    ]);
  });

  it("replaces an unconfirmed row with recovered evidence and labels an unresolved reconciliation", async () => {
    const call_id = "53cbbb7d-1ebe-56ea-83ed-296a240a1373";
    await emit("agent.step", { step: 1, action: "Выполняю команду", tool: "bash", ok: false,
      tool_diagnostic: { call_id, state: "unknown" } });
    await act(async () => container.querySelector<HTMLButtonElement>("ol > li button")!.click());
    await emit("agent.tool_finished", { step: 1, action: "bash",
      tool_diagnostic: { call_id, state: "succeeded", reconciled: true } });
    await emit("agent.tool_finished", { step: 2, action: "bash",
      tool_diagnostic: { call_id: "ec96f845-ac9a-5193-a2b4-c4d2547471e2", state: "unknown", reconciled: true } });
    const rows = container.querySelectorAll("ol > li");
    expect(rows).toHaveLength(2);
    expect(container.textContent).toContain("1 шаг.");
    await act(async () => rows[1].querySelector<HTMLButtonElement>("button")!.click());
    expect(rows[0].textContent).toContain("Результат восстановлен из журнала");
    expect(rows[0].textContent).toContain("Результат получен");
    expect(rows[1].textContent).toContain("Сверка после восстановления");
    expect(rows[1].textContent).not.toContain("Результат восстановлен из журнала");
  });

  it("carries the full legacy counter through stream, history restore and replay", async () => {
    const runId = "11111111-1111-1111-1111-111111111111";
    await act(async () => {
      client.setQueryData<GenerationRun>(["generation", "project-1"], previous => ({ ...previous!, id: runId }));
      TestSocket.instances[0].emit({ type: "agent.step", data: {
        message_id: "message-1", eventId: "22222222-2222-2222-2222-222222222222", runId,
        step: 201, kind: "step", action: "Читаю файл", path: "page.tsx",
        actionCount: 201, historyTruncated: true,
      } });
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(steps()).toHaveLength(1);
    expect(steps()[0]).toMatchObject({ actionCount: 201, historyTruncated: true });
    expect(container.textContent).toContain("201 шаг.");
    expect(container.textContent).toContain("Сохранённая история сокращена");
    await act(async () => {
      const persisted = [{ ...steps()[0], detail: "Saved detail" }];
      client.setQueryData(["agent-steps", "project-1", "message-1"], restorePersistedAgentSteps(steps(), persisted));
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(steps()).toHaveLength(1);
    expect(container.textContent).toContain("201 шаг.");
  });

  it("deduplicates identified legacy delivery while preserving actual repeated actions", async () => {
    const data = { message_id: "message-1", eventId: "legacy-event-1", runId: "run-1",
      step: 1, kind: "step" as const, action: "Читаю файл", path: "page.tsx" };
    await act(async () => {
      TestSocket.instances[0].emit({ type: "agent.step", data });
      TestSocket.instances[0].emit({ type: "agent.step", data });
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(steps()).toHaveLength(1);
    expect(steps()[0]).toMatchObject({ eventId: "legacy-event-1", runId: "run-1" });
    expect(container.textContent).toContain("1 шаг.");
    await act(async () => {
      TestSocket.instances[0].emit({ type: "agent.step", data: { ...data, eventId: "legacy-event-2" } });
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(steps()).toHaveLength(2);
    expect(container.textContent).toContain("2 шаг.");
  });

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
    expect(container.textContent).not.toContain("Итог запроса");
    await act(async () => {
      client.setQueryData<GenerationRun>(["generation", "project-1"], previous => ({
        ...previous!, acceptance: { run_id: "run-1", assistant_message_id: "message-1",
          snapshot_id: "accepted-snapshot", accepted_at: null, current_request_complete: true },
      }));
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(container.textContent).toContain("Готово");
    expect(container.textContent).toContain("Итог запроса");
    expect(container.querySelector("ol > li")?.getAttribute("title")).toBe("Готово");
  });

  it("renders the real complete phase in Russian without claiming durable acceptance", async () => {
    await emit("generation.phase", { phase: "complete" });
    expect(container.textContent).toContain("Завершаю проверку результата");
    expect(container.textContent).not.toContain("complete");
    expect(container.textContent).not.toContain("Итог запроса");
    expect(container.textContent).not.toContain("Готово");
  });
});
