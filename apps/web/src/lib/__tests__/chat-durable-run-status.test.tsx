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
const completed: GenerationRun = { id: "latest", project_id: "p", assistant_message_id: "current", status: "completed", response_mode: "build", created_at: "2026-10-04T08:00:00Z", started_at: "2026-10-04T08:00:00Z", finished_at: "2026-10-04T08:07:26Z",
  acceptance: { run_id: "latest", assistant_message_id: "current", snapshot_id: "saved", accepted_at: null, current_request_complete: true } };
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
        expect(container.textContent).toContain("Итог запроса");
        expect(container.textContent).toContain("7м 26с");
      } else expect(container.textContent).toContain("Устаревший сбой текущей попытки");
    }); });
    expect(client.getQueryData<Message[]>(["messages", "p"])?.at(-1)?.generation_status).toBe("failed");
    expect(client.getQueryData<Message[]>(["messages", "p"])?.at(-1)?.generation_failure?.message).toBe("Устаревший сбой текущей попытки");
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); }
});

it.each(["live", "reloaded", "edit"] as const)(
  "waits for the matching acceptance receipt before showing the %s MAX success report",
  async scenario => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    vi.clearAllMocks();
    const accepted = { ...completed, response_mode: scenario === "edit" ? "edit" : "build" } as const;
    const running: GenerationRun = {
      ...accepted,
      status: "running",
      finished_at: null,
      acceptance: { ...completed.acceptance!, snapshot_id: null, current_request_complete: false },
    };
    const report: Message = {
      ...message,
      content: "Приложение готово.\n\n**Что изменилось**\n\n- Добавлена форма записи",
      snapshot_id: "saved",
      tokens_out: 0,
      generation_status: "running",
      generation_failure: null,
      generation_finished_at: null,
    };
    const older: Message = {
      ...report,
      id: "older",
      snapshot_id: "older-snapshot",
      content: "Приложение готово.\n\nПредыдущая принятая версия",
      generation_status: "completed",
      generation_finished_at: completed.finished_at,
    };
    const rows = scenario === "edit" ? [older, report] : [report];
    api.history.mockResolvedValue(rows);
    api.latest.mockResolvedValue(running);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    if (scenario === "live") {
      // The stream's llm.done has marked the message complete before the server
      // settles the generation. Revalidation can still return a running row.
      client.setQueryData<Message[]>(["messages", "p"], [{ ...report, generation_status: "completed" }]);
      client.setQueryData(["generation", "p"], running);
    }
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    const current = () => container.querySelectorAll('[data-chat-role="assistant"]').item(rows.length - 1);
    try {
      await act(async () => root.render(
        <QueryClientProvider client={client}><ChatPanel projectId="p" projectSlug="p" /></QueryClientProvider>,
      ));
      await act(async () => { await vi.waitFor(() => {
        expect(current()?.textContent).toContain("Добавлена форма записи");
        expect(client.getQueryData<GenerationRun>(["generation", "p"])?.status).toBe("running");
      }); });
      expect(current()?.textContent).toContain("Версия сохранена.");
      expect(current()?.textContent).not.toContain("Приложение готово.");
      expect(current()?.textContent).not.toContain("Продолжить");
      if (scenario === "edit") {
        expect(container.querySelector('[data-chat-role="assistant"]')?.textContent)
          .toContain("Приложение готово.Предыдущая принятая версия");
      }

      api.latest.mockResolvedValue({
        ...accepted,
        acceptance: { ...completed.acceptance!, run_id: "previous-run" },
      });
      await act(async () => { await client.invalidateQueries({ queryKey: ["generation", "p"] }); });
      await act(async () => { await vi.waitFor(() => {
        expect(current()?.textContent).toContain("Проверка результата не подтверждена");
      }); });
      expect(current()?.textContent).not.toContain("Приложение готово.");

      api.latest.mockResolvedValue(accepted);
      await act(async () => { await client.invalidateQueries({ queryKey: ["generation", "p"] }); });
      await act(async () => { await vi.waitFor(() => {
        expect(current()?.textContent).toContain("Приложение готово.");
        expect(current()?.textContent).toContain("Добавлена форма записи");
      }); });
    } finally {
      await act(async () => root.unmount());
      client.clear();
      container.remove();
    }
  },
);

const storedReport: Message = {
  ...message,
  content: "Приложение готово.",
  snapshot_id: "saved",
  tokens_out: 0,
  generation_status: "completed",
  generation_failure: null,
  generation_finished_at: completed.finished_at,
};

const unaccepted: GenerationRun = {
  ...completed,
  acceptance: { ...completed.acceptance!, snapshot_id: null, current_request_complete: false },
};

async function withChat(
  client: QueryClient,
  check: (assistantText: () => string) => Promise<void>,
) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(
      <QueryClientProvider client={client}><ChatPanel projectId="p" projectSlug="p" /></QueryClientProvider>,
    ));
    const assistantText = () => container.querySelector('[data-chat-role="assistant"]')?.textContent ?? "";
    await act(async () => { await vi.waitFor(() => expect(assistantText()).not.toBe("")); });
    await check(assistantText);
  } finally {
    await act(async () => root.unmount());
    client.clear();
    container.remove();
  }
}

it.each(["legacy", "restored"] as const)("keeps the completed %s report without a current acceptance receipt", async scenario => {
  vi.clearAllMocks();
  api.history.mockResolvedValue([{
    ...storedReport,
    snapshot_id: scenario === "restored" ? "snapshot-before-restore" : "saved",
  }]);
  api.latest.mockResolvedValue(unaccepted);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  await withChat(client, async assistantText => {
    await act(async () => { await vi.waitFor(() => {
      expect(client.getQueryData(["generation", "p"])).toEqual(unaccepted);
      expect(assistantText()).toContain("Приложение готово.");
      expect(assistantText()).toContain("Что изменилось в этой версии");
    }); });
  });
});

const optimisticReport = { ...storedReport, generation_finished_at: null };

it.each(["empty", "previous"] as const)("holds llm.done success when the generation cache is %s", async scenario => {
  vi.clearAllMocks();
  api.latest.mockReturnValue(new Promise(() => {}));
  api.history.mockResolvedValue([optimisticReport]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  client.setQueryData(["messages", "p"], [optimisticReport]);
  if (scenario === "previous") {
    client.setQueryData(["generation", "p"], { ...completed, id: "previous-run", assistant_message_id: "previous-message" });
  }
  await withChat(client, async assistantText => {
    expect(assistantText()).toContain("Версия сохранена.");
    expect(assistantText()).not.toContain("Приложение готово.");
    await act(async () => client.setQueryData(["generation", "p"], completed));
    await act(async () => { await vi.waitFor(() => expect(assistantText()).toContain("Приложение готово.")); });
  });
});

it.each(["live cancellation", "reload after cancellation"] as const)("keeps the saved report provisional in %s", async scenario => {
  vi.clearAllMocks();
  const cancelled: GenerationRun = { ...unaccepted, status: "cancelled" };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  if (scenario === "live cancellation") {
    const active: GenerationRun = { ...unaccepted, status: "running", finished_at: null };
    api.history.mockResolvedValue([optimisticReport]);
    api.latest.mockResolvedValue(active);
    client.setQueryData(["generation", "p"], active);
  } else {
    api.history.mockResolvedValue([{ ...storedReport, generation_status: "cancelled" }]);
    api.latest.mockReturnValue(new Promise(() => {}));
  }
  await withChat(client, async assistantText => {
    if (scenario === "live cancellation") {
      await act(async () => client.setQueryData(["generation", "p"], { ...cancelled, status: "cancel_requested", finished_at: null }));
      await act(async () => { await vi.waitFor(() => expect(assistantText()).toContain("Останавливаю работу")); });
      expect(assistantText()).not.toContain("Приложение готово.");
      await act(async () => client.setQueryData(["generation", "p"], cancelled));
    }
    await act(async () => { await vi.waitFor(() => {
      expect(assistantText()).toContain("Работа остановлена");
      expect(assistantText()).toContain("Версия сохранена.");
    }); });
    expect(assistantText()).not.toContain("Приложение готово.");
  });
});

it("retains the partial report and error without announcing a failed build as ready", async () => {
  vi.clearAllMocks();
  const report = { ...optimisticReport, content: "Приложение готово.\n\nСохранена форма записи." };
  const failed: GenerationRun = { ...unaccepted, status: "failed" };
  api.history.mockResolvedValue([report]);
  api.latest.mockResolvedValue(failed);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  await withChat(client, async assistantText => {
    await act(async () => { await vi.waitFor(() => expect(client.getQueryData(["generation", "p"])).toEqual(failed)); });
    expect(assistantText()).toContain("Версия сохранена.");
    expect(assistantText()).toContain("Сохранена форма записи.");
    expect(assistantText()).not.toContain("Приложение готово.");

    api.latest.mockReturnValue(new Promise(() => {}));
    await act(async () => {
      client.setQueryData(["generation", "p"], null);
      client.setQueryData<Message[]>(["messages", "p"], [{
        ...report, generation_status: "failed", generation_finished_at: failed.finished_at,
        generation_failure: { code: "deadline", message: "Сбой при завершении этой сборки", retryable: true },
      }]);
    });
    await act(async () => { await vi.waitFor(() => expect(assistantText()).toContain("Сбой при завершении этой сборки")); });
    expect(assistantText()).toContain("Сохранена форма записи.");
    expect(assistantText()).not.toContain("Приложение готово.");
  });
});
