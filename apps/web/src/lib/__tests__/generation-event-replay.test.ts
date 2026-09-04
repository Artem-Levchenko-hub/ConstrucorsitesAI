import { describe, expect, it } from "vitest";

import type { AgentStep } from "@/lib/api/types";
import { mergeAgentStepsBySequence } from "@/lib/agent-steps";

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
      heartbeat,
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
});
