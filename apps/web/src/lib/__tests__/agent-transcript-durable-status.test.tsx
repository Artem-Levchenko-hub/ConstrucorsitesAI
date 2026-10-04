import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ latest: vi.fn() }));
vi.mock("@/lib/api/messages", () => ({ getLatestGeneration: api.latest }));
import { AgentTranscript } from "@/components/workspace/AgentTranscript";
import type { AgentStep, GenerationRun } from "@/lib/api/types";

const step: AgentStep = { step: 1, kind: "step", action: "Проверка каталога", path: "", ok: false };
const completed: GenerationRun = { id: "latest-run", project_id: "p", assistant_message_id: "current-message", status: "completed", response_mode: "build", created_at: "2026-10-04T08:00:00Z", started_at: "2026-10-04T08:00:00Z", finished_at: "2026-10-04T08:07:26Z" };

function render(messageId: string, generation: GenerationRun, streaming = false) {
  const client = new QueryClient();
  client.setQueryData(["generation", "p"], generation);
  client.setQueryData(["agent-steps", "p", messageId], [step]);
  const html = renderToStaticMarkup(<QueryClientProvider client={client}><AgentTranscript projectId="p" messageId={messageId} generationStatus="failed" streaming={streaming} initialSteps={[step]} /></QueryClientProvider>);
  expect(client.getQueryData(["agent-steps", "p", messageId])).toEqual([step]);
  client.clear();
  return html;
}
it("renders the exact completed run and its durable duration despite a failed tool step and stale streaming state", () => {
  const html = render("current-message", completed, true);
  expect(html).toContain("Изменения готовы");
  expect(html).toContain("7м 26с");
  expect(html).not.toContain("Сборка не завершена");
  expect(html).toContain('data-agent-transcript="idle"');
});
it("never transfers latest success onto an older failed message", () => {
  expect(render("old-message", completed)).toContain("Сборка не завершена");
});
it("never transfers a foreign project's lifecycle even when its message id matches", () => {
  expect(render("current-message", { ...completed, project_id: "foreign" })).toContain("Сборка не завершена");
});
it("keeps a true latest failed run failed even when the chat text describes success", () => {
  expect(render("current-message", { ...completed, status: "failed" })).toContain("Сборка не завершена");
});

it("hydrates exact authoritative completion after reload, while keeping the failed tool detail available", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  api.latest.mockResolvedValue(completed);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><AgentTranscript projectId="p" messageId="current-message" generationStatus="failed" initialSteps={[step]} /></QueryClientProvider>));
    await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain("Изменения готовы")); });
    expect(api.latest).toHaveBeenCalledWith("p");
    expect(container.textContent).toContain("7м 26с");
    await act(async () => container.querySelector("button")!.click());
    expect(container.textContent).toContain("Не получилось: проверка каталога");
    expect(container.textContent).not.toContain("Сборка не завершена");
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); }
});
