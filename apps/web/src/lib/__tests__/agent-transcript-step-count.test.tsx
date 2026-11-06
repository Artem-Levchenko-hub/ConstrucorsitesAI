import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AgentTranscript } from "@/components/workspace/AgentTranscript";
import { countAgentActions, mergeAgentStepsBySequence, restorePersistedAgentSteps } from "@/lib/agent-steps";
import { generationEventStep } from "@/lib/generation-event-step";
import type { AgentStep, GenerationEventEnvelope, GenerationRun } from "@/lib/api/types";

const projectId = "project";
const messageId = "new-failed-message";
const run: GenerationRun = {
  id: "new-failed-run", project_id: projectId, assistant_message_id: messageId,
  status: "failed", response_mode: "build", created_at: "2026-11-06T07:32:28Z",
  started_at: "2026-11-06T07:33:20Z", finished_at: "2026-11-06T07:53:05Z",
};

// Match the frozen failed run's kind totals without copying its private content:
// 48 actions and 30 progress/diagnostic records, each scoped to this message.
function history(): AgentStep[] {
  const kinds: AgentStep["kind"][] = [
    ...Array<AgentStep["kind"]>(48).fill("step"),
    ...Array<AgentStep["kind"]>(13).fill("checkpoint"),
    "escalate", "escalate", ...Array<AgentStep["kind"]>(11).fill("stalled"),
    "continuation", "continuation", "tool", "provider_error",
  ];
  return kinds.map((kind, index) => ({
    step: index + 1, kind, action: `Действие ${index + 1}`,
    path: "", tool: kind === "step" ? "read_file" : "", detail: `Детали ${index + 1}`,
    ok: kind === "provider_error" ? false : true,
  }));
}

function replay(snapshot: AgentStep[]): AgentStep[] {
  return snapshot.reduce<AgentStep[]>((current, data, index) => {
    const envelope: GenerationEventEnvelope = {
      event_id: `event-${index + 1}`, run_id: run.id, seq: index + 1,
      type: "agent.step", data: { ...data, message_id: messageId },
    };
    return mergeAgentStepsBySequence(current, [generationEventStep(envelope)!]);
  }, []);
}

function render(initialSteps: AgentStep[], cachedSteps?: AgentStep[]) {
  const client = new QueryClient();
  client.setQueryData(["generation", projectId], run);
  if (cachedSteps) client.setQueryData(["agent-steps", projectId, messageId], cachedSteps);
  const html = renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <AgentTranscript projectId={projectId} messageId="old-failed-message"
        generationStatus="failed" initialSteps={history().slice(0, 48)} />
      <AgentTranscript projectId={projectId} messageId={messageId}
        generationStatus="failed" initialSteps={initialSteps} />
    </QueryClientProvider>,
  );
  client.clear();
  return html;
}

describe("per-message action count across live events and history", () => {
  const countedRun = "11111111-1111-1111-1111-111111111111";
  const countedRow = (number: number): AgentStep => ({
    eventId: `00000000-0000-0000-0000-${number.toString(16).padStart(12, "0")}`,
    runId: countedRun, step: number, kind: "step", action: "Читаю файл", path: "",
    actionCount: number, historyTruncated: number > 200,
  });

  it("keeps the full201 action count with only first199 and latest retained after reload", () => {
    const live = Array.from({ length: 201 }, (_, index) => countedRow(index + 1));
    const saved = [...live.slice(0, 199), live[200]];
    expect(countAgentActions(live, countedRun)).toBe(201);
    expect(countAgentActions(saved, countedRun)).toBe(201);
    expect(countAgentActions(restorePersistedAgentSteps(live, saved)!, countedRun)).toBe(201);
    expect(countAgentActions(mergeAgentStepsBySequence(saved, live), countedRun)).toBe(201);
  });

  it("takes a maximum within the bound run without summing heartbeats or foreign counters", () => {
    const zero = { ...countedRow(1), actionCount: 0, kind: "checkpoint" as const };
    expect(countAgentActions([zero, zero], countedRun)).toBe(0);
    const current = countedRow(201);
    const foreign = { ...countedRow(2), runId: "22222222-2222-2222-2222-222222222222", actionCount: 999 };
    expect(countAgentActions([current, { ...current, kind: "provider_error" }, foreign], countedRun)).toBe(201);
    expect(countAgentActions([current, foreign])).toBeNull();
    expect(countAgentActions([{ ...zero, actionCount: undefined, tool: "capacity_wait" },
      { ...zero, actionCount: undefined, kind: "step", action: "Ожидаю ресурсы сервера" }])).toBe(0);
  });

  it.each([undefined, -1, 2 ** 31, Number.NaN, 1.5])("shows unknown for a truncated history with counter=%s", (actionCount) => {
    const step = { ...countedRow(201), actionCount };
    expect(countAgentActions([countedRow(199), step], countedRun)).toBeNull();
  });

  it("renders an honest truncated-history notice and an unavailable count", () => {
    const client = new QueryClient();
    const html = renderToStaticMarkup(<QueryClientProvider client={client}>
      <AgentTranscript projectId="project" messageId="count-message" streaming
        initialSteps={[{ ...countedRow(201), actionCount: undefined }]} />
    </QueryClientProvider>);
    expect(html).toContain("Число шагов недоступно");
    expect(html).toContain("Сохранённая история сокращена");
    expect(html).not.toContain("199 шаг.");
    client.clear();
  });

  it("counts the same 48 actions live and after reload, keeping older failures isolated", () => {
    const snapshot = history();
    const liveHtml = render(snapshot, replay(snapshot));
    const reloadedHtml = render(snapshot);

    expect(liveHtml.match(/48 шаг\./g)).toHaveLength(2);
    expect(reloadedHtml.match(/48 шаг\./g)).toHaveLength(2);
    expect(reloadedHtml.match(/Сборка не завершена/g)).toHaveLength(2);
    expect(snapshot.at(-1)?.kind).toBe("provider_error");
  });

  it("counts a new live action only on its own message, independently of the older 48", () => {
    const snapshot = history();
    const next = generationEventStep({
      event_id: "event-79", run_id: run.id, seq: 79, type: "agent.step",
      data: { message_id: messageId, step: 49, kind: "step", action: "Проверяю результат" },
    })!;
    const html = render(snapshot, mergeAgentStepsBySequence(replay(snapshot), [next]));

    expect(html.match(/48 шаг\./g)).toHaveLength(1);
    expect(html.match(/49 шаг\./g)).toHaveLength(1);
  });

  it("reconciles service kinds before matching history with durable replay", () => {
    const snapshot = history();
    const live = replay(snapshot);
    const restored = restorePersistedAgentSteps(live, snapshot);
    const reloaded = restorePersistedAgentSteps([], snapshot);

    expect(restored).toHaveLength(78);
    expect(restored).toEqual(live);
    expect(restorePersistedAgentSteps(restored, snapshot)).toEqual(live);
    expect(mergeAgentStepsBySequence(reloaded, live)).toEqual(live);
    expect(countAgentActions(restored!)).toBe(48);
    expect(restored?.at(-1)).toMatchObject({ ok: false, detail: "Детали 78" });
  });

  it("keeps unknown observations visible without counting them as actions", () => {
    const observation = generationEventStep({
      event_id: "future-event", run_id: run.id, seq: 1, type: "agent.step",
      data: { kind: "future-diagnostic", action: "Проверяю состояние", detail: "Состояние сохранено" },
    })!;

    expect(observation).toMatchObject({ kind: "heartbeat", detail: "Состояние сохранено" });
    expect(countAgentActions([observation])).toBe(0);
  });

  it.each([undefined, 1])("uses exact event identity across detail limits with seq=%s", (seq) => {
    const saved: AgentStep = { eventId: "same-event", runId: run.id, seq, step: 1,
      kind: "step", action: "Читаю файл", path: "page.tsx", detail: "x".repeat(4112) };
    const live = { ...saved, detail: saved.detail!.slice(0, 4096) };
    const restored = restorePersistedAgentSteps([live], [saved]);

    expect(restored).toEqual([saved]);
    expect(countAgentActions(restored!)).toBe(1);
    const actualRepeat = { ...saved, eventId: "another-event", seq: seq ? seq + 1 : undefined };
    expect(countAgentActions(mergeAgentStepsBySequence(restored, [actualRepeat]))).toBe(2);
  });
});
