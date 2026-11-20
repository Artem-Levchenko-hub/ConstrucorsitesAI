import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import type { GenerationContinuationReference, GenerationRun, Message } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({
  submit: vi.fn(), history: vi.fn(), profile: vi.fn(), pendingPrompt: null as string | null,
}));
vi.mock("@/hooks/usePromptStream", () => ({ usePromptStream: () => ({
  submit: mocks.submit, cancel: vi.fn(), cancelPending: vi.fn(), pendingPrompt: mocks.pendingPrompt,
}) }));
vi.mock("@/lib/api/messages", () => ({ listMessages: mocks.history, getLatestGeneration: vi.fn() }));
vi.mock("@/lib/api/owner-profile", () => ({ getOwnerProfile: mocks.profile }));
vi.mock("@/lib/api/max-studio", () => ({ getMaxProjectConfig: async () => ({ config_version: 1 }) }));
vi.mock("@/components/workspace/AgentTranscript", () => ({ AgentTranscript: () => null }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("sonner", () => ({ toast: { info: vi.fn(), success: vi.fn(), warning: vi.fn(), error: vi.fn() } }));

const failure: Message = {
  id: "failed-message", project_id: "p", role: "assistant", snapshot_id: null,
  content: "Последний запрос не выполнен", model_id: "model", tokens_in: null, tokens_out: null,
  created_at: "2026-11-06T04:00:00Z", generation_status: "failed",
  generation_failure: { code: "provider_unavailable", message: "Провайдер отклонил запрос (HTTP 422). Автоматический повтор остановлен.", retryable: false },
};
const continuation: GenerationContinuationReference = {
  run_id: "failed-run", assistant_message_id: failure.id, expected_snapshot_id: "saved-draft",
};
const failedRun = (overrides: Partial<GenerationRun> = {}): GenerationRun => ({
  id: continuation.run_id, project_id: "p", assistant_message_id: failure.id,
  status: "failed", response_mode: "build", created_at: failure.created_at,
  started_at: failure.created_at, finished_at: "2026-11-06T04:02:00Z", continuation,
  ...overrides,
});
let client: QueryClient, root: Root, container: HTMLDivElement;
const button = (text: string) => [...container.querySelectorAll("button")].find(b => b.textContent?.trim() === text);

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.clearAllMocks(); sessionStorage.clear(); window.history.replaceState(null, "", "/max/p");
  mocks.pendingPrompt = null;
  mocks.submit.mockResolvedValue(true);
  mocks.history.mockResolvedValue([failure]);
  mocks.profile.mockResolvedValue({ id: "owner", email: "qa@example.invalid", user_chat_messages_limit: 1, user_chat_messages_remaining: 1 });
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Unexpected network call in inert test")));
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount()); client.clear(); container.remove();
  vi.unstubAllGlobals(); sessionStorage.clear(); window.history.replaceState(null, "", "/");
});

async function render() {
  await act(async () => root.render(<QueryClientProvider client={client}><ChatPanel projectId="p" projectSlug="p" /></QueryClientProvider>));
  await act(async () => {
    await vi.waitFor(() => {
      expect(client.getQueryState(["owner-profile"])?.status).toBe("success");
      expect(container.textContent).toContain("Последний запрос не выполнен");
    });
  });
}

async function draft(text: string) {
  const input = container.querySelector("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  return input;
}

it("a provider rejection requires an explicit click and uses the unchanged normal submit pipeline once", async () => {
  let finish!: (accepted: boolean) => void;
  mocks.submit.mockImplementation(() => new Promise<boolean>(resolve => { finish = resolve; }));
  await render();
  const input = await draft("Мой несохранённый черновик");
  const retry = button("Повторить запрос")!;
  expect(retry).toBeDefined();
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
  await act(async () => { retry.click(); retry.click(); });
  expect(mocks.submit).toHaveBeenCalledTimes(1);
  expect(mocks.submit).toHaveBeenCalledWith("Попробуй ещё раз", "topmix-v1", [], { generationEngine: "sol" });
  expect(retry.disabled).toBe(true);
  await act(async () => finish(false));
  expect(container.textContent).toContain("Запрос пока не принят");
  expect(retry.disabled).toBe(false);
  expect(input.value).toBe("Мой несохранённый черновик");
  expect(fetch).not.toHaveBeenCalled();
});

it.each(["gpt-6.2-sol", "claude-sonnet", "legacy-model"])(
  "continues the exact server draft without inferring an engine from %s", async model => {
    mocks.history.mockResolvedValue([{ ...failure, model_id: model }]);
    client.setQueryData(["generation", "p"], failedRun());
    let finish!: (accepted: boolean) => void;
    mocks.submit.mockImplementation(() => new Promise<boolean>(resolve => { finish = resolve; }));
    await render();
    const input = await draft("Мой новый черновик пока не отправляй");
    expect(container.textContent).toContain("Продолжим с сохранённого черновика");
    expect(button("Повторить запрос")).toBeUndefined();
    const proceed = button("Продолжить сборку")!;
    await act(async () => { proceed.click(); proceed.click(); });
    expect(mocks.submit).toHaveBeenCalledTimes(1);
    expect(mocks.submit).toHaveBeenCalledWith("Продолжи сборку", "topmix-v1", [], {
      continuation, idempotencyKey: "continue:failed-run:failed-message:saved-draft",
    });
    await act(async () => finish(false));
    await act(async () => proceed.click());
    expect(mocks.submit.mock.calls[1]).toEqual(mocks.submit.mock.calls[0]);
    expect(input.value).toBe("Мой новый черновик пока не отправляй");
    await act(async () => finish(false));
    expect(fetch).not.toHaveBeenCalled();
  },
);

it("keeps the same addressed identity after remount and preserves a null source snapshot", async () => {
  client.setQueryData(["generation", "p"], failedRun({ continuation: { ...continuation, expected_snapshot_id: null } }));
  mocks.submit.mockResolvedValue(false);
  await render();
  await act(async () => button("Продолжить сборку")!.click());
  const first = mocks.submit.mock.calls[0];
  await act(async () => root.unmount());
  root = createRoot(container);
  client.setQueryData(["generation", "p"], failedRun({ continuation: { ...continuation, expected_snapshot_id: null } }));
  await render();
  await act(async () => button("Продолжить сборку")!.click());
  expect(mocks.submit.mock.calls[1]).toEqual(first);
  expect(first[3]).toEqual({ continuation: { ...continuation, expected_snapshot_id: null },
    idempotencyKey: "continue:failed-run:failed-message:none" });
});

it.each([
  { project_id: "foreign" }, { assistant_message_id: "different-message" },
  { continuation: { ...continuation, run_id: "older-run" } },
  { continuation: { ...continuation, assistant_message_id: "older-message" } },
  { continuation: null }, { continuation: undefined },
])("does not advertise an unbound or missing server draft %j", async override => {
  client.setQueryData(["generation", "p"], failedRun(override));
  await render();
  expect(button("Продолжить сборку")).toBeUndefined();
  expect(button("Повторить запрос")).toBeDefined();
  expect(mocks.submit).not.toHaveBeenCalled();
});

it("refuses a stale clicked descriptor without sending or changing the composed draft", async () => {
  client.setQueryData(["generation", "p"], failedRun());
  await render();
  const input = await draft("Не теряй мой текст");
  const proceed = button("Продолжить сборку")!;
  await act(async () => {
    client.setQueryData(["generation", "p"], failedRun({ continuation: { ...continuation, expected_snapshot_id: "new-head" } }));
    proceed.click();
  });
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(input.value).toBe("Не теряй мой текст");
});

it("does not derive continuation authority from an optimistic assistant row", async () => {
  const optimistic = { ...failure, id: "__opt_asst_pending" };
  mocks.history.mockResolvedValue([optimistic]);
  client.setQueryData(["generation", "p"], failedRun({ assistant_message_id: optimistic.id,
    continuation: { ...continuation, assistant_message_id: optimistic.id } }));
  await render();
  expect(button("Продолжить сборку")).toBeUndefined();
  expect(mocks.submit).not.toHaveBeenCalled();
});

it("a recovery-required failure returns focus without sending or changing the draft", async () => {
  mocks.history.mockResolvedValue([{ ...failure,
    generation_failure: { code: "runtime_unavailable", message: "Требуется восстановление среды на стороне сервиса.", retryable: false },
  }]);
  await render();
  const input = await draft("Сохрани мой текст");
  const back = button("Вернуться к запросу")!;
  expect(button("Продолжить сборку")).toBeUndefined();
  await act(async () => { back.focus(); back.click(); });
  expect(document.activeElement).toBe(input);
  expect(input.value).toBe("Сохрани мой текст");
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
});

it.each(["quota", "queued", "active"])("does not expose a new retry while %s blocks it", async mode => {
  if (mode === "quota") mocks.profile.mockResolvedValue({ id: "owner", user_chat_messages_limit: 1, user_chat_messages_remaining: 0 });
  if (mode === "queued") mocks.pendingPrompt = "Уже поставленный запрос";
  if (mode === "active") client.setQueryData<GenerationRun>(["generation", "p"], {
    id: "new-run", project_id: "p", assistant_message_id: "not-in-history-yet",
    status: "running", response_mode: "edit", created_at: "2026-11-06T04:01:00Z",
    started_at: "2026-11-06T04:01:00Z", finished_at: null,
  });
  await render();
  expect(button("Продолжить сборку")).toBeUndefined();
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
});

it("an older failure never retries a different last message", async () => {
  mocks.history.mockResolvedValue([failure, { ...failure, id: "completed-message", content: "Новая версия готова", generation_status: "completed", generation_failure: null }]);
  await render();
  expect(button("Продолжить сборку")).toBeUndefined();
  expect(button("Вернуться к запросу")).toBeUndefined();
  expect(mocks.submit).not.toHaveBeenCalled();
});
