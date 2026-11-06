import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import type { GenerationRun, Message } from "@/lib/api/types";

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
  expect(mocks.submit).toHaveBeenCalledWith("Попробуй ещё раз", "topmix-v1", [], undefined);
  expect(retry.disabled).toBe(true);
  await act(async () => finish(false));
  expect(container.textContent).toContain("Не удалось запустить генерацию");
  expect(retry.disabled).toBe(false);
  expect(input.value).toBe("Мой несохранённый черновик");
  expect(fetch).not.toHaveBeenCalled();
});

it("a recovery-required failure returns focus without sending or changing the draft", async () => {
  mocks.history.mockResolvedValue([{ ...failure,
    generation_failure: { code: "runtime_unavailable", message: "Требуется восстановление среды на стороне сервиса.", retryable: false },
  }]);
  await render();
  const input = await draft("Сохрани мой текст");
  const back = button("Вернуться к запросу")!;
  expect(button("Повторить запрос")).toBeUndefined();
  expect(button("Повторить генерацию")).toBeUndefined();
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
  expect(button("Повторить запрос")).toBeUndefined();
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
});

it("an older failure never retries a different last message", async () => {
  mocks.history.mockResolvedValue([failure, { ...failure, id: "completed-message", content: "Новая версия готова", generation_status: "completed", generation_failure: null }]);
  await render();
  expect(button("Повторить запрос")).toBeUndefined();
  expect(button("Вернуться к запросу")).toBeUndefined();
  expect(mocks.submit).not.toHaveBeenCalled();
});
