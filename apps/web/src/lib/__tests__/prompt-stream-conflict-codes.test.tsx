import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { usePromptStream } from "@/hooks/usePromptStream";
import { getLatestGeneration, sendPrompt } from "@/lib/api/messages";
import type { Message } from "@/lib/api/types";
import { OWNER_PROFILE_QUERY_KEY } from "@/lib/owner-chat-quota";
import { ApiError } from "@/lib/api/client";
import { toast } from "sonner";

/**
 * A refused prompt says WHY. Only `generation_active` — confirmed by the latest
 * run — may put the composer into streaming mode; any other 409 used to be shown
 * as «Генерация уже запущена» and the next prompt sat in a local queue forever.
 */
vi.mock("@/lib/api/messages", () => ({
  cancelGeneration: vi.fn(), getLatestGeneration: vi.fn(), sendPrompt: vi.fn(),
}));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn() } }));
class Socket {
  static OPEN = 1; static CONNECTING = 0;
  static instances: Socket[] = [];
  constructor() { Socket.instances.push(this); }
  emit(event: unknown) { this.onmessage?.({ data: JSON.stringify(event) }); }
  readyState = 1;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onopen: (() => void) | null = null;
  send() {}
  close() { this.readyState = 3; }
}
let client: QueryClient;
let root: Root;
let container: HTMLDivElement;
let stream: ReturnType<typeof usePromptStream>;
function Harness() {
  const value = usePromptStream("p", "fixture");
  useEffect(() => { stream = value; }, [value]);
  return null;
}
async function mount() {
  await act(async () => root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>));
}
const refusal = (code: string, message: string, details?: Record<string, unknown>) =>
  new ApiError(409, { code, message, details } as ConstructorParameters<typeof ApiError>[1]);
const running = { id: "run", project_id: "p", assistant_message_id: "m", status: "running",
  response_mode: "edit", created_at: "2026-09-19T00:00:00Z", started_at: null, finished_at: null };
const optimisticRows = () =>
  (client.getQueryData<Message[]>(["messages", "p"]) ?? []).filter((m) => m.id.startsWith("__opt_"));

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.useFakeTimers(); vi.stubGlobal("WebSocket", Socket); vi.clearAllMocks();
  localStorage.clear(); Socket.instances = [];
  vi.mocked(sendPrompt).mockReset(); vi.mocked(getLatestGeneration).mockReset();
  vi.mocked(getLatestGeneration).mockResolvedValue(null);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount()); client.clear(); container.remove();
  vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals();
});

it("follows the running build when the server confirms one", async () => {
  vi.mocked(sendPrompt).mockRejectedValueOnce(
    refusal("generation_active", "generation already in progress", { active_run_id: "run" }));
  vi.mocked(getLatestGeneration).mockResolvedValue(running as never);
  await mount();
  await act(async () => { expect(await stream.submit("Добавь поле", "model")).toBe(true); });
  expect(toast.info).toHaveBeenCalledWith("Генерация уже запущена", expect.anything());
  expect(toast.error).not.toHaveBeenCalled();
  expect(optimisticRows()).toEqual([]);
  // Streaming mode is on: the next prompt waits in the queue instead of being sent.
  await act(async () => { expect(await stream.submit("Ещё одно", "model")).toBe(true); });
  expect(sendPrompt).toHaveBeenCalledTimes(1);
  expect(stream.pendingPrompt).toBe("Ещё одно");
});

it("does not pretend a build is running when the run already ended", async () => {
  vi.mocked(sendPrompt).mockRejectedValueOnce(
    refusal("generation_active", "generation already in progress", { active_run_id: "run" }));
  vi.mocked(getLatestGeneration).mockResolvedValue({ ...running, status: "completed" } as never);
  await mount();
  await act(async () => { expect(await stream.submit("Добавь поле", "model")).toBe(false); });
  expect(toast.info).toHaveBeenCalledWith("Предыдущая сборка только что завершилась", expect.anything());
  expect(toast.info).not.toHaveBeenCalledWith("Генерация уже запущена", expect.anything());
  // The composer is free: the repeated prompt really goes to the server.
  vi.mocked(sendPrompt).mockResolvedValueOnce({ run_id: "r2", message_id: "m2", snapshot_id: null,
    replayed: false, run_status: "pending" } as never);
  await act(async () => { expect(await stream.submit("Добавь поле", "model")).toBe(true); });
  expect(sendPrompt).toHaveBeenCalledTimes(2);
  expect(stream.pendingPrompt).toBeNull();
});

it.each([
  ["restoration_active", "Идёт восстановление версии: примените или отмените его.", "восстановление версии"],
  ["source_changed", "Данные приложения изменились. Откройте настройки.", "Данные приложения изменились"],
  ["idempotency_conflict", "idempotency key was already used for another prompt", "уже был отправлен"],
  ["conflict", "Черновик изменился", "Черновик изменился"],
])("shows %s as its own refusal and keeps the composer free", async (code, message, shown) => {
  vi.mocked(sendPrompt).mockRejectedValueOnce(refusal(code, message + " token=NEVER_PUBLISH"));
  await mount();
  await act(async () => { expect(await stream.submit("Добавь поле", "model")).toBe(false); });
  expect(toast.info).not.toHaveBeenCalledWith("Генерация уже запущена", expect.anything());
  expect(toast.error).toHaveBeenCalledWith("Генерация не запустилась", expect.objectContaining({
    description: expect.stringContaining(shown),
  }));
  expect(getLatestGeneration).not.toHaveBeenCalled();
  expect(JSON.stringify(vi.mocked(toast.error).mock.calls)).not.toContain("NEVER_PUBLISH");
  // Not streaming: the next prompt is sent, not parked in a queue that never drains.
  vi.mocked(sendPrompt).mockResolvedValueOnce({ run_id: "r2", message_id: "m2", snapshot_id: null,
    replayed: false, run_status: "pending" } as never);
  await act(async () => { expect(await stream.submit("Ещё раз", "model")).toBe(true); });
  expect(sendPrompt).toHaveBeenCalledTimes(2);
  expect(stream.pendingPrompt).toBeNull();
});

it("trusts the server when the confirming probe itself fails", async () => {
  vi.mocked(sendPrompt).mockRejectedValueOnce(
    refusal("generation_active", "generation already in progress", { active_run_id: "run" }));
  vi.mocked(getLatestGeneration).mockRejectedValue(new Error("network"));
  await mount();
  await act(async () => { expect(await stream.submit("Добавь поле", "model")).toBe(true); });
  expect(toast.info).toHaveBeenCalledWith("Генерация уже запущена", expect.anything());
  expect(toast.error).not.toHaveBeenCalled();
});

it("does not get stuck when the build finishes while the status probe is in flight", async () => {
  // `llm.done` reaches the socket before the run row flips, so the probe can still say "running".
  // (This tab did not know about the other tab's build: no dangling message, no probe at mount.)
  vi.mocked(sendPrompt).mockRejectedValueOnce(refusal("generation_active", "generation already in progress",
    { active_run_id: "run", active_message_id: "m" }));
  let answer!: (value: unknown) => void;
  vi.mocked(getLatestGeneration).mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }) as never);
  await mount();
  let submitted!: Promise<boolean>;
  await act(async () => { submitted = stream.submit("Добавь поле", "model"); });
  await act(async () => Socket.instances.at(-1)!.emit({
    type: "llm.done", data: { message_id: "m", tokens_in: 2, tokens_out: 4, cost_rub: 0 },
  }));
  await act(async () => { answer(running); expect(await submitted).toBe(false); });
  expect(toast.info).not.toHaveBeenCalledWith("Генерация уже запущена", expect.anything());
  vi.mocked(sendPrompt).mockResolvedValueOnce({ run_id: "r2", message_id: "m2", snapshot_id: null,
    replayed: false, run_status: "pending" } as never);
  await act(async () => { expect(await stream.submit("Следующий запрос", "model")).toBe(true); });
  expect(sendPrompt).toHaveBeenCalledTimes(2);
  expect(stream.pendingPrompt).toBeNull();
});

it("keeps a restoration adaptation out of the follow-the-build branch", async () => {
  // Returning true here would make the shell clear the durable adaptation attachment.
  vi.mocked(sendPrompt).mockRejectedValueOnce(
    refusal("generation_active", "generation already in progress", { active_run_id: "run" }));
  vi.mocked(getLatestGeneration).mockResolvedValue(running as never);
  await mount();
  const adaptation = { restorationAdaptation: { operation_id: "op", expected_draft_snapshot_id: "head" },
    idempotencyKey: "restoration-adapt:op", skipClarify: true };
  await act(async () => { expect(await stream.submit("Adapt", "model", [], adaptation)).toBe(false); });
  expect(toast.info).not.toHaveBeenCalledWith("Генерация уже запущена", expect.anything());
  expect(toast.error).toHaveBeenCalledWith("Генерация не запустилась", expect.objectContaining({
    description: expect.stringContaining("идёт другая сборка"),
  }));
});

it.each(["unauthorized", "internal_error", "generation_active", "conflict"])(
  "explains an expired session for HTTP401 with code %s without leaking details", async (code) => {
    vi.mocked(sendPrompt).mockRejectedValueOnce(new ApiError(401, {
      code, message: "token=NEVER_PUBLISH private diagnostic",
      details: { token: "NEVER_PUBLISH" },
    } as ConstructorParameters<typeof ApiError>[1]));
    await mount();
    await act(async () => { expect(await stream.submit("Добавь поле", "model")).toBe(false); });
    expect(toast.error).toHaveBeenCalledWith("Генерация не запустилась", expect.objectContaining({
      description: "Сессия завершилась. Войдите в аккаунт снова и отправьте запрос ещё раз.",
    }));
    expect(getLatestGeneration).not.toHaveBeenCalled();
    expect(toast.info).not.toHaveBeenCalled();
    expect(JSON.stringify(vi.mocked(toast.error).mock.calls)).not.toContain("NEVER_PUBLISH");
    const rows = optimisticRows();
    expect(rows.find((m) => m.role === "user")?.content).toBe("Добавь поле");
    expect(rows.find((m) => m.role === "assistant")?.content).toContain("Сессия завершилась");
    expect(rows.every((m) => !m.generation_failure)).toBe(true);
    expect(sendPrompt).toHaveBeenCalledTimes(1);
  },
);

it("holds a queued prompt after401 instead of automatically sending it with the expired session", async () => {
  let refuse!: (reason: unknown) => void;
  vi.mocked(sendPrompt).mockReturnValueOnce(new Promise((_, reject) => { refuse = reject; }));
  await mount();
  let submitted!: Promise<boolean>;
  await act(async () => { submitted = stream.submit("Первый запрос", "model"); });
  await act(async () => { expect(await stream.submit("Сохранённый следующий запрос", "model")).toBe(true); });
  expect(stream.pendingPrompt).toBe("Сохранённый следующий запрос");
  await act(async () => {
    refuse(new ApiError(401, { code: "unauthorized", message: "NEVER_PUBLISH" }));
    expect(await submitted).toBe(false);
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(sendPrompt).toHaveBeenCalledTimes(1);
  expect(stream.pendingPrompt).toBe("Сохранённый следующий запрос");
  expect(getLatestGeneration).not.toHaveBeenCalled();
  // Only the owner's explicit retry makes another HTTP submit.
  vi.mocked(sendPrompt).mockResolvedValueOnce({ run_id: "r2", message_id: "m2", snapshot_id: null,
    replayed: false, run_status: "pending" } as never);
  await act(async () => { expect(await stream.submit("Первый запрос", "model")).toBe(true); });
  expect(sendPrompt).toHaveBeenCalledTimes(2);
});

it("keeps the generic safe network error message for non401 transport failures", async () => {
  vi.mocked(sendPrompt).mockRejectedValueOnce(new Error("private network token=NEVER_PUBLISH"));
  await mount();
  await act(async () => { expect(await stream.submit("Добавь поле", "model")).toBe(false); });
  expect(toast.error).toHaveBeenCalledWith("Генерация не запустилась", expect.objectContaining({
    description: "Не удалось отправить запрос. Проверьте соединение и попробуйте ещё раз.",
  }));
  expect(getLatestGeneration).not.toHaveBeenCalled();
  expect(JSON.stringify(vi.mocked(toast.error).mock.calls)).not.toContain("NEVER_PUBLISH");
});

it("accepted first Free prompt refreshes owner profile to0; stable terminal replay remains server-adjudicated", async()=>{
 client.setQueryData(OWNER_PROFILE_QUERY_KEY,{id:"owner",user_chat_messages_limit:1,user_chat_messages_remaining:1});
 vi.mocked(sendPrompt).mockResolvedValue({run_id:"existing",message_id:"m",snapshot_id:null,replayed:true,run_status:"completed"});
 await mount();const invalidate=vi.spyOn(client,"invalidateQueries");
 await act(async()=>{expect(await stream.submit("Первое описание","model",[],{idempotencyKey:"stable-first"})).toBe(true);});
 expect(client.getQueryData(OWNER_PROFILE_QUERY_KEY)).toMatchObject({user_chat_messages_remaining:0});
 expect(invalidate).toHaveBeenCalledWith({queryKey:OWNER_PROFILE_QUERY_KEY});
 await act(async()=>{expect(await stream.submit("Первое описание","model",[],{idempotencyKey:"stable-first"})).toBe(true);});
 expect(vi.mocked(sendPrompt)).toHaveBeenCalledTimes(2);
 expect(vi.mocked(sendPrompt).mock.calls[1][4]).toMatchObject({idempotencyKey:"stable-first"});
});
it("Free entitlement402 keeps draft unaccepted, reports upgrade and does not automatically drain another prompt", async()=>{
 client.setQueryData(OWNER_PROFILE_QUERY_KEY,{id:"owner",user_chat_messages_limit:1,user_chat_messages_remaining:0});
 vi.mocked(sendPrompt).mockRejectedValue(new ApiError(402,{code:"entitlement_exceeded",message:"Выберите тариф",details:{entitlement:"free_chat_messages",plan_code:"free",limit:1,used:1}}));
 await mount();await act(async()=>{expect(await stream.submit("Второе пожелание","model")).toBe(false);});
 expect(toast.error).toHaveBeenCalledWith("Генерация не запустилась",expect.objectContaining({description:expect.stringContaining("одно сообщение на весь аккаунт")}));
 expect(toast.info).not.toHaveBeenCalledWith("Генерация уже запущена",expect.anything());
 await act(async()=>vi.advanceTimersByTime(60000));expect(sendPrompt).toHaveBeenCalledOnce();
});
