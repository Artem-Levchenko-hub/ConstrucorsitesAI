import { describe, expect, it } from "vitest";

import type { AgentStep } from "@/lib/api/types";
import { countAgentActions, mergeAgentStepsBySequence } from "@/lib/agent-steps";
import { generationEventStep } from "@/lib/generation-event-step";

function event(seq: number, overrides: Partial<AgentStep> = {}): AgentStep {
  return {
    eventId: `event-${seq}`,
    runId: "00000000-0000-0000-0000-000000000001",
    seq,
    step: seq,
    kind: "step",
    action: "build",
    path: "",
    ...overrides,
  };
}

describe("generation event replay", () => {
  it("deduplicates a live event already included in replay", () => {
    const state = Array.from({ length: 80 }, (_, index) => event(index + 52));
    expect(mergeAgentStepsBySequence(state, [event(131)])).toEqual(state);
  });

  it("updates one long-running tool row instead of appending heartbeats", () => {
    const started = event(10, {
      eventId: "operation:00000000-0000-0000-0000-000000000010",
      kind: "heartbeat",
      operationId: "00000000-0000-0000-0000-000000000010",
      action: "Собираю проект",
    });
    const heartbeat = event(11, {
      eventId: started.eventId,
      kind: "heartbeat",
      operationId: started.operationId,
      action: "Собираю проект · ещё работаю",
    });

    expect(mergeAgentStepsBySequence([started], [heartbeat])).toEqual([
      { ...heartbeat, detail: undefined, orderSeq: 10 },
    ]);
  });

  it("replaces the persisted compatibility row with its durable replay event", () => {
    const legacy: AgentStep = {
      step: 7,
      kind: "step",
      action: "Проверяю проект",
      path: "",
      tool: "build",
      ok: true,
    };
    const replayed = event(42, legacy);

    expect(mergeAgentStepsBySequence([legacy], [replayed])).toEqual([replayed]);
  });

  it("preserves the committed action count when replaying a technical result over truncated history", () => {
    const persisted = event(432, {
      eventId: "11111111-1111-4111-a111-111111111111",
      kind: "heartbeat",
      action: "bash",
      actionCount: 218,
      historyTruncated: true,
      tool_diagnostic: { call_id: "53cbbb7d-1ebe-46ea-83ed-296a240a1373", state: "unknown" },
    });
    const replayed = generationEventStep({
      event_id: persisted.eventId!, run_id: persisted.runId!, seq: persisted.seq!,
      type: "agent.tool_finished", data: { ...persisted },
    });
    const restored = mergeAgentStepsBySequence([persisted], [replayed!]);
    expect(restored).toHaveLength(1);
    expect(restored[0]).toMatchObject({ kind: "heartbeat", historyTruncated: true });
    expect(countAgentActions(restored, persisted.runId)).toBe(218);
  });
});
