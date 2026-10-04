import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import type { GenerationRun, Message } from "@/lib/api/types";

const api = vi.hoisted(() => ({ latest: vi.fn(), history: vi.fn() }));
vi.mock("@/lib/api/messages", () => ({ listMessages: api.history, getLatestGeneration: api.latest }));
vi.mock("@/lib/api/owner-profile", () => ({ getOwnerProfile: async () => ({ id: "owner", user_chat_messages_limit: null, user_chat_messages_remaining: null }) }));
vi.mock("@/lib/api/max-studio", () => ({ getMaxProjectConfig: async () => ({ config_version: 1 }) }));
vi.mock("@/hooks/usePromptStream", () => ({ usePromptStream: () => ({ submit: vi.fn(), cancel: vi.fn(), cancelPending: vi.fn(), pendingPrompt: null }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
const completed: GenerationRun = { id: "latest", project_id: "p", assistant_message_id: "current", status: "completed", response_mode: "build", created_at: "2026-10-04T08:00:00Z", started_at: "2026-10-04T08:00:00Z", finished_at: "2026-10-04T08:07:26Z" };
const message: Message = { id: "current", project_id: "p", role: "assistant", content: "Каталог готов", model_id: "model", snapshot_id: null, tokens_in: 1, tokens_out: 1, generation_status: "failed", generation_failure: { code: "deadline", message: "Устаревший сбой текущей попытки", retryable: true }, agent_steps: [{ step: 1, kind: "step", action: "Проверка каталога", path: "", ok: false }], created_at: "2026-10-04T08:00:00Z" };

it.each(["completed", "foreign", "failed"])("binds latest %s lifecycle only to its exact chat message, retaining historical failure", async mode => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.clearAllMocks();
  api.history.mockResolvedValue([{ ...message, id: "older", content: "Предыдущая попытка", generation_failure: { code: "provider_access", message: "Историческая ошибка доступа", retryable: false } }, message]);
  api.latest.mockResolvedValue(mode === "foreign" ? { ...completed, project_id: "foreign" } : mode === "failed" ? { ...completed, status: "failed" } : completed);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><ChatPanel projectId="p" projectSlug="p" /></QueryClientProvider>));
    await act(async () => { await vi.waitFor(() => expect(api.latest).toHaveBeenCalledWith("p")); });
    await act(async () => { await vi.waitFor(() => {
      expect(container.textContent).toContain("Историческая ошибка доступа");
      if (mode === "completed") {
        expect(container.textContent).not.toContain("Устаревший сбой текущей попытки");
        expect(container.textContent).toContain("Изменения готовы");
        expect(container.textContent).toContain("7м 26с");
      } else expect(container.textContent).toContain("Устаревший сбой текущей попытки");
    }); });
    expect(client.getQueryData<Message[]>(["messages", "p"])?.at(-1)?.generation_status).toBe("failed");
    expect(client.getQueryData<Message[]>(["messages", "p"])?.at(-1)?.generation_failure?.message).toBe("Устаревший сбой текущей попытки");
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); }
});
